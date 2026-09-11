"""The lifecycle: the pending publish, reachability, erasure.

Roots are every ref on disk, pins and pending refs included. Marking
walks the structured formats by shape: a version yields its tree,
parents, receipt and attachments; a tree yields its entries; anything
else is a leaf. Reading a blob as a tree when its bytes happen to be
one keeps more than needed and never less, which is the safe
direction, and no namespace's meaning is read. Sweeping removes what
marking did not reach, re-scanning pending refs immediately before
deleting so a publish that began during the sweep still roots its
objects. The one age rule is orphaned scratch.

An erased object becomes a tombstone under its path: the name stays
valid in every tree that carries it, the bytes go, and landing it
again is refused while the tombstone stands.

The functions here are the bodies of the [livery.strongroom.Store][]
methods of the same names; reach for the methods.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from livery.strongroom._canonical import FormatError, Value
from livery.strongroom._digest import Digest
from livery.strongroom._errors import (
    MissingObject,
    NoSuchPending,
    RefConflict,
    RefProtected,
)
from livery.strongroom._fields import Subject
from livery.strongroom._records import RefRecord, Tombstone
from livery.strongroom._tree import Entry, Tree
from livery.strongroom._version import Version

if TYPE_CHECKING:
    from livery.strongroom._store import Store

PENDING = "pending"
"""The namespace of publishes that have begun and not committed."""

PINS = "pins"
"""The namespace of explicit roots."""

_PART = ".part"


def new_pending_id() -> str:
    """A fresh pending ref name; the seam tests fix for a known name."""
    return secrets.token_hex(8)


def _after_mark() -> None:
    """Nothing: the sweep proceeds from marking to deleting."""


after_mark: Callable[[], None] = _after_mark
"""Runs between marking and deleting.

A variable, not a function, so the conformance harness can begin a
publish there and put the sweep back afterwards.
"""


@dataclass(frozen=True)
class Pending:
    """A publish that has begun and not yet committed.

    Attributes:
        id: the pending ref's name under `pending/`.
        target: the tree or version the publish will name.
        record: the pending ref's record; its `meta` carries the lease
            in seconds under `lease`.
    """

    id: str
    target: Digest
    record: RefRecord


@dataclass(frozen=True)
class SweepReport:
    """What a sweep did.

    Attributes:
        reached: every object marking reached, present or not.
        removed: every object removed as unreached.
        scratch_removed: every scratch file removed as older than the
            age bound.
        pending: the pending refs found by the re-scan immediately
            before deleting, every one a root.
    """

    reached: tuple[Digest, ...] = field(default_factory=tuple)
    removed: tuple[Digest, ...] = field(default_factory=tuple)
    scratch_removed: tuple[Path, ...] = field(default_factory=tuple)
    pending: tuple[str, ...] = field(default_factory=tuple)


def publish_begin(
    store: Store, target: Digest, *, by: Subject, lease: float
) -> Pending:
    """Begin a publish: root *target* under a pending ref before anything else."""
    if store.state(target) != "present":
        raise MissingObject(
            f"publish target {target} is not present at {store.root}; land it"
            " before beginning"
        )
    pending_id = new_pending_id()
    meta: dict[str, Value] = {"lease": int(lease)}
    record = store.set_ref(PENDING, pending_id, target, previous=None, by=by, meta=meta)
    return Pending(pending_id, target, record)


def publish_commit(
    store: Store,
    pending_id: str,
    namespace: str,
    path: str,
    *,
    previous: Digest | None,
    by: Subject,
    receipt: Digest | None,
    meta: dict[str, Value] | None,
) -> RefRecord:
    """Commit a publish: move the real ref, then drop the pending one."""
    target = store.ref(PENDING, pending_id)
    if target is None:
        raise NoSuchPending(
            f"pending/{pending_id} does not exist at {store.root}; the publish"
            " was retired, committed already, or never begun"
        )
    record = store.set_ref(
        namespace, path, target, previous=previous, by=by, receipt=receipt, meta=meta
    )
    store.drop_ref(PENDING, pending_id, previous=target)
    return record


def retire(store: Store, pending_id: str) -> Digest:
    """Drop a pending ref deliberately; what it named is returned."""
    target = store.ref(PENDING, pending_id)
    if target is None:
        raise NoSuchPending(f"pending/{pending_id} does not exist at {store.root}")
    store.drop_ref(PENDING, pending_id, previous=target)
    return target


def drop_ref(store: Store, namespace: str, path: str, *, previous: Digest) -> None:
    """Remove a volatile ref and its record by compare-and-swap."""
    declared = store._namespace(namespace)
    if declared.mutation != "volatile":
        raise RefProtected(
            f"ref {namespace}/{path} is {declared.mutation}; only a volatile ref"
            " may be dropped, the rest go by pruning under a retention class"
        )
    target = store.ref_path(namespace, path)
    with store._locked(target):
        current = store.ref(namespace, path)
        if current != previous:
            raise RefConflict(
                f"ref {namespace}/{path} names {current}, not {previous}; re-read"
                " it and retry"
            )
        target.unlink()
        target.with_name(target.name + ".record").unlink()


def pin(store: Store, name: str, digest: Digest, *, by: Subject) -> RefRecord:
    """Root *digest* under `pins/<name>`, replacing an earlier pin of that name."""
    return store.set_ref(PINS, name, digest, previous=store.ref(PINS, name), by=by)


def unpin(store: Store, name: str) -> Digest:
    """Drop `pins/<name>` under the maintenance lease; what it named is returned."""
    with store._maintenance():
        current = store.ref(PINS, name)
        if current is None:
            raise RefConflict(f"pins/{name} does not exist at {store.root}")
        store.drop_ref(PINS, name, previous=current)
        return current


def erase(
    store: Store, digest: Digest, *, by: Subject, reason: str, receipt: Digest | None
) -> Tombstone:
    """Erase the bytes and keep the fact: a tombstone under the object's path."""
    stone = Tombstone(digest, store.clock(), by, receipt, reason)
    store.write_tombstone(stone)
    store.evict(digest)
    return stone


def reachable_from(store: Store, digest: Digest) -> Iterator[Digest]:
    """Every object reachable from *digest*, itself included.

    A digest that is absent or erased is yielded and not walked: a
    tree still names an erased entry, and availability is a fact
    separate from reachability.
    """
    seen: set[Digest] = set()
    stack = [digest]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        yield current
        if store.state(current) == "present":
            stack.extend(children(store.read(current)))


def children(data: bytes) -> list[Digest]:
    """The objects *data* names, read by shape: a version, a tree, or none."""
    # A blob misread as a structured format keeps more, never less, so
    # no reachable object is ever lost to a wrong guess.
    if not data.startswith(b"{") or not data.endswith(b"}"):
        return []
    try:
        version = Version.decode(data)
    except FormatError:
        pass
    else:
        found = [version.tree, *version.parents, *version.attachments.values()]
        if version.receipt is not None:
            found.append(version.receipt)
        return found
    try:
        tree = Tree.decode(data)
    except FormatError:
        return []
    return [entry.digest for entry in tree.entries if isinstance(entry, Entry)]


def sweep(store: Store, *, scratch_age: float, hook: Callable[[], None]) -> SweepReport:
    """Mark from every ref, re-scan pending refs, remove the unreached."""
    with store._maintenance():
        reached: set[Digest] = set()
        for root in store.roots():
            reached.update(reachable_from(store, root))
        hook()
        # A publish that began after the roots were read is rooted by
        # its pending ref, so pending refs are read again immediately
        # before anything is deleted.
        pending = tuple(store.refs(PENDING))
        late = [store.ref(PENDING, pending_id) for pending_id in pending]
        for target in [digest for digest in late if digest is not None]:
            reached.update(reachable_from(store, target))
        removed = tuple(sorted(d for d in store.objects() if d not in reached))
        for digest in removed:
            store.evict(digest)
        scratch_removed = tuple(sorted(_old_scratch(store.root, scratch_age)))
        for path in scratch_removed:
            path.unlink(missing_ok=True)
        return SweepReport(tuple(sorted(reached)), removed, scratch_removed, pending)


def _old_scratch(root: Path, age: float) -> Iterator[Path]:
    cutoff = time.time() - age
    for path in root.rglob(f"*{_PART}"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            yield path
