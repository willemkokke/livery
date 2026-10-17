"""The timing rows: refusals first, then the join and the reader.

The store side runs against a bare repository as origin, like the
store's own tests; the forge side is the fake, whose private run
state is the assertion surface, by design.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.footman import Failed
from livery.footman.testing import Runner
from livery.forge import Step
from livery.forge.testing import FakeForge
from livery.workshop import _ci_context, _metrics
from livery.workshop._ci_context import state_store
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

RUN = _ci_context.RunContext("gitea", "1013", "push", "refs/heads/main")
JOB = "check (ubuntu-latest, 3.14)"

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def work(seeds: Seeds) -> Path:
    """A clone of a bare origin, one commit on main, pushed."""
    return seeds("pushed", pushed) / "work"


@pytest.fixture(autouse=True)
def _in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    # A fake run, with the real runner's payload and refs scrubbed so
    # the suite reads the same under a runner as on a desk.
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)


def _trace(
    path: Path,
    *,
    tasks: dict[str, float],
    tests: dict[str, float] | None = None,
    steps: list[dict[str, object]] | None = None,
    pairs: list[dict[str, Any]] | None = None,
) -> Path:
    events: list[dict[str, object]] = [
        {"ph": "M", "name": "process_name", "pid": 1, "args": {"name": "fm"}}
    ]
    at = 1000.0
    for name, ms in tasks.items():
        events.append(
            {
                "ph": "X",
                "cat": "task",
                "name": name,
                "pid": 1,
                "tid": 1,
                "ts": at,
                "dur": ms * 1000,
            }
        )
    events.append(
        {
            "ph": "X",
            "cat": "lane",
            "name": "lane: serial",
            "pid": 1,
            "tid": 1,
            "ts": at,
            "dur": 250_000,
        }
    )
    for step in steps or []:
        events.append({"ph": "X", "cat": "step", "pid": 1, **step})
    # A pair under one id, as footman draws a step that overlaps a
    # sibling on its track; ``dur`` of None leaves the begin alone.
    for n, pair in enumerate(pairs or []):
        begin: dict[str, Any] = {
            "ph": "b",
            "cat": "step",
            "pid": 1,
            "id2": {"local": f"check#{n}"},
            **pair,
        }
        dur = begin.pop("dur")
        events.append(begin)
        if dur is not None:
            events.append({**begin, "ph": "e", "ts": begin["ts"] + dur})
    for nodeid, ms in (tests or {}).items():
        for phase in ("test.setup", "test.call", "test.teardown"):
            events.append(
                {
                    "ph": "X",
                    "cat": phase,
                    "name": nodeid,
                    "pid": 77,
                    "tid": 1,
                    "ts": at,
                    "dur": ms * 1000,
                }
            )
    path.write_text(json.dumps({"traceEvents": events}))
    return path


# --- the leg: refusals first --------------------------------------------------


def test_a_missing_trace_is_a_reason_not_a_row(tmp_path: Path) -> None:
    row, why = _metrics.leg_row(tmp_path / "fm-profile.json", job=JOB)
    assert row is None
    assert "did not run under --profile" in why


def test_an_unparsable_trace_is_a_reason(tmp_path: Path) -> None:
    trace = tmp_path / "fm-profile.json"
    trace.write_text("{not json")
    row, why = _metrics.leg_row(trace, job=JOB)
    assert row is None and "does not parse" in why
    trace.write_text('{"traceEvents": "nope"}')
    assert _metrics.leg_row(trace, job=JOB)[1].endswith("carries no event list")


def test_a_trace_without_tasks_is_a_reason(tmp_path: Path) -> None:
    trace = tmp_path / "fm-profile.json"
    trace.write_text(
        json.dumps(
            {
                "traceEvents": [
                    {
                        "ph": "M",
                        "name": "process_name",
                        "pid": 1,
                        "args": {"name": "fm"},
                    }
                ]
            }
        )
    )
    assert "records no task" in _metrics.leg_row(trace, job=JOB)[1]


def test_put_leg_writes_nothing_without_a_trace(work: Path, tmp_path: Path) -> None:
    lines, why = _metrics.put_leg(
        work, RUN, job=JOB, label="check-a", trace=tmp_path / "none.json"
    )
    assert why == ""
    assert any("did not run under --profile" in line for line in lines)
    assert git.list_refs(state_store(work), git.RUN_PREFIX) == {}


def test_a_leg_whose_files_cannot_be_put_is_red(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._ci_tasks import leg_flow

    monkeypatch.setenv("WORKSHOP_LEG", "check-a")
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    # The refusals first: a spool file no reader takes, and the leg's own
    # row's name, fail the write by name before anything is put.
    spool = work / _metrics.LEG_SPOOL
    spool.mkdir(parents=True)
    (spool / "odd.json").write_text("[1, 2]")
    with pytest.raises(
        _FAILURES, match=r"\.workshop/leg/odd\.json is not a JSON object"
    ):
        leg_flow(work, job=JOB, trace=trace)
    (spool / "odd.json").unlink()
    (spool / _metrics.ROW_FILE).write_text("{}")
    with pytest.raises(_FAILURES, match=r"row\.json is the leg's own row"):
        leg_flow(work, job=JOB, trace=trace)
    (spool / _metrics.ROW_FILE).unlink()
    # A write the store refuses is red, naming the store's reason.
    _metrics.leave_leg_file(work, "coverage.json", {"scope": "full"})
    origin = _git(work, "remote", "get-url", "origin").strip()
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(
        _FAILURES, match=r"the leg's row could not be put on its per-run ref \("
    ):
        leg_flow(work, job=JOB, trace=trace)
    _git(work, "remote", "set-url", "origin", origin)
    assert git.list_refs(state_store(work), git.RUN_PREFIX) == {}


def test_the_leg_row_reads_tasks_waits_and_packages(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path / "fm-profile.json",
        tasks={"check": 4640.0, "check/test/test": 2030.0},
        tests={
            "packages/forge/tests/test_a.py::test_one": 10.0,
            "packages/forge/tests/test_a.py::test_two": 20.0,
            "packages/workshop/tests/test_b.py::test_three": 5.0,
            "tests/test_root.py::test_outside": 99.0,
        },
    )
    row, why = _metrics.leg_row(trace, job=JOB)
    assert why == "" and row is not None
    assert row["job"] == JOB and "schema" not in row
    assert row["tasks"] == {"check": 4640.0, "check/test/test": 2030.0}
    assert row["waits_ms"] == {"serial": 250.0}
    # Every phase counts toward the package's time; only calls count tests.
    assert row["packages"] == {
        "forge": {"tests_ms": 90.0, "tests": 2},
        "workshop": {"tests_ms": 15.0, "tests": 1},
    }
    assert row["total_ms"] == 4640.0


def test_the_leg_row_sums_every_phase_per_test_file(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path / "fm-profile.json",
        tasks={"check": 4640.0},
        tests={
            "packages/forge/tests/test_a.py::test_one": 1000.0,
            "packages/forge/tests/test_a.py::test_two": 250.0,
            "packages/workshop/tests/test_b.py::test_three": 20.0,
            # The workspace's own suite is no package, and still a file.
            "tests/test_root.py::test_outside": 100.0,
        },
    )
    row, why = _metrics.leg_row(trace, job=JOB)
    assert why == "" and row is not None
    # Three phases each: 3750 ms, 60 ms and 300 ms, to a tenth of a second.
    assert row["files"] == {
        "packages/forge/tests/test_a.py": 3.8,
        "packages/workshop/tests/test_b.py": 0.1,
        "tests/test_root.py": 0.3,
    }
    # The package sums stand beside it, as before.
    assert row["packages"]["forge"] == {"tests_ms": 3750.0, "tests": 2}


# --- the collect: refusals first ----------------------------------------------


def test_a_step_belongs_to_the_innermost_task_holding_it_on_its_own_track(
    tmp_path: Path,
) -> None:
    """A step carries no task: the trace nests it under the one it ran in.

    So a step outside every task has no address, one on another track
    is not a task's however their times overlap, and a title a task ran
    twice is one key and their sum.
    """
    trace = _trace(
        tmp_path / "fm-profile.json",
        tasks={"check": 4640.0, "check/typecheck/typecheck": 2030.0},
        steps=[
            # Inside the inner task, which is inside the outer one.
            {"name": "basedpyright --warnings", "tid": 1, "ts": 1100.0, "dur": 900_000},
            {"name": "mypy", "tid": 1, "ts": 2100.0, "dur": 100_000},
            {"name": "mypy", "tid": 1, "ts": 2300.0, "dur": 50_000},
            # Inside the outer task alone: it runs after the inner one ends,
            # every task in this trace starting at the same instant.
            {"name": "ruff check", "tid": 1, "ts": 3_000_000.0, "dur": 10_000},
            # Another track, overlapping in time: no task holds it.
            {"name": "somewhere else", "tid": 9, "ts": 1100.0, "dur": 900_000},
        ],
    )
    row, why = _metrics.leg_row(trace, job=JOB)
    assert why == "" and row is not None
    assert row["task_steps"] == {
        "check/ruff check": 10.0,
        "check/typecheck/typecheck/basedpyright --warnings": 900.0,
        "check/typecheck/typecheck/mypy": 150.0,
    }
    # The tasks themselves are untouched, so nothing counts twice.
    assert row["tasks"] == {"check": 4640.0, "check/typecheck/typecheck": 2030.0}


def test_a_step_drawn_as_an_async_pair_is_read_as_its_span(tmp_path: Path) -> None:
    """A step that overlaps a sibling on its track is a begin/end pair, not a slice.

    footman draws a ``parallel()`` block's children so, since the
    slices on one track must nest. The pair is the step's span: held
    by the task on its track like a slice. A begin without its end is
    no span, a pair on another track is nobody's, and a pair that is
    not a step is not read.
    """
    trace = _trace(
        tmp_path / "fm-profile.json",
        tasks={"check": 4640.0},
        steps=[{"name": "typecheck.mypy", "tid": 1, "ts": 1100.0, "dur": 900_000}],
        pairs=[
            # Begun and never ended: no span.
            {"name": "lint.docrefs", "tid": 1, "ts": 1100.0, "dur": None},
            # Another track, overlapping in time: no task holds it.
            {"name": "somewhere else", "tid": 9, "ts": 1100.0, "dur": 900_000},
            # Not a step: a stream's window is not the gate's.
            {"name": "poll", "cat": "section", "tid": 1, "ts": 1100.0, "dur": 900_000},
            # Outlives the sibling that started before it: its whole wall.
            {"name": "test.pytest", "tid": 1, "ts": 1200.0, "dur": 4_000_000},
        ],
    )
    row, why = _metrics.leg_row(trace, job=JOB)
    assert why == "" and row is not None
    assert row["task_steps"] == {
        "check/test.pytest": 4000.0,
        "check/typecheck.mypy": 900.0,
    }


PARALLEL_TASKS = textwrap.dedent(
    """
    import sys
    import time

    import livery.footman as footman
    from livery.footman import task
    from livery.footman._compose import plugin

    plugin("footman.profile")

    def _sleeps(seconds):
        time.sleep(seconds)

    def _spawns(seconds):
        footman.run([sys.executable, "-c", f"import time; time.sleep({seconds})"])

    @task
    def check():
        footman.parallel(
            footman.step(_sleeps, title="lint.short")(0.05),
            footman.step(_spawns, title="test.pytest")(0.2),
            footman.step(_sleeps, title="typecheck.long")(0.2),
        )
    """
)


def test_every_step_of_a_profiled_parallel_block_has_its_wall_in_the_row(
    tmp_path: Path,
) -> None:
    """The gate runs its judges as the steps of one ``parallel()`` block.

    footman puts every child's steps on the task's own track, and a
    step that outlives the sibling that started before it is drawn as
    an async pair, since the slices on one track must nest. Two
    workers for three steps start the third after the first ends, so
    one of the two long steps straddles the other whichever of them
    started first. The row reads each step's whole wall either way, an
    in-process step and one that spawned a captured child alike.
    """
    src = tmp_path / "tasks.py"
    src.write_text(PARALLEL_TASKS)
    trace = tmp_path / "fm-profile.json"
    result = Runner().invoke(
        ["--jobs=2", f"--profile={trace}", "check"], tasks=src, cwd=tmp_path
    )
    assert result.ok, result.stderr
    events = json.loads(trace.read_text("utf-8"))["traceEvents"]
    # The shape under test: at least one step left the slice lane.
    assert any(e.get("ph") == "b" and e.get("cat") == "step" for e in events)
    row, why = _metrics.leg_row(trace, job=JOB)
    assert why == "" and row is not None
    steps = row["task_steps"]
    assert steps["check/lint.short"] >= 50.0
    assert steps["check/test.pytest"] >= 200.0
    assert steps["check/typecheck.long"] >= 200.0
    # The child the test step spawned is a step of the task as well.
    assert any("time.sleep" in key for key in steps), steps


def test_the_leg_row_names_the_slowest_tests_by_their_call_phase(
    tmp_path: Path,
) -> None:
    trace = _trace(
        tmp_path / "trace.json",
        tasks={"check": 100.0},
        tests={
            "packages/forge/tests/t.py::quick": 100.0,
            "packages/forge/tests/t.py::slow": 4000.0,
            "tests/test_root.py::mid": 900.0,
        },
    )
    row, why = _metrics.leg_row(trace, job="check (ubuntu-latest, 3.14)")
    assert why == "" and row is not None
    # The packages' tests, as the sums are; the root suite is no package.
    assert row["slowest"] == [
        {"test": "packages/forge/tests/t.py::slow", "s": 4.0},
        {"test": "packages/forge/tests/t.py::quick", "s": 0.1},
    ]


def test_a_shard_file_that_is_not_a_record_fails_the_legs_write_by_name(
    work: Path, tmp_path: Path
) -> None:
    from livery.workshop import _shard

    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    shard = work / _shard.SHARD_FILE
    shard.parent.mkdir(parents=True)
    shard.write_text("not json")
    _lines, why = _metrics.put_leg(
        work, RUN, job=JOB, label="check-a-2of4", trace=trace
    )
    assert why.startswith(f"{_shard.SHARD_FILE} does not parse")
    assert git.list_refs(state_store(work), git.RUN_PREFIX) == {}


def test_the_shard_record_rides_the_row_and_one_shard_of_one_carries_none(
    work: Path, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    from livery.workshop import _shard

    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, RUN, job=JOB, label="check-a", trace=trace)[1] == ""
    row, why = _metrics.RUNS.series(RUN.run_id, "check-a").row(
        state_store(work), _metrics.ROW_FILE
    )
    assert why == "" and row is not None and "shard" not in row.data
    before = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)
    _shard.leave_shard(
        work,
        shard=_shard.Shard(2, 4),
        digest="ab" * 32,
        files=["b.py", "a.py"],
        rows=["r1.json"],
        before=before,
        suites=["packages/b"],
    )
    label = "check-a-2of4"
    assert _metrics.put_leg(work, RUN, job=JOB, label=label, trace=trace)[1] == ""
    row, why = _metrics.RUNS.series(RUN.run_id, label).row(
        state_store(work), _metrics.ROW_FILE
    )
    assert why == "" and row is not None
    assert row.data["shard"] == {
        "shard": "2of4",
        "digest": "ab" * 32,
        "files": ["b.py", "a.py"],
        "rows": ["r1.json"],
        "before": before.isoformat(),
        "suites": ["packages/b"],
    }


def test_the_per_run_family_spells_the_janitors_prefix_and_safe_refs() -> None:
    assert _metrics.RUNS.prefix == git.RUN_PREFIX
    run = _ci_context.RunContext("github", "7", "push", "refs/heads/main")
    assert (
        _metrics.run_ref(run, "check-ubuntu-3.14")
        == "refs/workshop/run/7/check-ubuntu-3-14"
    )


def test_collect_says_so_when_no_leg_left_a_row(work: Path) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    lines = _metrics.collect(work, repo, RUN, sha="a" * 40)
    assert lines == [f"  run {RUN.run_id}: no leg left a row; nothing collected"]
    assert git.read(state_store(work), _metrics.SERIES.ref).files is None


def test_collect_skips_a_row_of_another_schema_and_names_it(work: Path) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    ref = _metrics.run_ref(RUN, "check-a")
    assert (
        git.put(
            state_store(work),
            ref,
            {_metrics.ROW_FILE: json.dumps({"schema": 99, "job": JOB})},
            message="m",
        )
        == ""
    )
    lines = _metrics.collect(work, repo, RUN, sha="a" * 40)
    assert (
        f"  {ref}: {_metrics.ROW_FILE}: schema 99, this reader speaks"
        f" {_metrics.SCHEMA}; skipped"
    ) in lines
    assert lines[-1] == f"  run {RUN.run_id}: no leg left a row; nothing collected"
    # The skipped half is kept: nothing landed, so nothing is dropped.
    assert git.read(state_store(work), ref).files is not None


def test_collect_names_a_job_the_forge_does_not_know(
    work: Path, tmp_path: Path
) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, RUN, job=JOB, label="check-a", trace=trace)[1] == ""
    lines = _metrics.collect(work, repo, RUN, sha="b" * 40)
    assert f"  run {RUN.run_id}: the forge lists no such run" in "\n".join(lines)
    assert f"  {JOB}: unknown to the forge; its trace half rides alone" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None and list(rows) == [_metrics.run_file(RUN.run_id)]
    entry = json.loads(rows[_metrics.run_file(RUN.run_id)])
    assert entry["jobs"][JOB]["tasks"] == {"check": 100.0}
    assert "wall_ms" not in entry["jobs"][JOB]
    # Collected halves are dropped; the series file is the record now.
    assert git.list_refs(state_store(work), git.RUN_PREFIX) == {}


def test_collect_keeps_the_halves_while_a_completed_job_is_red(
    work: Path, tmp_path: Path
) -> None:
    # A sibling job's failure turns the gate's verdict red, and the
    # forge's re-run of the failed jobs runs the gate again without
    # the legs: the halves must still be there for that union.
    from typing import Any, cast

    from livery.forge import Repository
    from livery.forge._types import Job, Run

    sha = "e" * 40
    run = _ci_context.RunContext("gitea", "78", "push", "refs/heads/main")
    forge_run = Run(
        id=78,
        workflow="ci.yml",
        head_sha=sha,
        event="push",
        status="running",
        conclusion="",
        created_at="2026-09-10T10:00:00Z",
        started_at="2026-09-10T10:00:05Z",
    )
    jobs = (
        Job(
            1,
            "check (a)",
            "completed",
            "success",
            "2026-09-10T10:00:10Z",
            "2026-09-10T10:02:10Z",
            (),
        ),
        Job(
            2,
            "docs",
            "completed",
            "failure",
            "2026-09-10T10:00:12Z",
            "2026-09-10T10:00:27Z",
            (),
        ),
        Job(3, "gate", "running", "", "2026-09-10T10:03:20Z", "", ()),
        Job(4, "deploy", "queued", "", "", "", ()),
        Job(5, "govern", "completed", "skipped", "", "", ()),
    )

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
            return (forge_run,)

        def jobs(self, run_id: int) -> tuple[Job, ...]:
            return jobs

    class _Repo:
        checks = _Checks()

    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert (
        _metrics.put_leg(work, run, job="check (a)", label="check-a", trace=trace)[1]
        == ""
    )
    ref = _metrics.run_ref(run, "check-a")
    lines = _metrics.collect(work, cast(Repository, cast(Any, _Repo())), run, sha=sha)
    assert f"  {_metrics.SERIES.ref}: run 78 recorded, 5 job(s)" in lines
    assert "  the per-run refs stay for a re-run of the gate: docs: failure" in lines
    assert f"  {ref}: dropped" not in lines
    assert git.read(state_store(work), ref).files is not None


def test_collect_joins_the_forge_times_and_drops_the_halves(
    work: Path, tmp_path: Path
) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    sha = "c" * 40
    fake.push("owner", "repo", "main", sha=sha)
    state = fake._repos[("owner", "repo")]
    run_state = next(iter(state.runs.values()))
    run = _ci_context.RunContext("gitea", str(run_state.id), "push", "refs/heads/main")
    run_state.created_at = "2026-09-09T00:49:13Z"
    run_state.started_at = "2026-09-09T00:49:16Z"
    run_state.completed_at = "2026-09-09T00:49:42Z"
    run_state.steps = (
        Step(
            "Run actions/checkout@v4",
            "success",
            "2026-09-09T00:49:16Z",
            "2026-09-09T00:49:17Z",
        ),
        Step("Gate", "success", "2026-09-09T00:49:37Z", "2026-09-09T00:49:42Z"),
    )
    trace = _trace(
        tmp_path / "t.json",
        tasks={"check": 4100.0},
        steps=[{"name": "test.pytest", "tid": 1, "ts": 1500.0, "dur": 2_000_000}],
    )
    assert _metrics.put_leg(work, run, job="gate", label="gate", trace=trace)[1] == ""
    lines = _metrics.collect(work, repo, run, sha=sha)
    assert f"  {_metrics.SERIES.ref}: run {run.run_id} recorded, 1 job(s)" in lines
    assert f"  {_metrics.run_ref(run, 'gate')}: dropped" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(run.run_id)])
    job = entry["jobs"]["gate"]
    assert job["queued_ms"] == 3000.0 and job["wall_ms"] == 26000.0
    assert job["steps"] == [
        {"name": "Run actions/checkout@v4", "ms": 1000.0},
        {"name": "Gate", "ms": 5000.0},
    ]
    assert job["tasks"] == {"check": 4100.0}
    # The steps a task ran ride under their task's address, so a
    # reader of the run's file sees the gate's steps, the test step
    # among them.
    assert job["task_steps"] == {"check/test.pytest": 2000.0}
    assert entry["created_at"] == "2026-09-09T00:49:13Z"
    assert git.list_refs(state_store(work), git.RUN_PREFIX) == {}


def test_collect_records_every_job_and_the_runs_wall(
    work: Path, tmp_path: Path
) -> None:
    from typing import Any, cast

    from livery.forge import Repository
    from livery.forge._types import Job, Run

    sha = "d" * 40
    run = _ci_context.RunContext("gitea", "77", "push", "refs/heads/main")
    forge_run = Run(
        id=77,
        workflow="ci.yml",
        head_sha=sha,
        event="push",
        status="running",
        conclusion="",
        created_at="2026-09-10T10:00:00Z",
        started_at="2026-09-10T10:00:05Z",
    )
    jobs = (
        Job(
            1,
            "check (a)",
            "completed",
            "success",
            "2026-09-10T10:00:10Z",
            "2026-09-10T10:02:10Z",
            (),
        ),
        Job(
            2,
            "docs",
            "completed",
            "success",
            "2026-09-10T10:00:12Z",
            "2026-09-10T10:03:12Z",
            (Step("Docs", "success", "2026-09-10T10:01:00Z", "2026-09-10T10:03:00Z"),),
        ),
        Job(3, "gate", "running", "", "2026-09-10T10:03:20Z", "", ()),
        Job(4, "deploy", "queued", "", "", "", ()),
    )

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
            return (forge_run,)

        def jobs(self, run_id: int) -> tuple[Job, ...]:
            return jobs

    class _Repo:
        checks = _Checks()

    trace = _trace(tmp_path / "t.json", tasks={"check": 4100.0})
    assert (
        _metrics.put_leg(work, run, job="check (a)", label="check-a", trace=trace)[1]
        == ""
    )
    lines = _metrics.collect(work, cast(Repository, cast(Any, _Repo())), run, sha=sha)
    assert f"  {_metrics.SERIES.ref}: run 77 recorded, 4 job(s)" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file("77")])
    # Refusals first: a job the forge has not started carries no times
    # and breaks nothing; the gate job, still running while it collects,
    # is measured to the collection and marked incomplete.
    deploy = entry["jobs"]["deploy"]
    assert deploy["wall_ms"] is None and deploy["queued_ms"] is None
    assert deploy["complete"] is False and deploy["status"] == "queued"
    gate = entry["jobs"]["gate"]
    assert gate["complete"] is False
    assert gate["wall_ms"] == _metrics._between(
        "2026-09-10T10:03:20Z", entry["collected_at"]
    )
    # A job without a leg row has the forge's times; the leg keeps its trace.
    docs = entry["jobs"]["docs"]
    assert docs["queued_ms"] == 12000.0 and docs["wall_ms"] == 180000.0
    assert docs["steps"] == [{"name": "Docs", "ms": 120000.0}] and docs["complete"]
    assert entry["jobs"]["check (a)"]["tasks"] == {"check": 4100.0}
    assert entry["jobs"]["check (a)"]["wall_ms"] == 120000.0
    # The run's own wall: its start to this collection.
    assert entry["run_wall_ms"] == _metrics._between(
        "2026-09-10T10:00:05Z", entry["collected_at"]
    )
    # Rendered as the run group beside the jobs that carry numbers; a
    # job without times renders no group, and an older row without the
    # field renders no run group and nothing breaks.
    text = "\n".join(_metrics.render(work))
    assert "\n  run\n" in text and "\n  docs\n" in text and "\n  gate\n" in text
    assert "\n  deploy\n" not in text
    assert _metrics._metrics({"jobs": {}}) == {}


def test_collect_writes_a_legs_file_seconds_onto_its_platforms_durations(
    work: Path, tmp_path: Path
) -> None:
    from livery.workshop import _shard
    from livery.workshop._verified import write_marker

    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    # The gate's marker beside the traces names the leg the rows are
    # keyed by. A leg without tests writes no durations row; a leg with
    # tests writes one under its platform leg, named as the run's file.
    write_marker(tmp_path, "full", leg="check-a")
    bare = _trace(tmp_path / "bare.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, RUN, job="gate", label="gate", trace=bare)[1] == ""
    tests = {
        "packages/forge/tests/test_a.py::test_x": 1000.0,
        "packages/forge/tests/test_b.py::test_y": 500.0,
    }
    full = _trace(tmp_path / "full.json", tasks={"check": 100.0}, tests=tests)
    assert _metrics.put_leg(work, RUN, job=JOB, label="check-a", trace=full)[1] == ""
    lines = _metrics.collect(work, repo, RUN, sha="b" * 40)
    series = _shard.DURATIONS.series("check-a")
    assert f"  {series.ref}: 2 file(s) recorded for run {RUN.run_id}" in lines
    assert not any(
        line.startswith(f"  {_shard.DURATIONS.series('gate').ref}") for line in lines
    )
    found = series.rows(state_store(work))
    assert not found.failed and len(found.rows) == 1
    assert found.rows[0].name == _metrics.run_file(RUN.run_id)
    assert found.rows[0].data["run"] == RUN.run_id
    assert found.rows[0].data["files"] == {
        "packages/forge/tests/test_a.py": 3.0,
        "packages/forge/tests/test_b.py": 1.5,
    }
    assert _shard.DURATIONS.series("gate").rows(state_store(work)).rows == ()


def test_collect_leaves_the_durations_to_the_fold_for_one_shards_row(
    work: Path, tmp_path: Path
) -> None:
    from livery.workshop import _shard

    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    tests = {"packages/forge/tests/test_a.py::test_x": 1000.0}
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0}, tests=tests)
    from datetime import UTC, datetime

    _shard.leave_shard(
        work,
        shard=_shard.Shard(1, 2),
        digest="ab" * 32,
        files=["packages/forge/tests/test_a.py"],
        rows=[],
        before=datetime(2026, 10, 17, tzinfo=UTC),
    )
    label = "check-a-1of2"
    assert _metrics.put_leg(work, RUN, job=JOB, label=label, trace=trace)[1] == ""
    lines = _metrics.collect(work, repo, RUN, sha="b" * 40)
    assert (
        f"  {JOB}: one shard's row; the platform's folded row carries the durations"
        in lines
    )
    assert _shard.DURATIONS.series("check-a").rows(state_store(work)).rows == ()


def test_collect_finds_a_pull_requests_run_under_the_events_head(
    work: Path, tmp_path: Path
) -> None:
    # The checkout sha is the merge commit; the forge filed the run
    # under the pull request's head, which the run context carries.
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    head = "d" * 40
    fake.push("owner", "repo", "feature", sha=head)
    fake.settle("owner", "repo", head)
    state = fake._repos[("owner", "repo")]
    run_state = next(iter(state.runs.values()))
    run = _ci_context.RunContext(
        "github", str(run_state.id), "pull_request", "refs/pull/1/merge", head
    )
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, run, job="gate", label="gate", trace=trace)[1] == ""
    merge_commit = "e" * 40
    lines = _metrics.collect(work, repo, run, sha=merge_commit)
    assert not any("lists no such run" in line for line in lines)
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(run.run_id)])
    assert entry["sha"] == head and entry["checkout"] == merge_commit
    assert entry["jobs"]["gate"]["wall_ms"] == 30000.0
    assert [step["name"] for step in entry["jobs"]["gate"]["steps"]] == ["gate"]


def test_run_files_sort_by_time() -> None:
    assert _metrics.run_file("9") < _metrics.run_file("10") < _metrics.run_file("1013")
    assert _metrics.run_file("abc") == "abc.json"


# --- the reader ---------------------------------------------------------------


def test_render_says_nothing_yet_on_an_empty_series(work: Path) -> None:
    assert _metrics.render(work) == [
        "  no timing rows yet: the gate job writes one per run"
    ]


def test_render_names_an_unreadable_series(work: Path, tmp_path: Path) -> None:
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (line,) = _metrics.render(work)
    assert line.startswith("  the metrics series could not be read: ")


def _entry(run: int, *, wall: float, check: float) -> str:
    return json.dumps(
        {
            "schema": _metrics.SCHEMA,
            "run": str(run),
            "sha": f"{run:040d}",
            "jobs": {
                "gate": {
                    "wall_ms": wall,
                    "tasks": {"check": check},
                    "steps": [{"name": "Gate", "ms": check}],
                }
            },
        }
    )


def test_render_skips_other_schemas_and_renders_percentiles_and_movers(
    work: Path,
) -> None:
    files = {
        _metrics.run_file(str(n)): _entry(n, wall=1000.0 * n, check=100.0 * n)
        for n in range(1, 11)
    }
    files[_metrics.run_file("11")] = json.dumps({"schema": 2, "run": "11"})
    files[_metrics.run_file("12")] = "{not json"
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""
    lines = _metrics.render(work, since=2, base=5)
    text = "\n".join(lines)
    eleven = _metrics.run_file("11")
    assert f"{eleven}: schema 2, this reader speaks {_metrics.SCHEMA}; skipped" in text
    assert f"{_metrics.run_file('12')}: does not parse; skipped" in text
    assert "10 run(s), latest 10" in text
    assert "  gate" in lines
    wall = next(line for line in lines if line.strip().startswith("wall_ms"))
    # latest 10.0s, p50 over ten runs is the fifth value, p90 the ninth.
    assert wall.split() == ["wall_ms", "10.0s", "5.0s", "9.0s"]
    assert "movers: the last 2 run(s) against the 5 before" in text
    # Recent median (runs 9, 10 -> 9.0s) against the base median (runs 4-8 -> 6.0s).
    assert "    +3.0s  gate: wall_ms (6.0s -> 9.0s)" in lines


def test_collect_measures_a_job_still_running_to_the_collection(
    work: Path, tmp_path: Path
) -> None:
    # The gate job collects while it runs, so its own end and its
    # Verdict step's end are not set yet: both are measured to the
    # collection, the row says it is incomplete, and the report names
    # it. A job the forge has not started carries no times at all.
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    sha = "d" * 40
    fake.push("owner", "repo", "main", sha=sha)
    state = fake._repos[("owner", "repo")]
    run_state = next(iter(state.runs.values()))
    run = _ci_context.RunContext("gitea", str(run_state.id), "push", "refs/heads/main")
    run_state.created_at = "2026-10-11T03:18:09Z"
    run_state.started_at = "2026-10-11T03:18:12Z"
    run_state.completed_at = ""
    run_state.steps = (
        Step(
            "Run actions/checkout@v4",
            "success",
            "2026-10-11T03:18:12Z",
            "2026-10-11T03:18:13Z",
        ),
        Step("Verdict", "", "2026-10-11T03:18:30Z", ""),
        Step("Post", "", "", ""),
    )
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, run, job="gate", label="gate", trace=trace)[1] == ""
    lines = _metrics.collect(work, repo, run, sha=sha)
    assert f"  {_metrics.SERIES.ref}: run {run.run_id} recorded, 1 job(s)" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(run.run_id)])
    job = entry["jobs"]["gate"]
    collected = _metrics._between("2026-10-11T03:18:12Z", entry["collected_at"])
    assert collected is not None and collected > 0
    assert job["complete"] is False
    assert job["queued_ms"] == 3000.0 and job["wall_ms"] == collected
    verdict = _metrics._between("2026-10-11T03:18:30Z", entry["collected_at"])
    assert job["steps"] == [
        {"name": "Run actions/checkout@v4", "ms": 1000.0},
        {"name": "Verdict", "ms": verdict},
        {"name": "Post", "ms": None},
    ]
    text = "\n".join(_metrics.render(work))
    assert (
        "still running when the gate job collected: measured to the collection" in text
    )
    assert not re.search(r"-\d+\.\ds", text)


def test_a_job_the_forge_has_not_started_carries_no_times() -> None:
    from livery.forge import Job

    row = _metrics._forge_row(
        Job(4, "deploy", "queued", "", "", "", ()),
        created="2026-10-11T03:18:09Z",
        now="2026-10-11T03:20:00Z",
    )
    assert row["complete"] is False
    assert row["queued_ms"] is None and row["wall_ms"] is None
    running = _metrics._forge_row(
        Job(3, "gate", "running", "", "2026-10-11T03:19:00Z", "", ()),
        created="2026-10-11T03:18:09Z",
        now="2026-10-11T03:20:00Z",
    )
    assert running["complete"] is False
    assert running["queued_ms"] == 51000.0 and running["wall_ms"] == 60000.0


# --- the run's sections: folded into the run's file, rendered as a trend ------


def test_collect_folds_the_coverage_row_the_gate_left(
    work: Path, tmp_path: Path
) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, RUN, job=JOB, label="check-a", trace=trace)[1] == ""
    # The fallbacks first: no section, and a file that is none, fold nothing.
    assert _metrics.sections(work) == {}
    (work / _metrics.RUN_ROW).mkdir(parents=True)
    (work / _metrics.RUN_ROW / "odd.json").write_text("not json")
    (work / _metrics.RUN_ROW / "bare.json").write_text('{"unit": "%"}')
    assert _metrics.sections(work) == {}
    line = _metrics.leave_section(
        work, "coverage", {"packages/x": 91.23, "packages/y": 100.0}, unit="%"
    )
    assert line == "coverage row: 2 package(s) for the run's record"
    lines = _metrics.collect(work, repo, RUN, sha="b" * 40)
    assert "  coverage: 2 package(s) recorded on the run" in lines
    rows = git.read(state_store(work), _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(RUN.run_id)])
    assert entry["sections"]["coverage"] == {
        "unit": "%",
        "packages": {"packages/x": 91.23, "packages/y": 100.0},
    }


def test_render_shows_the_coverage_rows_when_any_run_carries_one(work: Path) -> None:
    files = {}
    for n in range(1, 4):
        entry: dict[str, object] = {
            "schema": _metrics.SCHEMA,
            "run": str(n),
            "sha": "a" * 40,
            "jobs": {"gate": {"wall_ms": 1000.0 * n}},
        }
        values = {"packages/x": 90.0 + n, "packages/y": 100.0}
        if n > 1:
            entry["sections"] = {"coverage": {"unit": "%", "packages": values}}
        files[_metrics.run_file(str(n))] = json.dumps(entry)
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""
    lines = _metrics.render(work)
    assert "  coverage" in lines
    x = next(line for line in lines if line.strip().startswith("packages/x"))
    assert x.split() == ["packages/x", "93.0%", "92.0%", "93.0%"]
    y = next(line for line in lines if line.strip().startswith("packages/y"))
    assert y.split() == ["packages/y", "100.0%", "100.0%", "100.0%"]
    bare = {_metrics.run_file("9"): json.dumps({"schema": _metrics.SCHEMA, "jobs": {}})}
    assert git.put(state_store(work), _metrics.SERIES.ref, bare, message="bare") == ""
    assert "  coverage" in _metrics.render(work)  # older rows still carry it


# --- a platform's shards in the rendering ------------------------------------


def _run_entry(run: int, jobs: dict[str, dict[str, object]]) -> str:
    return json.dumps(
        {
            "schema": _metrics.SCHEMA,
            "run": str(run),
            "sha": "d" * 40,
            "run_wall_ms": 300000.0,
            "jobs": jobs,
        }
    )


def _leg_entry(
    *, queued: float, wall: float, total: float, shard: dict[str, object] | None = None
) -> dict[str, object]:
    """A leg's row as the collect joins it: its trace half and the forge's times."""
    row: dict[str, object] = {
        "total_ms": total,
        "tasks": {"check": total},
        "waits_ms": {},
        "packages": {"a": {"tests_ms": 1500.0, "tests": 5}},
        "slowest": [],
        "scope": {"scope": "full", "packages": [], "leg": "check-ubuntu-latest-3.14"},
        "conclusion": "success",
        "complete": True,
        "queued_ms": queued,
        "wall_ms": wall,
        "steps": [{"name": "Gate", "ms": 260000.0}],
    }
    if shard is not None:
        row["shard"] = shard
    return row


def _forge_entry(*, queued: float, wall: float) -> dict[str, object]:
    """A job's row from the forge's times alone, as a shard's or the gate's is."""
    return {
        "conclusion": "success",
        "status": "completed",
        "complete": True,
        "queued_ms": queued,
        "wall_ms": wall,
        "steps": [],
    }


def test_render_of_an_unsharded_platform_is_one_section_per_job(work: Path) -> None:
    jobs = {
        JOB: _leg_entry(queued=10000.0, wall=290000.0, total=250000.0),
        "gate": _forge_entry(queued=5000.0, wall=60000.0),
    }
    files = {_metrics.run_file("1013"): _run_entry(1013, jobs)}
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""
    expected = """\
  1 run(s), latest 1013 at dddddddddddd
  check (ubuntu-latest, 3.14)
    metric                                          latest       p50       p90
    queued_ms                                        10.0s     10.0s     10.0s
    step Gate                                       260.0s    260.0s    260.0s
    task check                                      250.0s    250.0s    250.0s
    tests a                                           1.5s      1.5s      1.5s
    total_ms                                        250.0s    250.0s    250.0s
    wall_ms                                         290.0s    290.0s    290.0s
  gate
    metric                                          latest       p50       p90
    queued_ms                                         5.0s      5.0s      5.0s
    wall_ms                                          60.0s     60.0s     60.0s
  run
    metric                                          latest       p50       p90
    wall_ms                                         300.0s    300.0s    300.0s
"""
    assert "\n".join(_metrics.render(work)) + "\n" == expected


def test_render_folds_a_platforms_shards_into_its_section_with_a_line_per_shard(
    work: Path,
) -> None:
    """The folded row is the platform's section; each shard is a line under it.

    The shards' own rows carry the forge's times and no scope: they
    are no sections of their own, and no mover names one. A shard the
    run's file has no forge row for shows no times.
    """
    shards = [
        {
            "shard": f"{index}of3",
            "leg": f"check-ubuntu-latest-3.14-{index}of3",
            "job": f"check (ubuntu-latest, 3.14, {index}of3)",
            "when": "",
            "files": 2,
            "total_ms": 250000.0,
            "packages": {},
        }
        for index in (1, 2, 3)
    ]
    folded = {
        "count": 3,
        "digest": "abc",
        "rows": ["r1.json"],
        "before": "2026-10-17T09:30:00+00:00",
        "shards": shards,
    }
    jobs = {
        JOB: _leg_entry(queued=10000.0, wall=290000.0, total=250000.0, shard=folded),
        "check (ubuntu-latest, 3.14, 1of3)": _forge_entry(
            queued=10000.0, wall=290000.0
        ),
        "check (ubuntu-latest, 3.14, 2of3)": _forge_entry(
            queued=12000.0, wall=200000.0
        ),
        "gate": _forge_entry(queued=5000.0, wall=60000.0),
    }
    files = {_metrics.run_file(str(n)): _run_entry(n, jobs) for n in (1, 2)}
    assert git.put(state_store(work), _metrics.SERIES.ref, files, message="rows") == ""
    lines = _metrics.render(work, since=1, base=5)
    assert "  check (ubuntu-latest, 3.14)" in lines
    assert [line for line in lines if "of3" in line] == [
        "    shard 1of3: wall 290.0s, queued 10.0s",
        "    shard 2of3: wall 200.0s, queued 12.0s",
        "    shard 3of3: wall -, queued -",
    ]
    # Under the platform's own metrics, before the next section.
    at = lines.index("    shard 1of3: wall 290.0s, queued 10.0s")
    assert lines[at - 1].startswith("    wall_ms")
    assert lines[at + 3] == "  gate"
    assert "  movers: the last 1 run(s) against the 1 before" in lines
