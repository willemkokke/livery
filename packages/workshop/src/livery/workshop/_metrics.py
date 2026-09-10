"""Timing rows on the CI state store, and the reader that renders them.

One row per job per run, one file per run on the ``metrics`` series
of the state store ([livery.workshop._state][]), named by zero-padded
run id so that name order is time order. A row is end to end, not
gate-only: the queue wait, the job's wall, and every step's wall as
the forge times them, beside the per-task durations and lane waits
the leg's own trace recorded, and the test time per package summed
from the trace's pytest slices.

Two writers, both inside CI. Each check leg ends with `put_leg`: it
reads the trace the profiled gate wrote and puts the task half of
its row on the leg's per-run ref. The run's gate job runs `collect`:
it reads the per-run refs, asks the forge for the run's jobs, joins
the two halves, puts the run's file on the series under its window,
and drops the per-run refs. Both fail open loudly, a printed reason
and never a red leg. Local runs read: `render` is what `fm
ci.timings` prints, the latest value, p50, and p90 per metric over
the window, and the movers against a base window.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.workshop._state import Keyed, RunContext, Series, drop

if TYPE_CHECKING:
    from livery.forge import Job, Repository

#: The rows' schema. A reader skips a row of another version and
#: names it; a new field is the same version, a changed meaning is
#: the next.
SCHEMA = 1

#: The series the gate job writes: one file per run, the newest 300
#: kept. Three hundred runs at a few kilobytes each keeps the tree
#: under a megabyte and a quarter's trend in reach.
SERIES = Series("metrics", window=300, schema=SCHEMA)

#: The per-run family: one series per run and check leg, holding the
#: leg's half of its row until the run's gate job collects it.
RUNS = Keyed("run", ("run", "leg"), schema=SCHEMA)

#: The one file a leg puts on its per-run ref.
ROW_FILE = "row.json"

#: The union's per-package percentages, left by the gate job's
#: coverage entry for the collect entry to fold into the run's file.
COVERAGE_ROW = "fm-coverage.json"


def run_ref(run: RunContext, leg: str) -> str:
    """The per-run ref one *leg* of *run* writes."""
    return RUNS.series(run.run_id, leg).ref


def write_coverage_row(root: Path, measured: dict[str, float]) -> str:
    """Leave the union's percentages beside the trace; the line to print."""
    (root / COVERAGE_ROW).write_text(
        json.dumps({path: round(value, 2) for path, value in sorted(measured.items())}),
        encoding="utf-8",
    )
    return f"coverage row: {len(measured)} package(s) for the run's record"


def read_coverage_row(root: Path) -> dict[str, float] | None:
    """The union's percentages the gate job left, or ``None`` when it left none."""
    path = root / COVERAGE_ROW
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    out: dict[str, float] = {}
    for name, value in loaded.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[str(name)] = float(value)
    return out


def _ms(event: dict[str, Any]) -> float:
    return round(float(event.get("dur", 0.0)) / 1000.0, 1)


def _package_of(nodeid: str) -> str:
    """The workspace package a test node belongs to, or ``""``."""
    parts = nodeid.split("::", 1)[0].split("/")
    if len(parts) >= 2 and parts[0] == "packages":
        return parts[1]
    return ""


def leg_row(trace: Path, *, job: str) -> tuple[dict[str, Any] | None, str]:
    """The task half of *job*'s row from its trace; ``(row, "")`` or ``(None, reason)``.

    The trace is the Chrome trace the profiled gate wrote: every
    task's slice (its request address and duration), the lane waits,
    and each pytest phase of every test as a slice of its own. A
    missing or unreadable trace is a reason, never a row.
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
    waits: dict[str, float] = defaultdict(float)
    packages: dict[str, dict[str, float]] = defaultdict(
        lambda: {"tests_ms": 0.0, "tests": 0}
    )
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
        elif cat == "lane":
            waits[name.removeprefix("lane: ")] += _ms(event)
        elif cat.startswith("test."):
            package = _package_of(name)
            if package:
                packages[package]["tests_ms"] = round(
                    packages[package]["tests_ms"] + _ms(event), 1
                )
                if cat == "test.call":
                    packages[package]["tests"] += 1
    if not tasks:
        return None, f"the trace at {trace} records no task: nothing to row"
    total = round((max(ends) - min(begins)) / 1000.0, 1) if begins else 0.0
    return {
        "job": job,
        "total_ms": total,
        "tasks": dict(sorted(tasks.items())),
        "waits_ms": {name: round(ms, 1) for name, ms in sorted(waits.items())},
        "packages": dict(sorted(packages.items())),
    }, ""


def put_leg(root: Path, run: RunContext, *, job: str, label: str, trace: Path) -> str:
    """Put *job*'s task half on the leg's per-run ref; ``""`` or the reason."""
    row, why = leg_row(trace, job=job)
    if row is None:
        return why
    from livery.workshop._verified import read_marker

    # The scope the gate ran, from the marker it left beside the
    # trace: the stamp after the verdict reads it back per leg.
    row["scope"] = read_marker(trace.parent if trace.is_absolute() else root)
    return RUNS.series(run.run_id, label).put(
        root, {ROW_FILE: row}, message=f"metrics: {job} of run {run.run_id}"
    )


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


def _forge_row(job: Job, *, created: str, now: str) -> dict[str, Any]:
    """A job's times as the forge reports them, for a job that left no trace.

    The docs build, the gate job itself, and the merge point's jobs
    have no leg row; their queue and wall times come from the forge
    alone. A job still running when the gate job collects (the gate
    job is always one) is measured to *now* and marked incomplete; a
    job the forge has not started carries no times.
    """
    started = job.started_at
    return {
        "conclusion": job.conclusion,
        "status": job.status,
        "complete": bool(job.completed_at),
        "queued_ms": _between(created, started) if started else None,
        "wall_ms": _between(started, job.completed_at or now) if started else None,
        "steps": [
            {"name": step.name, "ms": _between(step.started_at, step.completed_at)}
            for step in job.steps
        ],
    }


def collect(root: Path, repo: Repository, run: RunContext, *, sha: str) -> list[str]:
    """Join the legs' halves with the forge's times into the run's file; what happened.

    Every line names what was done or why not. Nothing is written
    when no leg left a row, and a per-run ref is dropped only after
    the run's file landed, so a failed put keeps the halves for a
    re-run. A job the forge does not know keeps its trace half and
    is named; a row of another schema is skipped and named. The run
    is looked up under the head the runner's event names, because a
    pull request's checkout is a merge commit the forge never files
    a run under; *sha* is the checkout, the fallback and the row's
    own record. Every job the forge lists is recorded, the legs with
    their traces, the others with their forge times alone, and the
    run's own wall from its start to this collection.
    """
    lines: list[str] = []
    keys = RUNS.listed(root, run.run_id)
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
        found, why = series.row(root, ROW_FILE)
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
            "waits_ms": half.get("waits_ms", {}),
            "packages": half.get("packages", {}),
            "scope": half.get("scope", {"scope": "unknown", "packages": []}),
        }
        if job is None:
            lines.append(f"  {name}: unknown to the forge; its trace half rides alone")
        else:
            row["conclusion"] = job.conclusion
            row["queued_ms"] = _between(entry["created_at"], job.started_at)
            row["wall_ms"] = _between(job.started_at, job.completed_at)
            row["steps"] = [
                {"name": step.name, "ms": _between(step.started_at, step.completed_at)}
                for step in job.steps
            ]
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
    coverage = read_coverage_row(root)
    if coverage is not None:
        entry["coverage"] = coverage
        lines.append(f"  coverage: {len(coverage)} package(s) recorded on the run")
    why = SERIES.put(
        root,
        {run_file(run.run_id): entry},
        message=f"metrics: run {run.run_id} at {sha[:12]}",
    )
    if why:
        lines.append(f"  {SERIES.ref}: {why}; the per-run refs stay for a re-run")
        return lines
    lines.append(
        f"  {SERIES.ref}: run {run.run_id} recorded, {len(entry['jobs'])} job(s)"
    )
    for ref in sorted(refs):
        gone = drop(root, ref)
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
    found = SERIES.rows(root)
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
    lines.extend(_render_coverage(entries))
    return lines


def _render_coverage(entries: list[dict[str, Any]]) -> list[str]:
    """The coverage rows: per package the latest, p50, and p90 percentage."""
    series: dict[str, list[float]] = defaultdict(list)
    latest: dict[str, float] = {}
    for entry in entries:
        row = entry.get("coverage")
        if not isinstance(row, dict):
            continue
        for package, value in sorted(row.items()):
            if isinstance(value, int | float) and not isinstance(value, bool):
                series[str(package)].append(float(value))
                latest[str(package)] = float(value)
    if not series:
        return []
    lines = ["  coverage", f"    {'package':<44} {'latest':>9} {'p50':>9} {'p90':>9}"]
    for package, values in sorted(series.items()):
        lines.append(
            f"    {package[:44]:<44} {latest[package]:8.1f}%"
            f" {_percentile(values, 0.5):8.1f}% {_percentile(values, 0.9):8.1f}%"
        )
    return lines
