"""The legs' traces: what a run cost, kept where no sync brings it.

A profiled leg pushes the trace it wrote to a namespace of its own, outside
the one refspec a sync mirrors. A checkout pays nothing for traces nobody
asked for, and the gate reads none of them. The file is a Chrome trace of
that leg: every task, step and test it ran, and the wall-clock origin of its
own clock, so one leg's timeline can be laid beside another's.

The workspace contract decides, under ``[ci]``: ``profile-legs`` whether a
leg pushes at all, ``profile-window`` how many runs are kept, and
``profile-into`` where an assembled file lands.

The push is observational. A contract that says no, a trace that is not
there, and a push origin refuses are each one printed line, and none of them
decides a job.

[livery.workshop._traces.assemble][] reads the other way: one trace for a
whole run, built from the forge's own job times and whatever traces the
channel holds. The forge is asked for the skeleton and nothing else, so
nothing here knows a forge.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.workshop._state import (
    LEG_VARIABLE,
    TRACE_NAMESPACE,
    Keyed,
    RunContext,
    Series,
    put,
    run_context,
    slug,
)

if TYPE_CHECKING:
    from livery.forge import Job, Repository

#: The one file a leg puts on its trace ref: the trace as its writer
#: wrote it, byte for byte, so nothing re-serialises a megabyte to
#: store it and two legs of one run delta against each other in the
#: pack.
TRACE_FILE = "trace.json"

#: The contract's keys, under ``[ci]``.
LEGS_KEY = "profile-legs"
WINDOW_KEY = "profile-window"
INTO_KEY = "profile-into"

#: What the contract answers when it says nothing. Twenty runs is a
#: guess made before any run had pushed one; the window is measured
#: and tuned once real runs have.
LEGS_DEFAULT = True
WINDOW_DEFAULT = 20
INTO_DEFAULT = ".fm/profiles"


@dataclass(frozen=True)
class Policy:
    """What the contract says about the legs' traces.

    Attributes:
        legs: Whether a leg pushes its trace.
        window: How many runs' traces are kept.
        into: Where an assembled file lands, relative to the
            workspace root unless it is absolute.
    """

    legs: bool
    window: int
    into: str


def policy(root: Path) -> tuple[Policy, str]:
    """The contract's ``[ci] profile-*`` for *root*, and what it got wrong.

    A key of the wrong type is named with what it must be, and the
    default stands for it: this decides whether a timeline is kept, so
    a typo must be visible and must not stop a job.
    """
    from livery.workshop._contract import load_contract

    contract = load_contract(root / "workshop.toml")
    found: dict[str, Any] = contract.get("ci") or {}
    legs, why_legs = _flag(found, LEGS_KEY, LEGS_DEFAULT)
    window, why_window = _count(found, WINDOW_KEY, WINDOW_DEFAULT)
    into, why_into = _text(found, INTO_KEY, INTO_DEFAULT)
    return Policy(legs, window, into), "; ".join(
        why for why in (why_legs, why_window, why_into) if why
    )


def _flag(found: dict[str, Any], key: str, fallback: bool) -> tuple[bool, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    if isinstance(value, bool):
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is true or false"


def _count(found: dict[str, Any], key: str, fallback: int) -> tuple[int, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    # A bool is an int in Python and not a count here.
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is a whole number of runs"


def _text(found: dict[str, Any], key: str, fallback: str) -> tuple[str, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    if isinstance(value, str) and value:
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is a path"


def _kept_runs(root: Path) -> int | None:
    """How many runs of traces the contract keeps, for the janitor."""
    kept, _why = policy(root)
    return kept.window


#: One ref per leg of a run, keyed as the per-run metrics are, so the
#: two halves of one leg answer to the same key. The files are not
#: rows, so the count of runs is the whole bound the janitor applies.
TRACES = Keyed(
    # Under the traces' own namespace, so the ref reads
    # ``refs/workshop-trace/run/<run>/<leg>``, the per-run metrics'
    # shape one namespace over.
    "run",
    ("run", "leg"),
    namespace=TRACE_NAMESPACE,
    holds_rows=False,
    keep=_kept_runs,
)


def this_leg() -> str:
    """The leg this process belongs to, as the job runner named it."""
    return os.environ.get(LEG_VARIABLE, "")


def push(root: Path, trace: Path, *, run: RunContext | None = None) -> str:
    """Put *trace* on this leg's ref of the run; the line to print.

    Empty when the contract asks for no traces, so a workspace that
    wants none neither pushes nor says anything. Every other answer is
    a line: a trace that was never written, a leg the runner did not
    name, a run this is not, or origin's own refusal.
    """
    kept, why = policy(root)
    if not kept.legs:
        return ""
    if why:
        return f"profile: {why}"
    found = run or run_context()
    if found is None:
        return "profile: not a CI run; the trace stays on this machine"
    leg = this_leg()
    if not leg:
        return f"profile: {LEG_VARIABLE} names no leg; the trace is not pushed"
    if not trace.is_file():
        return f"profile: no trace at {trace}; nothing to push"
    series = TRACES.series(found.run_id, leg)
    refused = put(
        root,
        series.ref,
        {TRACE_FILE: trace.read_text(encoding="utf-8")},
        message=f"trace: {leg} of run {found.run_id}",
        ci_only=True,
    )
    if refused:
        return f"profile: the trace was not pushed ({refused})"
    size = trace.stat().st_size
    return f"profile: {size // 1024} KiB pushed to {series.ref}"


#: The assembled trace's own process: the run, whose tracks are its
#: jobs. A leg's own trace keeps process groups of its own, numbered
#: from `LEG_PIDS` upward so two legs of one run never share one.
RUN_PID = 1
LEG_PIDS = 1000


def assemble(
    root: Path,
    repo: Repository,
    run_id: str,
    *,
    head_sha: str = "",
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One trace of a whole run: its jobs, their steps, and the legs' traces.

    The forge answers the skeleton: every job with its status, its
    conclusion, its start and end, and its own steps. Each job is a
    track of the run's process, so jobs that ran at once read as the
    parallel work they were. Where the channel holds that job's trace,
    its events are laid on the run's clock through the origin the
    trace recorded, in process groups of their own: the alignment is
    what nests them, and it comes from two clocks that were written
    down independently rather than from the order anything arrived in.

    A job with no trace keeps its slice and its steps. A job the forge
    gives no times at all, a skip, is an instant event carrying its
    conclusion, because a thing with no duration is an event and not a
    span. A job still running is a slice up to the moment of assembly.

    Args:
        root: The workspace root, which the traces are read through.
        repo: The repository whose run this is.
        run_id: The forge's own id for the run.
        head_sha: The commit the run checked, when the caller knows
            it. With it the run's own row is read too, so the wait
            before each job can be drawn; without it the jobs' own
            times are the whole skeleton.
        now: The moment a running job's slice ends; the present by
            default.

    Returns:
        The trace's events, and a line per thing worth saying. No
        events at all means the forge listed no job for the run.
    """
    moment = now or datetime.now(UTC)
    lines: list[str] = []
    jobs = repo.checks.jobs(int(run_id)) if run_id.isdigit() else ()
    if not run_id.isdigit():
        lines.append(f"run {run_id}: not a number, so the forge cannot be asked")
        return [], lines
    if not jobs:
        lines.append(f"run {run_id}: the forge lists no job for it")
        return [], lines
    created = _run_created(repo, run_id, head_sha, lines) if head_sha else None
    zero = _zero_of(jobs, created, moment)
    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "process_name",
            "pid": RUN_PID,
            "args": {"name": f"run {run_id}"},
        }
    ]
    held = TRACES.listed(root, run_id)
    if held is None:
        lines.append(f"{TRACES.prefix}{run_id}/*: could not be listed; no leg's own")
        held = []
    traced = {key[-1]: key for key in held}
    pids = itertools.count(LEG_PIDS)
    for tid, job in enumerate(sorted(jobs, key=_job_order), start=1):
        events += _job_events(job, tid=tid, zero=zero, created=created, now=moment)
        key = traced.get(slug(job.name))
        if key is None:
            continue
        found, why = _leg_events(
            root, TRACES.at(*key), pids=pids, zero=zero, job=job.name
        )
        if why:
            lines.append(why)
            continue
        events += found
        lines.append(f"{job.name}: {len(found)} event(s) of its own")
    return events, lines


def _epoch_us(stamp: str) -> float | None:
    """*stamp* as wall-clock microseconds, or ``None`` when it says nothing."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1e6
    except ValueError:
        return None


def _job_order(job: Job) -> tuple[float, str]:
    """Jobs in the order they started, the ones that never did last."""
    began = _epoch_us(job.started_at)
    return (float("inf") if began is None else began, job.name)


def _run_created(
    repo: Repository, run_id: str, head_sha: str, lines: list[str]
) -> float | None:
    """When the forge accepted the run, so the wait before a job can be drawn."""
    found = next(
        (r for r in repo.checks.runs(head_sha=head_sha) if str(r.id) == run_id), None
    )
    if found is None:
        lines.append(
            f"run {run_id}: not among the forge's runs for {head_sha[:12]};"
            " the waits before its jobs are not drawn"
        )
        return None
    return _epoch_us(found.created_at)


def _zero_of(jobs: tuple[Job, ...], created: float | None, now: datetime) -> float:
    """The assembled trace's zero: the first thing that happened in the run."""
    moments = [began for job in jobs if (began := _epoch_us(job.started_at))]
    if created is not None:
        moments.append(created)
    return min(moments, default=now.timestamp() * 1e6)


def _job_events(
    job: Job, *, tid: int, zero: float, created: float | None, now: datetime
) -> list[dict[str, Any]]:
    """One job as a track: the wait before it, its own span, and its steps."""
    args: dict[str, Any] = {"status": job.status, "conclusion": job.conclusion}
    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "thread_name",
            "pid": RUN_PID,
            "tid": tid,
            "args": {"name": job.name},
        }
    ]
    began = _epoch_us(job.started_at)
    if began is None:
        # Nothing ran, so nothing lasted: an event, not a span of no
        # length. A skipped job is here because it happened.
        events.append(
            {
                "ph": "i",
                "s": "t",
                "cat": "job",
                "name": job.name,
                "pid": RUN_PID,
                "tid": tid,
                "ts": 0.0,
                "args": args,
            }
        )
        return events
    ended = _epoch_us(job.completed_at)
    if ended is None:
        ended = now.timestamp() * 1e6
        args["running"] = True  # the slice ends where the reading was taken
    if created is not None and began > created:
        events.append(
            {
                "ph": "X",
                "cat": "queue",
                "name": f"{job.name}: queued",
                "pid": RUN_PID,
                "tid": tid,
                "ts": round(created - zero, 1),
                "dur": round(began - created, 1),
            }
        )
    events.append(
        {
            "ph": "X",
            "cat": "job",
            "name": job.name,
            "pid": RUN_PID,
            "tid": tid,
            "ts": round(began - zero, 1),
            "dur": round(ended - began, 1),
            "args": args,
        }
    )
    for step in job.steps:
        start, stop = _epoch_us(step.started_at), _epoch_us(step.completed_at)
        if start is None or stop is None:
            continue  # a step the forge did not time is not drawn
        events.append(
            {
                "ph": "X",
                "cat": "step",
                "name": step.name,
                "pid": RUN_PID,
                "tid": tid,
                "ts": round(start - zero, 1),
                "dur": round(max(stop - start, 0.0), 1),
                "args": {"conclusion": step.conclusion},
            }
        )
    return events


def _leg_events(
    root: Path, series: Series, *, pids: Iterator[int], zero: float, job: str
) -> tuple[list[dict[str, Any]], str]:
    """A leg's own trace on the run's clock; the events, or why not.

    The laying on is the trace format's own, so the plugin that writes a
    trace does it: this reads the leg's file out of the channel and
    names the job in whatever the plugin refuses.
    """
    from livery.footman.profile import laid_on

    found, why = series.file(root, TRACE_FILE)
    if why:
        return [], f"{job}: {why}"
    if found is None:
        return [], f"{job}: {series.ref} carries no {TRACE_FILE}"
    placed, refused = laid_on(found, zero=zero, pids=pids, label=job)
    return placed, f"{job}: {refused}" if refused else ""
