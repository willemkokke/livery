"""The store's failure paths the happy suite never reaches, each one forced."""

from __future__ import annotations

import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import livery.toolroom.tools as tools
from git_seeds import repos_fixture, run_git, store_of  # noqa: F401
from livery import git
from livery.git import _store as store_module

REF = git.NAMESPACE + "metrics"


def _failing(words: str, *, when: str | None = None, code: int = 1) -> Any:
    """A git door that fails calls whose first word is *when*, or every call."""
    real = store_module._git

    def door(root: Path, *args: str, stdin: str | bytes | None = None) -> tools.Result:
        first = next((a for a in args if not a.startswith("-")), "")
        if when is None or first == when:
            return tools.Result(code=code, stdout="", stderr=words)
        return real(root, *args, stdin=stdin)

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

    def door(root: Path, *args: str, stdin: str | bytes | None = None) -> tools.Result:
        done = real(root, *args, stdin=stdin)
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
    assert git.fetch_store(store_of(work)) == git.Fetched(0, "the stamp refused")


def test_a_fetched_block_inside_another_shares_it_and_lists_the_branches(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.fetch_store(store, branches=True).why == ""
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


def test_an_archive_with_a_directory_a_junk_tar_and_a_blank_listing_line(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.put(store, REF, {"nested/row.json": "{}"}, message="n") == ""
    assert git.read(store, REF).files == {"nested/row.json": "{}"}
    real = store_module._git

    def junk_archive(
        root: Path, *args: str, stdin: str | bytes | None = None
    ) -> tools.Result:
        if args[0] == "-c" and "archive" in args:
            Path(args[args.index("-o") + 1]).write_bytes(b"not a tar")
            return tools.Result(code=0, stdout="", stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(store_module, "_git", junk_archive)
    found = git.read(store, REF)
    assert found.failed and "the tree's archive" in found.reason


def test_the_committer_date_is_read_in_every_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-02T03:04:05")
    assert (
        store_module._committer_when()
        == f"{int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp())} +0000"
    )
    monkeypatch.setenv("GIT_COMMITTER_DATE", "@1700000000 +0100")
    assert store_module._committer_when() == "1700000000 +0000"
    monkeypatch.setenv("GIT_COMMITTER_DATE", "junk")
    assert store_module._committer_when().endswith(" +0000")


def test_an_import_whose_marks_are_missing_or_mute_is_refused(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    real = store_module._git

    def mute(root: Path, *args: str, stdin: str | bytes | None = None) -> tools.Result:
        if args[0] == "fast-import":
            marks = next(a for a in args if a.startswith("--export-marks="))
            Path(marks.removeprefix("--export-marks=")).write_text(":2 abc\n")
            return tools.Result(code=0, stdout="", stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(store_module, "_git", mute)
    assert store_module._build_commit(store, {"a": "{}"}, "m") == (
        "",
        "fast-import exported no mark for the commit",
    )

    def gone(root: Path, *args: str, stdin: str | bytes | None = None) -> tools.Result:
        if args[0] == "fast-import":
            return tools.Result(code=0, stdout="", stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(store_module, "_git", gone)
    sha, why = store_module._build_commit(store, {"a": "{}"}, "m")
    assert sha == "" and why.startswith("fast-import exported no marks")


def test_a_refused_update_and_a_refused_push_carry_gits_words(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    monkeypatch.setattr(
        store_module, "_git", _failing("fatal: locked", when="update-ref")
    )
    why = git.put(store, git.LOCAL_NAMESPACE + "x", {"a": "{}"}, message="l")
    assert why == "update refused: fatal: locked"
    monkeypatch.setattr(
        store_module, "_git", _failing("fatal: no route to host", when="push")
    )
    why = git.put(store, REF, {"a": "{}"}, message="r")
    assert why == "push refused: fatal: no route to host"


def test_commit_times_outside_a_snapshot_with_junk_stamps_and_failed_brings(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    local = git.LOCAL_NAMESPACE + "l"
    assert git.put(store, local, {"a": "{}"}, message="l") == ""
    assert git.put(store, REF, {"a": "{}"}, message="r") == ""
    # Remote refs outside a snapshot are unknown; a local one is read.
    times = store_module._commit_times(store, [local, REF])
    assert times[local] is not None and times[REF] is None
    real = store_module._git

    def junk(root: Path, *args: str, stdin: str | bytes | None = None) -> tools.Result:
        if args[0] == "for-each-ref":
            return tools.Result(code=0, stdout=f"{local} soon\n", stderr="")
        if args[0] == "log":
            return tools.Result(code=0, stdout="abc soon\n", stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(store_module, "_git", junk)
    with git.remote_snapshot(store):
        times = store_module._commit_times(store, [local, REF])
    assert times == {local: None, REF: None}
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
        lambda s, ref: git.Read(None, None, failed=True, reason="down"),
    )
    lines = store_module._sweep_series(
        store, fresh, dry_run=False, now=datetime.now(UTC)
    )
    assert lines == [
        f"  {fresh.ref}: the many series could not be read: down; nothing swept"
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
    assert git.fetch_store(store, branches=True).why == ""
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


def test_an_archive_member_without_a_handle_is_passed_over(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    store = store_of(work)
    assert git.put(store, REF, {"a": "{}"}, message="a") == ""
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)
    assert git.read(store, REF).files == {}


def test_a_local_series_declared_ci_only_is_refused() -> None:
    with pytest.raises(ValueError, match="declare it ci_only=False"):
        git.Series("local-rows", local=True)
    with pytest.raises(ValueError, match="declare one or the other"):
        git.Series("traced", ci_only=False, local=True, namespace=git.TRACE_NAMESPACE)
