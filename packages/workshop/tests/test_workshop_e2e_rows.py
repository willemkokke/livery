"""The rows the loop's proofs read: refusals first, then each reader's answer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from livery.footman import Failed
from livery.workshop import _ci_context, _e2e_rows, _metrics, _verified
from livery.workshop._ci_context import state_store
from livery.workshop._verified import tree_id
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

RUN = _ci_context.RunContext("gitea", "77", "push", "refs/heads/main")
LEG = "check-ubuntu-latest-3.14"
JOB = "check (ubuntu-latest, 3.14)"
_FAILURES = (SystemExit, Failed)


@pytest.fixture
def work(seeds: Seeds) -> Path:
    """A clone of a bare origin, one commit on main, pushed."""
    return seeds("pushed", pushed) / "work"


def _as_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every put below is a CI run's: the series are written by CI alone."""
    monkeypatch.setattr(_ci_context, "run_context", lambda environ=None: RUN)


def test_a_run_with_no_row_fails_naming_the_series_and_the_file(work: Path) -> None:
    with pytest.raises(_FAILURES) as caught:
        _e2e_rows.run_facts(work, "77")
    message = str(caught.value)
    assert _metrics.SERIES.ref in message
    assert _metrics.run_file("77") in message and "run 77" in message


def test_a_run_nobody_stamped_fails_naming_the_verified_series(work: Path) -> None:
    with pytest.raises(_FAILURES) as caught:
        _e2e_rows.verified_by(work, "77")
    assert _verified.SERIES.ref in str(caught.value)
    with pytest.raises(_FAILURES) as caught:
        _e2e_rows.verified_tree(work, "f" * 40)
    assert _verified.SERIES.ref in str(caught.value)


def test_a_row_naming_two_jobs_of_one_prefix_fails_naming_them(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    _put_metrics(work, {JOB: {}, "check (macos-latest, 3.14)": {}, "gate": {}})
    facts = _e2e_rows.run_facts(work, "77")
    with pytest.raises(_FAILURES) as caught:
        facts.job("check")
    message = str(caught.value)
    assert "2 job(s)" in message and "macos" in message and "gate" in message
    with pytest.raises(_FAILURES):
        facts.job("docs")


def _put_metrics(work: Path, jobs: dict[str, dict[str, object]]) -> None:
    entry = {
        "schema": _metrics.SCHEMA,
        "when": "2026-10-17T10:00:00+00:00",
        "run": "77",
        "jobs": jobs,
        "sections": {
            "coverage": {
                "unit": "%",
                "packages": {"packages/loop-echo": 100.0, "packages/loop-cpp": 97.5},
            }
        },
    }
    why = _metrics.SERIES.put(
        state_store(work), {_metrics.run_file("77"): entry}, message="metrics: run 77"
    )
    assert why == ""


def test_a_run_s_row_answers_each_job_s_scope_its_tasks_and_what_was_judged(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    _put_metrics(
        work,
        {
            JOB: {
                "scope": {
                    "scope": "affected",
                    "packages": ["packages/loop-echo"],
                    "leg": LEG,
                    "base_tree": "b" * 40,
                },
                "tasks": {"check": 12.0},
                "task_steps": {"check/test_pytest/test-pytest": 11.0},
            },
            "gate": {"conclusion": "success"},
        },
    )
    facts = _e2e_rows.run_facts(work, "77")
    leg = facts.job("check")
    assert leg.scope == "affected" and leg.packages == ("packages/loop-echo",)
    assert leg.leg == LEG and leg.base_tree == "b" * 40
    assert set(leg.tasks) == {"check", "check/test_pytest/test-pytest"}
    assert facts.job("gate").scope == "unknown"
    assert facts.coverage == {"packages/loop-echo": 100.0, "packages/loop-cpp": 97.5}


def test_a_verified_row_is_found_by_its_run_and_by_its_tree(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    tree = tree_id(work)
    why = _verified.stamp(
        work, RUN, tree=tree, sha="a" * 40, legs=(JOB,), branch="feat/1-x"
    )
    assert why == ""
    by_run = _e2e_rows.verified_by(work, "77")
    assert by_run.tree == tree and by_run.branch == "feat/1-x"
    assert by_run.base_tree == ""
    assert _e2e_rows.verified_tree(work, tree) == by_run


def test_a_base_s_record_answers_each_unit_s_run_and_skips_the_kept_rows(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    assert _e2e_rows.record_units(work, "main", LEG) == {}
    series = _e2e_rows.COVERAGE_RECORD.series("main", LEG)
    rows = {
        "packages-loop-echo.json": {
            "unit": "packages/loop-echo",
            "run": "12",
            "sha": "a" * 40,
            "closure": "c1",
            "when": "2026-10-17T10:00:00+00:00",
        },
        "tests.json": {
            "unit": "tests",
            "run": "77",
            "sha": "a" * 40,
            "closure": "c2",
            "when": "2026-10-17T10:00:01+00:00",
        },
        "20261015T232632Z.c0.packages-loop-echo.json": {
            "unit": "packages/loop-echo",
            "run": "9",
            "sha": "9" * 40,
            "closure": "c0",
            "when": "2026-10-15T23:26:32+00:00",
        },
    }
    assert series.put(state_store(work), rows, message="coverage: main") == ""
    units = _e2e_rows.record_units(work, "main", LEG)
    assert set(units) == {"packages/loop-echo", "tests"}
    assert units["packages/loop-echo"].run == "12"
    assert units["tests"].run == "77" and units["tests"].closure == "c2"


def test_the_marks_answer_the_newest_row_per_package(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    assert _e2e_rows.marks(work) == {}
    rows: dict[str, dict[str, Any]] = {
        "20261016T143704Z--packages-loop-native": {
            "package": "packages/loop-native",
            "value": 100.0,
            "kind": "ratchet",
            "by": "run 9",
            "when": "2026-10-16T14:37:04+00:00",
        },
        "20261017T002117Z--packages-loop-native": {
            "package": "packages/loop-native",
            "value": 90.0,
            "kind": "accept",
            "by": "Willem Kokke",
            "reason": "the loop proves an accepted lowering",
            "when": "2026-10-17T00:21:17+00:00",
        },
        "20261017T002118Z--packages-loop-echo": {
            "package": "packages/loop-echo",
            "value": "not a number",
            "when": "2026-10-17T00:21:18+00:00",
        },
    }
    assert _e2e_rows.COVERAGE_MARKS.put(state_store(work), rows, message="marks") == ""
    current = _e2e_rows.marks(work)
    assert set(current) == {"packages/loop-native"}
    mark = current["packages/loop-native"]
    assert (mark.value, mark.kind, mark.by) == (90.0, "accept", "Willem Kokke")
