"""The materialiser: refusals first, one per rung, then the doctrine, then the rungs."""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import (
    PATH_BUDGET,
    Digest,
    Entry,
    ErasedObject,
    FolderSource,
    FormatError,
    HttpSource,
    IntegrityError,
    Link,
    MissingObject,
    OriginHint,
    RungUnavailable,
    Store,
    Subject,
    Tree,
    Unreachable,
    ViewEntry,
    ViewRecord,
    _rungs,
    _store,
    _views,
    digest_of,
)

WILLEM = Subject("person", "willem")


def _refuse(source: Path, destination: Path, **kwargs: Any) -> None:
    raise RungUnavailable(1, "refused by the test")


def _fresh(tmp_path: Path, **kwargs: Any) -> Store:
    return Store.create(tmp_path / "s", **kwargs)


def _tree(store: Store, spec: dict[str, Any]) -> Digest:
    entries: list[Entry | Link] = []
    for name, value in spec.items():
        if isinstance(value, Link):
            entries.append(value)
        elif isinstance(value, dict):
            sub = _tree(store, value)
            entries.append(Entry(name, "tree", sub, len(store.read(sub))))
        else:
            executable = name.endswith(".sh")
            entries.append(
                Entry(name, "blob", store.put(value), len(value), executable)
            )
    return store.put(Tree.of(entries).encode())


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return _fresh(tmp_path, lock_timeout=0.1)


@pytest.fixture
def sample(store: Store) -> Digest:
    return _tree(
        store,
        {
            "README.md": b"read me",
            "run.sh": b"#!/bin/sh\n",
            "src": {"main.py": b"print()", "data": {"x.bin": b"\x00\x01"}},
            "latest": Link("latest", "src/main.py"),
            "srcdir": Link("srcdir", "src"),
        },
    )


# Refusals, one per rung, then the plan's own.


def test_view_refuses_a_non_empty_or_non_directory_target(
    store: Store, sample: Digest, tmp_path: Path
) -> None:
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_bytes(b"")
    with pytest.raises(FileExistsError, match="not an empty directory"):
        store.view(sample, full)
    (tmp_path / "file").write_bytes(b"")
    with pytest.raises(FileExistsError):
        store.view(sample, tmp_path / "file")


def test_view_names_the_entry_a_blob_is_missing_or_erased_for(
    store: Store, tmp_path: Path
) -> None:
    tree = _tree(store, {"a": b"a", "b": b"b"})
    store.evict(digest_of(b"b"))
    with pytest.raises(MissingObject, match="view entry 'b'"):
        store.view(tree, tmp_path / "v1")
    store.erase(digest_of(b"b"), by=WILLEM, reason="request 7")
    with pytest.raises(ErasedObject, match=r"view entry 'b'.*request 7"):
        store.view(tree, tmp_path / "v2")


def test_view_refuses_a_path_over_the_budget(store: Store, tmp_path: Path) -> None:
    deep: dict[str, Any] = {"leaf.txt": b"x"}
    for _ in range(5):
        deep = {"d" * 250: deep}
    tree = _tree(store, deep)
    with pytest.raises(FormatError, match=f"longer than {PATH_BUDGET}"):
        store.view(tree, tmp_path / "v")


def test_clone_refused_falls_to_hardlink_once_per_view(
    store: Store, sample: Digest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def refuse(source: Path, destination: Path) -> None:
        calls.append(destination.name)
        raise RungUnavailable(95, "not supported")

    monkeypatch.setattr(_rungs, "CLONE", refuse)
    record = store.view(sample, tmp_path / "v")
    rungs = {entry.path: entry.rung for entry in record.entries}
    assert rungs["README.md"] == "hardlink"
    assert rungs["src/main.py"] == "hardlink"
    assert rungs["run.sh"] == "copy"  # executable: never a shared inode
    assert calls == ["README.md"]  # the ladder closed the rung after one refusal


def test_hardlink_into_a_writable_view_is_refused_without_the_allowance(
    store: Store, sample: Digest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_rungs, "CLONE", _refuse)
    plain = store.view(sample, tmp_path / "w", writable=True)
    assert {e.rung for e in plain.entries if e.digest} == {"copy"}
    allowed = store.view(sample, tmp_path / "a", writable=True, allow_hardlink=True)
    rungs = {entry.path: entry.rung for entry in allowed.entries}
    assert rungs["README.md"] == "hardlink"
    assert rungs["run.sh"] == "copy"
    # A copied file in a writable view is writable; a hardlinked one never is.
    assert os.access(tmp_path / "w" / "README.md", os.W_OK)
    assert not os.access(tmp_path / "a" / "README.md", os.W_OK)


def test_link_is_used_only_from_a_read_only_view(
    store: Store, sample: Digest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_rungs, "CLONE", _refuse)
    monkeypatch.setattr(_rungs, "hardlink", _refuse)
    linked = store.view(sample, tmp_path / "r")
    rungs = {entry.path: entry.rung for entry in linked.entries}
    assert rungs["README.md"] == "link"
    assert rungs["run.sh"] == "copy"
    link = tmp_path / "r" / "README.md"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))
    assert link.resolve() == store.object_path(digest_of(b"read me")).resolve()
    writable = store.view(sample, tmp_path / "w", writable=True)
    assert {e.rung for e in writable.entries if e.digest} == {"copy"}


def test_copy_is_the_floor_and_keeps_the_executable_bit(
    store: Store, sample: Digest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_rungs, "CLONE", _refuse)
    monkeypatch.setattr(_rungs, "hardlink", _refuse)
    monkeypatch.setattr(_rungs, "symlink", _refuse)
    record = store.view(sample, tmp_path / "v")
    rungs = {entry.path: entry.rung for entry in record.entries}
    assert rungs["README.md"] == "copy"
    assert rungs["run.sh"] == "copy"
    assert (tmp_path / "v" / "run.sh").stat().st_mode & stat.S_IXUSR
    assert not (tmp_path / "v" / "README.md").stat().st_mode & stat.S_IXUSR
    assert not os.access(tmp_path / "v" / "README.md", os.W_OK)


def test_symlink_entries_fall_to_copies_or_park(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(
        store,
        {
            "src": {"main.py": b"main"},
            "to_file": Link("to_file", "src/main.py"),
            "to_dir": Link("to_dir", "src"),
            "escaping": Link("escaping", "../outside"),
            "dangling": Link("dangling", "src/nothing"),
        },
    )
    monkeypatch.setattr(_rungs, "symlink", _refuse)
    record = store.view(tree, tmp_path / "v")
    by_path = {entry.path: entry for entry in record.entries}
    assert by_path["to_file"].rung == "copy"
    assert by_path["to_file"].note.startswith("symlink materialised as a copy")
    assert (tmp_path / "v" / "to_file").read_bytes() == b"main"
    assert by_path["to_dir"].rung == "copy"
    assert (tmp_path / "v" / "to_dir" / "main.py").read_bytes() == b"main"
    assert by_path["escaping"].rung == "parked"
    assert by_path["escaping"].note.startswith("target escapes the view")
    assert not (tmp_path / "v" / "escaping").exists()
    assert by_path["dangling"].rung == "parked"
    assert by_path["dangling"].note.startswith("target is absent in the view")
    # Dropping removes the copies and skips the parked entries.
    report = store.drop_view(record.id)
    assert "to_dir" in report.removed
    assert report.left == ()
    assert not (tmp_path / "v").exists()


# The removal doctrine.


def test_drop_removes_only_what_the_record_lists(
    store: Store, sample: Digest, tmp_path: Path
) -> None:
    record = store.view(sample, tmp_path / "v")
    (tmp_path / "v" / "src" / "hand-made.txt").write_bytes(b"mine")
    report = store.drop_view(record.id)
    assert "README.md" in report.removed
    assert "src/data" in report.removed
    assert report.left == (
        "src: not created by the view: hand-made.txt",
        ".: not created by the view: src",
    )
    assert (tmp_path / "v" / "src" / "hand-made.txt").read_bytes() == b"mine"
    assert not (tmp_path / "v" / "README.md").exists()
    assert store.view_record(record.id) is None
    with pytest.raises(FileNotFoundError, match="is not recorded"):
        store.drop_view(record.id)


def test_a_view_whose_directory_is_gone_is_retired_not_touched(
    store: Store, sample: Digest, tmp_path: Path
) -> None:
    record = store.view(sample, tmp_path / "v")
    shutil.rmtree(tmp_path / "v")
    report = store.drop_view(record.id)
    assert report.removed == ()
    assert report.left == (f"{tmp_path / 'v'}: the view's directory is already gone",)
    assert store.views() == []


def test_a_live_view_roots_its_tree_and_a_gone_one_is_retired_by_the_sweep(
    store: Store, sample: Digest, tmp_path: Path
) -> None:
    record = store.view(sample, tmp_path / "v")
    assert store.sweep().removed == ()
    assert store.state(sample) == "present"
    shutil.rmtree(tmp_path / "v")
    report = store.sweep()
    assert sample in report.removed
    assert store.views() == []
    assert store.view_record(record.id) is None


def test_a_malformed_view_record_is_an_integrity_error(store: Store) -> None:
    (store.root / "index" / "views").mkdir(parents=True)
    (store.root / "index" / "views" / "bad.json").write_bytes(b"{")
    with pytest.raises(IntegrityError, match=r"view record bad\.json"):
        store.views()
    with pytest.raises(IntegrityError):
        store.view_record("bad")
    with pytest.raises(FormatError, match="not a rung"):
        ViewEntry.from_json(
            {"path": "a", "rung": "teleport", "digest": None, "note": ""}
        )


def test_a_view_record_round_trips(
    store: Store, sample: Digest, tmp_path: Path
) -> None:
    record = store.view(sample, tmp_path / "v")
    assert ViewRecord.decode(record.encode()) == record
    assert store.view_record(record.id) == record
    assert store.views() == [record]
    assert record.root == tmp_path / "v"
    assert record.created == store.clock()[:16] + record.created[16:]


# Collecting.


def test_collect_refuses_an_absent_path_and_a_bad_link(
    store: Store, tmp_path: Path
) -> None:
    at = tmp_path / "out"
    at.mkdir()
    (at / "file").write_bytes(b"f")
    with pytest.raises(FileNotFoundError, match="declared output 'missing' is absent"):
        store.collect(at, ["missing"])
    # An absolute target inside the view is content with an unportable
    # spelling; one outside the view is read through as bytes.
    os.symlink(str(at / "file"), at / "abs")
    with pytest.raises(FormatError, match="absolute"):
        store.collect(at, ["abs"])
    (tmp_path / "elsewhere").write_bytes(b"outside")
    os.symlink(str(tmp_path / "elsewhere"), at / "out")
    collected = store.collect(at, ["out"])
    entry = collected.entries[0]
    assert isinstance(entry, Entry) and entry.digest == digest_of(b"outside")
    (at / "CON").write_bytes(b"")
    with pytest.raises(FormatError, match="Windows-reserved"):
        store.collect(at, ["CON"])


def test_collect_refuses_a_path_over_the_budget(store: Store, tmp_path: Path) -> None:
    # The platform's own path limit is smaller than the budget, so the
    # budget is checked through the seam with a long relative path.
    (tmp_path / "leaf").write_bytes(b"x")
    with pytest.raises(FormatError, match=f"longer than {PATH_BUDGET}"):
        _views._collect_path(
            store, tmp_path, tmp_path / "leaf", "d" * (PATH_BUDGET + 1)
        )


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param([], id="platform-ladder"),
        pytest.param(["CLONE"], id="hardlink"),
        pytest.param(["CLONE", "hardlink"], id="link"),
        pytest.param(["CLONE", "hardlink", "symlink"], id="copy"),
    ],
)
def test_view_then_collect_round_trips_on_every_rung(
    store: Store,
    sample: Digest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refused: list[str],
) -> None:
    for name in refused:
        monkeypatch.setattr(_rungs, name, _refuse)
    at = tmp_path / "v"
    record = store.view(sample, at)
    collected = store.collect(at, ["README.md", "run.sh", "src", "latest", "srcdir"])
    if "symlink" in refused:
        # The links became copies, so the collected tree differs by design.
        assert collected.digest() != sample
        assert isinstance(collected.entries[0], Entry)
    else:
        assert collected.digest() == sample
        assert store.state(collected.digest()) == "present"
    rungs = {entry.rung for entry in record.entries if entry.digest is not None}
    if sys.platform == "darwin" and not refused:
        assert rungs == {"clone"}
    assert rungs <= {"clone", "hardlink", "link", "copy"}


def test_collect_lands_partial_declarations_and_nested_directories(
    store: Store, tmp_path: Path
) -> None:
    at = tmp_path / "out"
    (at / "keep" / "deep").mkdir(parents=True)
    (at / "keep" / "deep" / "a.txt").write_bytes(b"a")
    (at / "keep" / "b.sh").write_bytes(b"b")
    (at / "keep" / "b.sh").chmod(0o755)
    (at / "ignore.txt").write_bytes(b"not declared")
    os.symlink("deep/a.txt", at / "keep" / "link")
    tree = store.collect(at, ["keep", "keep/deep/a.txt"])
    names = [entry.name for entry in tree.entries]
    assert names == ["keep"]
    keep = Tree.decode(store.read(tree.entries[0].digest))  # type: ignore[union-attr]
    assert [entry.name for entry in keep.entries] == ["b.sh", "deep", "link"]
    b = keep.entries[0]
    assert isinstance(b, Entry) and b.executable
    assert isinstance(keep.entries[2], Link)
    assert store.state(digest_of(b"not declared")) == "absent"
    assert store.state(digest_of(b"a")) == "present"


# Warming and shedding.


def test_prefetch_warms_the_closure_and_names_the_first_miss(tmp_path: Path) -> None:
    mirror = Store.create(tmp_path / "m")
    tree = _tree(mirror, {"a": b"a", "sub": {"b": b"b", "again": b"a"}})
    local = Store.create(tmp_path / "s", sources=[FolderSource(mirror.root)])
    fetched = local.prefetch(tree)
    assert fetched[0] == tree
    assert set(fetched) == set(mirror.reachable(tree))
    assert len(fetched) == len(set(fetched))  # the shared blob once
    assert local.state(digest_of(b"b")) == "present"
    mirror.evict(digest_of(b"b"))
    fresh = Store.create(tmp_path / "s2", sources=[FolderSource(mirror.root)])
    with pytest.raises(MissingObject, match=str(digest_of(b"b"))):
        fresh.prefetch(tree)


def test_shed_evicts_what_a_folder_holds_and_keeps_what_a_view_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror = Store.create(tmp_path / "m")
    tree = _tree(mirror, {"a": b"a", "b": b"b"})
    local = Store.create(tmp_path / "s", sources=[FolderSource(mirror.root)])
    local.prefetch(tree)
    only_here = local.put(b"only here")
    monkeypatch.setattr(_rungs, "CLONE", _refuse)
    record = local.view(tree, tmp_path / "v")  # hardlinks: a and b are needed
    report = local.shed(FolderSource(mirror.root))
    assert report.source == f"folder {mirror.root} (copy)"
    assert set(report.kept) == {digest_of(b"a"), digest_of(b"b")}
    assert set(report.shed) == {tree}
    assert local.state(only_here) == "present"
    local.drop_view(record.id)
    gone = local.view(tree, tmp_path / "gone")
    shutil.rmtree(tmp_path / "gone")
    again = local.shed(FolderSource(mirror.root))
    # The second view fetched the tree back, so it is shed again too.
    assert set(again.shed) == {digest_of(b"a"), digest_of(b"b"), tree}
    assert local.state(only_here) == "present"
    assert local.view_record(gone.id) is not None  # shed retires nothing


def test_shed_asks_an_http_source_with_head_and_an_origin_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = Store.create(tmp_path / "s")
    a, b = local.put(b"a"), local.put(b"b")
    asked: list[tuple[str, str]] = []

    def head_only(url: str, **kwargs: Any) -> Any:
        asked.append((kwargs["method"], url))
        if url.endswith(a.path_parts[2]):
            return contextlib.nullcontext()
        raise Unreachable(f"{url}: HTTP 404")

    monkeypatch.setattr(_store, "fetch_url", head_only)
    report = local.shed(HttpSource("http://mirror.example/store"))
    assert report.shed == (a,)
    assert report.kept == ()
    assert {method for method, _ in asked} == {"HEAD"}
    assert local.state(b) == "present"
    hint = local.shed(OriginHint(b, "https://vendor.example/b"))
    assert hint.shed == (b,)
    assert local.shed(OriginHint(a, "https://vendor.example/a")).shed == ()


# The rungs themselves, each seam exercised.


@pytest.mark.skipif(sys.platform != "darwin", reason="clonefile is APFS")
def test_clone_darwin_clones_and_refuses(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"cloned")
    _rungs.clone_darwin(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b").read_bytes() == b"cloned"
    with pytest.raises(RungUnavailable, match="clonefile"):
        _rungs.clone_darwin(tmp_path / "missing", tmp_path / "c")


def test_clone_linux_wires_the_ioctl_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    calls: list[tuple[int, int]] = []

    def record(fd: int, request: int, arg: int = 0) -> int:
        calls.append((request, arg))
        return 0

    monkeypatch.setattr(fcntl, "ioctl", record)
    (tmp_path / "a").write_bytes(b"x")
    _rungs.clone_linux(tmp_path / "a", tmp_path / "b")
    assert calls[0][0] == 0x40049409
    assert (tmp_path / "b").exists()

    def refuse(fd: int, request: int, arg: int = 0) -> int:
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr(fcntl, "ioctl", refuse)
    with pytest.raises(RungUnavailable, match="FICLONE"):
        _rungs.clone_linux(tmp_path / "a", tmp_path / "c")
    assert not (tmp_path / "c").exists()


def test_the_other_rungs_refuse_and_succeed(tmp_path: Path) -> None:
    with pytest.raises(RungUnavailable, match="no clone rung"):
        _rungs.clone_unavailable(tmp_path / "a", tmp_path / "b")
    (tmp_path / "a").write_bytes(b"x")
    _rungs.hardlink(tmp_path / "a", tmp_path / "h")
    assert os.stat(tmp_path / "h").st_ino == os.stat(tmp_path / "a").st_ino
    with pytest.raises(RungUnavailable, match="link"):
        _rungs.hardlink(tmp_path / "a", tmp_path / "h")
    _rungs.symlink("a", tmp_path / "l")
    assert os.readlink(tmp_path / "l") == "a"
    with pytest.raises(RungUnavailable, match="symlink"):
        _rungs.symlink("a", tmp_path / "l")
    _rungs.copy(tmp_path / "a", tmp_path / "c")
    assert (tmp_path / "c").read_bytes() == b"x"
    assert _rungs.CLONE in (
        _rungs.clone_darwin,
        _rungs.clone_linux,
        _rungs.clone_unavailable,
    )


def test_the_platform_clone_clones_or_refuses(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x")
    try:
        _rungs.CLONE(tmp_path / "a", tmp_path / "b")
    except RungUnavailable:
        assert not (tmp_path / "b").exists()
    else:
        assert (tmp_path / "b").read_bytes() == b"x"
