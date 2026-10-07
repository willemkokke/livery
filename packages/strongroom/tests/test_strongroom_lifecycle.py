"""The lifecycle: refusals first, then the publish, the sweep and erasure."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from livery.strongroom import (
    Clock,
    Digest,
    Entry,
    ErasedObject,
    IntegrityError,
    Link,
    LockTimeout,
    MissingObject,
    Namespace,
    NoSuchPending,
    RefConflict,
    RefProtected,
    Store,
    Subject,
    Tombstone,
    Tree,
    Version,
    _lifecycle,
    digest_of,
)

WILLEM = Subject("person", "willem")
AT = "2026-09-11T12:00:00Z"


def ticking() -> Clock:
    second = [0]

    def clock() -> str:
        second[0] += 1
        return f"2026-09-11T12:00:{second[0]:02d}Z"

    return clock


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.create(
        tmp_path / "s",
        namespaces=[
            Namespace("tools", "write-once"),
            Namespace("datasets", "monotone"),
            Namespace("urls", "volatile"),
        ],
        clock=ticking(),
        lock_timeout=0.1,
    )


def _tree(store: Store, **blobs: bytes) -> Digest:
    entries: list[Entry | Link] = [
        Entry(name, "blob", store.put(data), len(data)) for name, data in blobs.items()
    ]
    return store.put(Tree.of(entries).encode())


def _version(store: Store, tree: Digest, *parents: Digest, **extra: Digest) -> Digest:
    version = Version(tree, parents, WILLEM, AT, "v", attachments=extra)
    return store.put(version.encode())


# Refusals.


def test_publish_begin_refuses_an_absent_target(store: Store) -> None:
    with pytest.raises(MissingObject, match="land it before beginning"):
        store.publish_begin(digest_of(b"nowhere"), by=WILLEM)
    assert store.pendings() == []


def test_commit_and_retire_refuse_a_pending_that_is_gone(store: Store) -> None:
    with pytest.raises(NoSuchPending, match="pending/nope does not exist"):
        store.publish_commit("nope", "urls", "x", previous=None, by=WILLEM)
    with pytest.raises(NoSuchPending):
        store.retire("nope")
    pending = store.publish_begin(_tree(store, a=b"a"), by=WILLEM)
    store.retire(pending.id)
    with pytest.raises(
        NoSuchPending, match="retired, committed already, or never begun"
    ):
        store.publish_commit(pending.id, "urls", "x", previous=None, by=WILLEM)


def test_commit_obeys_the_target_namespaces_class(store: Store) -> None:
    first = _version(store, _tree(store, a=b"a"))
    store.set_ref("datasets", "d/main", first, previous=None, by=WILLEM)
    unrelated = _version(store, _tree(store, b=b"b"))
    pending = store.publish_begin(unrelated, by=WILLEM)
    with pytest.raises(RefConflict, match="rebase the version"):
        store.publish_commit(
            pending.id, "datasets", "d/main", previous=first, by=WILLEM
        )
    # A refused commit leaves the pending ref standing for a retry.
    assert store.pendings() == [pending.id]


def test_dropping_a_protected_ref_is_refused(store: Store) -> None:
    a = store.put(b"a")
    store.set_ref("tools", "x", a, previous=None, by=WILLEM)
    with pytest.raises(RefProtected, match="is write-once; only a volatile ref"):
        store.drop_ref("tools", "x", previous=a)
    with pytest.raises(RefProtected, match="is monotone"):
        store.drop_ref("datasets", "x", previous=a)
    assert store.ref("tools", "x") == a


def test_dropping_loses_the_compare_and_swap_loudly(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    store.set_ref("urls", "x", a, previous=None, by=WILLEM)
    with pytest.raises(RefConflict, match=f"names {a}, not {b}"):
        store.drop_ref("urls", "x", previous=b)
    with pytest.raises(RefConflict, match=f"names None, not {a}"):
        store.drop_ref("urls", "missing", previous=a)
    assert store.ref("urls", "x") == a


def test_unpin_refuses_a_missing_pin_and_waits_for_the_lease(store: Store) -> None:
    with pytest.raises(RefConflict, match="pins/none does not exist"):
        store.unpin("none")
    lock = store.root / "index" / "maintenance.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
    with pytest.raises(LockTimeout):
        store.unpin("none")
    with pytest.raises(LockTimeout):
        store.sweep()
    lock.unlink()


def test_an_erased_object_names_its_tombstone_everywhere(store: Store) -> None:
    tree = _tree(store, face=b"a face")
    face = digest_of(b"a face")
    store.set_ref("urls", "keep", tree, previous=None, by=WILLEM)
    stone = store.erase(face, by=WILLEM, reason="erasure request 42")
    assert stone == Tombstone(
        face, "2026-09-11T12:00:02Z", WILLEM, None, "erasure request 42"
    )
    assert store.tombstone(face) == stone
    assert store.state(face) == "erased"
    with pytest.raises(
        ErasedObject,
        match="erased at 2026-09-11T12:00:02Z by willem: erasure request 42",
    ):
        store.path(face)
    with pytest.raises(ErasedObject, match="erasure request 42"):
        store.land(b"a face")
    with pytest.raises(ErasedObject, match="erasure request 42"):
        store.fetch(face)
    # The tree still names it, and the sweep keeps the tombstone.
    assert face in set(store.reachable(tree))
    report = store.sweep()
    assert face in report.reached
    assert store.state(face) == "erased"
    assert store.verified_size(face) is None


def test_a_malformed_tombstone_is_an_integrity_error(store: Store) -> None:
    digest = digest_of(b"x")
    path = store.object_path(digest)
    path.parent.mkdir(parents=True)
    path.with_name(path.name + ".tombstone").write_bytes(b"{}")
    with pytest.raises(IntegrityError, match="tombstone of"):
        store.tombstone(digest)
    # A tombstone that cannot be read still stands: the state is erased
    # and the refusal says so without a reason.
    assert store.state(digest) == "erased"
    with pytest.raises(IntegrityError):
        store.path(digest)


def test_no_tombstone_reads_as_none(store: Store) -> None:
    assert store.tombstone(digest_of(b"never erased")) is None


def test_erasing_an_absent_object_refuses_its_future_landing(store: Store) -> None:
    digest = digest_of(b"never again")
    store.erase(digest, by=WILLEM, reason="pre-empted")
    with pytest.raises(ErasedObject, match="pre-empted"):
        store.put(b"never again")


# The publish and the sweep.


def test_a_publish_roots_its_target_from_begin_to_commit(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_lifecycle, "new_pending_id", lambda: "p1")
    tree = _tree(store, a=b"a", b=b"b")
    pending = store.publish_begin(tree, by=WILLEM, lease=60)
    assert pending.id == "p1"
    assert pending.target == tree
    assert pending.record.meta == {"lease": 60}
    assert store.pendings() == ["p1"]
    report = store.sweep()
    assert report.removed == ()
    assert report.pending == ("p1",)
    record = store.publish_commit(
        "p1",
        "tools",
        "bun@1",
        previous=None,
        by=WILLEM,
        receipt=tree,
        meta={"host": "x"},
    )
    assert record.receipt == tree
    assert record.meta == {"host": "x"}
    assert store.ref("tools", "bun@1") == tree
    assert store.pendings() == []
    assert store.sweep().removed == ()


def test_the_sweep_removes_only_what_no_root_reaches(store: Store) -> None:
    kept_tree = _tree(store, a=b"kept")
    version = _version(store, kept_tree, card=store.put(b"card"))
    receipt = store.put(b"a receipt")
    version_with_receipt = store.put(
        Version(kept_tree, (version,), WILLEM, AT, "", receipt=receipt).encode()
    )
    store.set_ref("datasets", "d/main", version_with_receipt, previous=None, by=WILLEM)
    pinned = store.put(b"pinned")
    store.pin("keep-me", pinned, by=WILLEM)
    orphan = store.put(b"orphan")
    lonely_tree = _tree(store, z=b"lonely")
    # A ref in a namespace nobody declared at open still roots.
    stray = store.put(b"stray")
    other = Store.open(store.root, namespaces=[Namespace("elsewhere", "volatile")])
    other.set_ref("elsewhere", "x", stray, previous=None, by=WILLEM)
    # A ref file that is not a digest roots nothing and breaks nothing.
    (store.root / "refs" / "urls").mkdir(parents=True, exist_ok=True)
    (store.root / "refs" / "urls" / "junk").write_bytes(b"\xff\xfe")
    (store.root / "refs" / "urls" / "text").write_text("not a digest\n")
    old_scratch = store.root / "objects" / "sha256" / "old.1-1.part"
    old_scratch.write_bytes(b"")
    os.utime(old_scratch, (time.time() - 100000, time.time() - 100000))
    fresh_scratch = store.root / "objects" / "sha256" / "fresh.1-2.part"
    fresh_scratch.write_bytes(b"")

    report = store.sweep()

    assert set(report.removed) == {orphan, lonely_tree, digest_of(b"lonely")}
    assert set(report.reached) >= {
        kept_tree,
        digest_of(b"kept"),
        version,
        digest_of(b"card"),
        receipt,
        version_with_receipt,
        pinned,
        stray,
    }
    assert report.scratch_removed == (old_scratch,)
    assert fresh_scratch.exists()
    assert store.state(orphan) == "absent"
    assert store.state(pinned) == "present"
    assert store.read(stray) == b"stray"


def test_a_publish_begun_during_the_sweep_survives_it(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(store, a=b"late")
    begun: list[str] = []

    def begin_now() -> None:
        # Between marking and deleting: the tree was not reached, and a
        # publish begins. Its pending ref must root it.
        begun.append(store.publish_begin(tree, by=WILLEM).id)

    monkeypatch.setattr(_lifecycle, "after_mark", begin_now)
    report = store.sweep()
    assert report.removed == ()
    assert report.pending == tuple(begun)
    assert store.state(tree) == "present"
    assert store.state(digest_of(b"late")) == "present"


def test_pins_replace_and_unpin_sheds_reachability(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    store.pin("x", a, by=WILLEM)
    second = store.pin("x", b, by=WILLEM)
    assert second.previous == a
    assert store.ref("pins", "x") == b
    assert store.unpin("x") == b
    assert store.ref("pins", "x") is None
    assert store.record("pins", "x") is None
    assert set(store.sweep().removed) == {a, b}


def test_marking_reads_shape_not_namespace(store: Store) -> None:
    blob = store.put(b"{not json}")
    assert list(store.reachable(blob)) == [blob]
    curly = store.put(b'{"a":1}')
    assert list(store.reachable(curly)) == [curly]
    tree = _tree(store, a=b"a")
    assert set(store.reachable(tree)) == {tree, digest_of(b"a")}
    assert list(store.reachable(digest_of(b"absent"))) == [digest_of(b"absent")]
    assert _lifecycle.children(b"") == []


def test_a_lease_is_recorded_as_a_whole_number_of_seconds(store: Store) -> None:
    pending = store.publish_begin(_tree(store, a=b"a"), by=WILLEM, lease=1.9)
    assert pending.record.meta == {"lease": 1}
    assert store.retire(pending.id) == pending.target


def test_the_default_pending_id_is_fresh_hex() -> None:
    first, second = _lifecycle.new_pending_id(), _lifecycle.new_pending_id()
    assert first != second
    assert len(first) == 16
    _lifecycle.after_mark()
