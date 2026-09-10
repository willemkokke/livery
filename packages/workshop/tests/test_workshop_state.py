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

import pytest

from livery.workshop import _state
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401

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


def test_the_push_is_read_back(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    real_git = _state._git

    def lying_remote(root: Path, *args: str, stdin: str | None = None) -> object:
        result = real_git(root, *args, stdin=stdin)
        if args[:2] == ("ls-remote", "origin") and result.stdout.strip():
            # The readback reads three attributes; the lie is a sha the
            # push never made.
            return SimpleNamespace(
                code=0, stdout="0" * 40 + "\t" + args[2] + "\n", stderr=""
            )
        return result

    monkeypatch.setattr(_state, "_git", lying_remote)
    why = _state.put(work, REF, {"a.json": "1"}, message="a")
    assert why.startswith("push reported success but")
    assert "wanted" in why


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
    assert (
        _state.run_ref(github, "check-ubuntu-3.14")
        == "refs/workshop/run/7/check-ubuntu-3.14"
    )


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
    assert found.skipped == (
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


def test_the_window_keeps_the_newest_rows_by_their_stamps_not_their_names(
    repos: tuple[Path, Path],
) -> None:
    _, work = repos
    # The newest row has the smallest name: a name order would evict it.
    for name in ("zz", "mm", "aa", "00"):
        assert ROWS.put(work, {name: {}}, message=name) == ""
    assert [row.name for row in ROWS.rows(work).rows] == ["00", "aa", "mm"]


# --- the janitor -------------------------------------------------------------


def test_sweep_drops_only_stale_run_refs_and_says_so(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    old = _state.RUN_PREFIX + "11/check-a"
    young = _state.RUN_PREFIX + "12/check-a"
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")
    assert _state.put(work, old, {"row.json": "1"}, message="old") == ""
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert _state.put(work, young, {"row.json": "2"}, message="young") == ""
    lines = _state.sweep(work, older_than=timedelta(hours=6))
    assert any(
        line.startswith(f"  {old}:") and line.endswith("dropped") for line in lines
    )
    assert any(
        line.startswith(f"  {young}:") and line.endswith("kept") for line in lines
    )
    assert _state.read(work, old).files is None
    assert _state.read(work, young).files == {"row.json": "2"}
    # Re-running is the recovery procedure: the second sweep finds
    # nothing stale and drops nothing.
    again = _state.sweep(work, older_than=timedelta(hours=6))
    assert not any(line.endswith("dropped") for line in again)


def test_sweep_enforces_a_series_window(repos: tuple[Path, Path]) -> None:
    _, work = repos
    series = _state.Series("metrics", window=2)
    rows = {f"run-{n:04d}.json": str(n) for n in range(4)}
    assert _state.put(work, series.ref, rows, message="four") == ""
    lines = _state.sweep(work, (series,))
    assert f"  {series.ref}: 4 files trimmed to 2" in lines
    assert _state.read(work, series.ref).files == {
        "run-0002.json": "2",
        "run-0003.json": "3",
    }
    assert f"  {series.ref}: within its window of 2" in _state.sweep(work, (series,))


def test_sweep_trims_a_series_by_its_rows_stamps(repos: tuple[Path, Path]) -> None:
    _, work = repos
    series = _state.Series("trees", window=1, ci_only=False)
    # The newest row has the smallest name: a name order would evict it.
    files = {
        "aa": json.dumps({"schema": 1, "when": "2026-01-02T00:00:00+00:00"}),
        "zz": json.dumps({"schema": 1, "when": "2026-01-01T00:00:00+00:00"}),
    }
    assert _state.put(work, series.ref, files, message="two") == ""
    assert f"  {series.ref}: 2 files trimmed to 1" in _state.sweep(work, (series,))
    assert [row.name for row in series.rows(work).rows] == ["aa"]


def test_sweep_names_a_remote_it_cannot_list(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = _state.sweep(work, now=datetime.now(UTC))
    assert lines == [
        f"  {_state.RUN_PREFIX}*: the remote could not be listed; nothing swept"
    ]


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
