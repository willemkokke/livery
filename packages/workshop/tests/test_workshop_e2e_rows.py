"""The rows the loop's proofs read: refusals first, then each reader's answer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.footman import Failed
from livery.workshop import _ci_context, _e2e_rows, _metrics, _shard, _verified
from livery.workshop._ci_context import state_store
from livery.workshop._verified import tree_id
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

RUN = _ci_context.RunContext("gitea", "77", "push", "refs/heads/main")
LEG = "check-ubuntu-latest-3.14"
JOB = "check (ubuntu-latest, 3.14)"
SHARD1 = "check (ubuntu-latest, 3.14, 1of2)"
SHARD2 = "check (ubuntu-latest, 3.14, 2of2)"
#: The forge's row of a shard, as the collect keeps it beside the fold's row.
FORGE_ROW: dict[str, object] = {
    "conclusion": "success",
    "status": "completed",
    "queued_ms": 10.0,
    "wall_ms": 20.0,
    "complete": True,
}
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


def _put_metrics(
    work: Path, jobs: dict[str, dict[str, object]], run: str = "77"
) -> None:
    entry = {
        "schema": _metrics.SCHEMA,
        "when": "2026-10-17T10:00:00+00:00",
        "run": run,
        "jobs": jobs,
        "sections": {
            "coverage": {
                "unit": "%",
                "packages": {"packages/loop-echo": 100.0, "packages/loop-cpp": 97.5},
            }
        },
    }
    why = _metrics.SERIES.put(
        state_store(work),
        {_metrics.run_file(run): entry},
        message=f"metrics: run {run}",
    )
    assert why == ""


def _folded(items: tuple[int, int] = (2, 1)) -> dict[str, object]:
    """The fold's row of the platform: two shards, *items* each, one row read."""
    return {
        "scope": {
            "scope": "affected",
            "packages": ["packages/loop-echo"],
            "leg": LEG,
            "base_tree": "",
        },
        "tasks": {"check": 12.0},
        "conclusion": "success",
        "shard": {
            "count": 2,
            "digest": "ab" * 32,
            "rows": ["20261017T090000Z-76.json"],
            "before": "2026-10-17T09:30:00+00:00",
            "shards": [
                {
                    "shard": f"{index}of2",
                    "leg": f"{LEG}-{index}of2",
                    "job": job,
                    "when": "2026-10-17T09:5{index}:00+00:00",
                    "items": count,
                    "total_ms": 100.0,
                    "packages": {},
                }
                for index, (job, count) in enumerate(
                    zip((SHARD1, SHARD2), items, strict=True), 1
                )
            ],
        },
    }


def test_a_shards_row_with_no_folded_one_fails_naming_the_fold(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    # The forge's rows of the shards alone: the fold left no row for
    # their platform, so no reader takes one of them as the leg.
    _put_metrics(work, {SHARD1: FORGE_ROW, SHARD2: FORGE_ROW, "gate": {}})
    facts = _e2e_rows.run_facts(work, "77")
    assert facts.shard_jobs == (SHARD1, SHARD2) and set(facts.jobs) == {"gate"}
    with pytest.raises(_FAILURES) as caught:
        facts.job("check")
    message = str(caught.value)
    assert f"names {SHARD1}, {SHARD2} and no folded row" in message
    assert "the gate job's fold did not run" in message
    # One shard's own row, its record unfolded: the collect joined it
    # with the forge's times because the fold did not run.
    own: dict[str, object] = {
        "scope": {"scope": "full", "packages": [], "leg": f"{LEG}-1of2"},
        "shard": {
            "shard": "1of2",
            "digest": "ab" * 32,
            "items": ["tests/test_a.py"],
            "rows": [],
            "before": "2026-10-17T09:30:00+00:00",
        },
        "conclusion": "success",
    }
    _put_metrics(work, {SHARD1: own, "gate": {}}, run="78")
    facts = _e2e_rows.run_facts(work, "78")
    leg = facts.jobs[SHARD1]
    assert leg.shard == "1of2" and leg.fold is None and leg.leg == f"{LEG}-1of2"
    with pytest.raises(_FAILURES) as caught:
        facts.job("check")
    message = str(caught.value)
    assert f"holds {SHARD1} (1of2), one shard's own row each" in message
    assert "the gate job's fold did not run" in message
    # A shard's row without its record is known by its label alone.
    _put_metrics(work, {SHARD2: {"scope": {"leg": f"{LEG}-2of2"}}}, run="79")
    assert _e2e_rows.run_facts(work, "79").jobs[SHARD2].shard == "2of2"


def test_a_folded_row_answers_for_the_platform_past_the_shards_forge_rows(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as_ci(monkeypatch)
    _put_metrics(
        work,
        {
            JOB: _folded(),
            SHARD1: FORGE_ROW,
            SHARD2: FORGE_ROW,
            "gate": {"conclusion": "success"},
        },
    )
    facts = _e2e_rows.run_facts(work, "77")
    assert facts.shard_jobs == (SHARD1, SHARD2)
    assert set(facts.jobs) == {JOB, "gate"}
    leg = facts.job("check")
    assert leg.leg == LEG and leg.scope == "affected" and leg.shard == ""
    assert leg.fold is not None
    assert leg.fold.count == 2 and leg.fold.digest == "ab" * 32
    assert leg.fold.rows == ("20261017T090000Z-76.json",)
    assert [(s.shard, s.leg, s.job, s.items) for s in leg.fold.shards] == [
        ("1of2", f"{LEG}-1of2", SHARD1, 2),
        ("2of2", f"{LEG}-2of2", SHARD2, 1),
    ]
    # A job that ran as one shard of one carries no fold.
    assert facts.job("gate").fold is None and facts.job("gate").shard == ""


def test_a_runs_durations_row_is_read_on_the_platform_leg_or_is_none(
    work: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _as_ci(monkeypatch)
    series = _shard.DURATIONS.series(LEG)
    name = _metrics.run_file("77")
    # An absent series is no row, not a failure.
    assert _e2e_rows.durations_row(work, "77", f"{LEG}-2of2") == (
        _e2e_rows.DurationsRow(series.ref, name, None)
    )
    rows = {name: {"run": "77", "items": {"tests/test_a.py": 1.5, "tests/b.py": "x"}}}
    assert series.put(state_store(work), rows, message="durations: run 77") == ""
    # A shard's label reads its platform's series; a number is a reading.
    found = _e2e_rows.durations_row(work, "77", f"{LEG}-2of2")
    assert found == _e2e_rows.DurationsRow(series.ref, name, {"tests/test_a.py": 1.5})
    assert _e2e_rows.durations_row(work, 77, LEG) == found
    assert _e2e_rows.durations_row(work, "78", LEG).items is None
    # A series that cannot be read fails naming it.
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "gone.git")],
        cwd=work,
        check=True,
    )
    with pytest.raises(_FAILURES) as caught:
        _e2e_rows.durations_row(work, "77", LEG)
    message = str(caught.value)
    assert (
        series.ref in message and "could not be read" in message and "run 77" in message
    )


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
