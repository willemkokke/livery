"""The timing rows: refusals first, then the join and the reader.

The store side runs against a bare repository as origin, like the
store's own tests; the forge side is the fake, whose private run
state is the assertion surface, by design.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.forge import Step
from livery.forge.testing import FakeForge
from livery.workshop import _metrics, _state

RUN = _state.RunContext("gitea", "1013", "push", "refs/heads/main")
JOB = "check (ubuntu-latest, 3.14)"


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
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)


def _trace(
    path: Path, *, tasks: dict[str, float], tests: dict[str, float] | None = None
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
    why = _metrics.put_leg(
        work, RUN, job=JOB, label="check-a", trace=tmp_path / "none.json"
    )
    assert "did not run under --profile" in why
    assert _state.list_refs(work, _state.RUN_PREFIX) == {}


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
    assert row["schema"] == _metrics.SCHEMA and row["job"] == JOB
    assert row["tasks"] == {"check": 4640.0, "check/test/test": 2030.0}
    assert row["waits_ms"] == {"serial": 250.0}
    # Every phase counts toward the package's time; only calls count tests.
    assert row["packages"] == {
        "forge": {"tests_ms": 90.0, "tests": 2},
        "workshop": {"tests_ms": 15.0, "tests": 1},
    }
    assert row["total_ms"] == 4640.0


# --- the collect: refusals first ----------------------------------------------


def test_collect_says_so_when_no_leg_left_a_row(work: Path) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    lines = _metrics.collect(work, repo, RUN, sha="a" * 40)
    assert lines == [f"  run {RUN.run_id}: no leg left a row; nothing collected"]
    assert _state.read(work, _metrics.SERIES.ref).files is None


def test_collect_skips_a_row_of_another_schema_and_names_it(work: Path) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    ref = _state.run_ref(RUN, "check-a")
    assert (
        _state.put(
            work,
            ref,
            {_metrics.ROW_FILE: json.dumps({"schema": 99, "job": JOB})},
            message="m",
        )
        == ""
    )
    lines = _metrics.collect(work, repo, RUN, sha="a" * 40)
    assert any(
        line.startswith(f"  {ref}: schema 99, this reader speaks {_metrics.SCHEMA}")
        for line in lines
    )
    assert lines[-1] == f"  run {RUN.run_id}: no leg left a row; nothing collected"
    # The skipped half is kept: nothing landed, so nothing is dropped.
    assert _state.read(work, ref).files is not None


def test_collect_names_a_job_the_forge_does_not_know(
    work: Path, tmp_path: Path
) -> None:
    fake = FakeForge()
    fake.create_repo("owner", "repo")
    repo = fake.repository("owner", "repo")
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, RUN, job=JOB, label="check-a", trace=trace) == ""
    lines = _metrics.collect(work, repo, RUN, sha="b" * 40)
    assert f"  run {RUN.run_id}: the forge lists no such run" in "\n".join(lines)
    assert f"  {JOB}: unknown to the forge; its trace half rides alone" in lines
    rows = _state.read(work, _metrics.SERIES.ref).files
    assert rows is not None and list(rows) == [_metrics.run_file(RUN.run_id)]
    entry = json.loads(rows[_metrics.run_file(RUN.run_id)])
    assert entry["jobs"][JOB]["tasks"] == {"check": 100.0}
    assert "wall_ms" not in entry["jobs"][JOB]
    # Collected halves are dropped; the series file is the record now.
    assert _state.list_refs(work, _state.RUN_PREFIX) == {}


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
    run = _state.RunContext("gitea", str(run_state.id), "push", "refs/heads/main")
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
    trace = _trace(tmp_path / "t.json", tasks={"check": 4100.0})
    assert _metrics.put_leg(work, run, job="gate", label="gate", trace=trace) == ""
    lines = _metrics.collect(work, repo, run, sha=sha)
    assert f"  {_metrics.SERIES.ref}: run {run.run_id} recorded, 1 job(s)" in lines
    assert f"  {_state.run_ref(run, 'gate')}: dropped" in lines
    rows = _state.read(work, _metrics.SERIES.ref).files
    assert rows is not None
    entry = json.loads(rows[_metrics.run_file(run.run_id)])
    job = entry["jobs"]["gate"]
    assert job["queued_ms"] == 3000.0 and job["wall_ms"] == 26000.0
    assert job["steps"] == [
        {"name": "Run actions/checkout@v4", "ms": 1000.0},
        {"name": "Gate", "ms": 5000.0},
    ]
    assert job["tasks"] == {"check": 4100.0}
    assert entry["created_at"] == "2026-09-09T00:49:13Z"
    assert _state.list_refs(work, _state.RUN_PREFIX) == {}


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
    run = _state.RunContext(
        "github", str(run_state.id), "pull_request", "refs/pull/1/merge", head
    )
    trace = _trace(tmp_path / "t.json", tasks={"check": 100.0})
    assert _metrics.put_leg(work, run, job="gate", label="gate", trace=trace) == ""
    merge_commit = "e" * 40
    lines = _metrics.collect(work, repo, run, sha=merge_commit)
    assert not any("lists no such run" in line for line in lines)
    rows = _state.read(work, _metrics.SERIES.ref).files
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
    assert line.startswith(f"  {_metrics.SERIES.ref}: could not be read")


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
    assert _state.put(work, _metrics.SERIES.ref, files, message="rows") == ""
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
