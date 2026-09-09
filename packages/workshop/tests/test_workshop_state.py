"""The CI state store: refusals first, then the compare-and-swap path.

Every test drives real git against a bare repository standing in for
origin, so the refusals are the transport's own words, not a fake's.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.workshop import _state

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


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with one commit on main, and a working clone of it."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = _clone(tmp_path, "work", origin)
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return origin, work


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
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


def test_a_ci_run_may_write_a_ci_only_series(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    assert _state.put(work, REF, {"a.json": "1"}, message="one", ci_only=True) == ""


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


def test_sweep_names_a_remote_it_cannot_list(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, work = repos
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    lines = _state.sweep(work, now=datetime.now(UTC))
    assert lines == [
        f"  {_state.RUN_PREFIX}*: the remote could not be listed; nothing swept"
    ]
