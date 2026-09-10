"""The store's reader: refusals first, then the rows a person reads."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _metrics, _state, _store_tasks, _verified


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
    # The stamps the tests read are CI's to write; the runner's own
    # payload and refs are scrubbed so the suite reads the same on a
    # runner as on a desk.
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")


_FAILURES = (SystemExit, Exception)


def test_an_unknown_series_names_the_known_ones(work: Path) -> None:
    with pytest.raises(
        _FAILURES, match="no series named 'nope'; the series are metrics"
    ):
        _store_tasks.show_flow(work, "nope")


def test_a_family_without_its_key_lists_its_keys_and_refuses_a_wrong_shape(
    work: Path,
) -> None:
    assert _store_tasks.show_flow(work, "run") == [
        "  run is keyed; --key=<run>,<leg> picks one of:",
        "    (none yet)",
    ]
    half = {"row.json": json.dumps({"schema": _metrics.SCHEMA, "job": "check"})}
    assert _state.put(work, _metrics.run_ref(_RUN, "check-a"), half, message="h") == ""
    assert _store_tasks.show_flow(work, "run")[1:] == ["    7,check-a"]
    with pytest.raises(_FAILURES, match="keyed by run, leg; --key names 1 part"):
        _store_tasks.show_flow(work, "run", key="7")
    with pytest.raises(_FAILURES, match="metrics is not keyed; drop --key"):
        _store_tasks.show_flow(work, "metrics", key="7,check-a")


_RUN = _state.RunContext("gitea", "7", "push", "refs/heads/main")


def test_an_unreadable_series_fails_with_the_stores_reason(
    work: Path, tmp_path: Path
) -> None:
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(_FAILURES, match="the metrics series could not be read"):
        _store_tasks.show_flow(work, "metrics")
    listed = _store_tasks.ls_flow(work)
    assert any(
        line.startswith("  metrics: remote, window 300; the metrics series could not")
        for line in listed
    )
    assert "  run/<run>/<leg>: remote, no window; could not be listed" in listed


def test_ls_names_every_series_with_its_scope_window_and_count(work: Path) -> None:
    assert _verified.stamp(work, _RUN, tree="a" * 40, sha="b" * 40, legs=("c",)) == ""
    listed = _store_tasks.ls_flow(work)
    assert "  metrics: remote, window 300; 0 row(s)" in listed
    assert "  verified: remote, window 200; 1 row(s)" in listed
    assert "  coverage/<leg>/<package>: remote, window 6; 0 key(s)" in listed
    assert "  gate-record: local, window 200; 0 row(s)" in listed
    assert "  diagnostics: local, window 20; 0 row(s)" in listed


def test_show_prints_rows_newest_first_compactly_or_as_json(work: Path) -> None:
    assert _verified.stamp(work, _RUN, tree="a" * 40, sha="b" * 40, legs=("c",)) == ""
    junk = {"junk": "{not json"}
    assert _state.put(work, _verified.SERIES.ref, junk, message="junk") == ""
    lines = _store_tasks.show_flow(work, "verified")
    assert lines[0] == f"  {_verified.SERIES.ref}: 1 row(s)"
    assert lines[1].startswith(f"  {'a' * 40}  2026")
    assert lines[2] == (
        f"    forge: gitea, legs: [1 items], run: 7, scope: full, sha: {'b' * 40},"
        f" tree: {'a' * 40}"
    )
    assert lines[3] == "  junk: does not parse; skipped"
    (text,) = _store_tasks.show_flow(work, "verified", as_json=True)
    (row,) = json.loads(text)
    assert row["name"] == "a" * 40 and row["schema"] == 1 and row["legs"] == ["c"]
    half = {"row.json": json.dumps({"schema": _metrics.SCHEMA, "job": "check"})}
    assert _state.put(work, _metrics.run_ref(_RUN, "check-a"), half, message="h") == ""
    shown = _store_tasks.show_flow(work, "run", key="7,check-a")
    assert shown[0] == f"  {_metrics.run_ref(_RUN, 'check-a')}: 1 row(s)"
    assert shown[2] == "    job: check"
