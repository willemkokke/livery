"""CI as points: what each point is, what it runs, and the verb that runs it.

A consumer never thinks about CI. They reason about a named point in
the process and attach a task to it. The four points are ``gate``
(a pull request, or a dispatch by hand), ``merge`` (main after a
merge), ``nightly`` (the clock), and ``release`` (dispatched by the
merge point). A point is a declaration, [livery.workshop._points.Point][]:
its workflow file, its events, and its jobs, each job stating what it
needs in the workshop's words ([livery.workshop._points.Job][]); each
forge's renderer says those in its own. The emitted workflow is a
static shell, one ``fm ci.run --point=<p> --job=<j>`` per job, and
what a job does is data: the builtin schedule below, plus the
``[[ci.schedule]]`` entries a workspace declares in ``workshop.toml``.
The matrix shape belongs to a point's job, never to a task.

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
from datetime import date, timedelta
from pathlib import Path

import livery.footman as footman
from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._pytest_points import POINT_VARIABLE
from livery.workshop._state import LEG_VARIABLE, run_context

#: The events a point may run on, in the forges' words.
EVENT_NAMES = ("pull_request", "push", "schedule", "workflow_dispatch")


@dataclass(frozen=True)
class Input:
    """One input a dispatched point takes, as the forge prompts for it.

    Attributes:
        name: The input's name, as the dispatch passes it.
        description: What the input is, for the person dispatching.
        required: Whether a dispatch without it is refused.
        default: The value an absent optional input takes.
    """

    name: str
    description: str
    required: bool = False
    default: str = ""


@dataclass(frozen=True)
class Job:
    """One job of a point's shell, in the workshop's words.

    Each forge's renderer says these in that forge's words: a grant, a
    step, an action, an image. Nothing here is forge-shaped.

    Attributes:
        name: The job's name, and the ``--job`` its one call passes.
        matrix: The legs the job fans out to: ``legs`` for one per
            runner and gate Python, ``pythons`` for one per Python of
            the whole matrix on the first runner, ``wheels`` for one per
            declared wheel platform, ``""`` for one job on one runner.
        needs: The jobs of the point this one waits for.
        always: Whether the job runs when a job it needs was red, the
            verdict job's shape.
        fetch: How deep the checkout is: ``full`` for every commit,
            ``tags`` for the tags as well, ``2`` for the commit and its
            parent, ``""`` for the commit alone.
        token: The credential the verb sees as ``FORGE_TOKEN``: ``job``
            for the run's own token, ``repository`` for the repository's
            secret when present and the job's otherwise, ``secret`` for
            the repository's secret alone, ``admin`` for the admin
            secret as ``FORGE_ADMIN_TOKEN``, ``""`` for none.
        writes: Whether the job pushes to the repository (the state
            store, a receipt tag), which needs the write grant.
        profile: Whether the job's trace is kept as an artifact.
        docs_tools: Whether the docs generators' system requirements
            are installed before the call.
        deploy: Whether the job publishes the site through the
            contract's publish seam after the call.
        publishes: The artifact the job uploads, ``""`` for none.
        collects: The artifact the job downloads first, ``""`` for none.
        environment: The named deployment environment the job runs in,
            ``""`` for none.
        deploy_key: The secret written as the job's SSH deploy key,
            ``""`` for none.
        driver_pin: Whether the job installs the released workshop the
            point's ``workshop`` input names, over the checkout's own.
        only: When the job exists at all: ``wheels`` where a member
            declares wheel platforms, ``home`` where the workspace
            publishes a template artifact, ``""`` always.
        step: What the forge's run page calls the one call; the job's
            name capitalised when empty.
        note: The comment the rendered shell prints above the job.
    """

    name: str
    matrix: str = ""
    needs: tuple[str, ...] = ()
    always: bool = False
    fetch: str = ""
    token: str = ""
    writes: bool = False
    profile: bool = False
    docs_tools: bool = False
    deploy: bool = False
    publishes: str = ""
    collects: str = ""
    environment: str = ""
    deploy_key: str = ""
    driver_pin: bool = False
    only: str = ""
    step: str = ""
    note: str = ""


@dataclass(frozen=True)
class Point:
    """One point: its workflow file, its events, and its jobs.

    Attributes:
        name: The point's name, and the ``--point`` its jobs pass.
        workflow: The workflow file the point's shell is, as GitHub and
            Gitea address a dispatch and name a run. Two points may
            share one, the gate and the merge point do, when their
            events do not overlap. GitLab has one pipeline document and
            names a dispatched pipeline after this.
        events: The events that start the point's runs, from
            `EVENT_NAMES`.
        jobs: The point's own jobs, in order.
        inherits: A point whose jobs this one runs first, the gate's
            for the merge point; ``""`` for none.
        inputs: The inputs a dispatch takes; a point with any is
            dispatched by a verb that supplies them, never by hand.
        ref_input: The input naming the commit the jobs check out,
            ``""`` for the run's own commit.
        cron: When the clock starts the point, for a point whose
            events include ``schedule``, in cron's five fields, UTC.
            GitHub and Gitea read it from the workflow file; on
            GitLab the clock is a pipeline schedule the governance
            reconcile creates from it.
        note: The comment the rendered shell prints under its name.
    """

    name: str
    workflow: str
    events: tuple[str, ...]
    jobs: tuple[Job, ...] = ()
    inherits: str = ""
    inputs: tuple[Input, ...] = ()
    ref_input: str = ""
    cron: str = ""
    note: str = ""


#: The four builtin points, in the order a change meets them.
DECLARED: tuple[Point, ...] = (
    Point(
        "gate",
        "ci.yml",
        ("pull_request", "workflow_dispatch"),
        note=(
            "A pull request, a push to main, and a dispatch by hand. A"
            " dispatched run pays the full gate: the check verb reads the"
            " event and narrows on a pull request alone, so every event"
            " spells the same call."
        ),
        jobs=(
            Job(
                "check",
                matrix="legs",
                fetch="full",
                writes=True,
                profile=True,
                note=(
                    "The check legs: the tests run metered, and only they."
                    " The leg's measured suites ride its per-run ref on the"
                    " state store, and the gate job unions them with main's"
                    " record and judges once. The scoped gate diffs against"
                    " the merge base with the pull request's base branch,"
                    " which a shallow clone lacks."
                ),
            ),
            Job(
                "docs",
                docs_tools=True,
                note=(
                    "The strict site build: broken links and orphan pages go"
                    " red here, required through the gate context, never"
                    " inside the local check."
                ),
            ),
            Job(
                "gate",
                needs=("check", "docs"),
                always=True,
                fetch="full",
                token="job",
                writes=True,
                step="Verdict",
                note=(
                    "The one required context. Branch protection points"
                    " here, so the matrix can grow or shrink without touching"
                    " repository settings. Its entries union the legs'"
                    " measured suites with main's coverage record and judge"
                    " the floors, collect the run's timing rows, ask the"
                    " forge for the jobs it needs, and stamp the tree a green"
                    " run proved; a red run is judged too. The stamp composes"
                    " a narrowed run with its base tree's record through the"
                    " merge base, which a shallow clone lacks."
                ),
            ),
        ),
    ),
    Point(
        "merge",
        "ci.yml",
        ("push",),
        inherits="gate",
        jobs=(
            Job(
                "deploy",
                needs=("gate",),
                fetch="tags",
                token="job",
                docs_tools=True,
                deploy=True,
                note=(
                    "The site's deploy through the contract's seam, on the"
                    " push alone. The release view reads the receipt tags; a"
                    " shallow tagless clone renders its no-tags fallback page"
                    " instead."
                ),
            ),
            Job(
                "govern",
                fetch="2",
                token="admin",
                note=(
                    "The repository settings reconciled when the merge"
                    " changed a contract or the owners file. The commit's own"
                    " file list decides whether anything is governed; a depth"
                    " of one would read a squash as a root."
                ),
            ),
            Job(
                "dispatch",
                needs=("gate",),
                fetch="full",
                token="job",
                note=(
                    "The release wave is dispatched from here, after main's"
                    " own verdict: the verb reads the manifest at HEAD and the"
                    " receipts on the remote, and is green unless a merged"
                    " release is unpublished. The commit that stamped the"
                    " manifest can be far back."
                ),
            ),
        ),
    ),
    Point(
        "nightly",
        "nightly.yml",
        ("schedule", "workflow_dispatch"),
        cron="17 4 * * *",
        note=(
            "The nightly point: the clock and a dispatch entry, one call per"
            " Python. What runs is the workshop's builtin schedule plus"
            " [[ci.schedule]] in workshop.toml, and the tests that declare"
            " the nightly point are selected in."
        ),
        jobs=(
            Job(
                "nightly",
                matrix="pythons",
                fetch="full",
                token="repository",
                note=(
                    "A replay checks the tree out at a release tag. A pull"
                    " request a scheduled task opens with the job token starts"
                    " no workflow, so the repository's token carries the"
                    " nightly where there is one."
                ),
            ),
        ),
    ),
    Point(
        "release",
        "release.yml",
        ("workflow_dispatch",),
        inputs=(
            Input("ref", "the release squash to publish", required=True),
            Input(
                "workshop",
                "a released livery-workshop version to drive the wave; empty"
                " runs the squash's own",
            ),
        ),
        ref_input="ref",
        note=(
            "The train: a workflow.release PR merges, this publishes its"
            " squash, and the receipt tags are cut only after the index"
            " confirms each member. A tag is a receipt, never a trigger: the"
            " merge point's dispatch job starts the wave at the release"
            " squash, and a hand dispatch with --ref is the recovery entry"
            " when a publish died mid-wave; --workshop names a released"
            " driver for a wave whose own workshop was the fault."
        ),
        jobs=(
            Job(
                "wheels",
                matrix="wheels",
                only="wheels",
                fetch="full",
                driver_pin=True,
                publishes="wheels",
                note=(
                    "Every platform's wheels, built before the wave: the"
                    " matrix feeds the publish job through artifacts, so one"
                    " release ships the complete set."
                ),
            ),
            Job(
                "publish",
                needs=("wheels",),
                fetch="full",
                token="repository",
                writes=True,
                collects="wheels",
                environment="pypi",
                driver_pin=True,
                note=(
                    "The wave: publish the ref, cut the receipt tags after"
                    " the index confirms each member. The receipt push"
                    " carries the repository's token where there is one: the"
                    " job token may not push a ref whose commit carries a"
                    " workflow file that differs from the tip's."
                ),
            ),
            Job(
                "templates",
                needs=("publish",),
                only="home",
                token="secret",
                deploy_key="WORKSHOP_TEMPLATES_DEPLOY_KEY",
                driver_pin=True,
                note=(
                    "The home's release aftermath: the (composed) template"
                    " artifact, tagged in lockstep with the publishing"
                    " layer's receipt."
                ),
            ),
        ),
    ),
)


def verify_points(points: tuple[Point, ...]) -> None:
    """Refuse a set of declarations a shell cannot be rendered from.

    A point name declared twice, an event outside `EVENT_NAMES`, a
    point inheriting one that is not declared, two points sharing a
    workflow file whose events overlap (a run would belong to both),
    a job name declared twice in one point, a job needing a job the
    point does not have, and a job collecting an artifact no job of
    the point publishes each refuse naming the point and the fault.
    """
    by_name: dict[str, Point] = {}
    for point in points:
        if point.name in by_name:
            fail(f"the {point.name} point is declared twice")
        by_name[point.name] = point
    for point in points:
        for event in point.events:
            if event not in EVENT_NAMES:
                fail(
                    f"the {point.name} point runs on {event!r}, which is not an"
                    f" event; the events are {', '.join(EVENT_NAMES)}"
                )
        if point.inherits and point.inherits not in by_name:
            fail(
                f"the {point.name} point inherits {point.inherits!r}, which is"
                " not a declared point"
            )
        if ("schedule" in point.events) != bool(point.cron):
            fail(
                f"the {point.name} point "
                + (
                    "runs on the clock and names no cron"
                    if "schedule" in point.events
                    else "names a cron and does not run on the clock"
                )
            )
        for other in points:
            if other.name >= point.name or other.workflow != point.workflow:
                continue
            shared = sorted(set(point.events) & set(other.events))
            if shared:
                fail(
                    f"the {other.name} and {point.name} points share"
                    f" {point.workflow} and both run on {', '.join(shared)}:"
                    " a run would belong to both"
                )
        inherited = by_name[point.inherits].jobs if point.inherits else ()
        names: list[str] = [job.name for job in inherited]
        for job in point.jobs:
            if job.name in names:
                fail(f"the {point.name} point declares the job {job.name!r} twice")
            names.append(job.name)
        published = {
            job.publishes for job in (*inherited, *point.jobs) if job.publishes
        }
        for job in point.jobs:
            for need in job.needs:
                if need not in names:
                    fail(
                        f"the {point.name} point's {job.name} job needs"
                        f" {need!r}, which the point does not have; its jobs"
                        f" are {', '.join(names)}"
                    )
            if job.collects and job.collects not in published:
                fail(
                    f"the {point.name} point's {job.name} job collects"
                    f" {job.collects!r}, which no job of the point publishes"
                )


verify_points(DECLARED)

#: The declared points by name.
POINT_BY_NAME: dict[str, Point] = {point.name: point for point in DECLARED}

#: The points, in the order a change meets them.
POINTS = tuple(point.name for point in DECLARED)

#: The workflow file each point's shell is: the gate and the merge
#: point share one, the nightly and the release have their own.
WORKFLOWS = {point.name: point.workflow for point in DECLARED}

#: The events that trigger each point's runs, in the forges' words.
EVENTS = {point.name: point.events for point in DECLARED}

#: The points a person starts by hand through ``ci.dispatch``: a
#: dispatch entry and no inputs to supply. The merge point runs on a
#: push alone, and the release wave takes inputs, so the merge point
#: dispatches it through ``workflow.release.dispatch``.
DISPATCHABLE = tuple(
    point.name
    for point in DECLARED
    if "workflow_dispatch" in point.events and not point.inputs
)

#: A point whose jobs include another point's: the merge point runs
#: the gate's jobs and its own.
INHERITS = {point.name: point.inherits for point in DECLARED if point.inherits}

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
        every: The cadence, ``""`` for every run of the point, ``1w``
            for one run a week, ``2w`` for one run every two weeks;
            a run on any other day skips the entry and names the day
            it runs next.
    """

    point: str
    job: str
    task: str
    args: tuple[str, ...] = ()
    profiled: bool = False
    source: str = "builtin"
    every: str = ""


#: The cadences an entry may declare, in days.
CADENCES = {"1w": 7, "2w": 14}

today: Callable[[], date] = date.today
"""The day a run judges a cadence on, UTC as the runners keep it.

A variable, not a function, so a test fixes the day instead of
waiting for a Monday.
"""


def due(every: str, on: date) -> tuple[bool, date]:
    """Whether a cadence runs on *on*, and the day it runs next.

    A weekly entry runs on Mondays; a two-weekly entry on the Monday
    of an even ISO week, so every workspace agrees on the day without
    a record of the last run. An empty cadence is due on every run.
    """
    if not every:
        return True, on
    monday = on - timedelta(days=on.weekday())
    if every == "1w":
        return on == monday, (monday if on == monday else monday + timedelta(days=7))
    even = monday.isocalendar().week % 2 == 0
    if on == monday and even:
        return True, on
    following = monday + timedelta(days=7)
    if following.isocalendar().week % 2 != 0:
        following += timedelta(days=7)
    return False, following


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


def declared(root: Path) -> tuple[Entry, ...]:
    """The ``[[ci.schedule]]`` entries of *root*'s contract, refusing bad ones.

    Each entry names a ``point`` (one of `POINTS`), a ``task``, and
    optionally a ``job`` (the point's own name when absent), ``args``
    and ``every``, a cadence from `CADENCES`. An unknown point, a
    missing task, arguments that are not strings, or a cadence that
    is not one refuse at load, naming the entry.
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
        every = str(item.get("every", ""))
        if every and every not in CADENCES:
            fail(
                f"[[ci.schedule]] entry {index} ({point}, {task}): every {every!r}"
                f" is not a cadence; the cadences are {', '.join(CADENCES)}"
            )
        entries.append(
            Entry(
                point,
                str(item.get("job", point)),
                task,
                tuple(args),
                source="workshop.toml",
                every=every,
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
    """The jobs *point* has: declared ones, inherited first, then any an entry names.

    A ``[[ci.schedule]]`` entry may name a job no declaration has; it
    is listed after the declared jobs, in schedule order.
    """
    if point not in POINT_BY_NAME:
        fail(f"{point!r} is not a point; the points are {', '.join(POINTS)}")
    declared_point = POINT_BY_NAME[point]
    inherited = (
        POINT_BY_NAME[declared_point.inherits].jobs if declared_point.inherits else ()
    )
    names: list[str] = [job.name for job in (*inherited, *declared_point.jobs)]
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
    leg. An entry with a cadence runs only on its day, judged by
    `today`, and says when it runs next otherwise. A profiled entry
    runs under ``--profile`` with its trace
    left at `TRACE`. Every child's environment names the leg in
    `livery.workshop._state.LEG_VARIABLE`, the key of the leg's rows
    and stamps, and the resolved point in
    `livery.workshop._pytest_points.POINT_VARIABLE`, which selects the
    tests a point runs. *spawn* runs one entry's command in that
    environment and returns its exit code; the default is the runner's
    own child.
    """
    from livery.workshop._state import SNAPSHOT_VARIABLE, remote_snapshot

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
    with remote_snapshot(root, publish=True) as published:
        env = {**os.environ, LEG_VARIABLE: label, POINT_VARIABLE: resolved}
        if published:
            env[SNAPSHOT_VARIABLE] = published
        for entry in entries:
            if entry.every:
                is_due, when = due(entry.every, today())
                if not is_due:
                    print(
                        f"  {resolved}/{job}: {entry.task} ({entry.source}) runs"
                        f" every {entry.every}; next on {when:%Y-%m-%d}, skipped"
                    )
                    continue
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
