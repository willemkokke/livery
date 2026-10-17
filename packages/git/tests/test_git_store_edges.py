"""The store's failure paths the happy suite never reaches, each one forced."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import livery.toolroom.tools as tools
from git_seeds import repos_fixture, run_git, store_of  # noqa: F401
from livery import git
from livery.git import _objects
from livery.git import _store as store_module

REF = git.NAMESPACE + "metrics"


def _failing(words: str, *, when: str | None = None, code: int = 1) -> Any:
    """A git door that fails calls whose first word is *when*, or every call."""
    real = store_module._git

    def door(root: Path, *args: str) -> tools.Result:
        first = next((a for a in args if not a.startswith("-")), "")
        if when is None or first == when:
            return tools.Result(code=code, stdout="", stderr=words)
        return real(root, *args)

    return door


def test_a_family_refuses_a_key_of_the_wrong_arity() -> None:
    family = git.Keyed("pair", ("a", "b"), ci_only=False)
    with pytest.raises(ValueError, match="keyed by a, b; got 1 part"):
        family.at("one")


def test_a_series_file_is_its_text_absence_or_the_reason(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    series = git.Series("blobs", ci_only=False)
    assert git.put(store, series.ref, {"trace.txt": "lines\n"}, message="t") == ""
    assert series.file(store, "trace.txt") == ("lines\n", "")
    assert series.file(store, "nonesuch") == (None, "")
    monkeypatch.setattr(store_module, "_git", _failing("fatal: down", when="fetch"))
    text, why = series.file(store_of(work), "trace.txt")
    assert text is None and "could not be read" in why


def test_a_window_ranks_a_file_that_is_not_a_row_as_oldest(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    store = store_of(work)
    assert (
        git.put(store, REF, {"junk": "not json", "a": '{"when": "2"}'}, message="x")
        == ""
    )
    assert (
        git.put(
            store,
            REF,
            {"b": '{"when": "3"}'},
            message="y",
            window=2,
            order=store_module._by_when,
        )
        == ""
    )
    assert set(git.read(store, REF).files or {}) == {"a", "b"}


def test_listings_skip_blank_lines_and_a_failed_object_probe_counts_everything_missing(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    real = store_module._git

    def door(root: Path, *args: str) -> tools.Result:
        done = real(root, *args)
        if args[0] in ("ls-remote", "for-each-ref"):
            return tools.Result(code=0, stdout=done.stdout + "\n\n", stderr="")
        if args[0] == "cat-file":
            return tools.Result(code=1, stdout="", stderr="broken")
        return done

    monkeypatch.setattr(store_module, "_git", door)
    refs, why = store_module._list_namespace(store)
    assert refs is not None and why == ""
    heads = store_module._local_refs(store, "refs/heads/")
    assert heads is not None and set(heads) == {"refs/heads/main"}
    assert store_module._missing_locally(store, ["a" * 40]) == {"a" * 40}


def test_fetch_store_names_a_stamp_that_could_not_be_written(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    monkeypatch.setattr(store_module, "put", lambda *a, **k: "the stamp refused")
    fetched = git.fetch_store(store_of(work), series=("metrics",))
    assert fetched == git.Fetched(0, "the stamp refused")


def test_fetch_store_names_a_mirror_it_could_not_prune(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    run_git(work, "update-ref", git.FETCHED_NAMESPACE + "fam/a/x", "HEAD")

    def refusing(repo: object, ref: bytes) -> bool:
        raise OSError("read-only")

    monkeypatch.setattr(_objects, "drop_ref", refusing)
    fetched = git.fetch_store(store, series=("metrics",))
    assert fetched == git.Fetched(0, "the mirror could not be pruned: read-only")
    # The stray stands, and the stamp was not written over.
    assert git.list_refs(store, git.FETCHED_NAMESPACE + "fam/") is not None
    assert git.FETCHED.row(store, "origin") == (None, "")


def test_a_fetched_block_inside_another_shares_it_and_lists_the_branches(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.fetch_store(store, series=("metrics",), branches=True).why == ""
    with git.fetched_snapshot(store), git.fetched_snapshot(store):
        heads = git.list_refs(store, "refs/heads/")
    assert heads is not None and set(heads) == {"refs/heads/main"}


def test_a_published_listing_of_the_wrong_shape_is_none_and_an_unwritable_one_dropped(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    junk = tmp_path / "listing.json"
    junk.write_text(json.dumps({"root": str(work.resolve()), "refs": [], "local": []}))
    monkeypatch.setenv("WORKSHOP_SNAPSHOT", str(junk))
    assert store_module._published_snapshot(store, work.resolve()) is None
    snapshot = store_module._Snapshot(
        {}, "", set(), tmp_path
    )  # a directory: unwritable
    store_module._write_published(work.resolve(), snapshot)
    assert snapshot.published is None


def test_a_bring_names_a_failed_fetch_an_unlistable_remote_and_a_ref_that_keeps_moving(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.put(store, REF, {"a": "{}"}, message="a") == ""
    listed = git.list_refs(store, git.NAMESPACE) or {}
    snapshot = store_module._Snapshot(dict(listed))
    # The fetch fails: every wanted ref carries git's words.
    monkeypatch.setattr(store_module, "_git", _failing("fatal: no route", when="fetch"))
    monkeypatch.setattr(store_module, "_missing_locally", lambda s, shas: set(shas))
    assert store_module._bring(store, snapshot, {REF: listed[REF]}) == {
        REF: "fatal: no route"
    }
    monkeypatch.undo()
    # The fetch lands but the ref moved; the listing then fails.
    monkeypatch.setattr(store_module, "_missing_locally", lambda s, shas: set(shas))
    monkeypatch.setattr(store_module, "_list_namespace", lambda s: (None, "offline"))
    failed = store_module._bring(
        store, store_module._Snapshot(dict(listed)), {REF: listed[REF]}
    )
    assert failed == {REF: "the remote could not be listed: offline"}
    monkeypatch.undo()
    # The ref keeps moving through both rounds.
    monkeypatch.setattr(store_module, "_missing_locally", lambda s, shas: set(shas))
    failed = store_module._bring(
        store, store_module._Snapshot(dict(listed)), {REF: listed[REF]}
    )
    assert failed == {REF: "another writer kept moving it"}
    # And a read inside the snapshot of such a ref is a failure naming it.
    with git.remote_snapshot(store):
        found = git.read(store, REF)
    assert found.failed and "kept moving" in found.reason
    monkeypatch.undo()
    # A ref gone from the remote after the listing reads as absent.
    monkeypatch.setattr(store_module, "_missing_locally", lambda s, shas: set(shas))
    monkeypatch.setattr(store_module, "_list_namespace", lambda s: ({}, ""))
    with git.remote_snapshot(store):
        snap = store_module._snapshot(store)
        assert snap is not None
        snap.refs = {REF: "b" * 40}
        found = git.read(store, REF)
    assert found == git.Read(None, None, failed=False)
    monkeypatch.undo()
    # A listing that fails after a move: the read names it.
    monkeypatch.setattr(store_module, "_missing_locally", lambda s, shas: set(shas))
    monkeypatch.setattr(store_module, "_list_namespace", lambda s: (None, "gone"))
    with git.remote_snapshot(store):
        snap = store_module._snapshot(store)
        assert snap is not None
        snap.refs = {REF: "b" * 40}
        found = git.read(store, REF)
    assert found.failed and "could not be listed" in found.reason


def test_a_nested_path_round_trips_through_the_tree(repos: tuple[Path, Path]) -> None:
    _, work = repos
    store = store_of(work)
    packs = work / ".git" / "objects" / "pack"
    before = set(packs.iterdir()) if packs.is_dir() else set()
    assert git.put(store, REF, {"nested/row.json": "{}"}, message="n") == ""
    found = git.read(store, REF)
    assert found.files == {"nested/row.json": "{}"}
    # The commit and its tree went in loose, never as a pack of their own.
    assert found.sha is not None
    assert (work / ".git" / "objects" / found.sha[:2] / found.sha[2:]).is_file()
    assert (set(packs.iterdir()) if packs.is_dir() else set()) == before


def test_a_tree_read_names_what_it_cannot_decode_or_find(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    store = store_of(work)
    with _objects.opened(work) as repo:
        binary = _objects.root_commit(
            repo, {"a": b"\xff"}, "m", identity=store.identity, when=datetime.now(UTC)
        )
    found = store_module._tree_files(store, binary.decode())
    assert found.failed and found.reason.startswith("the tree's files:")
    # A submodule entry is neither a tree nor a blob and is passed over.
    head = git.head_sha(work)
    run_git(work, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    tree = run_git(work, "write-tree").strip()
    run_git(work, "rm", "-q", "--cached", "sub")
    files = store_module._tree_files(store, tree).files
    assert files is not None and "sub" not in files and files
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    outside = store_of(elsewhere)
    assert store_module._tree_files(outside, "0" * 40).reason == (
        f"{elsewhere} is not a git repository"
    )
    assert store_module._missing_locally(outside, ["a" * 40]) == {"a" * 40}
    assert git.list_refs(outside, git.LOCAL_NAMESPACE) is None
    assert store_module._commit_times(outside, [git.LOCAL_NAMESPACE + "x"]) == {
        git.LOCAL_NAMESPACE + "x": None
    }
    assert git.drop(outside, git.LOCAL_NAMESPACE + "x") == (
        f"delete refused: {elsewhere} is not a git repository"
    )
    assert (
        store_module._parse_listing(tools.Result(code=0, stdout="abc\t\n\n", stderr=""))
        == {}
    )


def test_the_committer_date_is_read_in_every_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-02T03:04:05")
    assert store_module._committer_moment() == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "@1700000000 +0100")
    assert store_module._committer_moment() == datetime.fromtimestamp(
        1700000000, tz=UTC
    )
    monkeypatch.setenv("GIT_COMMITTER_DATE", "junk")
    assert abs(store_module._committer_moment() - datetime.now(UTC)) < timedelta(
        minutes=1
    )


def test_a_commit_that_cannot_be_written_is_refused_naming_why(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert store_module._build_commit(store_of(elsewhere), {"a": "{}"}, "m") == (
        "",
        f"the commit could not be written: {elsewhere} is not a git repository",
    )

    def no_space(*_: Any, **__: Any) -> bytes:
        raise OSError("no space")

    monkeypatch.setattr(_objects, "root_commit", no_space)
    assert store_module._build_commit(store_of(work), {"a": "{}"}, "m") == (
        "",
        "the commit could not be written: no space",
    )


def test_a_refused_update_and_a_refused_push_carry_gits_words(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)

    def locked(*_: Any, **__: Any) -> bool:
        raise OSError("locked")

    monkeypatch.setattr(_objects, "set_ref", locked)
    why = git.put(store, git.LOCAL_NAMESPACE + "x", {"a": "{}"}, message="l")
    assert why == "update refused: locked"
    monkeypatch.setattr(_objects, "drop_ref", locked)
    assert git.drop(store, git.LOCAL_NAMESPACE + "x") == "delete refused: locked"
    monkeypatch.undo()
    monkeypatch.setattr(
        store_module, "_git", _failing("fatal: no route to host", when="push")
    )
    why = git.put(store, REF, {"a": "{}"}, message="r")
    assert why == "push refused: fatal: no route to host"
    # A refusal whose reason comes last arrives whole: what a push
    # protection found, where, and the link that allows it.
    refusal = "\n".join(
        (
            "remote: error: GH013: Repository rule violations found for refs/x.",
            "remote: ",
            "remote: - GITHUB PUSH PROTECTION",
            "remote:     Resolve the following violations before pushing again",
            "remote:     - Push cannot contain secrets",
            "remote:       locations:",
            "remote:         - commit: 0123456789abcdef",
            "remote:           path: coverage.json:1",
            "remote:     https://github.com/o/r/security/secret-scanning/unblock-secret/s",
        )
    )
    monkeypatch.setattr(store_module, "_git", _failing(refusal + "\n", when="push"))
    why = git.put(store, REF, {"a": "{}"}, message="r")
    assert why == f"push refused: {refusal}"


def test_commit_times_outside_a_snapshot_with_missing_objects_and_failed_brings(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    local = git.LOCAL_NAMESPACE + "l"
    assert git.put(store, local, {"a": "{}"}, message="l") == ""
    assert git.put(store, REF, {"a": "{}"}, message="r") == ""
    # Remote refs outside a snapshot are unknown; a local one is read,
    # and a local one that does not exist is unknown.
    absent = git.LOCAL_NAMESPACE + "absent"
    times = store_module._commit_times(store, [local, REF, absent])
    assert times[local] is not None and times[REF] is None
    assert times[absent] is None
    # A listed commit the bring reports present but the object store
    # lacks is unknown too, never a crash.
    monkeypatch.setattr(store_module, "_bring", lambda s, snap, wanted: {})
    with git.remote_snapshot(store):
        snapshot = store_module._snapshot(store)
        assert snapshot is not None and snapshot.refs is not None
        snapshot.refs[REF] = "f" * 40
        assert store_module._commit_times(store, [local, REF])[REF] is None
    monkeypatch.setattr(
        store_module, "_bring", lambda s, snap, wanted: dict.fromkeys(wanted, "down")
    )
    with git.remote_snapshot(store):
        assert store_module._commit_times(store, [REF]) == {REF: None}


def test_the_janitor_says_what_it_could_not_read_trim_or_tell(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    series = git.Series(
        "aged", window=2, ci_only=False, local=True, age=timedelta(days=1)
    )
    old = datetime.now(UTC) - timedelta(days=3)
    monkeypatch.setenv("GIT_COMMITTER_DATE", old.isoformat())
    assert series.put(store, {"a": {"x": 1}}, message="a") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    # One aged row (stamped by the store's clock, so forced through the text).
    stale = json.dumps({"schema": 1, "when": old.isoformat()})
    assert git.put(store, series.ref, {"a": stale}, message="age") == ""
    lines = store_module._sweep_series(
        store, series, dry_run=True, now=datetime.now(UTC)
    )
    assert any("older than 1 day" in line for line in lines)
    # Beyond the window alone.
    fresh = git.Series("many", window=1, ci_only=False, local=True)
    assert git.put(store, fresh.ref, {"a": "{}", "b": "{}"}, message="m") == ""
    lines = store_module._sweep_series(
        store, fresh, dry_run=True, now=datetime.now(UTC)
    )
    assert lines == [f"  {fresh.ref}: would drop 1 file(s) beyond its window of 1"]
    # A put the sweep could not make, and a series it could not read.
    monkeypatch.setattr(store_module, "put", lambda *a, **k: "the write refused")
    lines = store_module._sweep_series(
        store, fresh, dry_run=False, now=datetime.now(UTC)
    )
    assert lines[-1] == f"  {fresh.ref}: the write refused"
    monkeypatch.setattr(
        store_module,
        "read",
        lambda s, ref, **_: git.Read(None, None, failed=True, reason="down"),
    )
    lines = store_module._sweep_series(
        store, fresh, dry_run=False, now=datetime.now(UTC)
    )
    assert lines == [
        f"  {fresh.ref}: the many series could not be read: down; nothing swept"
    ]
    # A series without bounds is counted by name, and its read fails alike.
    unbounded = git.Series("open", ci_only=False, local=True)
    lines = store_module._sweep_series(
        store, unbounded, dry_run=False, now=datetime.now(UTC)
    )
    assert lines == [
        f"  {unbounded.ref}: the open series could not be read: down; nothing swept"
    ]


def test_the_janitor_keeps_a_key_whose_age_it_cannot_read_and_says_when_it_cannot_tell(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    family = git.Keyed(
        "fam",
        ("k",),
        ci_only=False,
        local=True,
        stale_after=timedelta(hours=1),
        current=lambda root: None,
        keep=lambda root: None,
    )
    assert family.series("one").put(store, {"a": {}}, message="a") == ""
    monkeypatch.setattr(
        store_module, "_commit_times", lambda s, refs: dict.fromkeys(refs)
    )
    lines = store_module._sweep_family(
        store, family, dry_run=True, now=datetime.now(UTC)
    )
    assert any("the current keys could not be told" in line for line in lines)
    assert any("how many ks to keep could not be told" in line for line in lines)
    assert any("unreadable; kept" in line for line in lines)
    assert store_module._beyond_the_count(store, family, [("one",)]) == (set(), "")


def test_a_rows_age_is_never_older_when_its_stamp_is_junk_or_naive() -> None:
    now = datetime.now(UTC)
    assert not store_module._older("not a date", timedelta(days=1), now)
    assert store_module._older("2000-01-01T00:00:00", timedelta(days=1), now)


def test_a_local_family_declared_ci_only_is_refused() -> None:
    with pytest.raises(ValueError, match="declare it ci_only=False"):
        git.Keyed("local-fam", ("k",), local=True)


def test_the_mirrors_branches_leave_origins_head_out(repos: tuple[Path, Path]) -> None:
    _, work = repos
    run_git(work, "remote", "set-head", "origin", "main")
    store = store_of(work)
    assert git.fetch_store(store, series=("metrics",), branches=True).why == ""
    with git.fetched_snapshot(store):
        heads = git.list_refs(store, "refs/heads/")
    assert heads is not None and set(heads) == {"refs/heads/main"}


def test_a_reachable_commit_under_an_unlistable_snapshot_names_the_listing(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.put(store, REF, {"a": "{}"}, message="a") == ""
    sha = git.read(store, REF).sha
    assert sha is not None
    snapshot = store_module._Snapshot(None, "offline")
    found, why = store_module._reachable(store, snapshot, REF, sha)
    assert found is None and why == "the remote could not be listed: offline"


def test_a_local_series_declared_ci_only_is_refused() -> None:
    with pytest.raises(ValueError, match="declare it ci_only=False"):
        git.Series("local-rows", local=True)
    with pytest.raises(ValueError, match="declare one or the other"):
        git.Series("traced", ci_only=False, local=True, namespace=git.TRACE_NAMESPACE)
