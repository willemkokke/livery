"""The legs' traces: what a run cost, kept where no sync brings it.

A profiled leg pushes the trace it wrote to a namespace of its own, outside
the one refspec a sync mirrors. A checkout pays nothing for traces nobody
asked for, and the gate reads none of them. The file is a Chrome trace of
that leg: every task, step and test it ran, and the wall-clock origin of its
own clock, so one leg's timeline can be laid beside another's.

The workspace contract decides, under ``[ci]``: ``profile`` whether CI
keeps a trace of itself at all, ``profile-window`` how many runs are kept,
and ``profile-into`` where an assembled file lands.

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
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.workshop._state import (
    LEG_VARIABLE,
    TRACE_NAMESPACE,
    Keyed,
    RunContext,
    put,
    run_context,
    slug,
)
from livery.workshop._state import read as state_read

if TYPE_CHECKING:
    from livery.forge import Job, Repository
    from livery.workshop._git_ops import GitOps

#: The one file a leg puts on its trace ref: the trace as its writer
#: wrote it, byte for byte, so nothing re-serialises a megabyte to
#: store it and two legs of one run delta against each other in the
#: pack.
TRACE_FILE = "trace.json"

#: Beside it, the job the forge lists this leg as. The ref is keyed by
#: the leg label the runner sets, and a matrix job's forge name is
#: spelled another way entirely (``check (ubuntu-latest, 3.14)``
#: against ``check-ubuntu-latest-3.14``), so the name the assembler
#: joins on is written down rather than derived.
JOB_FILE = "job.json"

#: The contract's keys, under ``[ci]``.
PROFILE_KEY = "profile"
WINDOW_KEY = "profile-window"
INTO_KEY = "profile-into"

#: What the contract answers when it says nothing. Twenty runs is a
#: guess made before any run had pushed one; the window is measured
#: and tuned once real runs have.
PROFILE_DEFAULT = True
WINDOW_DEFAULT = 20
INTO_DEFAULT = ".fm/profiles"


@dataclass(frozen=True)
class Policy:
    """What the contract says about the legs' traces.

    Attributes:
        legs: Whether CI keeps a trace of what it did at all. Off means
            zero cost: no entry runs profiled, nothing is written on a
            runner, nothing is pushed, nothing recorded. A run from a
            period when it was off still assembles at the job level,
            because that shape comes from the forge.
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
    legs, why_legs = _flag(found, PROFILE_KEY, PROFILE_DEFAULT)
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


def push(
    root: Path, trace: Path, *, job: str = "", run: RunContext | None = None
) -> str:
    """Put *trace* on this leg's ref of the run; the line to print.

    *job* is the job's name as the forge lists it, written down beside
    the trace: it is what an assembler joins the leg to its job by, and
    a matrix job's forge name cannot be derived from the leg's label.

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
    files = {TRACE_FILE: trace.read_text(encoding="utf-8")}
    if job:
        files[JOB_FILE] = json.dumps({"job": job}, sort_keys=True)
    refused = put(
        root,
        series.ref,
        files,
        message=f"trace: {leg} of run {found.run_id}",
        ci_only=True,
    )
    if refused:
        return f"profile: the trace was not pushed ({refused})"
    size = trace.stat().st_size
    return f"profile: {size // 1024} KiB pushed to {series.ref}"


@dataclass(frozen=True)
class Assembled:
    """One run as a timeline, and where that timeline's zero sits.

    Attributes:
        events: The trace's events, every stamp relative to ``origin``.
        origin: That zero, in wall-clock microseconds, which is what lets
            another timeline be laid on this one.
        lines: A line per thing worth saying about the assembling.
    """

    events: list[dict[str, Any]]
    origin: float
    lines: list[str]


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
    clock: float | None = None,
    now: datetime | None = None,
) -> Assembled:
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
        clock: The wall-clock moment, in microseconds, every stamp is
            measured from. The run's own first moment by default, which
            is a file that starts at zero; ``0.0`` leaves the stamps on
            the wall clock itself, which is what a drop box takes.
        now: The moment a running job's slice ends; the present by
            default.

    Returns:
        The events, the wall-clock moment they are measured from, and a line
        per thing worth saying. No events at all means the forge listed no
        job for the run.
    """
    # The plugin lays one trace on another's clock, and is optional: an
    # ordinary run of any verb must not import it, and importing this
    # module is how the submit reaches `drop_run`.
    from livery.footman.profile import laid_on

    moment = now or datetime.now(UTC)
    lines: list[str] = []
    jobs = repo.checks.jobs(int(run_id)) if run_id.isdigit() else ()
    if not run_id.isdigit():
        lines.append(f"run {run_id}: not a number, so the forge cannot be asked")
        return Assembled([], 0.0, lines)
    if not jobs:
        lines.append(f"run {run_id}: the forge lists no job for it")
        return Assembled([], 0.0, lines)
    created = _run_created(repo, run_id, head_sha, lines) if head_sha else None
    zero = _zero_of(jobs, created, moment) if clock is None else clock
    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "process_name",
            "pid": RUN_PID,
            "args": {"name": f"run {run_id}"},
        }
    ]
    traced, refusals = _legs_of(root, run_id)
    lines += refusals
    pids = itertools.count(LEG_PIDS)
    for tid, job in enumerate(sorted(jobs, key=_job_order), start=1):
        events += _job_events(job, tid=tid, zero=zero, created=created, now=moment)
        text = traced.get(job.name) or traced.get(slug(job.name))
        if text is None:
            continue
        found, why = laid_on(text, zero=zero, pids=pids, label=job.name)
        if why:
            lines.append(f"{job.name}: {why}")
            continue
        events += found
        lines.append(f"{job.name}: {len(found)} event(s) of its own")
    return Assembled(events, zero, lines)


def _epoch_us(stamp: str) -> float | None:
    """*stamp* as wall-clock microseconds, or ``None`` when it says nothing.

    A stamp with no offset is read as UTC, because that is what a forge
    means by one. Read as local time instead, which is what the standard
    library does with a naive stamp, a leg's trace would land as far from
    its job as the reader's own offset: an hour in London, two in
    Amsterdam, and silently.
    """
    if not stamp:
        return None
    try:
        read = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if read.tzinfo is None:
        read = read.replace(tzinfo=UTC)
    return read.timestamp() * 1e6


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
    ended = _epoch_us(job.completed_at) if began is not None else None
    if began is not None and ended is None:
        ended = now.timestamp() * 1e6
        args["running"] = True  # the slice ends where the reading was taken
    own: dict[str, Any] = {
        "cat": "job",
        "name": job.name,
        "pid": RUN_PID,
        "tid": tid,
        "ts": 0.0 if began is None else round(began - zero, 1),
        "args": args,
    }
    if began is None or ended is None or ended <= began:
        # Nothing ran, or nothing lasted: an event, not a span of no length.
        # A job the forge skipped is here because it happened, and a forge
        # times in whole seconds, which is how a skip came back ending a
        # second before it began. Its steps are still drawn below: they have
        # nothing to sit inside, and they happened too.
        events.append({**own, "ph": "i", "s": "t"})
    else:
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
        events.append({**own, "ph": "X", "dur": round(ended - began, 1)})
    for step in job.steps:
        start, stop = _epoch_us(step.started_at), _epoch_us(step.completed_at)
        if start is None or stop is None:
            continue  # a step the forge did not time is not drawn
        drawn: dict[str, Any] = {
            "cat": "step",
            "name": step.name,
            "pid": RUN_PID,
            "tid": tid,
            "ts": round(start - zero, 1),
            "args": {"conclusion": step.conclusion},
        }
        # The same rule as a job's: what had no extent is an event. A
        # forge times in whole seconds, so a step inside one is common.
        events.append(
            {**drawn, "ph": "X", "dur": round(stop - start, 1)}
            if stop > start
            else {**drawn, "ph": "i", "s": "t"}
        )
    return events


def _legs_of(root: Path, run_id: str) -> tuple[dict[str, str], list[str]]:
    """Every leg's trace for *run_id*, by the job it belongs to; and the lines.

    One read of each ref, which carries the trace and the name of the
    job the forge lists the leg as. A leg that recorded no job name is
    answered for by its own key, which is what a job whose name needs
    no spelling looks like.
    """
    keys = TRACES.listed(root, run_id)
    if keys is None:
        return {}, [f"{TRACES.prefix}{run_id}/*: could not be listed; no leg's own"]
    lines: list[str] = []
    found: dict[str, str] = {}
    for key in keys:
        series = TRACES.at(*key)
        read = state_read(root, series.ref)
        if read.files is None:
            lines.append(f"{key[-1]}: {read.reason or 'its ref could not be read'}")
            continue
        text = read.files.get(TRACE_FILE)
        if text is None:
            lines.append(f"{key[-1]}: {series.ref} carries no {TRACE_FILE}")
            continue
        found[_job_named(read.files.get(JOB_FILE)) or key[-1]] = text
    return found, lines


def _job_named(text: str | None) -> str:
    """The job name a leg wrote down, or empty when it wrote none."""
    if not text:
        return ""
    try:
        data: Any = json.loads(text)
    except ValueError:
        return ""
    name = data.get("job") if isinstance(data, dict) else None
    return name if isinstance(name, str) else ""


def newest_run(repo: Repository, head_sha: str) -> str:
    """The forge's newest run for *head_sha*, or empty when it lists none."""
    runs = repo.checks.runs(head_sha=head_sha)
    return str(runs[0].id) if runs else ""


def drop_run(repo: Repository, git: GitOps, *, run_id: str = "") -> str:
    """Put the run of *git*'s head in this command's own drop box; the line.

    For a verb that followed a run to its end. With a trace being kept, the
    run it watched goes into the same box its own children drop into, so one
    file holds the local command, the run it caused, every job of that run,
    and every leg's tasks and tests underneath. The stamps are the wall
    clock's, which is what a box takes, and this run's writer lays the whole
    thing on its own clock.

    Empty when no trace is being kept, which is the ordinary case: the verb
    calls this whatever its line asked for and pays one environment read.

    Never raises. A trace is something noticed about a run, so nothing here
    may change what the run decided: every failure, git's and the forge's
    alike, comes back as the line to print. A CI leg proved the point by
    running its own tests under a profile, where a rig with no repository
    turned a watched merge into a failure.
    """
    from livery.footman.profile import dropped, keeping

    # Assembling a run costs forge calls, so the cheap question comes first.
    if not keeping():
        return ""
    try:
        head_sha = git.head_sha()
        found = run_id or newest_run(repo, head_sha)
        if not found:
            return f"profile: the forge lists no run for {head_sha[:12]}"
        made = assemble(git.root, repo, found, head_sha=head_sha, clock=0.0)
        if not made.events:
            why = made.lines[0] if made.lines else f"run {found} assembled to nothing"
            return f"profile: {why}"
        if dropped(made.events) is None:
            return ""
    except Exception as error:
        return f"profile: the run was not traced ({error})"
    jobs = len({e["tid"] for e in made.events if e.get("cat") == "job"})
    return f"profile: run {found} joins this trace, {jobs} job(s)"


def write_run(
    root: Path,
    repo: Repository,
    *,
    run_id: str = "",
    head_sha: str = "",
    into: Path | None = None,
) -> tuple[Path | None, list[str]]:
    """Write a run's assembled trace where a person can open it; and the lines.

    For investigating a run that has already ended, which is what the traces
    are kept for. Without a *run_id* the forge's newest run for *head_sha* is
    the one. The directory is the contract's ``[ci] profile-into`` unless the
    caller names another.
    """
    from livery.footman.profile import as_trace

    kept, why = policy(root)
    if why:
        return None, [f"profile: {why}"]
    found = run_id or (newest_run(repo, head_sha) if head_sha else "")
    if not found:
        return None, [f"profile: the forge lists no run for {head_sha[:12]}"]
    made = assemble(root, repo, found, head_sha=head_sha)
    if not made.events:
        return None, made.lines
    where = into or Path(kept.into)
    at = where if where.is_absolute() else root / where
    at.mkdir(parents=True, exist_ok=True)
    path = at / f"run-{found}.json"
    # The stamps are relative to the run's own first moment, so the file says
    # where that sits: another timeline can be laid on this one.
    path.write_text(as_trace(made.events, origin=made.origin), encoding="utf-8")
    return path, made.lines
