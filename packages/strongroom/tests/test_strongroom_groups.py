"""Groups of ref moves: all or none, replay after a crash, the transaction."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.strongroom import (
    Digest,
    Entry,
    FormatError,
    GroupHalfApplied,
    Link,
    MissingObject,
    Move,
    Namespace,
    NoSuchPending,
    NotAGroup,
    RefConflict,
    Store,
    Subject,
    Tree,
    UnknownNamespace,
    Version,
    WriteOnceRefused,
    _groups,
    _lifecycle,
    digest_of,
)

WILLEM = Subject("person", "willem")
AT = "2026-09-12T12:00:00Z"


def _ticking():
    count = 0

    def now() -> str:
        nonlocal count
        count += 1
        return f"2026-09-12T12:00:{count:02d}Z"

    return now


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.create(
        tmp_path / "s",
        namespaces=[
            Namespace("tools", "write-once"),
            Namespace("datasets", "monotone"),
            Namespace("urls", "volatile"),
        ],
        clock=_ticking(),
        lock_timeout=0.1,
    )


def _tree(store: Store, **blobs: bytes) -> Digest:
    entries: list[Entry | Link] = [
        Entry(name, "blob", store.put(data), len(data)) for name, data in blobs.items()
    ]
    return store.put(Tree.of(entries).encode())


def _version(store: Store, tree: Digest, *parents: Digest) -> Digest:
    return store.put(Version(tree, parents, WILLEM, AT, "v").encode())


def _crash_after(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    def crash(applied: int) -> None:
        if applied == count:
            raise RuntimeError(f"crashed after {applied}")

    monkeypatch.setattr(_groups, "after_apply", crash)


# Refusals and fallbacks first.


def test_an_unknown_id_and_a_single_publish_are_refused_by_name(store: Store) -> None:
    tree = _tree(store, a=b"a")
    with pytest.raises(NoSuchPending, match="pending/nope does not exist"):
        store.group("nope")
    with pytest.raises(NoSuchPending):
        store.add("nope", "urls", "x", tree, previous=None)
    with pytest.raises(NoSuchPending):
        store.commit("nope", by=WILLEM)
    single = store.publish_begin(tree, by=WILLEM)
    with pytest.raises(NotAGroup, match="is a single publish, not a group"):
        store.group(single.id)
    with pytest.raises(NotAGroup):
        store.add(single.id, "urls", "x", tree, previous=None)
    with pytest.raises(NotAGroup):
        store.commit(single.id, by=WILLEM)
    # The single publish still retires and is not listed as a group.
    assert store.groups() == []
    assert store.pendings() == [single.id]
    store.retire(single.id)


def test_add_refuses_what_a_commit_could_not_apply(store: Store) -> None:
    tree = _tree(store, a=b"a")
    group = store.begin(by=WILLEM, lease=60)
    assert group.moves == () and group.lease == 60 and group.applied == 0
    with pytest.raises(UnknownNamespace):
        store.add(group.id, "nowhere", "x", tree, previous=None)
    with pytest.raises(MissingObject, match="land it before adding the move"):
        store.add(group.id, "urls", "x", digest_of(b"absent"), previous=None)
    store.add(group.id, "urls", "x", tree, previous=None)
    with pytest.raises(RefConflict, match="already moves urls/x; one move per ref"):
        store.add(group.id, "urls", "x", tree, previous=None)
    assert len(store.group(group.id).moves) == 1


def test_a_refused_second_move_leaves_the_first_unmoved(store: Store) -> None:
    # The whole point: the write-once refusal on the second move
    # stops the group before the first move is written, the pending
    # ref stands for a retry, and a corrected group commits.
    tree = _tree(store, a=b"a")
    other = _tree(store, b=b"b")
    store.set_ref("tools", "bun@1", other, previous=None, by=WILLEM)
    group = store.begin(by=WILLEM)
    store.add(group.id, "urls", "x", tree, previous=None)
    store.add(group.id, "tools", "bun@1", tree, previous=other)
    with pytest.raises(WriteOnceRefused):
        store.commit(group.id, by=WILLEM)
    assert store.ref("urls", "x") is None
    assert store.ref("tools", "bun@1") == other
    assert store.pendings() == [group.id]
    assert store.group(group.id).applied == 0
    # A compare-and-swap that lies is refused the same way, first.
    late = store.begin(by=WILLEM)
    store.add(late.id, "urls", "y", tree, previous=tree)
    with pytest.raises(RefConflict, match="names None, not"):
        store.commit(late.id, by=WILLEM)
    assert store.ref("urls", "y") is None
    # A monotone move that is not a fast-forward refuses with the
    # rest unmoved too.
    first = _version(store, tree)
    store.set_ref("datasets", "d/main", first, previous=None, by=WILLEM)
    unrelated = _version(store, other)
    stuck = store.begin(by=WILLEM)
    store.add(stuck.id, "urls", "z", tree, previous=None)
    store.add(stuck.id, "datasets", "d/main", unrelated, previous=first)
    with pytest.raises(RefConflict, match="rebase the version"):
        store.commit(stuck.id, by=WILLEM)
    assert store.ref("urls", "z") is None
    assert store.ref("datasets", "d/main") == first


def test_a_crash_between_two_applies_is_replayed_and_never_retired(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(store, a=b"a")
    version = _version(store, tree)
    group = store.begin(by=WILLEM)
    store.add(group.id, "tools", "bun@1", tree, previous=None)
    store.add(group.id, "datasets", "d/main", version, previous=None, meta={"k": 1})
    _crash_after(monkeypatch, 1)
    with pytest.raises(RuntimeError, match="crashed after 1"):
        store.commit(group.id, by=WILLEM)
    # The first move is on disk, the second is not, and the journal
    # says so; the group is finished, never dropped or extended.
    assert store.ref("tools", "bun@1") == tree
    assert store.ref("datasets", "d/main") is None
    half = store.group(group.id)
    assert half.applied == 1 and len(half.moves) == 2
    with pytest.raises(GroupHalfApplied, match=r"applied 1 of 2 moves \(tools/bun@1\)"):
        store.retire(group.id)
    with pytest.raises(GroupHalfApplied, match="nothing can be added"):
        store.add(group.id, "urls", "x", tree, previous=None)
    assert store.pendings() == [group.id]
    monkeypatch.setattr(_groups, "after_apply", _groups._after_apply)
    records = store.commit(group.id, by=WILLEM)
    assert [record.digest for record in records] == [tree, version]
    assert records[1].meta == {"k": 1}
    assert store.ref("datasets", "d/main") == version
    assert store.pendings() == []
    # The lock files went with the commit: a plain move follows.
    store.set_ref("urls", "x", tree, previous=None, by=WILLEM)


def test_a_crash_before_the_journal_advances_is_replayed_too(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The marker can lag the move by one: the ref is written and the
    # crash lands before the journal's count. Replay reads the ref,
    # sees its digest already there, and skips it as done.
    tree = _tree(store, a=b"a")
    other = _tree(store, b=b"b")
    group = store.begin(by=WILLEM)
    store.add(group.id, "urls", "x", tree, previous=None)
    store.add(group.id, "urls", "y", other, previous=None)
    original = store.set_ref
    calls = {"pending": 0}

    def set_ref(namespace: str, path: str, digest: Digest, **kwargs: object):
        if namespace == _lifecycle.PENDING:
            calls["pending"] += 1
            if calls["pending"] == 1:
                raise RuntimeError("crashed before the journal")
        return original(namespace, path, digest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "set_ref", set_ref)
    with pytest.raises(RuntimeError, match="before the journal"):
        store.commit(group.id, by=WILLEM)
    assert store.ref("urls", "x") == tree and store.ref("urls", "y") is None
    assert store.group(group.id).applied == 0
    records = store.commit(group.id, by=WILLEM)
    assert [record.digest for record in records] == [tree, other]
    assert store.ref("urls", "y") == other


def test_a_journal_that_is_not_a_journal_is_refused(store: Store) -> None:
    with pytest.raises(FormatError, match="wrong keys"):
        Move.from_json({"namespace": "x"}, where="m")
    with pytest.raises(FormatError, match="meta is not a JSON object"):
        Move.from_json(
            {
                "namespace": "urls",
                "path": "x",
                "digest": str(digest_of(b"a")),
                "previous": None,
                "receipt": None,
                "meta": 3,
            },
            where="m",
        )
    tree = _tree(store, a=b"a")
    group = store.begin(by=WILLEM)
    store.add(group.id, "urls", "x", tree, previous=None)
    read = store.group(group.id)
    store.set_ref(
        _lifecycle.PENDING,
        group.id,
        read.manifest,
        previous=read.manifest,
        by=WILLEM,
        meta={**read.record.meta, "applied": 5},
    )
    with pytest.raises(FormatError, match="applied 5 is outside its 1 moves"):
        store.group(group.id)


# The shapes.


def test_the_manifest_roots_every_target_from_the_first_move_to_the_commit(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_lifecycle, "new_pending_id", lambda: "g1")
    tree = _tree(store, a=b"a", b=b"b")
    version = _version(store, tree)
    group = store.begin(by=WILLEM, lease=90)
    assert group.id == "g1"
    assert Tree.decode(store.read(group.manifest)).entries == ()
    assert group.record.meta == {"lease": 90, "moves": [], "applied": 0}
    superseded = [group.manifest]
    group = store.add(group.id, "tools", "bun@1", tree, previous=None)
    superseded.append(group.manifest)
    group = store.add(group.id, "datasets", "d/main", version, previous=None)
    manifest = Tree.decode(store.read(group.manifest))
    listed = [
        (entry.name, entry.kind, entry.digest)
        for entry in manifest.entries
        if isinstance(entry, Entry)
    ]
    assert listed == [
        ("0000", "tree", tree),
        ("0001", "blob", version),
    ]
    assert group.record.meta["moves"] == [
        {
            "namespace": "tools",
            "path": "bun@1",
            "digest": str(tree),
            "previous": None,
            "receipt": None,
            "meta": {},
        },
        {
            "namespace": "datasets",
            "path": "d/main",
            "digest": str(version),
            "previous": None,
            "receipt": None,
            "meta": {},
        },
    ]
    assert store.groups() == [group]
    # A sweep mid-group removes the manifests each add superseded and
    # nothing the current manifest names.
    report = store.sweep()
    assert set(report.removed) == set(superseded) and report.pending == ("g1",)
    assert store.state(tree) == "present" and store.state(version) == "present"
    records = store.commit("g1", by=WILLEM)
    assert [record.digest for record in records] == [tree, version]
    assert store.pendings() == [] and store.groups() == []
    # The refs root what the manifest rooted; only the manifest goes.
    removed = store.sweep().removed
    assert removed == (group.manifest,)
    assert store.ref("tools", "bun@1") == tree


def test_a_move_already_done_is_skipped_and_an_empty_group_commits(
    store: Store,
) -> None:
    tree = _tree(store, a=b"a")
    store.set_ref("tools", "bun@1", tree, previous=None, by=WILLEM)
    group = store.begin(by=WILLEM)
    store.add(group.id, "tools", "bun@1", tree, previous=tree)
    records = store.commit(group.id, by=WILLEM)
    assert len(records) == 1
    assert records[0].digest == tree and records[0].previous is None
    assert store.commit(store.begin(by=WILLEM).id, by=WILLEM) == ()
    fresh = store.begin(by=WILLEM)
    assert store.retire(fresh.id) == fresh.manifest
    assert store.pendings() == []


def test_the_transaction_commits_on_a_clean_exit(store: Store) -> None:
    tree = _tree(store, a=b"a")
    version = _version(store, tree)
    with store.transaction(by=WILLEM, lease=30) as tx:
        assert store.group(tx.id).lease == 30
        tx.move("tools", "bun@1", tree, previous=None)
        state = tx.move("datasets", "d/main", version, previous=None, meta={"k": 1})
        assert len(state.moves) == 2
        assert store.pendings() == [tx.id]
    assert [record.digest for record in tx.records] == [tree, version]
    assert tx.records[1].meta == {"k": 1}
    assert store.ref("tools", "bun@1") == tree
    assert store.pendings() == []


def test_the_transaction_retires_on_an_exception_and_on_a_refused_commit(
    store: Store,
) -> None:
    tree = _tree(store, a=b"a")
    other = _tree(store, b=b"b")
    with (
        pytest.raises(ValueError, match="the caller's own"),
        store.transaction(by=WILLEM) as tx,
    ):
        tx.move("urls", "x", tree, previous=None)
        raise ValueError("the caller's own")
    assert store.ref("urls", "x") is None and store.pendings() == []
    store.set_ref("tools", "bun@1", other, previous=None, by=WILLEM)
    with pytest.raises(WriteOnceRefused), store.transaction(by=WILLEM) as tx:
        tx.move("urls", "x", tree, previous=None)
        tx.move("tools", "bun@1", tree, previous=other)
    assert store.ref("urls", "x") is None and store.pendings() == []
    # A group retired inside the body by hand has nothing to commit,
    # and the exit says so by name.
    with pytest.raises(NoSuchPending), store.transaction(by=WILLEM) as tx:
        store.retire(tx.id)
    assert store.pendings() == []


def test_the_transaction_leaves_a_half_applied_group_for_replay(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(store, a=b"a")
    other = _tree(store, b=b"b")
    _crash_after(monkeypatch, 1)
    ids: list[str] = []
    with (
        pytest.raises(RuntimeError, match="crashed after 1"),
        store.transaction(by=WILLEM) as tx,
    ):
        ids.append(tx.id)
        tx.move("urls", "x", tree, previous=None)
        tx.move("urls", "y", other, previous=None)
    (group_id,) = ids
    assert store.pendings() == [group_id]
    assert store.group(group_id).applied == 1
    monkeypatch.setattr(_groups, "after_apply", _groups._after_apply)
    store.commit(group_id, by=WILLEM)
    assert store.ref("urls", "y") == other and store.pendings() == []
