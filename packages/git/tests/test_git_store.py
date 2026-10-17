"""The state store: refusals first, then the compare-and-swap path.

Every test drives real git against a bare repository standing in for
origin, so the refusals are the transport's own words, not a fake's.
The store under test is built with a desk's policy: no CI run, the
tests' own identity, the default snapshot variable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from git_seeds import (
    IDENTITY,
    repos_fixture,  # noqa: F401
)
from git_seeds import (
    clone as _clone,
)
from git_seeds import (
    run_git as _git,
)
from git_seeds import (
    store_of as _store,
)
from git_spawns import counting_spawns
from livery import git
from livery.git import _objects
from livery.git import _store as store_module

REF = git.NAMESPACE + "metrics"


ROWS = git.Series("rows", window=3, ci_only=False, schema=2)


# --- refusals ----------------------------------------------------------------


def test_put_refuses_a_ref_outside_the_namespace(repos: tuple[Path, Path]) -> None:
    _, work = repos
    why = git.put(_store(work), "refs/heads/main", {"a.json": "{}"}, message="m")
    assert why.startswith("refusing refs/heads/main")
    assert git.NAMESPACE in why
    assert (
        _git(work, "ls-remote", "origin", "refs/heads/main").split()[0]
        == _git(work, "rev-parse", "HEAD").strip()
    )


def test_put_refuses_without_an_origin(tmp_path: Path) -> None:
    lonely = tmp_path / "lonely"
    _git(tmp_path, "init", "-q", str(lonely))
    why = git.put(_store(lonely), REF, {"a.json": "{}"}, message="m")
    assert "could not be read" in why
    assert "origin" in why


def test_read_calls_a_missing_ref_absent(repos: tuple[Path, Path]) -> None:
    _, work = repos
    found = git.read(_store(work), REF)
    assert found == git.Read(None, None, failed=False)


def test_read_calls_an_unreachable_remote_a_failure(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found = git.read(_store(work), REF)
    assert found.files is None and found.failed
    assert found.reason


def test_put_refuses_to_rewrite_from_an_unread_state(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    # The rule that protects every file the writer did not know
    # about: an unreachable ref blocks the whole-tree rewrite.
    origin, work = repos
    assert git.put(_store(work), REF, {"a.json": "1"}, message="first") == ""
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    why = git.put(_store(work), REF, {"b.json": "2"}, message="second")
    assert "could not be read" in why and "would erase" in why
    survivor = _clone(tmp_path, "survivor", origin)
    assert git.read(_store(survivor), REF).files == {"a.json": "1"}


def test_a_stale_lease_re_reads_merges_and_wins(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two writers: the second reads, the first lands in between, and
    # the second's lease is stale. The server refuses atomically; the
    # retry re-reads and merges, so both files survive.
    origin, work = repos
    other = _clone(tmp_path, "other", origin)
    assert git.put(_store(work), REF, {"a.json": "1"}, message="a") == ""
    real_read = git.read
    raced = {"done": False}

    def read_then_race(root: git.StateStore, ref: str) -> git.Read:
        found = real_read(root, ref)
        if root.root == other and not raced["done"]:
            raced["done"] = True
            assert git.put(_store(work), REF, {"b.json": "2"}, message="b") == ""
        return found

    monkeypatch.setattr(store_module, "read", read_then_race)
    assert git.put(_store(other), REF, {"c.json": "3"}, message="c") == ""
    assert real_read(_store(work), REF).files == {
        "a.json": "1",
        "b.json": "2",
        "c.json": "3",
    }


def test_an_exhausted_lease_names_the_other_writer(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, work = repos
    other = _clone(tmp_path, "other", origin)
    assert git.put(_store(work), REF, {"a.json": "1"}, message="a") == ""
    real_read = git.read
    counter = {"n": 0}

    def always_raced(root: git.StateStore, ref: str) -> git.Read:
        found = real_read(root, ref)
        if root.root == other:
            counter["n"] += 1
            assert (
                git.put(_store(work), REF, {f"x{counter['n']}.json": "x"}, message="x")
                == ""
            )
        return found

    monkeypatch.setattr(store_module, "read", always_raced)
    why = git.put(_store(other), REF, {"c.json": "3"}, message="c", attempts=2)
    assert "gave up" in why and "2 attempts" in why
    assert "c.json" not in (real_read(_store(work), REF).files or {})


def test_drop_of_a_missing_ref_is_already_done(repos: tuple[Path, Path]) -> None:
    _, work = repos
    assert git.drop(_store(work), REF) == ""
    assert git.drop(_store(work), "refs/heads/main").startswith("refusing")


# --- the happy path, and the properties every write keeps --------------------


def test_put_lays_files_over_the_kept_tree(repos: tuple[Path, Path]) -> None:
    _, work = repos
    assert (
        git.put(_store(work), REF, {"a.json": "1", "b.json": "2"}, message="one") == ""
    )
    assert (
        git.put(_store(work), REF, {"b.json": "22", "c.json": "3"}, message="two") == ""
    )
    found = git.read(_store(work), REF)
    assert found.files == {"a.json": "1", "b.json": "22", "c.json": "3"}
    assert found.sha == _git(work, "ls-remote", "origin", REF).split()[0]
    assert not found.failed


def test_a_window_keeps_the_newest_names(repos: tuple[Path, Path]) -> None:
    _, work = repos
    rows = {f"run-{n:04d}.json": str(n) for n in range(5)}
    assert git.put(_store(work), REF, rows, message="five", window=3) == ""
    assert git.read(_store(work), REF).files == {
        "run-0002.json": "2",
        "run-0003.json": "3",
        "run-0004.json": "4",
    }


def test_the_commit_is_a_root_with_identity_and_skip_ci(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    _, work = repos
    assert git.put(_store(work), REF, {"a.json": "1"}, message="one") == ""
    assert git.put(_store(work), REF, {"b.json": "2"}, message="two") == ""
    _git(work, "fetch", "-q", "origin", REF)
    parents, author, email, subject = _git(
        work, "log", "-1", "--format=%P%n%an%n%ae%n%s", "FETCH_HEAD"
    ).splitlines()
    assert parents == ""  # replace-only: depth one, no history
    assert (author, email) == IDENTITY
    assert subject == "two [skip ci]"


def test_a_read_creates_no_local_ref_and_a_default_fetch_never_sees_the_namespace(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    origin, work = repos
    assert git.put(_store(work), REF, {"a.json": "1"}, message="one") == ""
    assert git.read(_store(work), REF).files == {"a.json": "1"}
    assert _git(work, "for-each-ref", "refs/workshop") == ""
    fresh = _clone(tmp_path, "fresh", origin)
    _git(fresh, "fetch", "-q", "origin")
    listed = _git(fresh, "for-each-ref", "--format=%(refname)")
    assert "refs/workshop" not in listed
    assert "refs/remotes/origin/main" in listed


def test_an_absent_series_reads_as_no_rows_and_no_failure(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.rows(_store(work)) == git.Rows(())
    assert ROWS.row(_store(work), "any") == (None, "")


def test_an_unreachable_series_names_itself_and_the_reason(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found = ROWS.rows(_store(work))
    assert found.failed and found.rows == () and found.skipped == ()
    assert found.reason.startswith("the rows series could not be read: ")
    assert ROWS.row(_store(work), "any") == (None, found.reason)


def test_a_file_that_is_not_a_row_is_skipped_and_named(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    files = {
        "a": "{not json",
        "b": json.dumps({"schema": 1}),
        "c": json.dumps([1, 2]),
        "d": json.dumps({"schema": 2, "x": 1}),
    }
    assert git.put(_store(work), ROWS.ref, files, message="junk") == ""
    found = ROWS.rows(_store(work))
    assert found.skipped[0] == git.Skipped("a", "does not parse")
    assert tuple(str(item) for item in found.skipped) == (
        "a: does not parse; skipped",
        "b: schema 1, this reader speaks 2; skipped",
        "c: schema none, this reader speaks 2; skipped",
    )
    assert found.rows == (git.Row("d", {"schema": 2, "x": 1}),)
    assert not found.failed and found.reason == ""
    assert ROWS.row(_store(work), "a") == (None, "a: does not parse")
    assert ROWS.row(_store(work), "b") == (None, "b: schema 1, this reader speaks 2")
    assert ROWS.row(_store(work), "d") == (git.Row("d", {"schema": 2, "x": 1}), "")


def _object_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every object the store reads in-process from here on, by its id."""
    from dulwich.repo import Repo

    asked: list[str] = []
    original = Repo.__getitem__

    def counted(self: Repo, name: bytes) -> Any:
        asked.append(bytes(name).decode())
        return original(self, name)

    monkeypatch.setattr(Repo, "__getitem__", counted)
    return asked


def _blob_ids(work: Path) -> dict[str, str]:
    """The blob id of each file the last commit the store built holds."""
    listed = _git(work, "ls-tree", "-r", store_module.IMPORT_REF)
    return {line.split()[3]: line.split()[2] for line in listed.splitlines()}


def test_a_read_that_names_its_files_reads_those_alone_and_names_the_rest(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A series of large rows answers a reader of a few at the cost of
    # those few: a file the read passes over is named, never read.
    _, work = repos
    files = {name: json.dumps({"schema": 2, "x": name}) for name in ("a", "b", "c")}
    assert git.put(_store(work), ROWS.ref, files, message="three") == ""
    blobs = _blob_ids(work)
    asked = _object_reads(monkeypatch)
    found = ROWS.rows(_store(work), only="b".__eq__)
    assert [row.name for row in found.rows] == ["b"]
    assert found.unread == ("a", "c") and found.skipped == ()
    assert blobs["b"] in asked
    assert blobs["a"] not in asked and blobs["c"] not in asked
    named = git.read(_store(work), ROWS.ref, only=lambda _name: False)
    assert named.files == {} and named.names == ("a", "b", "c")
    # One row or one file by name reads that file alone.
    asked.clear()
    assert ROWS.row(_store(work), "c") == (git.Row("c", json.loads(files["c"])), "")
    assert ROWS.file(_store(work), "a") == (files["a"], "")
    assert blobs["b"] not in asked
    # A whole read reads every file and names them all.
    whole = git.read(_store(work), ROWS.ref)
    assert whole.files == files and whole.names == ("a", "b", "c")


def test_the_janitor_counts_a_series_without_bounds_by_name_alone(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A series with neither a window nor an age has nothing to trim, so
    # the janitor reads none of its rows to say so.
    _, work = repos
    unbounded = git.Series("open", ci_only=False, local=True)
    rows = {name: {"x": name} for name in ("a", "b")}
    assert unbounded.put(_store(work), rows, message="two") == ""
    asked = _object_reads(monkeypatch)
    lines = git.sweep(_store(work), (unbounded,), remote=False)
    assert lines == [f"  {unbounded.ref}: 2 row(s), within its bounds"]
    blobs = _blob_ids(work)
    assert not set(blobs.values()) & set(asked)


def test_a_series_refuses_blobs_without_a_directory_or_beside_a_window(
    repos: tuple[Path, Path],
) -> None:
    with pytest.raises(ValueError, match="blobs name a directory ending in a slash"):
        git.Series("kept", ci_only=False, blobs="facts")
    with pytest.raises(ValueError, match="declares no window"):
        git.Series("kept", ci_only=False, blobs="facts/", window=3)
    # A row named inside the blobs, or a blob outside them, is refused.
    _, work = repos
    kept = git.Series("kept", ci_only=False, local=True, blobs="facts/")
    for rows, blobs in (({"facts/a.json": {"x": 1}}, {}), ({}, {"a.json": "{}"})):
        why = kept.put(_store(work), rows, message="astray", blobs=blobs)
        assert why.startswith(f"refusing {kept.ref}: ")
        assert "a row lives outside the series' blobs (facts/)" in why
    assert kept.rows(_store(work)) == git.Rows(())


def test_a_series_keeps_its_blobs_as_written_and_reads_them_back_as_text(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    family = git.Keyed("kept", ("leg",), ci_only=False, local=True, blobs="facts/")
    kept = family.series("a")
    assert kept.blobs == "facts/"
    text = '{"statements":"1-2"}'
    why = kept.put(
        _store(work),
        {"row.json": {"x": 1}},
        message="one row, one blob",
        blobs={"facts/one.json": text},
    )
    assert why == ""
    # A blob is the text given, unstamped: its git blob id is the text's.
    blobs = _blob_ids(work)
    header = f"blob {len(text)}\0".encode()
    assert (
        blobs["facts/one.json"]
        == hashlib.sha1(header + text.encode(), usedforsecurity=False).hexdigest()
    )
    # A read never parses a blob as a row: it hands back the text of the
    # ones it accepts, and names the rest.
    found = kept.rows(_store(work))
    assert [row.name for row in found.rows] == ["row.json"] and found.skipped == ()
    assert found.blobs == {"facts/one.json": text}
    asked = _object_reads(monkeypatch)
    found = kept.rows(_store(work), only="row.json".__eq__)
    assert found.blobs == {} and found.unread == ("facts/one.json",)
    assert blobs["facts/one.json"] not in asked
    # A write lays its rows over the blobs, which stand until named.
    assert kept.put(_store(work), {"row.json": {"x": 2}}, message="again") == ""
    assert kept.rows(_store(work)).blobs == {"facts/one.json": text}
    assert kept.put(_store(work), {}, message="drop", remove=["facts/one.json"]) == ""
    assert kept.rows(_store(work)).blobs == {}
    # The janitor counts the rows alone.
    again = kept.put(_store(work), {}, message="blob", blobs={"facts/two.json": text})
    assert again == ""
    lines = git.sweep(_store(work), (family,), remote=False)
    assert lines == [f"  {kept.ref}: 1 row(s), within its bounds"]


def test_a_tree_the_checkout_lacks_fails_naming_it(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    found = store_module._tree_files(_store(work), "0" * 40)
    assert found.failed and found.files is None
    assert found.reason == f"the tree {'0' * 12} is not in the repository"


def test_a_row_written_with_carriage_returns_reads_back_as_written(
    repos: tuple[Path, Path],
) -> None:
    # A row a Windows checkout wrote with carriage returns, before blobs
    # were written without newline translation: the blobs are read from
    # the object store as bytes, so the row comes back as it is, and no
    # process starts.
    _, work = repos
    ref = git.LOCAL_NAMESPACE + "legacy"
    crlf = work / "crlf.json"
    crlf.write_bytes(b'{"schema": 2, "when": "2026-01-01T00:00:00+00:00", "x": 1}\r\n')
    # As written: hash-object given a path applies core.autocrlf, which
    # Git for Windows turns on, and would store the row with plain
    # newlines there.
    blob = _git(work, "hash-object", "-w", "--no-filters", str(crlf)).strip()
    plain = work / "plain.json"
    plain.write_bytes(b'{"schema": 2, "when": "2026-01-01T00:00:01+00:00", "x": 2}\n')
    blob2 = _git(work, "hash-object", "-w", "--no-filters", str(plain)).strip()
    # NUL-separated, as the store feeds it: a newline through a
    # text-mode stdin becomes "\r\n" on Windows and names the file "a\r".
    tree = subprocess.run(
        ["git", "mktree", "-z"],
        cwd=work,
        input=f"100644 blob {blob}\ta\x00100644 blob {blob2}\tb\x00",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit = _git(work, "commit-tree", tree, "-m", "legacy").strip()
    _git(work, "update-ref", ref, commit)
    with counting_spawns() as spawned:
        found = git.read(_store(work), ref)
    assert not found.failed and found.files is not None
    assert found.files["a"] == crlf.read_bytes().decode()
    assert found.files["b"] == plain.read_bytes().decode()
    assert spawned["git"] == 0, spawned
    rows = git.Series("legacy", local=True, ci_only=False, schema=2).rows(_store(work))
    assert [row.data["x"] for row in rows.rows] == [2, 1]


def test_a_write_whose_commit_cannot_be_written_is_refused_naming_why(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos

    def no_space(*_: Any, **__: Any) -> bytes:
        raise OSError("no space")

    monkeypatch.setattr(_objects, "root_commit", no_space)
    why = ROWS.put(_store(work), {"a": {"x": 1}, "b": {"x": 2}}, message="two")
    assert why == "the commit could not be written: no space"
    monkeypatch.undo()
    assert ROWS.rows(_store(work)) == git.Rows(())


def test_a_written_row_carries_no_platform_line_ends(
    repos: tuple[Path, Path],
) -> None:
    # The import stream goes as bytes: the blob git holds is the row's
    # UTF-8 exactly, whatever platform wrote it, so a reader on another
    # platform finds the same bytes and the same blob id.
    _, work = repos
    assert (
        ROWS.put(_store(work), {"a": {"x": "caf\u00e9", "n": [1, 2]}}, message="one")
        == ""
    )
    found = git.read(_store(work), ROWS.ref)
    assert found.files is not None and found.sha is not None
    blob = subprocess.run(
        ["git", "show", f"{found.sha}:a"], cwd=work, capture_output=True, check=True
    ).stdout
    assert blob == found.files["a"].encode("utf-8")
    assert b"\r" not in blob


def test_a_read_and_a_write_cost_a_fixed_handful_of_processes(
    repos: tuple[Path, Path],
) -> None:
    # The pin: a series of sixty rows costs the same processes as one
    # of six. A write is a read (the fetch), the commit written
    # in-process, and the push.
    _, work = repos
    wide = git.Series("wide", window=400, ci_only=False, schema=2)
    assert wide.put(_store(work), {"r000": {"x": 0}}, message="one") == ""
    with counting_spawns() as six:
        assert (
            wide.put(
                _store(work),
                {f"r{n:03d}": {"x": n} for n in range(1, 7)},
                message="six",
            )
            == ""
        )
    with counting_spawns() as sixty:
        assert (
            wide.put(
                _store(work),
                {f"r{n:03d}": {"x": n} for n in range(7, 67)},
                message="sixty",
            )
            == ""
        )
    # The fetch and the push: two, whatever the size, and no readback.
    assert six["git"] == sixty["git"] == 2, (six, sixty)
    assert sixty["git ls-remote"] == 0
    with counting_spawns() as reading:
        found = wide.rows(_store(work))
    assert len(found.rows) == 67 and not found.failed
    assert reading["git"] == 1, reading


# --- the snapshot: refusals first, then one listing for a verb ----------------


def test_a_snapshot_whose_listing_failed_fails_every_read_and_listing(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with git.remote_snapshot(_store(work), fetch=("rows",)):
        found = git.read(_store(work), ROWS.ref)
        assert found.failed and found.reason.startswith(
            "the remote could not be listed"
        )
        assert git.list_refs(_store(work), git.NAMESPACE) is None
        why = ROWS.put(_store(work), {"b": {"x": 2}}, message="two")
        assert "could not be read" in why and "the remote could not be listed" in why
        assert git.drop(_store(work), ROWS.ref).startswith("delete refused")


def test_a_snapshot_reads_an_absent_ref_as_absence_without_a_fetch(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    with git.remote_snapshot(_store(work)):
        with counting_spawns() as spawned:
            found = git.read(_store(work), ROWS.ref)
        assert found == git.Read(None, None, failed=False)
        assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0
        assert git.list_refs(_store(work), git.NAMESPACE) == {}


def test_a_snapshot_lists_once_and_fetches_the_named_series_together(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    series = [
        git.Series(name, window=50, ci_only=False, schema=2)
        for name in ("alpha", "beta", "gamma")
    ]
    for item in series:
        assert item.put(_store(other), {"r": {"x": item.name}}, message=item.name) == ""
    # A checkout that holds none of the commits: one listing, one fetch.
    with (
        counting_spawns() as cold,
        git.remote_snapshot(_store(work), fetch=("alpha", "beta", "gamma")),
    ):
        for item in series:
            found = item.rows(_store(work))
            assert [row.data["x"] for row in found.rows] == [item.name]
        listed = git.list_refs(_store(work), git.NAMESPACE)
        assert listed is not None and set(listed) >= {item.ref for item in series}
    assert cold["git ls-remote"] == 1 and cold["git fetch"] == 1, cold
    # The commits are local now: one listing and no fetch at all.
    with counting_spawns() as warm, git.remote_snapshot(_store(work), fetch=("alpha",)):
        for item in series:
            assert not item.rows(_store(work)).failed
    assert warm["git ls-remote"] == 1 and warm["git fetch"] == 0, warm
    # Outside a snapshot every read fetches on its own, as before.
    with counting_spawns() as bare:
        for item in series:
            assert not item.rows(_store(work)).failed
    assert bare["git fetch"] == 3


def test_a_write_inside_a_snapshot_re_lists_on_a_stale_lease_and_records_its_sha(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    with git.remote_snapshot(_store(work), fetch=("rows",)):
        assert [row.name for row in ROWS.rows(_store(work)).rows] == ["a"]
        # Another writer moves the ref after the listing.
        assert ROWS.put(_store(other), {"b": {"x": 2}}, message="two") == ""
        with counting_spawns() as spawned:
            assert ROWS.put(_store(work), {"c": {"x": 3}}, message="three") == ""
        assert spawned["git ls-remote"] == 1  # the re-listing alone, no readback
        # The write merged the other writer's row and recorded its own sha.
        found = ROWS.rows(_store(work))
        assert sorted(row.name for row in found.rows) == ["a", "b", "c"]
        assert found.rows[0].name == "c"
        listed = git.list_refs(_store(work), ROWS.ref)
        assert (
            listed is not None
            and listed[ROWS.ref] == git.read(_store(work), ROWS.ref).sha
        )


def test_a_read_of_a_ref_the_remote_moved_past_the_listing_takes_the_current_commit(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    # A clone made before the store's first write holds none of its
    # commits (a local clone copies the whole object store, so the
    # order matters): the listing names the ref's commit, and the
    # read has to fetch it.
    fresh = _clone(tmp_path, "fresh", tmp_path / "origin.git")
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    with git.remote_snapshot(_store(fresh)):
        # Another writer moves the ref between the listing and the read,
        # so a fetch by the ref's name brings the new commit and not
        # the listed one; a read of the listed sha would fail with
        # git's "not a tree object".
        assert ROWS.put(_store(other), {"b": {"x": 2}}, message="two") == ""
        with counting_spawns() as spawned:
            found = ROWS.rows(_store(fresh))
        assert not found.failed, found.reason
        assert sorted(row.name for row in found.rows) == ["a", "b"]
        assert found.rows[0].name == "b"
        # One fetch, and one listing again for the moved ref.
        assert spawned["git fetch"] == 1 and spawned["git ls-remote"] == 1, spawned
        # A write inside leases on the current commit and lands.
        assert ROWS.put(_store(fresh), {"c": {"x": 3}}, message="three") == ""
    assert sorted(row.name for row in ROWS.rows(_store(fresh)).rows) == ["a", "b", "c"]


def test_a_published_listing_opened_after_a_move_reads_the_current_rows(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CI shape: the job runner lists once, an earlier run's gate
    # job moves a ref a second later, and an entry opens the published
    # listing naming the family it prefetches.
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    fresh = _clone(tmp_path, "fresh", tmp_path / "origin.git")
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    monkeypatch.delenv("WORKSHOP_SNAPSHOT", raising=False)
    with git.remote_snapshot(_store(fresh), publish=True) as named:
        assert named is not None
        monkeypatch.setenv("WORKSHOP_SNAPSHOT", named)
        assert ROWS.put(_store(other), {"b": {"x": 2}}, message="two") == ""
        store_module._SNAPSHOTS.clear()
        with git.remote_snapshot(_store(fresh), fetch=("rows",)):
            found = ROWS.rows(_store(fresh))
            assert not found.failed, found.reason
            assert found.rows[0].name == "b"
        monkeypatch.delenv("WORKSHOP_SNAPSHOT")


def test_the_listing_carries_the_branches_and_only_them_beside_the_store(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    with counting_spawns() as spawned, git.remote_snapshot(_store(work)):
        heads = git.list_refs(_store(work), "refs/heads/")
        assert heads is not None and "refs/heads/main" in heads
        assert git.list_refs(_store(work), "refs/heads/nowhere") == {}
        # A namespace the listing did not take is asked for on its own.
        assert git.list_refs(_store(work), "refs/tags/") == {}
    assert spawned["git ls-remote"] == 2  # the snapshot's, and the tags'


def test_a_published_snapshot_serves_the_children_and_carries_their_writes(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    _, work = repos
    monkeypatch.delenv("WORKSHOP_SNAPSHOT", raising=False)
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    with git.remote_snapshot(_store(work), publish=True) as named:
        assert named is not None
        # The parent hands the path to its children; a child is
        # its own process in truth, so here the variable is set by
        # hand for the blocks below.
        monkeypatch.setenv("WORKSHOP_SNAPSHOT", named)
        published = _json.loads(Path(named).read_text("utf-8"))
        assert (
            published["root"] == str(work.resolve()) and ROWS.ref in published["refs"]
        )
        # A child: the same checkout, its own process in truth, its own
        # block here; it lists nothing and reads what the file says.
        store_module._SNAPSHOTS.clear()
        with (
            counting_spawns() as child,
            git.remote_snapshot(_store(work), fetch=("rows",)),
        ):
            assert [row.name for row in ROWS.rows(_store(work)).rows] == ["a"]
            assert ROWS.put(_store(work), {"b": {"x": 2}}, message="two") == ""
        assert child["git ls-remote"] == 0, child
        # The child's write reached the file for the entries after it.
        after = _json.loads(Path(named).read_text("utf-8"))
        assert after["refs"][ROWS.ref] != published["refs"][ROWS.ref]
        store_module._SNAPSHOTS.clear()
        with counting_spawns() as next_child, git.remote_snapshot(_store(work)):
            assert [row.name for row in ROWS.rows(_store(work)).rows] == ["b", "a"]
        assert next_child["git ls-remote"] == 0 and next_child["git fetch"] == 0
        monkeypatch.delenv("WORKSHOP_SNAPSHOT")
    assert not Path(named).exists()


def test_a_published_snapshot_that_cannot_be_read_or_is_anothers_counts_as_none(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    corrupt = tmp_path / "snapshot.json"
    corrupt.write_text("{not json")
    monkeypatch.setenv("WORKSHOP_SNAPSHOT", str(corrupt))
    with counting_spawns() as spawned, git.remote_snapshot(_store(work)):
        assert [row.name for row in ROWS.rows(_store(work)).rows] == ["a"]
    assert spawned["git ls-remote"] == 1
    other = tmp_path / "other.json"
    other.write_text('{"root": "/elsewhere", "refs": {}, "local": []}')
    monkeypatch.setenv("WORKSHOP_SNAPSHOT", str(other))
    with counting_spawns() as spawned, git.remote_snapshot(_store(work)):
        assert [row.name for row in ROWS.rows(_store(work)).rows] == ["a"]
    assert spawned["git ls-remote"] == 1
    # A parent whose listing failed publishes nothing.
    monkeypatch.delenv("WORKSHOP_SNAPSHOT")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with git.remote_snapshot(_store(work), publish=True) as named:
        assert named is None


def test_a_drop_inside_a_snapshot_forgets_the_ref_and_nesting_shares_the_listing(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.put(_store(work), {"a": {"x": 1}}, message="one") == ""
    with counting_spawns() as spawned, git.remote_snapshot(_store(work)):
        with git.remote_snapshot(_store(work)):  # nested: the outer listing serves
            assert git.list_refs(_store(work), ROWS.ref) is not None
        assert git.drop(_store(work), ROWS.ref) == ""
        assert git.list_refs(_store(work), ROWS.ref) == {}
        assert git.read(_store(work), ROWS.ref) == git.Read(None, None, failed=False)
    assert spawned["git ls-remote"] == 1
    # The block is closed: the direct path again, and the ref is gone.
    assert git.read(_store(work), ROWS.ref) == git.Read(None, None, failed=False)


def test_put_stamps_the_schema_and_the_time_over_what_a_row_carries(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.put(_store(work), {"first": {"x": 1}}, message="one") == ""
    late = {"second": {"schema": 99, "when": "never"}}
    assert ROWS.put(_store(work), late, message="two") == ""
    found = ROWS.rows(_store(work))
    assert [row.name for row in found.rows] == ["second", "first"]
    assert all(row.data["schema"] == 2 for row in found.rows)
    assert found.rows[0].when > found.rows[1].when > "2026"


def test_put_removes_the_named_rows_and_keeps_the_rest(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert (
        ROWS.put(
            _store(work), {"a": {"x": 1}, "b": {"x": 2}, "c": {"x": 3}}, message="three"
        )
        == ""
    )
    # A record replaced in place: one row fresh, one removed, one
    # standing with the stamp it had.
    before = {row.name: row.when for row in ROWS.rows(_store(work)).rows}
    assert (
        ROWS.put(_store(work), {"a": {"x": 11}}, message="again", remove=["b", "gone"])
        == ""
    )
    found = ROWS.rows(_store(work))
    assert {row.name: row.data["x"] for row in found.rows} == {"a": 11, "c": 3}
    stamps = {row.name: row.when for row in found.rows}
    assert stamps["c"] == before["c"] and stamps["a"] > before["a"]


def test_the_window_keeps_the_newest_rows_by_their_stamps_not_their_names(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    # The newest row has the smallest name: a name order would evict it.
    for name in ("zz", "mm", "aa", "00"):
        assert ROWS.put(_store(work), {name: {}}, message=name) == ""
    assert [row.name for row in ROWS.rows(_store(work)).rows] == ["00", "aa", "mm"]


# --- keyed families: refusals first -------------------------------------------

FAMILY = git.Keyed("family", ("leg", "package"), window=2, ci_only=False)


def test_a_family_refuses_a_key_of_the_wrong_arity_and_makes_safe_refs() -> None:
    with pytest.raises(ValueError, match="keyed by leg, package; got 1 part"):
        FAMILY.series("one")
    series = FAMILY.series("check-ubuntu-3.14", "livery/forge")
    assert series.ref == "refs/workshop/family/check-ubuntu-3-14/livery-forge"
    assert series.window == 2 and not series.ci_only and series.schema == 1


def test_a_family_that_cannot_be_listed_says_so(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert FAMILY.listed(_store(work)) is None


def test_a_family_lists_its_keys_and_leaves_other_refs_out(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert FAMILY.listed(_store(work)) == []
    for leg, package in (("b", "y"), ("a", "x"), ("a", "y")):
        assert (
            FAMILY.series(leg, package).put(_store(work), {"r": {}}, message="m") == ""
        )
    # A ref of another arity under the prefix (the marks beside the
    # coverage families) is not the family's.
    assert git.put(_store(work), FAMILY.prefix + "marks", {"m": "1"}, message="s") == ""
    assert FAMILY.listed(_store(work)) == [("a", "x"), ("a", "y"), ("b", "y")]
    assert FAMILY.listed(_store(work), "a") == [("a", "x"), ("a", "y")]


# --- the local scope: refusals first ------------------------------------------

LOCAL = git.Series("local-rows", window=2, ci_only=False, local=True)


def test_a_local_read_outside_a_repository_is_a_failure_with_git_words(
    tmp_path: Path,
) -> None:
    found = LOCAL.rows(_store(tmp_path))
    assert found.failed and "not a git repository" in found.reason
    assert "could not be read" in LOCAL.put(_store(tmp_path), {"r": {}}, message="m")


def test_a_local_put_never_reaches_the_remote_and_a_fresh_clone_starts_empty(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    origin, work = repos
    assert LOCAL.ref == "refs/workshop-local/local-rows"
    assert LOCAL.put(_store(work), {"r": {"x": 1}}, message="local") == ""
    _git(work, "push", "-q", "origin", "main")
    assert "workshop-local" not in _git(work, "ls-remote", "origin")
    assert LOCAL.rows(_store(_clone(tmp_path, "fresh", origin))) == git.Rows(())
    assert [row.name for row in LOCAL.rows(_store(work)).rows] == ["r"]


def test_worktrees_share_the_local_series_and_the_window_holds(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    tree = tmp_path / "tree"
    _git(work, "worktree", "add", "-q", "-b", "side", str(tree))
    assert LOCAL.put(_store(tree), {"from-tree": {}}, message="t") == ""
    assert [row.name for row in LOCAL.rows(_store(work)).rows] == ["from-tree"]
    assert LOCAL.put(_store(work), {"from-work": {}}, message="w") == ""
    assert LOCAL.put(_store(work), {"newest": {}}, message="n") == ""
    assert [row.name for row in LOCAL.rows(_store(tree)).rows] == [
        "newest",
        "from-work",
    ]


def test_a_local_write_that_lost_the_race_re_reads_and_wins(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    ref = git.LOCAL_NAMESPACE + "race"
    assert git.put(_store(work), ref, {"a": "{}"}, message="a") == ""
    real_read = git.read
    raced = {"done": False}

    def read_then_race(root: git.StateStore, name: str) -> git.Read:
        found = real_read(root, name)
        if not raced["done"]:
            raced["done"] = True
            assert git.put(_store(work), ref, {"b": "{}"}, message="b") == ""
        return found

    monkeypatch.setattr(store_module, "read", read_then_race)
    assert git.put(_store(work), ref, {"c": "{}"}, message="c") == ""
    assert set(real_read(_store(work), ref).files or {}) == {"a", "b", "c"}


def test_a_local_drop_is_idempotent_and_a_local_family_lists_its_keys(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = git.Keyed("local-family", ("a", "b"), ci_only=False, local=True)
    assert family.listed(_store(work)) == []
    for key in (("x", "y"), ("x", "z")):
        assert family.series(*key).put(_store(work), {"r": {}}, message="m") == ""
    assert family.listed(_store(work)) == [("x", "y"), ("x", "z")]
    assert family.listed(_store(work), "x") == [("x", "y"), ("x", "z")]
    ref = family.series("x", "y").ref
    assert ref == "refs/workshop-local/local-family/x/y"
    assert git.drop(_store(work), ref) == ""
    assert git.drop(_store(work), ref) == ""
    assert family.listed(_store(work)) == [("x", "z")]


# --- the janitor: refusals first ------------------------------------------------

HALVES = git.Keyed("run", ("run", "leg"), stale_after=timedelta(hours=6))
BOUNDED = git.Series(
    "bounded", window=3, ci_only=False, local=True, age=timedelta(days=7)
)


def _stamped(when: datetime) -> str:
    return json.dumps({"schema": 1, "when": when.isoformat()})


def _unknown(root: Path) -> set[tuple[str, ...]] | None:
    return None


def _only_a_x(root: Path) -> set[tuple[str, ...]] | None:
    keys: set[tuple[str, ...]] = {("a", "x")}
    return keys


def test_the_janitor_never_touches_a_remote_series_from_a_machine(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    remote = git.Series("remote-rows", window=1, ci_only=False)
    files = {f"r{n}": _stamped(datetime(2026, 1, n, tzinfo=UTC)) for n in range(1, 4)}
    assert git.put(_store(work), remote.ref, files, message="three") == ""
    lines = git.sweep(_store(work), (remote, HALVES), remote=False)
    assert lines == ["  remote series: swept inside CI, never from a machine"]
    assert len(git.read(_store(work), remote.ref).files or {}) == 3


def test_a_family_that_cannot_be_listed_drops_nothing_and_says_so(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = git.sweep(_store(work), (HALVES,), remote=True)
    assert lines == [f"  {git.RUN_PREFIX}*: could not be listed; nothing swept"]


def test_a_family_whose_current_keys_cannot_be_told_drops_no_orphan(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False, current=_unknown)
    assert family.series("a", "x").put(_store(work), {"r": {}}, message="m") == ""
    lines = git.sweep(_store(work), (family,), remote=True)
    assert lines[0] == (
        f"  {family.prefix}*: the current keys could not be told; no orphan dropped"
    )
    assert family.listed(_store(work)) == [("a", "x")]


def test_the_janitor_drops_stale_halves_and_keeps_young_ones(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    old, young = HALVES.series("11", "check-a"), HALVES.series("12", "check-a")
    half = json.dumps({"schema": 1, "job": "check"})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert git.put(_store(work), old.ref, {"row.json": half}, message="old") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert git.put(_store(work), young.ref, {"row.json": half}, message="young") == ""
    # A half spelled before the family made its parts ref-safe is
    # listed as its ref spells it and dropped by that ref.
    dotted = git.RUN_PREFIX + "10/check-a-3.14"
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert git.put(_store(work), dotted, {"row.json": half}, message="dotted") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert HALVES.at("10", "check-a-3.14").ref == dotted
    said = git.sweep(_store(work), (HALVES,), remote=True, dry_run=True)
    assert any(
        line.startswith(f"  {dotted}: ") and "would drop" in line for line in said
    )
    assert any(
        line.startswith(f"  {old.ref}: ")
        and line.endswith("old, past 6.0h; would drop")
        for line in said
    )
    assert git.read(_store(work), old.ref).files == {"row.json": half}
    lines = git.sweep(_store(work), (HALVES,), remote=True)
    assert any(
        line.startswith(f"  {old.ref}: ") and line.endswith("; dropped")
        for line in lines
    )
    assert f"  {young.ref}: 1 row(s), within its bounds" in lines
    assert git.read(_store(work), old.ref).files is None
    assert git.read(_store(work), dotted).files is None
    assert git.read(_store(work), young.ref).files == {"row.json": half}
    # Re-running is the recovery procedure: the second sweep drops nothing.
    again = git.sweep(_store(work), (HALVES,), remote=True)
    assert not any(line.endswith("dropped") for line in again)


def test_the_janitor_trims_a_window_and_ages_rows_and_a_dry_run_only_says(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    now = datetime.now(UTC)
    files = {
        "old1": _stamped(now - timedelta(days=9)),
        "old2": _stamped(now - timedelta(days=8)),
        "n4": _stamped(now - timedelta(hours=4)),
        "n3": _stamped(now - timedelta(hours=3)),
        "n2": _stamped(now - timedelta(hours=2)),
        "n1": _stamped(now - timedelta(hours=1)),
    }
    assert git.put(_store(work), BOUNDED.ref, files, message="six") == ""
    said = git.sweep(_store(work), (BOUNDED,), remote=False, dry_run=True)
    assert said == [
        f"  {BOUNDED.ref}: would drop 2 row(s) older than 7 day(s)",
        f"  {BOUNDED.ref}: would drop 1 file(s) beyond its window of 3",
    ]
    assert set(git.read(_store(work), BOUNDED.ref).files or {}) == set(files)
    done = git.sweep(_store(work), (BOUNDED,), remote=False)
    assert done == [
        f"  {BOUNDED.ref}: dropped 2 row(s) older than 7 day(s)",
        f"  {BOUNDED.ref}: dropped 1 file(s) beyond its window of 3",
    ]
    assert set(git.read(_store(work), BOUNDED.ref).files or {}) == {"n1", "n2", "n3"}
    assert git.sweep(_store(work), (BOUNDED,), remote=False) == [
        f"  {BOUNDED.ref}: 3 row(s), within its bounds"
    ]


def test_a_lingering_family_keeps_a_gone_key_for_a_run_in_flight_then_drops_it(
    repos: tuple[Path, Path],
) -> None:
    # The refusal first: a branch's coverage record is read by main's run
    # for the squash that merged it, after the merge deleted the branch,
    # so a key the world no longer produces stays for the family's linger
    # and goes only once it is older than that.
    _, work = repos
    family = git.Keyed(
        "cov",
        ("leg", "unit"),
        window=2,
        ci_only=False,
        current=_only_a_x,
        linger=timedelta(hours=1),
    )
    for key in (("a", "x"), ("b", "x")):
        assert family.series(*key).put(_store(work), {"r": {}}, message="m") == ""
    gone = family.series("b", "x").ref
    kept = git.sweep(_store(work), (family,), remote=True)
    assert any(
        line.startswith(f"  {gone}: no current leg and unit produces it; kept ")
        and "into the 1.0h a run in flight may still need it" in line
        for line in kept
    )
    assert family.listed(_store(work)) == [("a", "x"), ("b", "x")]
    later = datetime.now(UTC) + timedelta(hours=2)
    done = git.sweep(_store(work), (family,), remote=True, now=later)
    assert f"  {gone}: no current leg and unit produces it; dropped" in done
    assert family.listed(_store(work)) == [("a", "x")]


def test_the_janitor_drops_a_key_no_current_producer_makes_and_keeps_the_rest(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = git.Keyed(
        "cov", ("leg", "unit"), window=2, ci_only=False, current=_only_a_x
    )
    for key in (("a", "x"), ("b", "x")):
        assert family.series(*key).put(_store(work), {"r": {}}, message="m") == ""
    gone = family.series("b", "x").ref
    said = git.sweep(_store(work), (family,), remote=True, dry_run=True)
    assert f"  {gone}: no current leg and unit produces it; would drop" in said
    assert family.listed(_store(work)) == [("a", "x"), ("b", "x")]
    done = git.sweep(_store(work), (family,), remote=True)
    assert f"  {gone}: no current leg and unit produces it; dropped" in done
    assert f"  {family.series('a', 'x').ref}: 1 row(s), within its bounds" in done
    assert family.listed(_store(work)) == [("a", "x")]


def test_the_fetched_snapshot_fails_every_read_until_the_store_was_fetched(
    repos: tuple[Path, Path],
) -> None:
    """Never fetched reads as unreachable, naming the sync, and never as absence."""
    _, work = repos
    with counting_spawns() as spawned, git.fetched_snapshot(_store(work)):
        found = git.read(_store(work), REF)
        assert found.failed
        assert "has not been fetched" in found.reason and "sync`" in found.reason
        assert git.list_refs(_store(work), git.NAMESPACE) is None
        rows = git.Series("metrics", window=50, ci_only=False).rows(_store(work))
        assert rows.failed and "has not been fetched" in rows.reason
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0


def test_a_read_outside_the_mirror_fails_naming_the_ref_and_the_scope(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    """The mirror holds the named series alone: any other remote ref is no absence."""
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False)
    key = family.series("a", "x")
    assert alpha.put(_store(other), {"r": {"x": "alpha"}}, message="alpha") == ""
    assert key.put(_store(other), {"r": {"x": "fam"}}, message="fam") == ""
    assert git.fetch_store(_store(work), series=("alpha", "beta"))[:2] == (1, "")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with counting_spawns() as spawned, git.fetched_snapshot(_store(work)):
        # A family's series origin holds is outside the mirror: the
        # reason names the ref, the mirror's scope, and the sync.
        found = key.rows(_store(work))
        assert found.failed and not found.rows
        assert key.ref in found.reason
        assert "alpha, beta" in found.reason and "sync`" in found.reason
        one, why = key.row(_store(work), "r")
        assert one is None and key.ref in why
        # So is a trace, which no fetch mirrors.
        trace = git.read(_store(work), git.TRACE_NAMESPACE + "about/abc")
        assert trace.failed and trace.reason.startswith(git.TRACE_NAMESPACE)
        # A named series origin holds no ref for yet is absence.
        beta = git.Series("beta", window=50, ci_only=False)
        assert git.read(_store(work), beta.ref) == git.Read(None, None, failed=False)
        assert not beta.rows(_store(work)).failed
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0


def test_a_listing_under_a_prefix_the_mirror_does_not_hold_is_unlistable_not_empty(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False)
    assert alpha.put(_store(other), {"r": {"x": "alpha"}}, message="alpha") == ""
    assert family.series("a", "x").put(_store(other), {"r": {}}, message="fam") == ""
    assert git.fetch_store(_store(work), series=("alpha",))[:2] == (1, "")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with counting_spawns() as spawned, git.fetched_snapshot(_store(work)):
        # The family's keys, the whole namespace and the traces: none
        # of them is the mirror's to answer.
        assert family.listed(_store(work)) is None
        assert git.list_refs(_store(work), git.NAMESPACE) is None
        assert git.list_refs(_store(work), git.TRACE_NAMESPACE) is None
        # A prefix at a mirrored series answers, and so do the branches.
        listed = git.list_refs(_store(work), alpha.ref)
        assert listed is not None and set(listed) == {alpha.ref}
        heads = git.list_refs(_store(work), "refs/heads/")
        assert heads is not None and set(heads) == {"refs/heads/main"}
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0


def test_a_stamp_of_another_schema_or_naming_no_series_reads_as_not_fetched(
    repos: tuple[Path, Path],
) -> None:
    """The gate names the sync, which takes the mirror again under this scope."""
    _, work = repos
    assert git.fetch_store(_store(work), series=("alpha",))[:2] == (0, "")
    older = git.Series("fetched", window=1, ci_only=False, schema=1, local=True)
    assert older.put(_store(work), {"origin": {"refs": 3}}, message="old") == ""
    with counting_spawns() as spawned, git.fetched_snapshot(_store(work)):
        found = git.read(_store(work), REF)
        assert found.failed
        assert "has not been fetched" in found.reason and "sync`" in found.reason
        assert git.list_refs(_store(work), git.NAMESPACE + "alpha") is None
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0
    # A row of the schema that names no series, or names one that is
    # not a name, reads the same way.
    for series in (None, ["alpha", 7]):
        row: dict[str, Any] = {"schema": git.FETCHED.schema, "refs": 0, "when": "2026"}
        if series is not None:
            row["series"] = series
        bare = json.dumps(row)
        assert (
            git.put(_store(work), git.FETCHED.ref, {"origin": bare}, message="x") == ""
        )
        with git.fetched_snapshot(_store(work)):
            found = git.read(_store(work), REF)
        assert found.failed
        assert "has not been fetched" in found.reason and "sync`" in found.reason
    # The sync's own stamp reads again.
    assert git.fetch_store(_store(work), series=("alpha",))[:2] == (0, "")
    with git.fetched_snapshot(_store(work)):
        assert not git.read(_store(work), git.NAMESPACE + "alpha").failed


def test_fetch_store_takes_out_of_the_mirror_what_a_wider_fetch_left(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    """A whole-namespace mirror holds a family's refs; the next fetch drops them."""
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False)
    assert alpha.put(_store(other), {"r": {"x": "alpha"}}, message="alpha") == ""
    assert family.series("a", "x").put(_store(other), {"r": {}}, message="fam") == ""
    _git(
        work,
        "fetch",
        "--quiet",
        "origin",
        f"+{git.NAMESPACE}*:{git.FETCHED_NAMESPACE}*",
    )
    wider = git.list_refs(_store(work), git.FETCHED_NAMESPACE)
    assert wider is not None and len(wider) == 2
    # The family's ref goes, and counts as a change; alpha stands.
    assert git.fetch_store(_store(work), series=("alpha",)) == git.Fetched(1, "", 1)
    assert git.list_refs(_store(work), git.FETCHED_NAMESPACE) == {
        git.FETCHED_NAMESPACE + "alpha": wider[git.FETCHED_NAMESPACE + "alpha"]
    }
    # Origin keeps the family: the mirror alone was pruned.
    assert family.listed(_store(work)) == [("a", "x")]
    # Named no series and no branches, nothing is fetched and the
    # mirror is emptied; the stamp names no series.
    with counting_spawns() as spawned:
        assert git.fetch_store(_store(work), series=()) == git.Fetched(0, "", 1)
    assert spawned["git fetch"] == 0
    assert git.list_refs(_store(work), git.FETCHED_NAMESPACE) == {}
    stamped, why = git.FETCHED.row(_store(work), "origin")
    assert why == "" and stamped is not None and stamped.data["series"] == []


def test_fetch_store_mirrors_the_named_series_alone_prunes_and_stamps(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    beta = git.Series("beta", window=50, ci_only=False)
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False)
    for item in (alpha, beta, family.series("a", "x")):
        assert item.put(_store(other), {"r": {"x": item.name}}, message=item.name) == ""
    # Two of the three named series exist on origin; the family never
    # comes, and a series origin holds no ref for costs nothing.
    with counting_spawns() as spawned:
        assert git.fetch_store(_store(work), series=("beta", "alpha", "gamma"))[:2] == (
            2,
            "",
        )
    assert spawned["git fetch"] == 1 and spawned["git ls-remote"] == 0
    mirrored = git.list_refs(_store(work), git.FETCHED_NAMESPACE)
    assert mirrored is not None and set(mirrored) == {
        git.FETCHED_NAMESPACE + "alpha",
        git.FETCHED_NAMESPACE + "beta",
    }
    stamped, why = git.FETCHED.row(_store(work), "origin")
    assert why == "" and stamped is not None
    assert stamped.data["refs"] == 2
    assert stamped.data["series"] == ["alpha", "beta", "gamma"]
    # A ref origin dropped goes from the mirror on the next fetch.
    assert git.drop(_store(other), beta.ref) == ""
    assert git.fetch_store(_store(work), series=("alpha", "beta"))[:2] == (1, "")
    assert git.list_refs(_store(work), git.FETCHED_NAMESPACE) == {
        git.FETCHED_NAMESPACE + "alpha": mirrored[git.FETCHED_NAMESPACE + "alpha"]
    }
    # The mirror is never pushed and never written by hand.
    assert "workshop-origin" not in _git(work, "ls-remote", "origin")
    assert "writes only under" in git.put(
        _store(work), git.FETCHED_NAMESPACE + "alpha", {"r": "{}"}, message="no"
    )
    # An origin that cannot answer is the reason, and the last mirror stands.
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    fetched = git.fetch_store(_store(work), series=("alpha",))
    assert fetched.held == 0 and fetched.why and fetched.changed == 0
    assert git.list_refs(_store(work), git.FETCHED_NAMESPACE) is not None


def test_fetch_store_brings_origins_branches_in_the_same_fetch_when_asked(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    assert alpha.put(_store(other), {"r": {"x": "alpha"}}, message="alpha") == ""
    _git(other, "checkout", "-b", "feat/9-theirs")
    (other / "theirs.txt").write_text("t\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: theirs")
    _git(other, "push", "-u", "origin", "feat/9-theirs")
    # The narrow fetch first: the store alone, no branch comes with it.
    assert git.fetch_store(_store(work), series=("alpha",))[:2] == (1, "")
    assert "origin/feat/9-theirs" not in _git(work, "branch", "-r")
    # Asked for the branches, one fetch brings the store and origin's
    # heads, and a branch origin dropped goes from the remote-tracking
    # refs the way a dropped ref goes from the mirror.
    with counting_spawns() as spawned:
        assert git.fetch_store(_store(work), series=("alpha",), branches=True)[:2] == (
            1,
            "",
        )
    assert spawned["git fetch"] == 1
    assert "origin/feat/9-theirs" in _git(work, "branch", "-r")
    assert git.list_refs(_store(work), git.FETCHED_NAMESPACE) is not None
    _git(other, "push", "origin", "--delete", "feat/9-theirs")
    assert git.fetch_store(_store(work), series=("alpha",), branches=True)[:2] == (
        1,
        "",
    )
    assert "origin/feat/9-theirs" not in _git(work, "branch", "-r")


def test_fetch_store_counts_what_moved_and_a_quiet_origin_moves_nothing(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    # A failed fetch first: nothing held, nothing changed, the reason.
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    failed = git.fetch_store(_store(work), series=("alpha",))
    assert failed.held == 0 and failed.changed == 0 and failed.why
    _git(work, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    alpha = git.Series("alpha", window=50, ci_only=False)
    assert alpha.put(_store(other), {"r": {"x": "one"}}, message="one") == ""
    assert git.fetch_store(_store(work), series=("alpha",)) == git.Fetched(1, "", 1)
    # Quiet origin: the round trip is the whole cost and nothing moves.
    assert git.fetch_store(_store(work), series=("alpha",)) == git.Fetched(1, "", 0)
    # A moved ref and a pruned one each count once.
    assert alpha.put(_store(other), {"r": {"x": "two"}}, message="two") == ""
    assert git.fetch_store(_store(work), series=("alpha",)) == git.Fetched(1, "", 1)
    assert git.drop(_store(other), alpha.ref) == ""
    assert git.fetch_store(_store(work), series=("alpha",)) == git.Fetched(0, "", 1)


def test_the_fetched_snapshot_answers_from_the_mirror_and_never_reaches_origin(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    alpha = git.Series("alpha", window=50, ci_only=False)
    assert alpha.put(_store(other), {"r": {"x": "alpha"}}, message="alpha") == ""
    assert git.fetch_store(_store(work), series=("alpha", "beta"))[:2] == (1, "")
    # A ref written after the fetch is not in the mirror: the snapshot
    # is what the last sync saw, by design.
    beta = git.Series("beta", window=50, ci_only=False)
    assert beta.put(_store(other), {"r": {"x": "beta"}}, message="beta") == ""
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    local = git.Series("scratch", window=5, ci_only=False, local=True)
    with counting_spawns() as spawned, git.fetched_snapshot(_store(work)):
        assert [row.data["x"] for row in alpha.rows(_store(work)).rows] == ["alpha"]
        assert git.read(_store(work), beta.ref) == git.Read(None, None, failed=False)
        listed = git.list_refs(_store(work), alpha.ref)
        assert listed is not None and set(listed) == {alpha.ref}
        heads = git.list_refs(_store(work), "refs/heads/")
        assert heads is not None and set(heads) == {"refs/heads/main"}
        # A remote snapshot opened inside shares the mirror.
        with git.remote_snapshot(_store(work), fetch=("alpha", "beta")):
            assert not alpha.rows(_store(work)).failed
        # A remote series is refused; a local one writes as ever.
        why = alpha.put(_store(work), {"r": {"x": "changed"}}, message="no")
        assert "writes nothing to origin" in why
        assert local.put(_store(work), {"r": {"x": "local"}}, message="yes") == ""
        assert [row.data["x"] for row in local.rows(_store(work)).rows] == ["local"]
    assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0
    assert spawned["git push"] == 0


def test_the_mirror_that_cannot_be_listed_and_a_stamp_that_is_not_a_row_are_reasons(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every fallback is a printed reason, never an absence."""
    _, work = repos
    named = ("metrics",)
    assert git.fetch_store(_store(work), series=named)[:2] == (0, "")
    # A stamp that is not a row of the schema: the reason names it.
    assert git.put(_store(work), git.FETCHED.ref, {"origin": "junk"}, message="x") == ""
    with git.fetched_snapshot(_store(work)):
        found = git.read(_store(work), REF)
    assert found.failed and "origin:" in found.reason
    # The stamp is a row again.
    assert git.fetch_store(_store(work), series=named)[:2] == (0, "")
    # git refusing the listing, on the fetch and under the snapshot.
    monkeypatch.setattr(store_module, "_local_refs", lambda root, prefix: None)
    assert git.fetch_store(_store(work), series=named)[:2] == (
        0,
        "the mirror could not be listed",
    )
    with git.fetched_snapshot(_store(work)):
        found = git.read(_store(work), REF)
    assert found.failed and found.reason.endswith("the mirror could not be listed")


# --- the batch is the only way a read reaches origin --------------------------


def test_a_read_inside_a_snapshot_brings_the_refs_key_group_in_one_fetch(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    family = git.Keyed("fam", ("leg", "unit"), ci_only=False)
    keys = [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")]
    for key in keys:
        assert (
            family.series(*key).put(_store(other), {"r": {"k": key}}, message="m") == ""
        )
    # A single series beside the keys, one level up: its own group.
    marks = git.Series("fam/marks", ci_only=False)
    assert marks.put(_store(other), {"r": {"m": 1}}, message="marks") == ""
    with git.remote_snapshot(_store(work)), counting_spawns() as spawned:
        assert [row.name for row in marks.rows(_store(work)).rows] == ["r"]
        assert spawned["git fetch"] == 1
        # A key's read brings its siblings under the same parent; the
        # sibling's read then answers from the object store.
        expected = {("a", "x"): 2, ("a", "y"): 2, ("b", "x"): 3, ("b", "y"): 3}
        for key in keys:
            found = family.series(*key).rows(_store(work))
            assert [row.data["k"] for row in found.rows] == [list(key)], key
            assert spawned["git fetch"] == expected[key], (key, spawned)


def test_a_snapshot_names_a_prefix_of_another_namespace_in_full(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    traces = git.Keyed(
        "tr", ("run", "leg"), ci_only=False, namespace=git.TRACE_NAMESPACE
    )
    for key in (("1", "a"), ("1", "b"), ("2", "a")):
        assert traces.series(*key).put(_store(other), {"r": {}}, message="m") == ""
    with (
        counting_spawns() as spawned,
        git.remote_snapshot(_store(work), fetch=(traces.prefix,)),
    ):
        assert spawned["git fetch"] == 1
        for key in (("1", "a"), ("1", "b"), ("2", "a")):
            assert not traces.series(*key).rows(_store(work)).failed
        assert spawned["git fetch"] == 1, spawned


def test_a_block_inside_another_brings_what_it_names(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    series = [git.Series(name, ci_only=False) for name in ("alpha", "beta", "gamma")]
    for item in series:
        assert item.put(_store(other), {"r": {"x": item.name}}, message=item.name) == ""
    with (
        git.remote_snapshot(_store(work), fetch=("alpha",)),
        counting_spawns() as inner,
    ):
        with git.remote_snapshot(_store(work), fetch=("beta", "gamma")):
            assert inner["git fetch"] == 1
            for item in series[1:]:
                assert [row.data["x"] for row in item.rows(_store(work)).rows] == [
                    item.name
                ]
        assert inner["git fetch"] == 1 and inner["git ls-remote"] == 0, inner


def test_the_janitor_reads_a_familys_commit_times_in_one_fetch_and_one_log(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    family = git.Keyed(
        "halves", ("run", "leg"), ci_only=False, stale_after=timedelta(hours=6)
    )
    keys = [(str(run), leg) for run in (1, 2, 3) for leg in ("a", "b")]
    for key in keys:
        assert family.series(*key).put(_store(other), {"r": {}}, message="m") == ""
    # The fallback first: no block open, the sweep opens its own, so
    # the keys are listed once, their commits brought in one fetch,
    # and their times and rows read from the object store.
    with counting_spawns() as bare:
        lines = git.sweep(_store(work), (family,), remote=True, dry_run=True)
    assert len(lines) == len(keys) and all("within its bounds" in x for x in lines)
    assert bare["git fetch"] == 1 and bare["git ls-remote"] == 1, bare
    assert bare["git"] == 2, bare
    # Inside a caller's block, from a checkout holding none of the
    # commits (a clone over the wire brings the branches alone): one
    # fetch, and no listing beyond the block's own.
    cold = tmp_path / "cold"
    _git(tmp_path, "clone", "-q", "--no-local", str(tmp_path / "origin.git"), str(cold))
    with git.remote_snapshot(_store(cold)), counting_spawns() as snap:
        lines = git.sweep(_store(cold), (family,), remote=True, dry_run=True)
    assert len(lines) == len(keys) and all("within its bounds" in x for x in lines)
    assert snap["git fetch"] == 1 and snap["git"] == 1, snap
    assert snap["git ls-remote"] == 0, snap


def test_a_local_familys_commit_times_are_read_in_one_call_and_no_fetch(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    family = git.Keyed(
        "aged",
        ("run", "leg"),
        ci_only=False,
        local=True,
        stale_after=timedelta(hours=6),
    )
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert family.series("1", "a").put(_store(work), {"r": {}}, message="old") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert family.series("2", "a").put(_store(work), {"r": {}}, message="young") == ""
    with counting_spawns() as spawned:
        lines = git.sweep(_store(work), (family,), remote=False)
    # The keys are listed and every key's commit time read in-process.
    assert spawned["git"] == 0, spawned
    old, young = family.series("1", "a").ref, family.series("2", "a").ref
    assert any(line.startswith(f"  {old}: ") and "dropped" in line for line in lines)
    assert f"  {young}: 1 row(s), within its bounds" in lines
