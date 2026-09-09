"""CI as points: what each point runs, and the verb that runs it.

A consumer never thinks about CI. They reason about a named point in
the process and attach a task to it. The four points are ``gate``
(a pull request), ``merge`` (main after a merge), ``nightly`` (the
clock), and ``release`` (dispatched by the merge point). The emitted
workflow is a static shell, one ``fm ci.run --point=<p> --job=<j>``
per job, and what a job does is data: the builtin schedule below,
plus the ``[[ci.schedule]]`` entries a workspace declares in
``workshop.toml``. The matrix shape belongs to a point's job, never
to a task.

Reach for [livery.workshop._points.run_point][] to run a job's
entries, and `schedule` to see them. Every entry runs as a child of
the runner's own command, so a scheduled task is any task the
workspace mounts, and the child's exit is the entry's.

The merge point inherits the gate's jobs: a push to main runs the
same checks, plus the jobs only a merge has (the docs deploy, the
governance reconcile). A shell spells ``--point=gate`` on the shared
jobs and the verb promotes the point on a push, so the YAML carries
no decision.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import livery.footman as footman
from livery.footman import fail
from livery.workshop._state import run_context

#: The points, in the order a change meets them.
POINTS = ("gate", "merge", "nightly", "release")

#: The trace the profiled gate writes, read by the leg's row.
TRACE = "fm-profile.json"


@dataclass(frozen=True)
class Entry:
    """One scheduled task: at which point and job, which task, which arguments.

    Attributes:
        point: One of `POINTS`.
        job: The job of the point the entry runs in.
        task: The task's dotted address, as the workspace mounts it.
        args: The arguments, each formatted with the run's facts:
            ``{display}`` the job's display name, ``{label}`` the
            leg's label, ``{os}`` and ``{python}`` the matrix values.
        profiled: Whether the task runs under ``--profile``, its
            trace left at `TRACE` for the leg's timing row.
        source: Where the entry was declared.
    """

    point: str
    job: str
    task: str
    args: tuple[str, ...] = ()
    profiled: bool = False
    source: str = "builtin"


#: The workshop's own schedule. The gate's check job runs the gate
#: profiled and records the leg's timing row; its verdict job
#: collects the run's rows, then judges the jobs it needs. The merge
#: point adds the deploy and the governance reconcile, which
#: classifies its own commit and exits fast when no contract path
#: changed.
BUILTIN: tuple[Entry, ...] = (
    Entry("gate", "check", "check", profiled=True),
    Entry("gate", "check", "ci.metrics.leg", ("--job={display}", "--label={label}")),
    Entry("gate", "docs", "docs.build"),
    Entry("gate", "gate", "ci.metrics.collect"),
    Entry("gate", "gate", "ci.verdict", ("--needs=check,docs,release-title",)),
    Entry("gate", "release-title", "workflow.release.check-title"),
    Entry("merge", "deploy", "docs.build"),
    Entry("merge", "deploy", "docs.publish"),
    Entry("merge", "govern", "workflow.configure", ("--if-changed",)),
)

#: A point whose jobs include another point's: the merge point runs
#: the gate's jobs and its own.
INHERITS = {"merge": "gate"}


def declared(root: Path) -> tuple[Entry, ...]:
    """The ``[[ci.schedule]]`` entries of *root*'s contract, refusing bad ones.

    Each entry names a ``point`` (one of `POINTS`), a ``task``, and
    optionally a ``job`` (the point's own name when absent) and
    ``args``. An unknown point, a missing task, or arguments that are
    not strings refuse at load, naming the entry.
    """
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    raw = (contract.get("ci") or {}).get("schedule") or []
    if not isinstance(raw, list):
        fail("[ci] schedule must be a list of [[ci.schedule]] tables")
    entries: list[Entry] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            fail(f"[[ci.schedule]] entry {index} is not a table")
        point = str(item.get("point", ""))
        task = str(item.get("task", ""))
        if point not in POINTS:
            fail(
                f"[[ci.schedule]] entry {index}: point {point!r} is not a"
                f" point; the points are {', '.join(POINTS)}"
            )
        if not task:
            fail(f"[[ci.schedule]] entry {index} ({point}): names no task")
        args = item.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            fail(
                f"[[ci.schedule]] entry {index} ({point}, {task}): args must be strings"
            )
        entries.append(
            Entry(
                point,
                str(item.get("job", point)),
                task,
                tuple(args),
                source="workshop.toml",
            )
        )
    return tuple(entries)


def schedule(root: Path) -> tuple[Entry, ...]:
    """Every entry, the builtin ones first, then the contract's."""
    return BUILTIN + declared(root)


def jobs_of(root: Path, point: str) -> tuple[str, ...]:
    """The jobs *point* has, inherited ones first, in schedule order."""
    if point not in POINTS:
        fail(f"{point!r} is not a point; the points are {', '.join(POINTS)}")
    names: list[str] = []
    for entry in schedule(root):
        if entry.point in (INHERITS.get(point), point) and entry.job not in names:
            names.append(entry.job)
    return tuple(names)


def entries_for(root: Path, point: str, job: str) -> tuple[Entry, ...]:
    """The entries *job* of *point* runs, in order; refuses an unknown job."""
    jobs = jobs_of(root, point)
    if job not in jobs:
        fail(
            f"the {point} point has no job {job!r}; its jobs are"
            f" {', '.join(jobs) or 'none'}"
        )
    return tuple(
        entry
        for entry in schedule(root)
        if entry.point in (INHERITS.get(point), point) and entry.job == job
    )


def effective_point(point: str) -> str:
    """The point a shell's ``gate`` means on a push: the merge point.

    The shared jobs spell ``--point=gate``; inside CI, a push event
    is the merge point, so the promotion happens here and never in
    the YAML. Outside CI, and on every other event, the point stands.
    """
    run = run_context()
    if point == "gate" and run is not None and run.event == "push":
        return "merge"
    return point


def _spawn(argv: list[str]) -> int:
    """Run one entry as a child of the runner's own command; its exit code."""
    from livery.footman import run

    return run(argv, nofail=True).code


def run_point(
    root: Path,
    point: str,
    job: str,
    *,
    os_label: str = "",
    python: str = "",
    spawn: Callable[[list[str]], int] = _spawn,
) -> None:
    """Run every entry of *job* at *point*, in order; the first red entry fails.

    *os_label* and *python* are the matrix facts the shell passes for
    a matrix job; they format the entries' arguments and name the
    leg. A profiled entry runs under ``--profile`` with its trace
    left at `TRACE`. *spawn* runs one entry's command and returns its
    exit code; the default is the runner's own child.
    """
    resolved = effective_point(point)
    entries = entries_for(root, resolved, job)
    if resolved != point:
        print(f"  point: {point} on a push is the {resolved} point")
    display = f"{job} ({os_label}, {python})" if os_label or python else job
    label = f"{job}-{os_label}-{python}" if os_label or python else job
    facts = {"display": display, "label": label, "os": os_label, "python": python}
    prog = footman.prog()
    for entry in entries:
        argv = [prog]
        if entry.profiled:
            argv.append(f"--profile={TRACE}")
        argv.append(entry.task)
        argv.extend(arg.format(**facts) for arg in entry.args)
        print(f"  {resolved}/{job}: {entry.task} ({entry.source})")
        code = spawn(argv)
        if code != 0:
            fail(f"{resolved}/{job}: {entry.task} exited {code}")
    if not entries:
        print(f"  {resolved}/{job}: nothing scheduled")
