"""The local store: refusals first, then the behaviour that holds."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from livery.strongroom import (
    LAYOUT_VERSION,
    MANIFEST_NAME,
    Digest,
    ErasedObject,
    FormatError,
    IntegrityError,
    LockTimeout,
    Manifest,
    ManifestError,
    MissingObject,
    Namespace,
    NotFastForward,
    RefConflict,
    RefTampered,
    Store,
    Subject,
    UnknownNamespace,
    Version,
    WriteOnceRefused,
    _store,
    canonical,
    check_timestamp,
    digest_of,
    now,
)

WILLEM = Subject("person", "willem")
TOOLS = Namespace("tools", "write-once")
DATA = Namespace("datasets", "monotone")
URLS = Namespace("urls", "volatile")


def ticking() -> _store.Clock:
    second = [0]

    def clock() -> str:
        second[0] += 1
        return f"2026-09-11T12:00:{second[0]:02d}Z"

    return clock


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.create(
        tmp_path / "store", namespaces=[TOOLS, DATA, URLS], clock=ticking()
    )


def _tombstone(store: Store, digest: Digest) -> Path:
    path = store.object_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    stone = path.with_name(path.name + ".tombstone")
    stone.write_bytes(b"{}")
    return stone


# Opening: every way a directory is not this store.


def test_open_refuses_a_directory_without_a_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match=r"no strongroom\.json"):
        Store.open(tmp_path)


def test_open_refuses_a_manifest_that_is_not_one(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_bytes(b"{")
    with pytest.raises(ManifestError, match="not a manifest"):
        Store.open(tmp_path)
    (tmp_path / MANIFEST_NAME).write_bytes(canonical({"layout": 1}))
    with pytest.raises(ManifestError, match="wrong keys"):
        Store.open(tmp_path)
    (tmp_path / MANIFEST_NAME).write_bytes(canonical({"layout": "1", "algorithm": "x"}))
    with pytest.raises(ManifestError, match="not an integer"):
        Store.open(tmp_path)


def test_open_refuses_another_layout_version(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_bytes(Manifest(2, "sha256").encode())
    with pytest.raises(ManifestError, match="layout 2"):
        Store.open(tmp_path)


def test_open_refuses_an_unregistered_algorithm(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_bytes(Manifest(LAYOUT_VERSION, "blake3").encode())
    with pytest.raises(ManifestError, match="not in the registry"):
        Store.open(tmp_path)


def test_create_refuses_an_existing_store(tmp_path: Path) -> None:
    Store.create(tmp_path)
    with pytest.raises(ManifestError, match="already a store"):
        Store.create(tmp_path)


def test_create_refuses_an_unregistered_algorithm(tmp_path: Path) -> None:
    with pytest.raises(FormatError, match="not in the registry"):
        Store.create(tmp_path, algorithm="sha1")
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_a_namespace_refuses_a_bad_name_or_class() -> None:
    with pytest.raises(ValueError, match="lowercase letters"):
        Namespace("Tools", "volatile")
    with pytest.raises(ValueError, match="lowercase letters"):
        Namespace("", "volatile")
    with pytest.raises(ValueError, match="not one of"):
        Namespace("tools", "append")  # type: ignore[arg-type]


def test_open_refuses_redeclaring_an_owned_namespace_differently(
    tmp_path: Path,
) -> None:
    Store.create(tmp_path)
    with pytest.raises(ValueError, match="already declared volatile"):
        Store.open(tmp_path, namespaces=[Namespace("pins", "write-once")])
    # The same declaration again is not a conflict.
    assert (
        "pins"
        in Store.open(tmp_path, namespaces=[Namespace("pins", "volatile")]).namespaces
    )


# Objects: what landing and access refuse.


def test_object_path_refuses_another_algorithms_name(store: Store) -> None:
    with pytest.raises(IntegrityError, match="address space is sha256"):
        store.object_path(Digest("blake3", "ab" * 32))


def test_landing_refuses_bytes_that_miss_the_expected_digest(store: Store) -> None:
    expected = digest_of(b"abc")
    with pytest.raises(IntegrityError, match="nothing was kept"):
        store.land(b"abd", expected=expected)
    assert store.state(expected) == "absent"
    assert list(store.root.rglob("*.part")) == []


def test_landing_refuses_an_erased_object(store: Store) -> None:
    digest = digest_of(b"gone")
    _tombstone(store, digest)
    assert store.state(digest) == "erased"
    with pytest.raises(ErasedObject, match="tombstone stands"):
        store.land(b"gone")
    with pytest.raises(ErasedObject, match="tombstone stands"):
        store.path(digest)
    assert list(store.root.rglob("*.part")) == []


def test_path_and_verify_refuse_an_absent_object(store: Store) -> None:
    digest = digest_of(b"never")
    with pytest.raises(MissingObject, match="not in the store"):
        store.path(digest)
    with pytest.raises(MissingObject, match="not in the store"):
        store.verify(digest)
    with pytest.raises(MissingObject):
        store.read(digest)


def test_path_evicts_an_object_whose_size_moved(store: Store) -> None:
    digest = store.put(b"twelve bytes")
    store.object_path(digest).write_bytes(b"short")
    with pytest.raises(IntegrityError, match="5 bytes on disk and was verified at 12"):
        store.path(digest)
    assert store.state(digest) == "absent"
    assert store.verified_size(digest) is None


def test_path_verifies_in_full_an_object_landed_without_a_mark(store: Store) -> None:
    good = digest_of(b"copied in")
    path = store.object_path(good)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"copied ix")  # same size, other bytes
    with pytest.raises(IntegrityError, match="holds bytes named"):
        store.path(good)
    assert store.state(good) == "absent"
    path.write_bytes(b"copied in")
    assert store.path(good) == path
    assert store.verified_size(good) == 9


def test_scrub_removes_the_corrupt_and_lists_the_verified(store: Store) -> None:
    kept = store.put(b"kept")
    broken = store.put(b"broken")
    store.object_path(broken).write_bytes(b"br0ken")
    (store.root / "objects" / "sha256" / "zz.part").write_bytes(b"")
    store.object_path(kept).with_name("leftover.1-1.part").write_bytes(b"")
    _tombstone(store, digest_of(b"erased earlier"))
    report = store.scrub()
    assert report.verified == (kept,)
    assert report.corrupt == (broken,)
    assert store.state(broken) == "absent"
    assert sorted(store.objects()) == [kept]


def test_landing_survives_a_replace_refused_by_a_reader(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Windows case through its seam: the destination already holds
    # the right bytes when the replace is refused, so it is success.
    digest = digest_of(b"held open")
    destination = store.object_path(digest)

    def refuse(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"held open")
        raise PermissionError("held open by a reader")

    monkeypatch.setattr(_store, "_replace", refuse)
    landed = store.land(b"held open")
    assert landed == _store.Landed(digest, 9, written=False)
    assert store.verified_size(digest) == 9
    assert destination.read_bytes() == b"held open"
    assert list(store.root.rglob("*.part")) == []


def test_a_refused_replace_without_the_bytes_in_place_is_an_error(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_missing(source: Path, target: Path) -> None:
        raise PermissionError("no destination")

    monkeypatch.setattr(_store, "_replace", refuse_missing)
    with pytest.raises(PermissionError, match="no destination"):
        store.land(b"nowhere")

    def refuse_wrong(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"other")
        raise PermissionError("wrong bytes")

    monkeypatch.setattr(_store, "_replace", refuse_wrong)
    with pytest.raises(PermissionError, match="wrong bytes"):
        store.land(b"right")
    assert list(store.root.rglob("*.part")) == []


# Refs: what naming refuses.


def test_ref_path_refuses_an_undeclared_namespace(store: Store) -> None:
    with pytest.raises(UnknownNamespace, match="not declared at open"):
        store.ref_path("secrets", "x")
    with pytest.raises(UnknownNamespace):
        store.refs("secrets")
    with pytest.raises(UnknownNamespace):
        store.set_ref("secrets", "x", digest_of(b""), previous=None, by=WILLEM)


def test_ref_path_refuses_a_reserved_suffix_or_a_bad_component(store: Store) -> None:
    with pytest.raises(FormatError, match="reserved suffix"):
        store.ref_path("tools", "x.record")
    with pytest.raises(FormatError, match="reserved suffix"):
        store.ref_path("tools", "a/b.lock")
    with pytest.raises(FormatError, match="relative directory marker"):
        store.ref_path("tools", "../x")
    with pytest.raises(FormatError, match="empty"):
        store.ref_path("tools", "a//b")


def test_a_ref_edited_out_of_band_is_tampered(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    store.set_ref("urls", "x", a, previous=None, by=WILLEM)
    target = store.ref_path("urls", "x")
    target.write_text(f"{b}\n")
    with pytest.raises(RefTampered, match="record names"):
        store.ref("urls", "x")
    target.write_text("not a digest\n")
    with pytest.raises(RefTampered, match="is not a digest"):
        store.ref("urls", "x")
    target.write_text(f"{a}\n")
    target.with_name("x.record").write_bytes(b"{}")
    with pytest.raises(RefTampered, match="record of urls/x"):
        store.ref("urls", "x")
    target.with_name("x.record").unlink()
    with pytest.raises(RefTampered, match="no record"):
        store.ref("urls", "x")
    with pytest.raises(RefTampered):
        store.set_ref("urls", "x", b, previous=a, by=WILLEM)


def test_set_ref_loses_the_compare_and_swap_loudly(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    store.set_ref("urls", "x", a, previous=None, by=WILLEM)
    with pytest.raises(RefConflict, match=f"names {a}, not None"):
        store.set_ref("urls", "x", b, previous=None, by=WILLEM)
    with pytest.raises(RefConflict, match=f"names {a}, not {b}"):
        store.set_ref("urls", "x", b, previous=b, by=WILLEM)
    assert store.ref("urls", "x") == a


def test_write_once_refuses_a_second_digest_and_repeats_the_first(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    first = store.set_ref("tools", "bun@1", a, previous=None, by=WILLEM)
    with pytest.raises(WriteOnceRefused, match="is write-once and names"):
        store.set_ref("tools", "bun@1", b, previous=a, by=WILLEM)
    again = store.set_ref("tools", "bun@1", a, previous=a, by=WILLEM)
    assert again == first  # no new record, the clock did not advance it
    assert store.ref("tools", "bun@1") == a


def test_monotone_refuses_a_target_that_is_not_a_version_here(store: Store) -> None:
    first = _version(store, parents=())
    store.set_ref("datasets", "corpus/main", first, previous=None, by=WILLEM)
    absent = digest_of(b"not landed")
    with pytest.raises(NotFastForward, match="not a version present here"):
        store.set_ref("datasets", "corpus/main", absent, previous=first, by=WILLEM)
    blob = store.put(b"a blob, not a version")
    with pytest.raises(NotFastForward, match="not a version present here"):
        store.set_ref("datasets", "corpus/main", blob, previous=first, by=WILLEM)
    _tombstone(store, absent)
    with pytest.raises(NotFastForward, match="tombstone"):
        store.set_ref("datasets", "corpus/main", absent, previous=first, by=WILLEM)


def test_monotone_refuses_a_version_that_does_not_descend(store: Store) -> None:
    first = _version(store, parents=())
    store.set_ref("datasets", "corpus/main", first, previous=None, by=WILLEM)
    sibling = _version(store, parents=(), message="a root, not a child")
    with pytest.raises(NotFastForward, match="rebase the version and retry"):
        store.set_ref("datasets", "corpus/main", sibling, previous=first, by=WILLEM)
    assert store.ref("datasets", "corpus/main") == first


def test_a_live_lock_times_out(tmp_path: Path) -> None:
    store = Store.create(tmp_path, namespaces=[URLS], lock_timeout=0.1)
    lock = store.ref_path("urls", "x").with_name("x.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
    with pytest.raises(LockTimeout, match="held by a live process"):
        store.set_ref("urls", "x", digest_of(b""), previous=None, by=WILLEM)
    assert lock.exists()


def test_a_stale_lock_is_broken_only_on_proof(tmp_path: Path) -> None:
    store = Store.create(tmp_path, namespaces=[URLS], lock_timeout=0.1, lock_stale=600)
    lock = store.ref_path("urls", "x").with_name("x.lock")
    lock.parent.mkdir(parents=True)
    a = store.put(b"a")
    # Old by age, holder alive: broken.
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time() - 601}))
    store.set_ref("urls", "x", a, previous=None, by=WILLEM)
    assert not lock.exists()
    # Holder dead: broken.
    lock.write_text(json.dumps({"pid": _dead_pid(), "at": time.time()}))
    store.set_ref("urls", "x", a, previous=a, by=WILLEM)
    assert not lock.exists()
    # Torn (empty) lock file: a holder that died mid-write, broken.
    lock.write_text("")
    store.set_ref("urls", "x", a, previous=a, by=WILLEM)
    lock.write_text(json.dumps({"pid": "x"}))
    store.set_ref("urls", "x", a, previous=a, by=WILLEM)
    assert not lock.exists()


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_liveness_probes_read_the_kernel_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _store._pid_alive_posix(os.getpid()) is True
    assert _store._pid_alive_unknown(1) is True

    def lookup_error(pid: int, signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", lookup_error)
    assert _store._pid_alive_posix(1) is False

    def permission_error(pid: int, signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", permission_error)
    assert _store._pid_alive_posix(1) is True


# Then what holds.


def test_create_writes_the_manifest_and_the_layout(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "s")
    data = (tmp_path / "s" / MANIFEST_NAME).read_bytes()
    assert data == b'{"algorithm":"sha256","layout":1}'
    assert Manifest.decode(data) == Manifest(1, "sha256")
    for child in ("objects", "refs", "index"):
        assert (tmp_path / "s" / child).is_dir()
    assert store.algorithm.name == "sha256"
    assert sorted(store.namespaces) == ["pending", "pins"]
    reopened = Store.open(tmp_path / "s", namespaces=[TOOLS])
    assert sorted(reopened.namespaces) == ["pending", "pins", "tools"]


def test_landing_is_verified_idempotent_and_named(store: Store) -> None:
    digest = digest_of(b"hello")
    first = store.land(io.BytesIO(b"hello"), expected=digest)
    assert first == _store.Landed(digest, 5, written=True)
    assert store.state(digest) == "present"
    assert store.verified_size(digest) == 5
    second = store.land(b"hello")
    assert second == _store.Landed(digest, 5, written=False)
    assert store.read(digest) == b"hello"
    assert store.path(digest) == store.object_path(digest)
    assert store.object_path(digest).relative_to(store.root).as_posix() == (
        f"objects/sha256/{digest.encoded[:2]}/{digest.encoded[2:]}"
    )
    assert list(store.root.rglob("*.part")) == []
    assert list(store.objects()) == [digest]
    assert store.verify(digest) == 5


def test_two_processes_landing_the_same_bytes_both_succeed(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    Store.create(root)
    script = (
        "import sys; from pathlib import Path; from livery.strongroom import Store;"
        f" s = Store.open(Path({str(root)!r})); s.land(b'x' * 300000)"
    )
    children = [
        subprocess.Popen([sys.executable, "-c", script], stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    for child in children:
        _, err = child.communicate(timeout=60)
        assert child.returncode == 0, err.decode()
    store = Store.open(root)
    assert list(store.objects()) == [digest_of(b"x" * 300000)]
    assert list(root.rglob("*.part")) == []


def test_set_ref_writes_the_record_and_the_ref(store: Store) -> None:
    a, b = store.put(b"a"), store.put(b"b")
    created = store.set_ref(
        "urls", "sha256/abc", a, previous=None, by=WILLEM, meta={"max-age": 30}
    )
    assert created.previous is None
    assert created.at == "2026-09-11T12:00:01Z"
    assert created.meta == {"max-age": 30}
    assert store.ref("urls", "sha256/abc") == a
    assert store.record("urls", "sha256/abc") == created
    target = store.ref_path("urls", "sha256/abc")
    assert target.read_text() == f"{a}\n"
    assert not target.with_name("sha256.lock").exists()
    moved = store.set_ref("urls", "sha256/abc", b, previous=a, by=WILLEM, receipt=a)
    assert moved.previous == a
    assert moved.receipt == a
    assert moved.at == "2026-09-11T12:00:02Z"
    assert store.ref("urls", "sha256/abc") == b
    assert store.ref("urls", "missing") is None
    assert store.record("urls", "missing") is None
    store.set_ref("urls", "zed", a, previous=None, by=WILLEM)
    assert store.refs("urls") == ["sha256/abc", "zed"]
    assert store.refs("tools") == []


def test_monotone_fast_forwards(store: Store) -> None:
    first = _version(store, parents=())
    store.set_ref("datasets", "corpus/main", first, previous=None, by=WILLEM)
    second = _version(store, parents=(first,))
    store.set_ref("datasets", "corpus/main", second, previous=first, by=WILLEM)
    assert store.ref("datasets", "corpus/main") == second


def test_now_is_a_spec_instant() -> None:
    assert check_timestamp(now(), where="now") == now()[:19] + "Z"


def _version(
    store: Store, *, parents: tuple[Digest, ...], message: str = "v"
) -> Digest:
    tree = store.put(b'{"entries":[]}')
    version = Version(tree, parents, WILLEM, "2026-09-11T12:00:00Z", message)
    return store.put(version.encode())
