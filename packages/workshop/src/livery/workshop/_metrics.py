"""Timing rows on the CI state store, and the reader that renders them.

One row per job per run, one file per run on the ``metrics`` series
of the state store ([livery.git][]), named by zero-padded
run id so that name order is time order. A row is end to end, not
gate-only: the queue wait, the job's wall, and every step's wall as
the forge times them, beside the per-task durations and lane waits
the leg's own trace recorded, and the test time per package summed
from the trace's pytest slices.

Two writers, both inside CI. Each check leg ends with `put_leg`: it
reads the trace the profiled gate wrote and puts the task half of
its row on the leg's per-run ref, in one write with every file the
leg's other entries left in its spool (`LEG_SPOOL`); a write that
fails is a red leg. The run's gate job runs `collect`: it reads the
per-run refs, asks the forge for the run's jobs, joins the two
halves, folds in the sections the job's judges left (`RUN_ROW`),
puts the run's file on the series under its window, and drops the
per-run refs; it fails open loudly, a printed reason and never a
red job. Local runs read: `render` is what `fm ci.timings` prints,
the latest value, p50, and p90 per metric over the window, the
movers against a base window, and each section's trend.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery import git
from livery.workshop._ci_context import RunContext, state_store

if TYPE_CHECKING:
    from livery.forge import Job, Repository

#: The rows' schema. A reader skips a row of another version and
#: names it; a new field is the same version, a changed meaning is
#: the next.
SCHEMA = 1

#: The series the gate job writes: one file per run, the newest 300
#: kept. Three hundred runs at a few kilobytes each keeps the tree
#: under a megabyte and a quarter's trend in reach.
SERIES = git.Series("metrics", window=300, schema=SCHEMA)

#: The per-run family: one series per run and check leg, keyed
#: ``(run, leg)``, holding the leg's half of its timing row, and beside
#: it every file the leg's entries left for its one write, until the
#: run's gate job collects the halves and drops the refs, those files
#: included. A half older than six hours outlived a run that was
#: cancelled or died before its gate job could collect, and the janitor
#: drops it: no run lasts that long, and the forge has no run-by-id
#: lookup to ask.
RUNS: git.Keyed = git.Keyed(
    "run", ("run", "leg"), schema=SCHEMA, stale_after=timedelta(hours=6)
)

#: The leg's own file on its per-run ref: the task half of its row.
ROW_FILE = "row.json"

#: Where a check leg's entries leave the files its one write puts on
#: its per-run ref beside the row, one JSON object per file, each put
#: under its own name.
LEG_SPOOL = ".workshop/leg"

#: Where the gate job's entries leave the sections the run's file
#: carries beside the timings, one file per section, named after it:
#: a number per package, which `fm ci.timings` renders as a trend.
RUN_ROW = ".workshop/run-row"

#: The slowest tests a leg's row names, by their call phase.
SLOWEST = 20


def run_ref(run: RunContext, leg: str) -> str:
    """The per-run ref one *leg* of *run* writes."""
    return RUNS.series(run.run_id, leg).ref


def leave_leg_file(root: Path, name: str, data: Mapping[str, Any]) -> Path:
    """Leave *data* in the leg's spool as *name*, for the leg's one write.

    The leg's last entry, ``ci.leg``, puts every file the spool holds
    on the leg's per-run ref, a series of [livery.workshop.RUNS][],
    beside its timing row and under its own name, where the gate job
    reads it. That write refuses a file named ``row.json``, the timing
    row's own name. A file left twice under one name keeps the last
    *data*.

    Returns:
        The path of the file left under *root*.
    """
    spool = root / LEG_SPOOL
    spool.mkdir(parents=True, exist_ok=True)
    path = spool / name
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    return path


def leg_files(root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """The files the leg's entries left in its spool, by name; or why one cannot ride.

    A file that is not a JSON object, or one named as the leg's own
    row, refuses by name: the write would carry what no reader takes.
    """
    found: dict[str, dict[str, Any]] = {}
    spool = root / LEG_SPOOL
    if not spool.is_dir():
        return found, ""
    for path in sorted(spool.glob("*.json")):
        if path.name == ROW_FILE:
            return (
                {},
                f"{LEG_SPOOL}/{ROW_FILE} is the leg's own row; name the file otherwise",
            )
        try:
            loaded = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as error:
            return {}, f"{LEG_SPOOL}/{path.name} does not parse ({error})"
        if not isinstance(loaded, dict):
            return {}, f"{LEG_SPOOL}/{path.name} is not a JSON object"
        found[path.name] = loaded
    return found, ""


def leave_section(
    root: Path, name: str, packages: Mapping[str, float], *, unit: str = ""
) -> str:
    """Leave a section of the run's row, a number per package, for the gate job.

    The gate job's ``ci.metrics.collect`` entry, before its verdict,
    folds every section left at *root* into the run's file on the
    metrics series, and ``fm ci.timings`` renders each as a trend, the
    numbers followed by *unit*. A section left twice under one *name*
    keeps the last numbers.

    Returns:
        The line to print, naming the section and how many packages
        it holds.
    """
    directory = root / RUN_ROW
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps({"unit": unit, "packages": dict(sorted(packages.items()))}),
        encoding="utf-8",
    )
    return f"{name} row: {len(packages)} package(s) for the run's record"


def sections(root: Path) -> dict[str, dict[str, Any]]:
    """The sections the gate job's entries left, by name; a malformed file is left out.

    A section is its unit and a number per package.
    """
    found: dict[str, dict[str, Any]] = {}
    directory = root / RUN_ROW
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        try:
            loaded = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        section = _section(loaded)
        if section is not None:
            found[path.stem] = section
    return found


def _section(raw: Any) -> dict[str, Any] | None:
    """*raw* as a section, its unit and a number per package; None when it is none."""
    if not isinstance(raw, dict):
        return None
    packages = raw.get("packages")
    if not isinstance(packages, dict):
        return None
    numbers = {
        str(name): float(value)
        for name, value in packages.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    return {"unit": str(raw.get("unit", "") or ""), "packages": numbers}


def _ms(event: dict[str, Any]) -> float:
    return round(float(event.get("dur", 0.0)) / 1000.0, 1)


def _track(event: dict[str, Any]) -> int:
    return int(event.get("tid", 0))


def _start(event: dict[str, Any]) -> float:
    return float(event.get("ts", 0.0))


def _end(event: dict[str, Any]) -> float:
    return _start(event) + float(event.get("dur", 0.0))


def _steps_by_task(
    spans: list[tuple[str, int, float, float]],
    ran: list[tuple[str, int, float, float, float]],
) -> dict[str, float]:
    """What each task's own steps took, by `<task>/<step title>`.

    A step carries no task of its own: the trace nests it under the
    task it ran in, on that task's track and inside its span, which is
    how the timeline draws it. So a step belongs to the innermost task
    containing it on its own track, and a step on another track is not
    that task's however their times overlap. A title a task ran twice
    is one key and their sum, since the question a key answers is what
    the task spent on that command.

    A step no task contains has no address and is left out; the trace
    writes one only inside a task, so this is a trace that is not the
    shape it claims.
    """
    by_key: dict[str, float] = {}
    for name, track, start, end, ms in ran:
        holding = [
            span
            for span in spans
            if span[1] == track and span[2] <= start and end <= span[3]
        ]
        if not holding:
            continue
        task = min(holding, key=lambda span: span[3] - span[2])[0]
        key = f"{task}/{name}"
        by_key[key] = round(by_key.get(key, 0.0) + ms, 1)
    return by_key


def _package_of(nodeid: str) -> str:
    """The member a test node is of: ``forge``, ``extensions/widgets``, or empty."""
    from livery.workshop._pytest_speed import package_of

    return package_of(nodeid).removeprefix("packages/")


def leg_row(trace: Path, *, job: str) -> tuple[dict[str, Any] | None, str]:
    """The task half of *job*'s row from its trace; ``(row, "")`` or ``(None, reason)``.

    The trace is the Chrome trace the profiled gate wrote: every
    task's slice (its request address and duration), the lane waits,
    and each pytest phase of every test as a slice of its own. The
    row's ``files`` is the seconds each test file took, every phase of
    every test in it summed, to a tenth. A missing or unreadable trace
    is a reason, never a row.
    """
    if not trace.is_file():
        return None, f"no trace at {trace}: the gate did not run under --profile"
    try:
        loaded = json.loads(trace.read_text("utf-8"))
    except (OSError, ValueError) as error:
        return None, f"the trace at {trace} does not parse: {error}"
    events = loaded.get("traceEvents") if isinstance(loaded, dict) else loaded
    if not isinstance(events, list):
        return None, f"the trace at {trace} carries no event list"
    tasks: dict[str, float] = {}
    spans: list[tuple[str, int, float, float]] = []
    ran: list[tuple[str, int, float, float, float]] = []
    waits: dict[str, float] = defaultdict(float)
    packages: dict[str, dict[str, float]] = defaultdict(
        lambda: {"tests_ms": 0.0, "tests": 0}
    )
    # Every phase of every test, summed per test file: what a shard
    # partitions the next run's files by.
    files: dict[str, float] = defaultdict(float)
    calls: list[tuple[float, str]] = []
    begins: list[float] = []
    ends: list[float] = []
    for event in events:
        if not isinstance(event, dict) or event.get("ph") != "X":
            continue
        cat = str(event.get("cat", ""))
        name = str(event.get("name", ""))
        if event.get("pid") == 1:
            begins.append(float(event.get("ts", 0.0)))
            ends.append(float(event.get("ts", 0.0)) + float(event.get("dur", 0.0)))
        if cat == "task":
            tasks[name] = _ms(event)
            spans.append((name, _track(event), _start(event), _end(event)))
        elif cat == "step":
            ran.append((name, _track(event), _start(event), _end(event), _ms(event)))
        elif cat == "lane":
            waits[name.removeprefix("lane: ")] += _ms(event)
        elif cat.startswith("test."):
            files[name.split("::", 1)[0]] += _ms(event)
            package = _package_of(name)
            if package:
                packages[package]["tests_ms"] = round(
                    packages[package]["tests_ms"] + _ms(event), 1
                )
                if cat == "test.call":
                    packages[package]["tests"] += 1
                    calls.append((_ms(event), name))
    if not tasks:
        return None, f"the trace at {trace} records no task: nothing to row"
    steps = _steps_by_task(spans, ran)
    total = round((max(ends) - min(begins)) / 1000.0, 1) if begins else 0.0
    # The slowest tests by their call phase, for the speed judge's
    # warning to name; the per-package sums count every phase.
    calls.sort(reverse=True)
    return {
        "job": job,
        "total_ms": total,
        "tasks": dict(sorted(tasks.items())),
        "task_steps": dict(sorted(steps.items())),
        "waits_ms": {name: round(ms, 1) for name, ms in sorted(waits.items())},
        "packages": dict(sorted(packages.items())),
        "files": {path: round(ms / 1000.0, 1) for path, ms in sorted(files.items())},
        "slowest": [
            {"test": name, "s": round(ms / 1000.0, 2)} for ms, name in calls[:SLOWEST]
        ],
    }, ""


def put_leg(
    root: Path, run: RunContext, *, job: str, label: str, trace: Path
) -> tuple[list[str], str]:
    """Put *job*'s task half and the leg's spool on its per-run ref, in one write.

    Returns the lines to print and why the write failed, ``""`` when
    it landed or there was nothing to write. The row is read from
    *trace* ([livery.workshop._metrics.leg_row][]), with the scope the
    gate ran; a trace that cannot be read leaves the row out, says
    why, and the spool's files go alone. Every file the leg's entries
    left in `LEG_SPOOL` rides beside the row under its own name
    ([livery.workshop._metrics.leg_files][]), so the leg writes its
    ref once. A leg without a label, and a spool file no reader takes,
    fail the write by name.
    """
    lines: list[str] = []
    if not label:
        return lines, "the leg has no label, so its row has no key"
    files: dict[str, Mapping[str, Any]] = {}
    row, why = leg_row(trace, job=job)
    if row is None:
        lines.append(f"  {job}: {why}; the timing row stays unwritten")
    else:
        from dataclasses import asdict

        from livery.workshop._verified import leg_scope

        # The scope the gate ran, from the marker it left beside the
        # trace: the stamp after the verdict reads it back per leg.
        scope = leg_scope(trace.parent if trace.is_absolute() else root)
        row["scope"] = {**asdict(scope), "packages": list(scope.packages)}
        files[ROW_FILE] = row
    spooled, why = leg_files(root)
    if why:
        return lines, why
    files.update(spooled)
    if not files:
        return lines, ""
    why = RUNS.series(run.run_id, label).put(
        state_store(root), files, message=f"metrics: {job} of run {run.run_id}"
    )
    if why:
        return lines, why
    lines.append(f"  {label}: {', '.join(sorted(files))} on run {run.run_id}'s ref")
    return lines, ""


def _moment(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _between(start: str, end: str) -> float | None:
    """Milliseconds from *start* to *end*, or ``None`` when either is unknown."""
    a, b = _moment(start), _moment(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 1)


def run_file(run_id: str) -> str:
    """The series file for *run_id*: zero-padded when numeric, so names sort by time."""
    return f"{int(run_id):020d}.json" if run_id.isdigit() else f"{run_id}.json"


def _times(job: Job, *, created: str, now: str) -> dict[str, Any]:
    """A job's queue, wall and step times from the forge's stamps.

    A job still running when the gate job collects (the gate job is
    always one) is measured to *now*, its running step too, and marked
    incomplete, so the row reads as a real duration with its end named
    rather than as a time the forge never set. A job the forge has not
    started carries no queue or wall time.
    """
    started = job.started_at
    return {
        "complete": bool(job.completed_at),
        "queued_ms": _between(created, started) if started else None,
        "wall_ms": _between(started, job.completed_at or now) if started else None,
        "steps": [
            {
                "name": step.name,
                "ms": (
                    _between(step.started_at, step.completed_at or now)
                    if step.started_at
                    else None
                ),
            }
            for step in job.steps
        ],
    }


def _forge_row(job: Job, *, created: str, now: str) -> dict[str, Any]:
    """A job's times as the forge reports them, for a job that left no trace.

    The docs build, the merge point's jobs, and a gate job that ran
    without a profile have no leg row; their queue and wall times come
    from the forge alone, measured as [livery.workshop._metrics._times][]
    measures every job's.
    """
    return {
        "conclusion": job.conclusion,
        "status": job.status,
        **_times(job, created=created, now=now),
    }


def collect(root: Path, repo: Repository, run: RunContext, *, sha: str) -> list[str]:
    """Join the legs' halves with the forge's times into the run's file; what happened.

    Every line names what was done or why not. Nothing is written
    when no leg left a row, and a per-run ref is dropped only after
    the run's file landed and while no completed job is red: a failed
    put or a red sibling job keeps the halves, since the forge's
    re-run of the failed jobs runs the gate again without its legs,
    and that union reads what the first attempt left. A job the forge
    does not know keeps its trace half and
    is named; a row of another schema is skipped and named. The run
    is looked up under the head the runner's event names, because a
    pull request's checkout is a merge commit the forge never files
    a run under; *sha* is the checkout, the fallback and the row's
    own record. Every job the forge lists is recorded, the legs with
    their traces, the others with their forge times alone, and the
    run's own wall from its start to this collection.
    """
    lines: list[str] = []
    keys = RUNS.listed(state_store(root), run.run_id)
    if keys is None:
        return [
            f"  {RUNS.prefix}{run.run_id}/*: the remote could not be listed;"
            " nothing collected"
        ]
    halves: dict[str, dict[str, Any]] = {}
    refs: list[str] = []
    for key in keys:
        series = RUNS.series(*key)
        refs.append(series.ref)
        found, why = series.row(state_store(root), ROW_FILE)
        if why:
            lines.append(f"  {series.ref}: {why}; skipped")
            continue
        if found is None:
            lines.append(f"  {series.ref}: empty; skipped")
            continue
        halves[str(found.data.get("job", key[-1]))] = found.data
    if not halves:
        lines.append(f"  run {run.run_id}: no leg left a row; nothing collected")
        return lines
    # The run by its own id among the runs for this sha; a forge that
    # numbers the run differently from the environment (a pull
    # request's merge sha, say) still yields the newest run for the
    # sha, named as a substitution.
    head = run.head_sha or sha
    runs = repo.checks.runs(head_sha=head)
    forge_run = next((r for r in runs if str(r.id) == run.run_id), None)
    if forge_run is None and runs:
        forge_run = runs[0]
        lines.append(
            f"  run {run.run_id}: not among the forge's runs for {head[:12]};"
            f" using the newest, run {forge_run.id}"
        )
    jobs = (
        {job.name: job for job in repo.checks.jobs(forge_run.id)} if forge_run else {}
    )
    if forge_run is None:
        lines.append(
            f"  run {run.run_id}: the forge lists no such run for {head[:12]};"
            " the rows carry their trace halves alone"
        )
    entry: dict[str, Any] = {
        "forge": run.forge,
        "run": run.run_id,
        "workflow": forge_run.workflow if forge_run else "",
        "event": run.event,
        "sha": head,
        "checkout": sha,
        "ref": run.ref,
        "created_at": forge_run.created_at if forge_run else "",
        "started_at": forge_run.started_at if forge_run else "",
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "jobs": {},
    }
    for name, half in sorted(halves.items()):
        job = jobs.get(name)
        row: dict[str, Any] = {
            "total_ms": half.get("total_ms"),
            "tasks": half.get("tasks", {}),
            "task_steps": half.get("task_steps", {}),
            "waits_ms": half.get("waits_ms", {}),
            "packages": half.get("packages", {}),
            "slowest": half.get("slowest", []),
            "scope": half.get("scope", {"scope": "unknown", "packages": []}),
        }
        if job is None:
            lines.append(f"  {name}: unknown to the forge; its trace half rides alone")
        else:
            row["conclusion"] = job.conclusion
            row.update(
                _times(job, created=entry["created_at"], now=entry["collected_at"])
            )
        entry["jobs"][name] = row
    for name, job in sorted(jobs.items()):
        if name not in entry["jobs"]:
            entry["jobs"][name] = _forge_row(
                job, created=entry["created_at"], now=entry["collected_at"]
            )
    entry["run_wall_ms"] = (
        _between(entry["started_at"], entry["collected_at"])
        if entry["started_at"]
        else None
    )
    left = sections(root)
    if left:
        entry["sections"] = left
        for name, section in left.items():
            lines.append(
                f"  {name}: {len(section['packages'])} package(s) recorded on the run"
            )
    why = SERIES.put(
        state_store(root),
        {run_file(run.run_id): entry},
        message=f"metrics: run {run.run_id} at {sha[:12]}",
    )
    if why:
        lines.append(f"  {SERIES.ref}: {why}; the per-run refs stay for a re-run")
        return lines
    lines.append(
        f"  {SERIES.ref}: run {run.run_id} recorded, {len(entry['jobs'])} job(s)"
    )
    red = sorted(
        f"{name}: {job.conclusion}"
        for name, job in jobs.items()
        if job.status == "completed" and job.conclusion not in ("success", "skipped")
    )
    if red:
        lines.append(
            "  the per-run refs stay for a re-run of the gate: " + ", ".join(red)
        )
        return lines
    for ref in sorted(refs):
        gone = git.drop(state_store(root), ref)
        lines.append(f"  {ref}: {gone or 'dropped'}")
    return lines


def _percentile(values: list[float], fraction: float) -> float:
    """The nearest-rank percentile of *values*: the value at rank ceil(p n)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _metrics(entry: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Every numeric metric of a run's file, by job then metric name.

    The run's own wall, start to collection, renders as the ``run``
    group, so the floor the legs never touch has a row of its own.
    """
    out: dict[str, dict[str, float]] = {}
    wall = entry.get("run_wall_ms")
    if isinstance(wall, int | float):
        out["run"] = {"wall_ms": float(wall)}
    for name, row in sorted(entry.get("jobs", {}).items()):
        metrics: dict[str, float] = {}
        for key in ("queued_ms", "wall_ms", "total_ms"):
            value = row.get(key)
            if isinstance(value, int | float):
                metrics[key] = float(value)
        for task, ms in row.get("tasks", {}).items():
            metrics[f"task {task}"] = float(ms)
        for step in row.get("steps", []):
            if isinstance(step.get("ms"), int | float):
                metrics[f"step {step['name']}"] = float(step["ms"])
        # The job's steps above are the workflow's; these are the ones a
        # task ran, addressed under it, so a checker that moved is named
        # rather than left inside its task's total.
        for key, ms in row.get("task_steps", {}).items():
            metrics[f"step {key}"] = float(ms)
        for package, data in row.get("packages", {}).items():
            metrics[f"tests {package}"] = float(data.get("tests_ms", 0.0))
        out[name] = metrics
    return out


def render(
    root: Path, *, since: int = 1, base: int = 20, movers: int = 10
) -> list[str]:
    """The timings, rendered: per job and metric the latest, p50, p90, and the movers.

    The window is every run the series holds. The latest *since* runs
    are the recent side; the *base* runs before them are the base the
    movers compare against, by the change of the recent median over
    the base median. Nothing yet, an unreadable series, and a row of
    another schema each say so.
    """
    found = SERIES.rows(state_store(root))
    if found.failed:
        return [f"  {found.reason}"]
    if not found.rows and not found.skipped:
        return ["  no timing rows yet: the gate job writes one per run"]
    lines = [f"  {line}" for line in found.skipped]
    # Oldest first: the trend reads the newest run at the end.
    entries = [row.data for row in reversed(found.rows)]
    if not entries:
        lines.append("  no readable timing rows")
        return lines
    newest = entries[-1]
    lines.append(
        f"  {len(entries)} run(s), latest {newest.get('run')}"
        f" at {str(newest.get('sha', ''))[:12]}"
    )
    recent, older = entries[-since:], entries[:-since]
    base_entries = older[-base:] if base else older
    series: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for entry in entries:
        for job, metrics in _metrics(entry).items():
            for metric, value in metrics.items():
                series[job][metric].append(value)
    latest = _metrics(entries[-1])
    for job in sorted(series):
        lines.append(f"  {job}")
        if newest.get("jobs", {}).get(job, {}).get("complete") is False:
            lines.append(
                "    still running when the gate job collected: measured to the"
                " collection"
            )
        lines.append(f"    {'metric':<44} {'latest':>9} {'p50':>9} {'p90':>9}")
        for metric, values in sorted(series[job].items()):
            now = latest.get(job, {}).get(metric)
            shown = f"{now / 1000:.1f}s" if now is not None else "-"
            lines.append(
                f"    {metric[:44]:<44} {shown:>9}"
                f" {_percentile(values, 0.5) / 1000:8.1f}s"
                f" {_percentile(values, 0.9) / 1000:8.1f}s"
            )
    if base_entries and recent:
        deltas: list[tuple[float, str, str, float, float]] = []
        recent_series: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        base_series: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for entry in recent:
            for job, metrics in _metrics(entry).items():
                for metric, value in metrics.items():
                    recent_series[job][metric].append(value)
        for entry in base_entries:
            for job, metrics in _metrics(entry).items():
                for metric, value in metrics.items():
                    base_series[job][metric].append(value)
        for job, per_metric in recent_series.items():
            for metric, recent_values in per_metric.items():
                before = base_series.get(job, {}).get(metric)
                if not before:
                    continue
                now, then = _percentile(recent_values, 0.5), _percentile(before, 0.5)
                deltas.append((now - then, job, metric, now, then))
        deltas.sort(key=lambda d: -abs(d[0]))
        lines.append(
            f"  movers: the last {len(recent)} run(s) against the"
            f" {len(base_entries)} before"
        )
        for delta, job, metric, now, then in deltas[:movers]:
            sign = "+" if delta >= 0 else "-"
            lines.append(
                f"    {sign}{abs(delta) / 1000:.1f}s  {job}: {metric}"
                f" ({then / 1000:.1f}s -> {now / 1000:.1f}s)"
            )
    lines.extend(_render_sections(entries))
    return lines


def _sections_of(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The sections *entry* carries, by name, each its unit and its numbers per package.

    A row without ``sections`` carries none.
    """
    found: dict[str, dict[str, Any]] = {}
    raw = entry.get("sections")
    if isinstance(raw, dict):
        for name, value in raw.items():
            section = _section(value)
            if section is not None:
                found[str(name)] = section
    return found


def _render_sections(entries: list[dict[str, Any]]) -> list[str]:
    """Each section's rows: per package the latest, p50, and p90 number."""
    series: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    latest: dict[str, dict[str, float]] = defaultdict(dict)
    units: dict[str, str] = {}
    for entry in entries:
        for name, section in _sections_of(entry).items():
            units[name] = section["unit"]
            for package, value in sorted(section["packages"].items()):
                series[name][package].append(value)
                latest[name][package] = value
    lines: list[str] = []
    for name in sorted(series):
        unit = units[name]
        lines += [
            f"  {name}",
            f"    {'package':<44} {'latest':>9} {'p50':>9} {'p90':>9}",
        ]
        for package, values in sorted(series[name].items()):
            lines.append(
                f"    {package[:44]:<44} {latest[name][package]:8.1f}{unit}"
                f" {_percentile(values, 0.5):8.1f}{unit}"
                f" {_percentile(values, 0.9):8.1f}{unit}"
            )
    return lines
