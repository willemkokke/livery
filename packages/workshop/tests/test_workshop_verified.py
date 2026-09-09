"""The verified-tree record: the fallbacks that run the gate, then the skip."""

from __future__ import annotations

import json
import shutil
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
    assert found is None and "another schema" in why
    shutil.rmtree(tmp_path / "origin.git")
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
    assert line.startswith("  verified: no stamp") and "no metrics series" in line
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
    assert "check (b) did not run the full gate" in line
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
    }
    trace = _trace(work / "fm-profile.json")
    assert (
        _metrics.put_leg(work, RUN, job="check (a)", label="check-a", trace=trace) == ""
    )
    found = _state.read(work, _state.run_ref(RUN, "check-a"))
    assert found.files is not None
    row = json.loads(found.files[_metrics.ROW_FILE])
    assert row["scope"] == {"scope": "affected", "packages": ["packages/x"]}
