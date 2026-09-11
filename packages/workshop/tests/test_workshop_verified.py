"""The verified-tree record: the fallbacks that run the gate, then the skip."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _metrics, _quality, _state, _verified
from livery.workshop._git_ops import GitOps

RUN = _state.RunContext("gitea", "1013", "push", "refs/heads/main")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


@pytest.fixture(autouse=True)
def _in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)


def _trace(path: Path) -> Path:
    events = [
        {"ph": "M", "name": "process_name", "pid": 1, "args": {"name": "fm"}},
        {
            "ph": "X",
            "cat": "task",
            "name": "check",
            "pid": 1,
            "tid": 1,
            "ts": 0,
            "dur": 1000,
        },
    ]
    path.write_text(json.dumps({"traceEvents": events}))
    return path


# --- the fallbacks first: every one runs the gate -----------------------------


def test_an_absent_record_and_an_unknown_tree_are_quiet(work: Path) -> None:
    tree = _verified.tree_id(GitOps(work))
    assert _verified.record(work, tree) == (None, "")
    assert _verified.stamp(work, RUN, tree=tree, sha="a" * 40, legs=("check",)) == ""
    assert _verified.record(work, "f" * 40) == (None, "")


def _rmtree(path: Path) -> None:
    """Remove *path* whole; git's pack files are read-only, which Windows honours."""
    for entry in path.rglob("*"):
        if entry.is_file():
            entry.chmod(entry.stat().st_mode | stat.S_IWRITE)
    shutil.rmtree(path)


def test_an_unreadable_store_and_a_foreign_entry_name_their_reason(
    work: Path, tmp_path: Path
) -> None:
    tree = _verified.tree_id(GitOps(work))
    assert (
        _state.put(
            work,
            _verified.SERIES.ref,
            {tree: "not json", "other": json.dumps({"schema": 99})},
            message="junk",
            ci_only=True,
        )
        == ""
    )
    found, why = _verified.record(work, tree)
    assert found is None and "does not parse" in why
    found, why = _verified.record(work, "other")
    assert found is None and "this reader speaks" in why
    _rmtree(tmp_path / "origin.git")
    found, why = _verified.record(work, tree)
    assert found is None and "could not be read" in why


def test_the_stamp_refuses_outside_ci(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS")
    monkeypatch.delenv("GITEA_ACTIONS")
    why = _verified.stamp(work, RUN, tree="a" * 40, sha="b" * 40, legs=("check",))
    assert "only a CI run writes" in why


def test_a_narrowed_leg_or_a_missing_row_leaves_the_tree_unstamped(work: Path) -> None:
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    assert line == f"  verified: no stamp, run {RUN.run_id} left no row"
    narrowed = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            "check (a)": {"scope": {"scope": "full", "packages": []}},
            "check (b)": {"scope": {"scope": "affected", "packages": ["packages/x"]}},
        },
    }
    assert (
        _state.put(
            work,
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(narrowed)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    # A narrowed leg composes only with a verified base; this tree's
    # base is itself, unrecorded, so the tree stays unstamped.
    assert "check (b) ran narrowed on base tree" in line
    assert "not proved in full" in line
    assert _verified.record(work, _verified.tree_id(GitOps(work))) == (None, "")


def test_the_gate_runs_when_the_record_cannot_decide_or_names_a_narrowed_tree(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "livery.workshop._verified.record",
        lambda root, tree: (None, "the record could not be read: down"),
    )
    assert not _quality.verified_already(work)
    assert "could not be read: down; running the gate" in capsys.readouterr().out
    monkeypatch.setattr(
        "livery.workshop._verified.record",
        lambda root, tree: (
            _verified.Verified(tree, "5", "c" * 40, _verified.AFFECTED, ()),
            "",
        ),
    )
    assert not _quality.verified_already(work)
    assert capsys.readouterr().out == ""


# --- the skip ----------------------------------------------------------------


def test_a_full_stamp_is_read_back_and_skips_the_gate(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    full = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            "check (a)": {"scope": {"scope": "full", "packages": []}},
            "docs": {"scope": {"scope": "unknown", "packages": []}},
        },
    }
    assert (
        _state.put(
            work,
            _metrics.SERIES.ref,
            {_metrics.run_file(RUN.run_id): json.dumps(full)},
            message="rows",
            ci_only=True,
        )
        == ""
    )
    line = _verified.stamp_from_metrics(work, RUN, sha="a" * 40)
    tree = _verified.tree_id(GitOps(work))
    assert line == f"  verified: tree {tree[:12]} recorded as proved green by run 1013"
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None
    assert found.scope == _verified.FULL and found.legs == ("check (a)",)
    assert _quality.verified_already(work)
    assert "proved green by run 1013" in capsys.readouterr().out
    # The gate itself: a verified tree ends the run before any step, marker left.
    (work / "workshop.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: work)
    monkeypatch.setattr("livery.workshop._state.run_context", lambda: RUN)
    monkeypatch.setattr(
        "livery.workshop._quality._affected", lambda base="main": pytest.fail("ran")
    )
    _quality.check()
    assert _verified.read_marker(work)["scope"] == _verified.VERIFIED


def test_the_marker_reaches_the_legs_row(work: Path) -> None:
    assert _verified.read_marker(work)["scope"] == "unknown"
    (work / _verified.MARKER).write_text("junk")
    assert _verified.read_marker(work)["scope"] == "unknown"
    _verified.write_marker(work, _verified.AFFECTED, ("packages/x",))
    assert _verified.read_marker(work) == {
        "scope": "affected",
        "packages": ["packages/x"],
        "leg": "",
    }
    _verified.write_marker(work, _verified.FULL, leg="check-a")
    assert _verified.read_marker(work)["leg"] == "check-a"
    _verified.write_marker(work, _verified.AFFECTED, ("packages/x",))
    trace = _trace(work / "fm-profile.json")
    assert (
        _metrics.put_leg(work, RUN, job="check (a)", label="check-a", trace=trace) == ""
    )
    found = _state.read(work, _metrics.run_ref(RUN, "check-a"))
    assert found.files is not None
    row = json.loads(found.files[_metrics.ROW_FILE])
    assert row["scope"] == {
        "scope": "affected",
        "packages": ["packages/x"],
        "leg": "",
    }


# --- the composed stamp: a narrowed run on a verified base --------------------

PULL = _state.RunContext(
    "gitea",
    "1014",
    "pull_request",
    "refs/pull/1/merge",
    base_ref="main",
    head_ref="feat/one",
)


def _rows(work: Path, run: _state.RunContext, scopes: dict[str, str]) -> None:
    """The run's metrics row: one check leg per name, with the scope it left."""
    entry = {
        "schema": _metrics.SCHEMA,
        "jobs": {
            name: {"scope": {"scope": scope, "packages": []}}
            for name, scope in scopes.items()
        },
    }
    assert (
        _state.put(
            work,
            _metrics.SERIES.ref,
            {_metrics.run_file(run.run_id): json.dumps(entry)},
            message="rows",
            ci_only=True,
        )
        == ""
    )


def _branch_commit(work: Path, name: str) -> None:
    """A commit on a new branch off the checkout, as a pull request run sees it."""
    _git(work, "checkout", "-q", "-b", name)
    (work / f"{name.replace('/', '-')}.txt").write_text(f"{name}\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", f"feat: {name}")


def test_a_narrowed_run_stays_unstamped_without_a_verified_base(work: Path) -> None:
    from livery.workshop._git_ops import GitError

    git = GitOps(work)
    # A leg that left no scope: no stamp, the leg named.
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "unknown"})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp") and "check (b) left no scope" in line
    # Narrowed legs on a base the record does not name: no stamp, the
    # base named, so a reader knows which tree's proof is missing.
    _branch_commit(work, "feat/one")
    base = _verified.tree_id(git, "origin/main")
    tree = _verified.tree_id(git)
    _rows(work, PULL, {"check (a)": "affected", "check (b)": "nothing"})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp")
    assert f"base tree {base[:12]}" in line and "not proved in full" in line
    assert _verified.record(work, tree) == (None, "")
    # A base row of another scope is no proof either.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "livery.workshop._verified.record",
            lambda root, tree: (
                _verified.Verified(tree, "5", "c" * 40, _verified.AFFECTED, ()),
                "",
            ),
        )
        line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp") and f"base tree {base[:12]}" in line
    assert _verified.record(work, tree) == (None, "")
    # No merge base, as a shallow checkout has none: no stamp, git's words.
    with pytest.MonkeyPatch.context() as patch:

        def _none(self: GitOps, base: str) -> str:
            raise GitError("fatal: no merge base found")

        patch.setattr("livery.workshop._git_ops.GitOps.merge_base", _none)
        line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line.startswith("  verified: no stamp")
    assert "no merge base with origin/main" in line and "fatal: no merge base" in line
    assert _verified.record(work, tree) == (None, "")


def test_a_narrowed_run_on_a_verified_base_stamps_its_tree_naming_the_base(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git = GitOps(work)
    base = _verified.tree_id(git)
    stamped = _verified.stamp(work, RUN, tree=base, sha="b" * 40, legs=("check (a)",))
    assert stamped == ""
    _branch_commit(work, "feat/one")
    tree = _verified.tree_id(git)
    _rows(work, PULL, {"check (a)": "affected"})
    line = _verified.stamp_from_metrics(work, PULL, sha="a" * 40)
    assert line == (
        f"  verified: tree {tree[:12]} recorded as proved green by run 1014"
        f" on top of tree {base[:12]} (run 1013)"
    )
    found, why = _verified.record(work, tree)
    assert why == "" and found is not None
    assert found.scope == _verified.FULL and found.legs == ("check (a)",)
    assert found.base_tree == base and found.base_run == "1013"
    # The row names the branch whose run proved the tree, so main's run
    # after the squash finds the branch's coverage record to copy; the
    # base row, stamped by main's own push, names none.
    assert found.branch == "feat/one"
    base_row, _ = _verified.record(work, base)
    assert base_row is not None and base_row.branch == ""
    # The legs read a composed row like a full one, and say what it rests on.
    assert _quality.verified_already(work)
    out = capsys.readouterr().out
    assert "proved green by run 1014" in out and f"on top of tree {base[:12]}" in out
    # A composed row is a full row for the next narrowed run: the proof
    # chains, and a note-only diff composes the same way.
    _git(work, "push", "-q", "origin", "feat/one:main")
    _git(work, "fetch", "-q", "origin")
    _branch_commit(work, "feat/two")
    later = _state.RunContext(
        "gitea", "1015", "pull_request", "refs/pull/2/merge", base_ref="main"
    )
    _rows(work, later, {"check (a)": "nothing"})
    line = _verified.stamp_from_metrics(work, later, sha="a" * 40)
    assert f"on top of tree {tree[:12]} (run 1014)" in line
    found, _ = _verified.record(work, _verified.tree_id(git))
    assert found is not None and found.base_tree == tree
    # Every leg skipped on the record already: nothing to add.
    _rows(work, later, {"check (a)": "verified"})
    line = _verified.stamp_from_metrics(work, later, sha="a" * 40)
    assert "already recorded" in line
