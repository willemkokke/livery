"""The CI state store: refusals first, then the compare-and-swap path.

Every test drives real git against a bare repository standing in for
origin, so the refusals are the transport's own words, not a fake's.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from livery.workshop import _state
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401
from workshop_spawns import counting_spawns

REF = _state.NAMESPACE + "metrics"


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )
    return done.stdout


def _clone(tmp_path: Path, name: str, origin: Path) -> Path:
    work = tmp_path / name
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    return work


def _seed(base: Path) -> None:
    """A bare origin with one commit on main and a clone, built once per session."""
    origin = base / "origin.git"
    _git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = _clone(base, "work", origin)
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")


@pytest.fixture
def repos(seeds: Seeds, tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on main, and a working clone of it."""
    seeds("state", _seed)
    return tmp_path / "origin.git", tmp_path / "work"


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_REF",
        "GITHUB_JOB",
    ):
        monkeypatch.delenv(name, raising=False)


# --- refusals ----------------------------------------------------------------


def test_put_refuses_a_ref_outside_the_namespace(repos: tuple[Path, Path]) -> None:
    _, work = repos
    why = _state.put(work, "refs/heads/main", {"a.json": "{}"}, message="m")
    assert why.startswith("refusing refs/heads/main")
    assert _state.NAMESPACE in why
    assert (
        _git(work, "ls-remote", "origin", "refs/heads/main").split()[0]
        == _git(work, "rev-parse", "HEAD").strip()
    )


def test_put_refuses_a_ci_only_write_from_a_local_run(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    why = _state.put(work, REF, {"a.json": "{}"}, message="m", ci_only=True)
    assert "only a CI run writes this series" in why
    assert _git(work, "ls-remote", "origin", REF) == ""


def test_put_refuses_without_an_origin(tmp_path: Path) -> None:
    lonely = tmp_path / "lonely"
    _git(tmp_path, "init", "-q", str(lonely))
    why = _state.put(lonely, REF, {"a.json": "{}"}, message="m")
    assert "could not be read" in why
    assert "origin" in why


def test_read_calls_a_missing_ref_absent(repos: tuple[Path, Path]) -> None:
    _, work = repos
    found = _state.read(work, REF)
    assert found == _state.Read(None, None, failed=False)


def test_read_calls_an_unreachable_remote_a_failure(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found = _state.read(work, REF)
    assert found.files is None and found.failed
    assert found.reason


def test_put_refuses_to_rewrite_from_an_unread_state(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    # The rule that protects every file the writer did not know
    # about: an unreachable ref blocks the whole-tree rewrite.
    origin, work = repos
    assert _state.put(work, REF, {"a.json": "1"}, message="first") == ""
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    why = _state.put(work, REF, {"b.json": "2"}, message="second")
    assert "could not be read" in why and "would erase" in why
    survivor = _clone(tmp_path, "survivor", origin)
    assert _state.read(survivor, REF).files == {"a.json": "1"}


def test_a_stale_lease_re_reads_merges_and_wins(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two writers: the second reads, the first lands in between, and
    # the second's lease is stale. The server refuses atomically; the
    # retry re-reads and merges, so both files survive.
    origin, work = repos
    other = _clone(tmp_path, "other", origin)
    assert _state.put(work, REF, {"a.json": "1"}, message="a") == ""
    real_read = _state.read
    raced = {"done": False}

    def read_then_race(root: Path, ref: str) -> _state.Read:
        found = real_read(root, ref)
        if root == other and not raced["done"]:
            raced["done"] = True
            assert _state.put(work, REF, {"b.json": "2"}, message="b") == ""
        return found

    monkeypatch.setattr(_state, "read", read_then_race)
    assert _state.put(other, REF, {"c.json": "3"}, message="c") == ""
    assert real_read(work, REF).files == {"a.json": "1", "b.json": "2", "c.json": "3"}


def test_an_exhausted_lease_names_the_other_writer(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, work = repos
    other = _clone(tmp_path, "other", origin)
    assert _state.put(work, REF, {"a.json": "1"}, message="a") == ""
    real_read = _state.read
    counter = {"n": 0}

    def always_raced(root: Path, ref: str) -> _state.Read:
        found = real_read(root, ref)
        if root == other:
            counter["n"] += 1
            assert (
                _state.put(work, REF, {f"x{counter['n']}.json": "x"}, message="x") == ""
            )
        return found

    monkeypatch.setattr(_state, "read", always_raced)
    why = _state.put(other, REF, {"c.json": "3"}, message="c", attempts=2)
    assert "gave up" in why and "2 attempts" in why
    assert "c.json" not in (real_read(work, REF).files or {})


def test_drop_of_a_missing_ref_is_already_done(repos: tuple[Path, Path]) -> None:
    _, work = repos
    assert _state.drop(work, REF) == ""
    assert _state.drop(work, "refs/heads/main").startswith("refusing")


# --- the happy path, and the properties every write keeps --------------------


def test_put_lays_files_over_the_kept_tree(repos: tuple[Path, Path]) -> None:
    _, work = repos
    assert _state.put(work, REF, {"a.json": "1", "b.json": "2"}, message="one") == ""
    assert _state.put(work, REF, {"b.json": "22", "c.json": "3"}, message="two") == ""
    found = _state.read(work, REF)
    assert found.files == {"a.json": "1", "b.json": "22", "c.json": "3"}
    assert found.sha == _git(work, "ls-remote", "origin", REF).split()[0]
    assert not found.failed


def test_a_window_keeps_the_newest_names(repos: tuple[Path, Path]) -> None:
    _, work = repos
    rows = {f"run-{n:04d}.json": str(n) for n in range(5)}
    assert _state.put(work, REF, rows, message="five", window=3) == ""
    assert _state.read(work, REF).files == {
        "run-0002.json": "2",
        "run-0003.json": "3",
        "run-0004.json": "4",
    }


def test_the_commit_is_a_root_with_identity_and_skip_ci(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    _, work = repos
    assert _state.put(work, REF, {"a.json": "1"}, message="one") == ""
    assert _state.put(work, REF, {"b.json": "2"}, message="two") == ""
    _git(work, "fetch", "-q", "origin", REF)
    parents, author, email, subject = _git(
        work, "log", "-1", "--format=%P%n%an%n%ae%n%s", "FETCH_HEAD"
    ).splitlines()
    assert parents == ""  # replace-only: depth one, no history
    assert (author, email) == ("hse ci state", "ci-state@hse.invalid")
    assert subject == "two [skip ci]"


def test_a_read_creates_no_local_ref_and_a_default_fetch_never_sees_the_namespace(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    origin, work = repos
    assert _state.put(work, REF, {"a.json": "1"}, message="one") == ""
    assert _state.read(work, REF).files == {"a.json": "1"}
    assert _git(work, "for-each-ref", "refs/workshop") == ""
    fresh = _clone(tmp_path, "fresh", origin)
    _git(fresh, "fetch", "-q", "origin")
    listed = _git(fresh, "for-each-ref", "--format=%(refname)")
    assert "refs/workshop" not in listed
    assert "refs/remotes/origin/main" in listed


def test_run_context_reads_each_forge_and_is_none_locally() -> None:
    assert _state.run_context({}) is None
    github = _state.run_context(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "7",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
        }
    )
    assert github is not None
    assert github == _state.RunContext("github", "7", "push", "refs/heads/main")
    gitea = _state.run_context(
        {"GITHUB_ACTIONS": "true", "GITEA_ACTIONS": "true", "GITHUB_RUN_ID": "9"}
    )
    assert gitea is not None and gitea.forge == "gitea" and gitea.run_id == "9"
    gitlab = _state.run_context(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "42",
            "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_REF_NAME": "main",
        }
    )
    assert gitlab == _state.RunContext("gitlab", "42", "push", "main")


def test_the_run_context_head_falls_back_when_the_payload_is_missing_or_junk(
    tmp_path: Path,
) -> None:
    base = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7", "GITHUB_SHA": "a" * 40}
    missing = _state.run_context({**base, "GITHUB_EVENT_PATH": str(tmp_path / "none")})
    assert missing is not None and missing.head_sha == "a" * 40
    junk = tmp_path / "event.json"
    junk.write_text("{not json")
    broken = _state.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert broken is not None and broken.head_sha == "a" * 40
    junk.write_text(json.dumps({"pull_request": {"head": {}}}))
    headless = _state.run_context({**base, "GITHUB_EVENT_PATH": str(junk)})
    assert headless is not None and headless.head_sha == "a" * 40


def test_the_run_context_head_is_the_pull_requests_on_a_pull_request(
    tmp_path: Path,
) -> None:
    # The checkout is the merge commit; the run is filed under the
    # pull request's head, which only the payload names.
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_PATH": str(event),
    }
    run = _state.run_context(env)
    assert run is not None and run.head_sha == "b" * 40
    event.write_text(json.dumps({"after": "a" * 40}))
    push = _state.run_context({**env, "GITHUB_EVENT_NAME": "push"})
    assert push is not None and push.head_sha == "a" * 40
    gitlab = _state.run_context({"GITLAB_CI": "true", "CI_COMMIT_SHA": "c" * 40})
    assert gitlab is not None and gitlab.head_sha == "c" * 40


def test_the_run_context_head_branch_is_the_pull_requests_and_empty_otherwise(
    tmp_path: Path,
) -> None:
    event = tmp_path / "event.json"
    base = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
    }
    # Fallbacks first: a payload without the head names no branch, the
    # runner's own variable stands in, and a push names none.
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    bare = _state.run_context(base)
    assert bare is not None and bare.head_ref == ""
    named = _state.run_context({**base, "GITHUB_HEAD_REF": "feat/x"})
    assert named is not None and named.head_ref == "feat/x"
    event.write_text(json.dumps({"after": "a" * 40}))
    push = _state.run_context({**base, "GITHUB_EVENT_NAME": "push"})
    assert push is not None and push.head_ref == ""
    # The payload names the branch, and wins over the variable.
    event.write_text(
        json.dumps({"pull_request": {"head": {"sha": "b" * 40, "ref": "feat/y"}}})
    )
    run = _state.run_context({**base, "GITHUB_HEAD_REF": "feat/x"})
    assert run is not None and run.head_ref == "feat/y"
    gitlab = _state.run_context(
        {"GITLAB_CI": "true", "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feat/z"}
    )
    assert gitlab is not None and gitlab.head_ref == "feat/z"


def test_the_event_payload_is_none_when_missing_junk_or_not_an_object(
    tmp_path: Path,
) -> None:
    assert _state.event_payload({}) is None
    assert _state.event_payload({"GITHUB_EVENT_PATH": str(tmp_path / "none")}) is None
    junk = tmp_path / "event.json"
    junk.write_text("{not json")
    assert _state.event_payload({"GITHUB_EVENT_PATH": str(junk)}) is None
    junk.write_text("[1, 2]")
    assert _state.event_payload({"GITHUB_EVENT_PATH": str(junk)}) is None
    junk.write_text(json.dumps({"action": "opened"}))
    assert _state.event_payload({"GITHUB_EVENT_PATH": str(junk)}) == {
        "action": "opened"
    }


def test_a_ci_run_may_write_a_ci_only_series(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    assert _state.put(work, REF, {"a.json": "1"}, message="one", ci_only=True) == ""


# --- the row layer: refusals first --------------------------------------------

ROWS = _state.Series("rows", window=3, ci_only=False, schema=2)


def test_an_absent_series_reads_as_no_rows_and_no_failure(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.rows(work) == _state.Rows(())
    assert ROWS.row(work, "any") == (None, "")


def test_an_unreachable_series_names_itself_and_the_reason(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found = ROWS.rows(work)
    assert found.failed and found.rows == () and found.skipped == ()
    assert found.reason.startswith("the rows series could not be read: ")
    assert ROWS.row(work, "any") == (None, found.reason)


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
    assert _state.put(work, ROWS.ref, files, message="junk") == ""
    found = ROWS.rows(work)
    assert found.skipped[0] == _state.Skipped("a", "does not parse")
    assert tuple(str(item) for item in found.skipped) == (
        "a: does not parse; skipped",
        "b: schema 1, this reader speaks 2; skipped",
        "c: schema none, this reader speaks 2; skipped",
    )
    assert found.rows == (_state.Row("d", {"schema": 2, "x": 1}),)
    assert not found.failed and found.reason == ""
    assert ROWS.row(work, "a") == (None, "a: does not parse")
    assert ROWS.row(work, "b") == (None, "b: schema 1, this reader speaks 2")
    assert ROWS.row(work, "d") == (_state.Row("d", {"schema": 2, "x": 1}), "")


def test_a_ci_only_series_refuses_a_local_put_by_its_rule(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    guarded = _state.Series("guarded")
    why = guarded.put(work, {"a": {}}, message="a")
    assert "only a CI run writes this series; local runs read" in why
    assert guarded.rows(work) == _state.Rows(())


def _batch_faults(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    """Make the next ``cat-file --batch`` answer *output* and exit 0."""
    real = _state._git

    def faulty(root: Path, *args: str, stdin: str | None = None) -> Any:
        if args[:2] == ("cat-file", "--batch"):
            return SimpleNamespace(code=0, stdout=output, stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(_state, "_git", faulty)


def test_a_batch_read_that_is_not_whole_fails_naming_the_row(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    assert ROWS.put(work, {"a": {"x": 1}, "b": {"x": 2}}, message="two") == ""
    # An object git does not have: the stream says so, the read fails.
    _batch_faults(monkeypatch, '0123 blob 8\n{"x": 1}\n0456 missing\n')
    found = _state.read(work, ROWS.ref)
    assert found.failed and found.reason == "b: git said missing"
    # A stream that ends before a header is no answer either.
    _batch_faults(monkeypatch, '0123 blob 8\n{"x": 1}\n')
    found = _state.read(work, ROWS.ref)
    assert (
        found.failed and found.reason == "b: the batch stream ended before its header"
    )
    # A header that is not a blob's is refused with git's words.
    _batch_faults(monkeypatch, '0123 tree 8\n{"x": 1}\n0456 blob 8\n{"x": 2}\n')
    found = _state.read(work, ROWS.ref)
    assert found.failed and found.reason == "a: git said tree 8"


def test_a_blob_whose_bytes_disagree_with_its_size_is_read_on_its_own(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A row written with carriage returns, as a Windows checkout did
    # before blobs were written without newline translation: the pipe
    # translates them away, the sizes no longer index the stream, and
    # the read falls back to one show per remaining file.
    _, work = repos
    ref = _state.LOCAL_NAMESPACE + "legacy"
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
        found = _state.read(work, ref)
    assert not found.failed and found.files is not None
    assert found.files["a"].rstrip("\r\n") == crlf.read_bytes().decode().rstrip("\r\n")
    assert found.files["b"] == plain.read_bytes().decode()
    # ls-tree, the batch, then one show per file after the mismatch.
    assert spawned["git"] == 5


def test_a_write_whose_hashes_do_not_match_its_files_is_refused(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    real = _state._git

    def short(root: Path, *args: str, stdin: str | None = None) -> Any:
        if args[:2] == ("hash-object", "-w"):
            return SimpleNamespace(code=0, stdout="0123\n", stderr="")
        return real(root, *args, stdin=stdin)

    monkeypatch.setattr(_state, "_git", short)
    why = ROWS.put(work, {"a": {"x": 1}, "b": {"x": 2}}, message="two")
    assert why == "hash-object returned 1 hash(es) for 2 file(s)"


def test_a_read_and_a_write_cost_a_fixed_handful_of_processes(
    repos: tuple[Path, Path],
) -> None:
    # The pin: a series of sixty rows costs the same processes as one
    # of six. A write is a read, one hash-object for every blob, the
    # tree, the commit, the push, and the readback.
    _, work = repos
    wide = _state.Series("wide", window=400, ci_only=False, schema=2)
    assert wide.put(work, {"r000": {"x": 0}}, message="one") == ""
    with counting_spawns() as six:
        assert (
            wide.put(work, {f"r{n:03d}": {"x": n} for n in range(1, 7)}, message="six")
            == ""
        )
    with counting_spawns() as sixty:
        assert (
            wide.put(
                work, {f"r{n:03d}": {"x": n} for n in range(7, 67)}, message="sixty"
            )
            == ""
        )
    # fetch, rev-parse, ls-tree, cat-file; hash-object, mktree,
    # commit-tree; push: eight, whatever the size, and no readback.
    assert six["git"] == sixty["git"] == 8, (six, sixty)
    assert sixty["git ls-remote"] == 0
    with counting_spawns() as reading:
        found = wide.rows(work)
    assert len(found.rows) == 67 and not found.failed
    assert reading["git"] == 4, reading


# --- the snapshot: refusals first, then one listing for a verb ----------------


def test_a_snapshot_whose_listing_failed_fails_every_read_and_listing(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with _state.remote_snapshot(work, fetch=("rows",)):
        found = _state.read(work, ROWS.ref)
        assert found.failed and found.reason.startswith(
            "the remote could not be listed"
        )
        assert _state.list_refs(work, _state.NAMESPACE) is None
        why = ROWS.put(work, {"b": {"x": 2}}, message="two")
        assert "could not be read" in why and "the remote could not be listed" in why
        assert _state.drop(work, ROWS.ref).startswith("delete refused")


def test_a_snapshot_reads_an_absent_ref_as_absence_without_a_fetch(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    with _state.remote_snapshot(work):
        with counting_spawns() as spawned:
            found = _state.read(work, ROWS.ref)
        assert found == _state.Read(None, None, failed=False)
        assert spawned["git fetch"] == 0 and spawned["git ls-remote"] == 0
        assert _state.list_refs(work, _state.NAMESPACE) == {}


def test_a_snapshot_lists_once_and_fetches_the_named_series_together(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    series = [
        _state.Series(name, window=50, ci_only=False, schema=2)
        for name in ("alpha", "beta", "gamma")
    ]
    for item in series:
        assert item.put(other, {"r": {"x": item.name}}, message=item.name) == ""
    # A checkout that holds none of the commits: one listing, one fetch.
    with (
        counting_spawns() as cold,
        _state.remote_snapshot(work, fetch=("alpha", "beta", "gamma")),
    ):
        for item in series:
            found = item.rows(work)
            assert [row.data["x"] for row in found.rows] == [item.name]
        listed = _state.list_refs(work, _state.NAMESPACE)
        assert listed is not None and set(listed) >= {item.ref for item in series}
    assert cold["git ls-remote"] == 1 and cold["git fetch"] == 1, cold
    # The commits are local now: one listing and no fetch at all.
    with counting_spawns() as warm, _state.remote_snapshot(work, fetch=("alpha",)):
        for item in series:
            assert not item.rows(work).failed
    assert warm["git ls-remote"] == 1 and warm["git fetch"] == 0, warm
    # Outside a snapshot every read fetches on its own, as before.
    with counting_spawns() as bare:
        for item in series:
            assert not item.rows(work).failed
    assert bare["git fetch"] == 3


def test_a_write_inside_a_snapshot_re_lists_on_a_stale_lease_and_records_its_sha(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    with _state.remote_snapshot(work, fetch=("rows",)):
        assert [row.name for row in ROWS.rows(work).rows] == ["a"]
        # Another writer moves the ref after the listing.
        assert ROWS.put(other, {"b": {"x": 2}}, message="two") == ""
        with counting_spawns() as spawned:
            assert ROWS.put(work, {"c": {"x": 3}}, message="three") == ""
        assert spawned["git ls-remote"] == 1  # the re-listing alone, no readback
        # The write merged the other writer's row and recorded its own sha.
        found = ROWS.rows(work)
        assert sorted(row.name for row in found.rows) == ["a", "b", "c"]
        assert found.rows[0].name == "c"
        listed = _state.list_refs(work, ROWS.ref)
        assert (
            listed is not None and listed[ROWS.ref] == _state.read(work, ROWS.ref).sha
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
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    with _state.remote_snapshot(fresh):
        # Another writer moves the ref between the listing and the read,
        # so a fetch by the ref's name brings the new commit and not
        # the listed one; a read of the listed sha would fail with
        # git's "not a tree object".
        assert ROWS.put(other, {"b": {"x": 2}}, message="two") == ""
        with counting_spawns() as spawned:
            found = ROWS.rows(fresh)
        assert not found.failed, found.reason
        assert sorted(row.name for row in found.rows) == ["a", "b"]
        assert found.rows[0].name == "b"
        # One fetch, and one listing again for the moved ref.
        assert spawned["git fetch"] == 1 and spawned["git ls-remote"] == 1, spawned
        # A write inside leases on the current commit and lands.
        assert ROWS.put(fresh, {"c": {"x": 3}}, message="three") == ""
    assert sorted(row.name for row in ROWS.rows(fresh).rows) == ["a", "b", "c"]


def test_a_published_listing_opened_after_a_move_reads_the_current_rows(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CI shape: the job runner lists once, an earlier run's gate
    # job moves a ref a second later, and an entry opens the published
    # listing naming the family it prefetches.
    _, work = repos
    other = _clone(tmp_path, "other", tmp_path / "origin.git")
    fresh = _clone(tmp_path, "fresh", tmp_path / "origin.git")
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    monkeypatch.delenv(_state.SNAPSHOT_VARIABLE, raising=False)
    with _state.remote_snapshot(fresh, publish=True) as named:
        assert named is not None
        monkeypatch.setenv(_state.SNAPSHOT_VARIABLE, named)
        assert ROWS.put(other, {"b": {"x": 2}}, message="two") == ""
        _state._SNAPSHOTS.clear()
        with _state.remote_snapshot(fresh, fetch=("rows",)):
            found = ROWS.rows(fresh)
            assert not found.failed, found.reason
            assert found.rows[0].name == "b"
        monkeypatch.delenv(_state.SNAPSHOT_VARIABLE)


def test_the_listing_carries_the_branches_and_only_them_beside_the_store(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    with counting_spawns() as spawned, _state.remote_snapshot(work):
        heads = _state.list_refs(work, "refs/heads/")
        assert heads is not None and "refs/heads/main" in heads
        assert _state.list_refs(work, "refs/heads/nowhere") == {}
        # A namespace the listing did not take is asked for on its own.
        assert _state.list_refs(work, "refs/tags/") == {}
    assert spawned["git ls-remote"] == 2  # the snapshot's, and the tags'


def test_a_published_snapshot_serves_the_children_and_carries_their_writes(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    _, work = repos
    monkeypatch.delenv(_state.SNAPSHOT_VARIABLE, raising=False)
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    with _state.remote_snapshot(work, publish=True) as named:
        assert named is not None
        # The parent hands the path to its children; a child is
        # its own process in truth, so here the variable is set by
        # hand for the blocks below.
        monkeypatch.setenv(_state.SNAPSHOT_VARIABLE, named)
        published = _json.loads(Path(named).read_text("utf-8"))
        assert (
            published["root"] == str(work.resolve()) and ROWS.ref in published["refs"]
        )
        # A child: the same checkout, its own process in truth, its own
        # block here; it lists nothing and reads what the file says.
        _state._SNAPSHOTS.clear()
        with counting_spawns() as child, _state.remote_snapshot(work, fetch=("rows",)):
            assert [row.name for row in ROWS.rows(work).rows] == ["a"]
            assert ROWS.put(work, {"b": {"x": 2}}, message="two") == ""
        assert child["git ls-remote"] == 0, child
        # The child's write reached the file for the entries after it.
        after = _json.loads(Path(named).read_text("utf-8"))
        assert after["refs"][ROWS.ref] != published["refs"][ROWS.ref]
        _state._SNAPSHOTS.clear()
        with counting_spawns() as next_child, _state.remote_snapshot(work):
            assert [row.name for row in ROWS.rows(work).rows] == ["b", "a"]
        assert next_child["git ls-remote"] == 0 and next_child["git fetch"] == 0
        monkeypatch.delenv(_state.SNAPSHOT_VARIABLE)
    assert not Path(named).exists()


def test_a_published_snapshot_that_cannot_be_read_or_is_anothers_counts_as_none(
    repos: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    corrupt = tmp_path / "snapshot.json"
    corrupt.write_text("{not json")
    monkeypatch.setenv(_state.SNAPSHOT_VARIABLE, str(corrupt))
    with counting_spawns() as spawned, _state.remote_snapshot(work):
        assert [row.name for row in ROWS.rows(work).rows] == ["a"]
    assert spawned["git ls-remote"] == 1
    other = tmp_path / "other.json"
    other.write_text('{"root": "/elsewhere", "refs": {}, "local": []}')
    monkeypatch.setenv(_state.SNAPSHOT_VARIABLE, str(other))
    with counting_spawns() as spawned, _state.remote_snapshot(work):
        assert [row.name for row in ROWS.rows(work).rows] == ["a"]
    assert spawned["git ls-remote"] == 1
    # A parent whose listing failed publishes nothing.
    monkeypatch.delenv(_state.SNAPSHOT_VARIABLE)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with _state.remote_snapshot(work, publish=True) as named:
        assert named is None


def test_a_drop_inside_a_snapshot_forgets_the_ref_and_nesting_shares_the_listing(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.put(work, {"a": {"x": 1}}, message="one") == ""
    with counting_spawns() as spawned, _state.remote_snapshot(work):
        with _state.remote_snapshot(work):  # nested: the outer listing serves
            assert _state.list_refs(work, ROWS.ref) is not None
        assert _state.drop(work, ROWS.ref) == ""
        assert _state.list_refs(work, ROWS.ref) == {}
        assert _state.read(work, ROWS.ref) == _state.Read(None, None, failed=False)
    assert spawned["git ls-remote"] == 1
    # The block is closed: the direct path again, and the ref is gone.
    assert _state.read(work, ROWS.ref) == _state.Read(None, None, failed=False)


def test_put_stamps_the_schema_and_the_time_over_what_a_row_carries(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert ROWS.put(work, {"first": {"x": 1}}, message="one") == ""
    late = {"second": {"schema": 99, "when": "never"}}
    assert ROWS.put(work, late, message="two") == ""
    found = ROWS.rows(work)
    assert [row.name for row in found.rows] == ["second", "first"]
    assert all(row.data["schema"] == 2 for row in found.rows)
    assert found.rows[0].when > found.rows[1].when > "2026"


def test_put_removes_the_named_rows_and_keeps_the_rest(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert (
        ROWS.put(work, {"a": {"x": 1}, "b": {"x": 2}, "c": {"x": 3}}, message="three")
        == ""
    )
    # A record replaced in place: one row fresh, one removed, one
    # standing with the stamp it had.
    before = {row.name: row.when for row in ROWS.rows(work).rows}
    assert ROWS.put(work, {"a": {"x": 11}}, message="again", remove=["b", "gone"]) == ""
    found = ROWS.rows(work)
    assert {row.name: row.data["x"] for row in found.rows} == {"a": 11, "c": 3}
    stamps = {row.name: row.when for row in found.rows}
    assert stamps["c"] == before["c"] and stamps["a"] > before["a"]


def test_the_window_keeps_the_newest_rows_by_their_stamps_not_their_names(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    # The newest row has the smallest name: a name order would evict it.
    for name in ("zz", "mm", "aa", "00"):
        assert ROWS.put(work, {name: {}}, message=name) == ""
    assert [row.name for row in ROWS.rows(work).rows] == ["00", "aa", "mm"]


# --- keyed families: refusals first -------------------------------------------

FAMILY = _state.Keyed("family", ("leg", "package"), window=2, ci_only=False)


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
    assert FAMILY.listed(work) is None


def test_a_family_lists_its_keys_and_leaves_other_refs_out(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    assert FAMILY.listed(work) == []
    for leg, package in (("b", "y"), ("a", "x"), ("a", "y")):
        assert FAMILY.series(leg, package).put(work, {"r": {}}, message="m") == ""
    # A ref of another arity under the prefix (the marks beside the
    # coverage families) is not the family's.
    assert _state.put(work, FAMILY.prefix + "marks", {"m": "1"}, message="s") == ""
    assert FAMILY.listed(work) == [("a", "x"), ("a", "y"), ("b", "y")]
    assert FAMILY.listed(work, "a") == [("a", "x"), ("a", "y")]


# --- the local scope: refusals first ------------------------------------------

LOCAL = _state.Series("local-rows", window=2, ci_only=False, local=True)


def test_a_local_series_declared_ci_only_is_refused() -> None:
    with pytest.raises(ValueError, match="written by local runs"):
        _state.Series("x", local=True)
    with pytest.raises(ValueError, match="written by local runs"):
        _state.Keyed("x", ("k",), local=True)


def test_a_local_read_outside_a_repository_is_a_failure_with_git_words(
    tmp_path: Path,
) -> None:
    found = LOCAL.rows(tmp_path)
    assert found.failed and "not a git repository" in found.reason
    assert "could not be read" in LOCAL.put(tmp_path, {"r": {}}, message="m")


def test_a_local_put_never_reaches_the_remote_and_a_fresh_clone_starts_empty(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    origin, work = repos
    assert LOCAL.ref == "refs/workshop-local/local-rows"
    assert LOCAL.put(work, {"r": {"x": 1}}, message="local") == ""
    _git(work, "push", "-q", "origin", "main")
    assert "workshop-local" not in _git(work, "ls-remote", "origin")
    assert LOCAL.rows(_clone(tmp_path, "fresh", origin)) == _state.Rows(())
    assert [row.name for row in LOCAL.rows(work).rows] == ["r"]


def test_worktrees_share_the_local_series_and_the_window_holds(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    tree = tmp_path / "tree"
    _git(work, "worktree", "add", "-q", "-b", "side", str(tree))
    assert LOCAL.put(tree, {"from-tree": {}}, message="t") == ""
    assert [row.name for row in LOCAL.rows(work).rows] == ["from-tree"]
    assert LOCAL.put(work, {"from-work": {}}, message="w") == ""
    assert LOCAL.put(work, {"newest": {}}, message="n") == ""
    assert [row.name for row in LOCAL.rows(tree).rows] == ["newest", "from-work"]


def test_a_local_write_that_lost_the_race_re_reads_and_wins(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    ref = _state.LOCAL_NAMESPACE + "race"
    assert _state.put(work, ref, {"a": "{}"}, message="a") == ""
    real_read = _state.read
    raced = {"done": False}

    def read_then_race(root: Path, name: str) -> _state.Read:
        found = real_read(root, name)
        if not raced["done"]:
            raced["done"] = True
            assert _state.put(work, ref, {"b": "{}"}, message="b") == ""
        return found

    monkeypatch.setattr(_state, "read", read_then_race)
    assert _state.put(work, ref, {"c": "{}"}, message="c") == ""
    assert set(real_read(work, ref).files or {}) == {"a", "b", "c"}


def test_a_local_drop_is_idempotent_and_a_local_family_lists_its_keys(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = _state.Keyed("local-family", ("a", "b"), ci_only=False, local=True)
    assert family.listed(work) == []
    for key in (("x", "y"), ("x", "z")):
        assert family.series(*key).put(work, {"r": {}}, message="m") == ""
    assert family.listed(work) == [("x", "y"), ("x", "z")]
    assert family.listed(work, "x") == [("x", "y"), ("x", "z")]
    ref = family.series("x", "y").ref
    assert ref == "refs/workshop-local/local-family/x/y"
    assert _state.drop(work, ref) == ""
    assert _state.drop(work, ref) == ""
    assert family.listed(work) == [("x", "z")]


# --- the janitor: refusals first ------------------------------------------------

HALVES = _state.Keyed("run", ("run", "leg"), stale_after=timedelta(hours=6))
BOUNDED = _state.Series(
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
    remote = _state.Series("remote-rows", window=1, ci_only=False)
    files = {f"r{n}": _stamped(datetime(2026, 1, n, tzinfo=UTC)) for n in range(1, 4)}
    assert _state.put(work, remote.ref, files, message="three") == ""
    lines = _state.sweep(work, (remote, HALVES), remote=False)
    assert lines == ["  remote series: swept inside CI, never from a machine"]
    assert len(_state.read(work, remote.ref).files or {}) == 3


def test_a_family_that_cannot_be_listed_drops_nothing_and_says_so(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = _state.sweep(work, (HALVES,), remote=True)
    assert lines == [f"  {_state.RUN_PREFIX}*: could not be listed; nothing swept"]


def test_a_family_whose_current_keys_cannot_be_told_drops_no_orphan(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = _state.Keyed("fam", ("leg", "unit"), ci_only=False, current=_unknown)
    assert family.series("a", "x").put(work, {"r": {}}, message="m") == ""
    lines = _state.sweep(work, (family,), remote=True)
    assert lines[0] == (
        f"  {family.prefix}*: the current keys could not be told; no orphan dropped"
    )
    assert family.listed(work) == [("a", "x")]


def test_the_janitor_drops_stale_halves_and_keeps_young_ones(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    old, young = HALVES.series("11", "check-a"), HALVES.series("12", "check-a")
    half = json.dumps({"schema": 1, "job": "check"})
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert _state.put(work, old.ref, {"row.json": half}, message="old") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert _state.put(work, young.ref, {"row.json": half}, message="young") == ""
    # A half spelled before the family made its parts ref-safe is
    # listed as its ref spells it and dropped by that ref.
    dotted = _state.RUN_PREFIX + "10/check-a-3.14"
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert _state.put(work, dotted, {"row.json": half}, message="dotted") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert HALVES.at("10", "check-a-3.14").ref == dotted
    said = _state.sweep(work, (HALVES,), remote=True, dry_run=True)
    assert any(
        line.startswith(f"  {dotted}: ") and "would drop" in line for line in said
    )
    assert any(
        line.startswith(f"  {old.ref}: ")
        and line.endswith("old, past 6.0h; would drop")
        for line in said
    )
    assert _state.read(work, old.ref).files == {"row.json": half}
    lines = _state.sweep(work, (HALVES,), remote=True)
    assert any(
        line.startswith(f"  {old.ref}: ") and line.endswith("; dropped")
        for line in lines
    )
    assert f"  {young.ref}: 1 row(s), within its bounds" in lines
    assert _state.read(work, old.ref).files is None
    assert _state.read(work, dotted).files is None
    assert _state.read(work, young.ref).files == {"row.json": half}
    # Re-running is the recovery procedure: the second sweep drops nothing.
    again = _state.sweep(work, (HALVES,), remote=True)
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
    assert _state.put(work, BOUNDED.ref, files, message="six") == ""
    said = _state.sweep(work, (BOUNDED,), remote=False, dry_run=True)
    assert said == [
        f"  {BOUNDED.ref}: would drop 2 row(s) older than 7 day(s)",
        f"  {BOUNDED.ref}: would drop 1 file(s) beyond its window of 3",
    ]
    assert set(_state.read(work, BOUNDED.ref).files or {}) == set(files)
    done = _state.sweep(work, (BOUNDED,), remote=False)
    assert done == [
        f"  {BOUNDED.ref}: dropped 2 row(s) older than 7 day(s)",
        f"  {BOUNDED.ref}: dropped 1 file(s) beyond its window of 3",
    ]
    assert set(_state.read(work, BOUNDED.ref).files or {}) == {"n1", "n2", "n3"}
    assert _state.sweep(work, (BOUNDED,), remote=False) == [
        f"  {BOUNDED.ref}: 3 row(s), within its bounds"
    ]


def test_the_janitor_drops_a_key_no_current_producer_makes_and_keeps_the_rest(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    family = _state.Keyed(
        "cov", ("leg", "unit"), window=2, ci_only=False, current=_only_a_x
    )
    for key in (("a", "x"), ("b", "x")):
        assert family.series(*key).put(work, {"r": {}}, message="m") == ""
    gone = family.series("b", "x").ref
    said = _state.sweep(work, (family,), remote=True, dry_run=True)
    assert f"  {gone}: no current leg and unit produces it; would drop" in said
    assert family.listed(work) == [("a", "x"), ("b", "x")]
    done = _state.sweep(work, (family,), remote=True)
    assert f"  {gone}: no current leg and unit produces it; dropped" in done
    assert f"  {family.series('a', 'x').ref}: 1 row(s), within its bounds" in done
    assert family.listed(work) == [("a", "x")]


def test_the_run_context_base_is_the_pull_requests_and_empty_otherwise(
    tmp_path: Path,
) -> None:
    event = tmp_path / "event.json"
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "7",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/3/merge",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_PATH": str(event),
    }
    # Fallbacks first: no base in the payload, a push, a missing payload.
    event.write_text(json.dumps({"pull_request": {"head": {"sha": "b" * 40}}}))
    run = _state.run_context(env)
    assert run is not None and run.base_ref == ""
    pushed = _state.run_context({**env, "GITHUB_EVENT_NAME": "push"})
    assert pushed is not None and pushed.base_ref == ""
    missing = _state.run_context({**env, "GITHUB_EVENT_PATH": str(tmp_path / "none")})
    assert missing is not None and missing.base_ref == ""
    event.write_text(
        json.dumps(
            {"pull_request": {"head": {"sha": "b" * 40}, "base": {"ref": "main"}}}
        )
    )
    run = _state.run_context(env)
    assert run is not None and run.base_ref == "main"
    gitlab = _state.run_context(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "9",
            "CI_PIPELINE_SOURCE": "merge_request_event",
            "CI_COMMIT_REF_NAME": "feat/x",
            "CI_COMMIT_SHA": "c" * 40,
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        }
    )
    assert gitlab is not None and gitlab.base_ref == "main"
