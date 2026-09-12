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

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import livery.footman as footman
from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._pytest_points import POINT_VARIABLE
from livery.workshop._state import LEG_VARIABLE, run_context

#: The points, in the order a change meets them.
POINTS = ("gate", "merge", "nightly", "release")

#: The workflow file each point's shell is, as GitHub and Gitea
#: address a dispatch and name a run: the gate and the merge point
#: share one, the nightly and the release have their own. GitLab has
#: one pipeline definition and names no workflow on a run.
WORKFLOWS = {
    "gate": "ci.yml",
    "merge": "ci.yml",
    "nightly": "nightly.yml",
    "release": "release.yml",
}

#: The events that trigger each point's runs, in the forges' words.
EVENTS = {
    "gate": ("pull_request",),
    "merge": ("push",),
    "nightly": ("schedule", "workflow_dispatch"),
    "release": ("workflow_dispatch",),
}

#: The points a person starts by hand through ``ci.dispatch``: their
#: shells carry a dispatch entry. The release wave is dispatched by
#: the merge point through ``workflow.release.dispatch``.
DISPATCHABLE = ("nightly",)

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
#: point adds the deploy, the governance reconcile, which classifies
#: its own commit and exits fast when no contract path changed, and
#: the release dispatch, green unless a merged release is unpublished.
BUILTIN: tuple[Entry, ...] = (
    Entry("gate", "check", "check", profiled=True),
    # The leg's one write: its timing row and its measured suites go
    # on its per-run ref together.
    Entry("gate", "check", "coverage.leg", ("--job={display}",)),
    Entry("gate", "docs", "docs.build"),
    # The title check first: it reads the pull request's title from
    # the event payload, is green off a release branch, and refuses a
    # release title the changelogs do not match before anything else
    # is judged. It runs here so the legs never wait for a job of
    # its own.
    Entry("gate", "gate", "workflow.release.check-title"),
    # The render gate and the provenance check live here, not in the
    # check legs: a scoped leg skips them, and this job runs once on
    # every run, whatever the legs narrowed to.
    Entry("gate", "gate", "template.check"),
    Entry("gate", "gate", "provenance"),
    # The union before the collect: its per-package percentages ride
    # the run's row beside the timings.
    Entry("gate", "gate", "coverage.union"),
    Entry("gate", "gate", "ci.metrics.collect"),
    # The speed judge reads the run's row the collect just put: a
    # suite over its mark for the second run in a row is red here,
    # before the verdict, so the record never names a slow tree green.
    Entry("gate", "gate", "speed.judge"),
    Entry("gate", "gate", "ci.verdict", ("--needs=check,docs",)),
    # After a green verdict only: a red verdict fails the job before
    # this entry, so the record never names a tree a run proved red.
    Entry("gate", "gate", "ci.verified.stamp"),
    Entry("merge", "deploy", "docs.build"),
    Entry("merge", "deploy", "docs.publish"),
    Entry("merge", "govern", "workflow.configure", ("--if-changed",)),
    Entry("merge", "dispatch", "workflow.release.dispatch"),
    # The janitor after the stamp, on the merge point alone: every
    # merge tidies the remote store, and a pull request's run writes
    # nothing it does not own.
    Entry("merge", "gate", "janitor"),
    # The clock's point: the whole check, with the tests that declare
    # the nightly point selected in, on every python of the matrix.
    Entry("nightly", "nightly", "check", profiled=True),
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
    contract = load_contract(root / "workshop.toml")
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


def workflow_of(point: str) -> str:
    """The workflow file *point*'s shell is; refuses a name that is not a point."""
    if point not in WORKFLOWS:
        fail(f"{point!r} is not a point; the points are {', '.join(POINTS)}")
    return WORKFLOWS[point]


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
    """The entries *job* of *point* runs, in order; refuses an unknown job.

    A point's own name is always a job of it, with nothing scheduled
    until an entry attaches: the nightly shell exists before its
    first entry, and runs green and empty until then.
    """
    jobs = jobs_of(root, point)
    if job not in jobs and job != point:
        fail(
            f"the {point} point has no job {job!r}; its jobs are"
            f" {', '.join(jobs) or 'none'}"
        )
    return tuple(
        entry
        for entry in schedule(root)
        if entry.point in (INHERITS.get(point), point) and entry.job == job
    )


def check_legs(root: Path) -> list[str]:
    """The labels of the check legs the gate produces, ``check-<os>-<python>`` each.

    One per runner the contract names (``[ci] runners``, ubuntu-latest
    without it) and Python the gate runs
    ([livery.workshop._pythons.gate_pythons][]), spelled as `run_point`
    names a matrix job's leg.
    """
    from livery.workshop._pythons import gate_pythons

    contract = load_contract(root / "workshop.toml")
    runners = list((contract.get("ci") or {}).get("runners") or ["ubuntu-latest"])
    pythons = gate_pythons(root)
    return [f"check-{runner}-{python}" for runner in runners for python in pythons]


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


def _spawn(argv: list[str], env: dict[str, str]) -> int:
    """Run one entry as a child of the runner's own command; its exit code.

    Streamed, never captured: the job's log is the run's evidence,
    and what each verb said belongs there in order, green or red.
    *env* is the child's whole environment.
    """
    from livery.footman import run

    return run(argv, nofail=True, capture=False, env=env).code


def run_point(
    root: Path,
    point: str,
    job: str,
    *,
    os_label: str = "",
    python: str = "",
    spawn: Callable[[list[str], dict[str, str]], int] = _spawn,
) -> None:
    """Run every entry of *job* at *point*, in order; the first red entry fails.

    *os_label* and *python* are the matrix facts the shell passes for
    a matrix job; they format the entries' arguments and name the
    leg. A profiled entry runs under ``--profile`` with its trace
    left at `TRACE`. Every child's environment names the leg in
    `livery.workshop._state.LEG_VARIABLE`, the key of the leg's rows
    and stamps, and the resolved point in
    `livery.workshop._pytest_points.POINT_VARIABLE`, which selects the
    tests a point runs. *spawn* runs one entry's command in that
    environment and returns its exit code; the default is the runner's
    own child.
    """
    from livery.workshop._state import remote_snapshot

    resolved = effective_point(point)
    entries = entries_for(root, resolved, job)
    if resolved != point:
        print(f"  point: {point} on a push is the {resolved} point")
    display = f"{job} ({os_label}, {python})" if os_label or python else job
    label = f"{job}-{os_label}-{python}" if os_label or python else job
    facts = {"display": display, "label": label, "os": os_label, "python": python}
    prog = footman.prog()
    # One listing of the state store's namespace for the whole job:
    # every entry reads through it and records what it writes for the
    # entries after it, so the job lists once, not once per entry.
    with remote_snapshot(root, publish=True):
        env = {**os.environ, LEG_VARIABLE: label, POINT_VARIABLE: resolved}
        for entry in entries:
            argv = [prog]
            if entry.profiled:
                argv.append(f"--profile={TRACE}")
            argv.append(entry.task)
            argv.extend(arg.format(**facts) for arg in entry.args)
            print(f"  {resolved}/{job}: {entry.task} ({entry.source})")
            code = spawn(argv, env)
            if code != 0:
                fail(f"{resolved}/{job}: {entry.task} exited {code}")
    if not entries:
        print(f"  {resolved}/{job}: nothing scheduled")
