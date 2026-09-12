"""Groups of ref moves that land as a whole or not at all.

A group is one pending ref, `pending/<id>`, naming a manifest tree
whose entries are every move's target, so the sweep keeps them all
from the first move to the commit. The record's `meta` carries the
journal: the lease, the moves in order, and how many have been
applied. `begin` opens a group, `add` appends a move, `commit` checks
every move under the maintenance lease and the refs' locks, refuses
the whole group with nothing moved on the first refusal, then applies
the moves and drops the pending ref. A crash between two applies
leaves the journal with its count, and `commit` on the same id
replays it. `retire` abandons a group nothing of which has moved.

The single publish, [livery.strongroom.Store.publish_begin][] and
[livery.strongroom.Store.publish_commit][], shares the pending
namespace and the move code but names its ref at commit, so its
pending ref names the target itself and carries no journal.

The functions here are the bodies of the [livery.strongroom.Store][]
methods of the same names; reach for the methods, and for
[livery.strongroom.Store.transaction][] in everyday code.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import livery.strongroom._lifecycle as lifecycle
from livery.strongroom._canonical import FormatError, Value
from livery.strongroom._digest import Digest
from livery.strongroom._errors import (
    GroupHalfApplied,
    MissingObject,
    NoSuchPending,
    NotAGroup,
    RefConflict,
)
from livery.strongroom._fields import (
    Subject,
    expect_digest,
    expect_int,
    expect_list,
    expect_object,
    expect_optional_digest,
    expect_str,
)
from livery.strongroom._lifecycle import PENDING
from livery.strongroom._records import RefRecord
from livery.strongroom._tree import Entry, EntryKind, Tree

if TYPE_CHECKING:
    from livery.strongroom._store import Store


def _after_apply(applied: int) -> None:
    """Nothing: the commit proceeds from one apply to the next."""


after_apply: Callable[[int], None] = _after_apply
"""Runs after each move of a commit is applied and journaled.

A variable, not a function, so the conformance harness can make a
commit stop part-way, as a crash would, and put it back afterwards.
"""


@dataclass(frozen=True)
class Move:
    """One ref move of a group.

    Attributes:
        namespace: a declared namespace.
        path: the ref's path.
        digest: what the ref names afterwards.
        previous: what the ref is expected to name now; None to
            create.
        receipt: the receipt of the moving call, if any.
        meta: the consumer's own object for the ref's record.
    """

    namespace: str
    path: str
    digest: Digest
    previous: Digest | None
    receipt: Digest | None = None
    meta: dict[str, Value] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """`<namespace>/<path>`, the ref's name in a message."""
        return f"{self.namespace}/{self.path}"

    def to_json(self) -> dict[str, Value]:
        """The move as a JSON object, the journal's entry."""
        return {
            "namespace": self.namespace,
            "path": self.path,
            "digest": str(self.digest),
            "previous": None if self.previous is None else str(self.previous),
            "receipt": None if self.receipt is None else str(self.receipt),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_json(cls, value: Value, *, where: str) -> Move:
        """A move from a journal entry.

        Raises:
            FormatError: when the entry is not a move.
        """
        data = expect_object(
            value,
            ("namespace", "path", "digest", "previous", "receipt", "meta"),
            where=where,
        )
        meta = data["meta"]
        if not isinstance(meta, dict):
            raise FormatError(f"{where} meta is not a JSON object")
        return cls(
            expect_str(data["namespace"], where=f"{where} namespace"),
            expect_str(data["path"], where=f"{where} path"),
            expect_digest(data["digest"], where=f"{where} digest"),
            expect_optional_digest(data["previous"], where=f"{where} previous"),
            expect_optional_digest(data["receipt"], where=f"{where} receipt"),
            dict(meta),
        )


@dataclass(frozen=True)
class Group:
    """A group of moves that has begun and not yet committed.

    Attributes:
        id: the pending ref's name under `pending/`.
        moves: the moves, in the order they apply.
        applied: how many of them a commit has already applied; more
            than zero only after a commit stopped part-way.
        lease: the seconds the group expected to take, from `begin`.
        manifest: the tree of every move's target the pending ref
            names.
        record: the pending ref's record, the journal in its `meta`.
    """

    id: str
    moves: tuple[Move, ...]
    applied: int
    lease: int
    manifest: Digest
    record: RefRecord


def is_group(record: RefRecord) -> bool:
    """Whether a pending ref's record carries a group's journal."""
    return "moves" in record.meta


def _journal(record: RefRecord, *, where: str) -> tuple[tuple[Move, ...], int, int]:
    moves = tuple(
        Move.from_json(entry, where=f"{where} move {index}")
        for index, entry in enumerate(expect_list(record.meta["moves"], where=where))
    )
    applied = expect_int(record.meta.get("applied", 0), where=f"{where} applied")
    lease = expect_int(record.meta.get("lease", 0), where=f"{where} lease")
    if not 0 <= applied <= len(moves):
        raise FormatError(
            f"{where} applied {applied} is outside its {len(moves)} moves"
        )
    return moves, applied, lease


def _meta(moves: tuple[Move, ...], *, applied: int, lease: int) -> dict[str, Value]:
    return {
        "lease": lease,
        "moves": [move.to_json() for move in moves],
        "applied": applied,
    }


def _manifest(store: Store, moves: tuple[Move, ...]) -> Digest:
    """Land the tree of every move's target, one entry per move in order."""
    entries: list[Entry] = []
    for index, move in enumerate(moves):
        data = store.read(move.digest)
        kind: EntryKind = "tree" if _is_tree(data) else "blob"
        entries.append(Entry(f"{index:04d}", kind, move.digest, len(data)))
    return store.put(Tree.of(entries).encode())


def _is_tree(data: bytes) -> bool:
    if not data.startswith(b"{"):
        return False
    try:
        Tree.decode(data)
    except FormatError:
        return False
    return True


def read_group(store: Store, group_id: str) -> Group:
    """The group under `pending/<group_id>`.

    Raises:
        NoSuchPending: when the pending ref does not exist.
        NotAGroup: when it is a single publish's.
    """
    found = store._read_ref(PENDING, group_id)
    if found is None:
        raise NoSuchPending(
            f"pending/{group_id} does not exist at {store.root}; the group was"
            " retired, committed already, or never begun"
        )
    manifest, record = found
    if not is_group(record):
        raise NotAGroup(
            f"pending/{group_id} is a single publish, not a group; commit it with"
            " publish_commit or retire it"
        )
    moves, applied, lease = _journal(record, where=f"pending/{group_id}")
    return Group(group_id, moves, applied, lease, manifest, record)


def groups(store: Store) -> list[Group]:
    """Every group that has begun and not committed, sorted by id."""
    found: list[Group] = []
    for pending_id in store.refs(PENDING):
        read = store._read_ref(PENDING, pending_id)
        if read is not None and is_group(read[1]):
            found.append(read_group(store, pending_id))
    return found


def begin(store: Store, *, by: Subject, lease: float) -> Group:
    """Open an empty group: a pending ref naming an empty manifest."""
    group_id = lifecycle.new_pending_id()
    manifest = _manifest(store, ())
    record = store.set_ref(
        PENDING,
        group_id,
        manifest,
        previous=None,
        by=by,
        meta=_meta((), applied=0, lease=int(lease)),
    )
    return Group(group_id, (), 0, int(lease), manifest, record)


def add(
    store: Store,
    group_id: str,
    namespace: str,
    path: str,
    digest: Digest,
    *,
    previous: Digest | None,
    receipt: Digest | None,
    meta: dict[str, Value] | None,
) -> Group:
    """Append a move to the group, re-rooting its manifest.

    Raises:
        NoSuchPending: when the group does not exist.
        NotAGroup: when the pending ref is a single publish's.
        GroupHalfApplied: when a commit already applied part of it.
        MissingObject: when *digest* is not present here.
        RefConflict: when the group already moves that ref.
        UnknownNamespace: when the namespace was not declared.
    """
    group = read_group(store, group_id)
    if group.applied:
        raise GroupHalfApplied(
            f"pending/{group_id} has {group.applied} of {len(group.moves)} moves"
            " applied; commit it again to finish, nothing can be added"
        )
    store.ref_path(namespace, path)
    if store.state(digest) != "present":
        raise MissingObject(
            f"move target {digest} is not present at {store.root}; land it"
            " before adding the move"
        )
    move = Move(namespace, path, digest, previous, receipt, dict(meta or {}))
    if any(other.ref == move.ref for other in group.moves):
        raise RefConflict(
            f"pending/{group_id} already moves {move.ref}; one move per ref"
        )
    moves = (*group.moves, move)
    manifest = _manifest(store, moves)
    record = store.set_ref(
        PENDING,
        group_id,
        manifest,
        previous=group.manifest,
        by=group.record.by,
        meta=_meta(moves, applied=0, lease=group.lease),
    )
    return Group(group_id, moves, 0, group.lease, manifest, record)


def commit(store: Store, group_id: str, *, by: Subject) -> tuple[RefRecord, ...]:
    """Apply every move, or none: check them all, then move them all.

    Under the maintenance lease, so no sweep runs beside it, and the
    moved refs' locks in sorted order. Every move is checked first,
    the compare-and-swap and the namespace's class; the first refusal
    stops the group with nothing moved. Then each move applies in
    order and the journal's count advances, so a crash between two
    applies is replayed by the next commit: a ref that already names
    its digest is skipped. The pending ref is dropped last.

    Returns:
        One record per move, in order; the existing record where a
        move was already applied.

    Raises:
        NoSuchPending: when the group does not exist.
        NotAGroup: when the pending ref is a single publish's.
        RefConflict: when a ref does not name its expected previous,
            or its class refuses the move; nothing has moved.
        LockTimeout: when a lease or a lock outlasts the timeout.
    """
    group = read_group(store, group_id)
    ordered = sorted(group.moves, key=lambda move: move.ref)
    with store._maintenance(), contextlib.ExitStack() as locks:
        for move in ordered:
            target = store.ref_path(move.namespace, move.path)
            locks.enter_context(store._locked(target))
        for move in group.moves[group.applied :]:
            # A ref that already names its digest is a move a crashed
            # commit wrote before its journal advanced: done, not
            # checked against a previous it no longer names.
            existing = store._read_ref(move.namespace, move.path)
            if existing is not None and existing[0] == move.digest:
                continue
            store._check_move(move.namespace, move.path, move.digest, move.previous)
        records: list[RefRecord] = []
        applied = group.applied
        for index, move in enumerate(group.moves):
            existing = store._read_ref(move.namespace, move.path)
            if existing is not None and existing[0] == move.digest:
                records.append(existing[1])
            else:
                records.append(
                    store._move_locked(
                        move.namespace,
                        move.path,
                        move.digest,
                        previous=move.previous,
                        by=by,
                        receipt=move.receipt,
                        meta=move.meta,
                    )
                )
            if index >= applied:
                applied = index + 1
                store.set_ref(
                    PENDING,
                    group_id,
                    group.manifest,
                    previous=group.manifest,
                    by=group.record.by,
                    meta=_meta(group.moves, applied=applied, lease=group.lease),
                )
                after_apply(applied)
        store.drop_ref(PENDING, group_id, previous=group.manifest)
    return tuple(records)


def refuse_half_applied(store: Store, pending_id: str, record: RefRecord) -> None:
    """Refuse retiring a group a commit has already applied part of.

    Raises:
        GroupHalfApplied: naming the moves applied; the group is
            finished by another commit, never dropped.
    """
    if not is_group(record):
        return
    moves, applied, _lease = _journal(record, where=f"pending/{pending_id}")
    if applied:
        done = ", ".join(move.ref for move in moves[:applied])
        raise GroupHalfApplied(
            f"pending/{pending_id} has applied {applied} of {len(moves)} moves"
            f" ({done}); commit it again to finish it, it cannot be retired"
        )


class Transaction:
    """A group under a `with`: moves recorded as they come, the exit decides.

    Entering begins the group. A clean exit commits it, every move or
    none, and `records` then holds the records written. An exception
    before the commit began retires the group, nothing moved. A
    commit refused on its checks retires the group too, since nothing
    moved, and the refusal is what the `with` raises. A commit that
    stopped between two applies re-raises and leaves the group for a
    later [livery.strongroom.Store.commit][] on its id.

    Attributes:
        id: the group's pending ref name, known after entry.
        records: the records the commit wrote, in move order; empty
            until the commit.
    """

    def __init__(self, store: Store, *, by: Subject, lease: float) -> None:
        self._store = store
        self._by = by
        self._lease = lease
        self.id = ""
        self.records: tuple[RefRecord, ...] = ()

    def move(
        self,
        namespace: str,
        path: str,
        digest: Digest,
        *,
        previous: Digest | None,
        receipt: Digest | None = None,
        meta: dict[str, Value] | None = None,
    ) -> Group:
        """Record a move; the group's state after it."""
        return add(
            self._store,
            self.id,
            namespace,
            path,
            digest,
            previous=previous,
            receipt=receipt,
            meta=meta,
        )


@contextlib.contextmanager
def transaction(
    store: Store, *, by: Subject, lease: float
) -> Generator[Transaction]:
    """The body of [livery.strongroom.Store.transaction][]."""
    handle = Transaction(store, by=by, lease=lease)
    handle.id = begin(store, by=by, lease=lease).id
    try:
        yield handle
    except BaseException:
        _retire_if_untouched(store, handle.id)
        raise
    try:
        handle.records = commit(store, handle.id, by=by)
    except RefConflict:
        _retire_if_untouched(store, handle.id)
        raise


def _retire_if_untouched(store: Store, group_id: str) -> None:
    found = store._read_ref(PENDING, group_id)
    if found is None:
        return
    manifest, record = found
    if is_group(record) and _journal(record, where=f"pending/{group_id}")[1]:
        return
    store.drop_ref(PENDING, group_id, previous=manifest)
