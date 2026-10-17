"""CI as points: what each point is, what it runs, and the verb that runs it.

A consumer never thinks about CI. They reason about a named point in
the process and attach a task to it. The four points are ``gate``
(a pull request, or a dispatch by hand), ``merge`` (main after a
merge), ``nightly`` (the clock), and ``release`` (dispatched by the
merge point). A point is a declaration, [livery.workshop._points.Point][]:
its workflow file, its events, and its jobs, each job stating what it
needs in the workshop's words ([livery.workshop._points.Job][]); each
forge's renderer says those in its own. The emitted workflow is a
static shell, one ``fm ci.run --point=<p> --job=<j>`` per job, with
``--shard=<i>of<n>`` on a check leg the contract splits, and what a
job does is data: the builtin schedule below, plus the
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

A package contributes a point of its own through ``[[ci.point]]`` in
its ``workshop.toml``: a scheduled point with a dispatch entry, one
job on the runners and Pythons it names, running the task it names
with the job token and nothing more. `points` is every point a
workspace has, the builtin four first; every reader takes that set.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import livery.footman as footman
from livery.footman import Tasks, fail
from livery.workshop._ci_context import (
    LEG_VARIABLE,
    POINT_VARIABLE,
    run_context,
    state_store,
)
from livery.workshop._contract import load_contract
from livery.workshop._shard import ONE, Shard

if TYPE_CHECKING:
    from livery.workshop._influence import Inputs

#: The events a point may run on, in the forges' words.
EVENT_NAMES = ("pull_request", "push", "schedule", "workflow_dispatch")


#: The nightly's dispatch input naming the scheduled tasks a run takes,
#: comma-separated; empty runs the whole schedule. A rehearsal names the
#: loop alone, ``fm ci.dispatch --point=nightly --only=ci.e2e``.
ONLY = "only"


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
            the whole matrix on the first runner, ``runners`` for one
            per runner label the job's ``labels`` function names, with
            no Python of its own, ``declared`` for one per runner and
            Python the job itself names, ``""`` for one job on one
            runner.
        runners: The runners a ``declared`` matrix fans out to.
        pythons: The Pythons a ``declared`` matrix fans out to.
        labels: The function naming the runner labels a ``runners``
            matrix fans out to, given the workspace's root; None for a
            job of any other matrix. A ``runners`` job whose function
            names no label renders no job, and a need on it drops.
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
        pushes: Whether the job pushes through git itself, so the
            checkout carries the credential the push needs.
        publishes_index: Whether the job uploads to a package index,
            so the index token reaches it as ``UV_PUBLISH_TOKEN``.
        installs: The function naming the system packages the job
            installs before it enters, given the workspace's root;
            None for none. The extension that contributes the job
            names it, so the render installs them without knowing
            what they are for.
        conan_cache: Whether the job builds native packages, so the
            lane restores and saves conan's home around it.
        deploy: The function naming the seam the job publishes a site
            through after the call, given the workspace's root; None
            for a job that publishes none. A ``pages`` seam is the
            forge's own hosting: GitHub's grant and upload, GitLab's
            ``pages`` job.
        publishes: The artifact the job uploads, ``""`` for none.
        paths: The paths the uploaded artifact carries, as the forge
            globs them; ``()`` for a job that publishes none.
        collects: The artifact the job downloads first, ``""`` for none.
        environment: The named deployment environment the job runs in,
            ``""`` for none.
        dispatches: Whether the job starts another workflow through the
            forge's API, which GitHub's workflow token may do only with
            the ``actions: write`` grant.
        inputs: The files the job's entries read, declared as a step
            declares its own: a pull request's run that changed none of
            them skips the entries. None for a job that always runs.
        step: What the forge's run page calls the one call; the job's
            name capitalised when empty.
        note: The comment the rendered shell prints above the job.
        joins: Whether the job is a contribution to a job the point
            already has, declared or inherited. It renders as no job of
            its own: its entries follow that job's, and its plumbing,
            what it collects and publishes, the seam it deploys through
            and the packages it installs, renders on that job under the
            contributing point's own event condition.
    """

    name: str
    matrix: str = ""
    runners: tuple[str, ...] = ()
    pythons: tuple[str, ...] = ()
    labels: Callable[[Path], Sequence[str]] | None = None
    needs: tuple[str, ...] = ()
    always: bool = False
    fetch: str = ""
    token: str = ""
    writes: bool = False
    pushes: bool = False
    publishes_index: bool = False
    installs: Callable[[Path], Sequence[str]] | None = None
    conan_cache: bool = False
    deploy: Callable[[Path], str] | None = None
    publishes: str = ""
    paths: tuple[str, ...] = ()
    collects: str = ""
    environment: str = ""
    dispatches: bool = False
    inputs: Inputs | None = None
    step: str = ""
    note: str = ""
    joins: bool = False


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
        inputs: The inputs a dispatch takes. A point with a required
            one is dispatched by a verb that supplies it, never by
            hand. Every job of a point that takes a ``workshop`` input
            installs the released workshop it names, over the
            checkout's own, before its one call.
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


#: When the clock starts a scheduled point, UTC: the nightly's hour,
#: and every contributed point's, whose cadence is judged on the
#: runner by `due` rather than by a cron of its own.
CLOCK = "17 4 * * *"

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
                note=(
                    "The check legs: each runs the gate, and its row rides"
                    " its per-run ref on the state store, in one write with"
                    " the files its other entries left, for the gate job."
                    " The scoped gate measures from"
                    " the proved tree nearest the checkout's, an earlier"
                    " push's merge rebuilt from its two commits among them,"
                    " or from the merge base with the pull request's base"
                    " branch; each needs history a shallow clone lacks."
                ),
            ),
            Job(
                "gate",
                needs=("check",),
                always=True,
                fetch="full",
                token="job",
                writes=True,
                dispatches=True,
                step="Verdict",
                note=(
                    "The one required context. Branch protection points"
                    " here, so the matrix can grow or shrink without touching"
                    " repository settings. Its entries run the judges the"
                    " extensions add, collect the run's timing rows, ask the"
                    " forge for the jobs it needs, and stamp the tree a green"
                    " run proved; a red run is judged too. The stamp composes"
                    " a narrowed run with the record of the tree its legs"
                    " measured from, and names the two commits a merge"
                    " checkout joined. On a push to main the same job then"
                    " dispatches the release wave, runs what the extensions"
                    " add to the merge point, and runs the janitor."
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
        ),
    ),
    Point(
        "nightly",
        "nightly.yml",
        ("schedule", "workflow_dispatch"),
        inputs=(
            Input(
                ONLY,
                "the scheduled tasks to run, comma-separated; empty runs the"
                " whole schedule",
            ),
        ),
        cron=CLOCK,
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
                writes=True,
                note=(
                    "A verification checks the tree out at a release tag. A pull"
                    " request a scheduled task opens with the job token starts"
                    " no workflow, so the repository's token carries the"
                    " nightly where there is one. The job's profile trace"
                    " rides the state store at its end, which needs the write"
                    " grant as the check legs' puts do."
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
                "publish",
                fetch="full",
                token="repository",
                writes=True,
                pushes=True,
                publishes_index=True,
                environment="pypi",
                step="Publish the wave",
                note=(
                    "The wave: publish the ref, cut the receipt tags after"
                    " the index confirms each member. The receipt push"
                    " carries the repository's token where there is one: the"
                    " job token may not push a ref whose commit carries a"
                    " workflow file that differs from the tip's."
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
    point does not have, a job collecting an artifact no job of the
    point publishes, and a job collecting an artifact from jobs it
    does not wait for each refuse naming the point and the fault. A
    job starts once the jobs it needs end, so a job it does not wait
    for may not have uploaded the artifact yet. A join waits for what
    the job it joins waits for.
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
        names: list[str] = [job.name for job in inherited if not job.joins]
        for job in point.jobs:
            if job.joins:
                continue
            if job.name in names:
                fail(f"the {point.name} point declares the job {job.name!r} twice")
            names.append(job.name)
        lineage = (*inherited, *point.jobs)
        # A join renders on the job it joins: it waits for what that job
        # waits for, and its own needs never render.
        waits = {job.name: job.needs for job in lineage if not job.joins}
        for job in point.jobs:
            for need in job.needs:
                if need not in names:
                    fail(
                        f"the {point.name} point's {job.name} job needs"
                        f" {need!r}, which the point does not have; its jobs"
                        f" are {', '.join(names)}"
                    )
            if not job.collects:
                continue
            publishers = list(
                dict.fromkeys(
                    other.name for other in lineage if other.publishes == job.collects
                )
            )
            if not publishers:
                fail(
                    f"the {point.name} point's {job.name} job collects"
                    f" {job.collects!r}, which no job of the point publishes"
                )
            if not set(publishers) & set(waits.get(job.name, job.needs)):
                fail(
                    f"the {point.name} point's {job.name} job collects"
                    f" {job.collects!r} and waits for no job that publishes it"
                    f" ({', '.join(publishers)}); a job collects only from the"
                    " jobs it needs"
                )


verify_points(DECLARED)

#: The builtin points by name.
POINT_BY_NAME: dict[str, Point] = {point.name: point for point in DECLARED}

#: The builtin points, in the order a change meets them. A workspace
#: may have more: `points` adds the ones its packages contribute.
POINTS = tuple(point.name for point in DECLARED)

#: What a contributed point's name may look like: a workflow file
#: name and a job name at once.
POINT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


#: The runner's merged task tree, kept by the workshop's ``pre_tasks``
#: hook for the span of one invocation: discovery merges every extension's
#: tree into the invocation and resets the module-level root, so a
#: check against what this runner mounts reads it here.
MOUNTED: Tasks | None = None


def _mounted(task: str) -> bool:
    """Whether the runner mounts the task at the dotted address *task*.

    The invocation's merged tree when a run kept one. A process that
    kept no tree, a test reading the repository's own contract, holds
    only what it happened to import, never the workspace's mounted
    set, so it cannot answer and defers: the runner checks at the
    point's dispatch and at every load inside a run.
    """
    if MOUNTED is None:
        return True
    return MOUNTED.get(task) is not None


@dataclass(frozen=True)
class Contribution:
    """A point a package contributes, and the entry that runs it.

    Attributes:
        point: The point, a scheduled one with a dispatch entry and
            one job named after it.
        entry: The task the job runs, with its cadence.
    """

    point: Point
    entry: Entry


def contributed(
    root: Path, *, mounted: Callable[[str], bool] | None = None
) -> tuple[Contribution, ...]:
    """The ``[[ci.point]]`` declarations of *root*'s packages, refusing bad ones.

    Each table names a ``name`` (the point's, a workflow file name and
    a job name at once), a ``task``, and optionally ``args``,
    ``every`` (a cadence from `CADENCES`), ``runners`` (the root
    contract's when absent) and ``pythons`` (the newest gate Python
    when absent). A name that is a builtin point or that two packages
    claim, a name `POINT_NAME` refuses, a cadence that is not one, a
    task *mounted* does not know (the runner's own registry when
    absent), and a permission, secret or environment key each refuse
    at load, naming the package and the point.
    """
    from livery.workshop._packages import discover_packages
    from livery.workshop._pythons import gate_pythons

    mounted = mounted or _mounted
    contract = load_contract(root / "workshop.toml")
    ci_table = contract.get("ci") or {}
    default_runners = tuple(
        str(r) for r in (ci_table.get("runners") or ["ubuntu-latest"])
    )
    default_pythons: tuple[str, ...] | None = None
    found: dict[str, str] = {}
    contributions: list[Contribution] = []
    for package in discover_packages(root):
        package_contract = load_contract(package.directory / "workshop.toml")
        raw = (package_contract.get("ci") or {}).get("point") or []
        if not isinstance(raw, list):
            fail(f"{package.path}: [ci] point must be a list of [[ci.point]] tables")
        for index, item in enumerate(raw, start=1):
            where = f"{package.path} [[ci.point]] entry {index}"
            if not isinstance(item, dict):
                fail(f"{where} is not a table")
            name = str(item.get("name", ""))
            task = str(item.get("task", ""))
            if not name:
                fail(f"{where}: names no point")
            if name in POINT_BY_NAME:
                fail(
                    f"{where}: {name!r} is a builtin point; a package contributes"
                    " a point of its own and adds no job to the gate"
                )
            if not POINT_NAME.match(name):
                fail(
                    f"{where}: {name!r} is not a point name; a name is a workflow"
                    " file's, lower-case letters, digits and dashes, starting"
                    " with a letter"
                )
            if name in found:
                fail(
                    f"{where}: {name!r} is already the point {found[name]}"
                    " declares; two packages cannot share one"
                )
            if not task:
                fail(f"{where} ({name}): names no task")
            if not mounted(task):
                fail(
                    f"{where} ({name}): the runner mounts no task {task!r}; a"
                    " point runs a task some extension mounts"
                )
            args = item.get("args", [])
            every = str(item.get("every", ""))
            runners = item.get("runners", list(default_runners))
            pythons = item.get("pythons")
            if pythons is None:
                if default_pythons is None:
                    default_pythons = (gate_pythons(root)[-1],)
                pythons = list(default_pythons)
            for label, values in (("runners", runners), ("pythons", pythons)):
                if not values or not all(values):
                    fail(
                        f"{where} ({name}): {label} must be a non-empty list of strings"
                    )
            found[name] = package.path
            point = Point(
                name,
                f"{name}.yml",
                ("schedule", "workflow_dispatch"),
                cron=CLOCK,
                note=(
                    f"The {name} point, contributed by {package.path}: the clock"
                    " and a dispatch entry, one call per runner and Python it"
                    " names, with the job token and nothing more."
                ),
                jobs=(
                    Job(
                        name,
                        matrix="declared",
                        runners=tuple(str(r) for r in runners),
                        pythons=tuple(str(v) for v in pythons),
                        token="job",
                    ),
                ),
            )
            entry = Entry(
                name, name, task, tuple(args), source=package.path, every=every
            )
            contributions.append(Contribution(point, entry))
    return tuple(contributions)


@dataclass(frozen=True)
class JobContribution:
    """One job a mounted extension adds to a builtin point, with its entries.

    Attributes:
        point: The builtin point the job joins.
        job: The job, as the point would declare it.
        entries: The tasks the job runs, each naming the point and the job.
        gates: Whether the point's verdict waits for the job.
        extension: The extension that contributed it, for a refusal.
    """

    point: str
    job: Job
    entries: tuple[Entry, ...] = ()
    gates: bool = False
    extension: str = ""


_CONTRIBUTED_JOBS: dict[tuple[str, str], JobContribution] = {}

#: The jobs the extensions joined, by point, job and extension: several
#: extensions may join one job.
_JOINED_JOBS: dict[tuple[str, str, str], JobContribution] = {}


def point_has_job(point: str, name: str) -> bool:
    """Whether the builtin *point* has the job *name*, declared, inherited or added."""
    by_name = {declared.name: declared for declared in DECLARED}
    if point not in by_name:
        return False
    lineage = [point]
    if by_name[point].inherits:
        lineage.append(by_name[point].inherits)
    for name_ in lineage:
        if name in {job.name for job in by_name[name_].jobs}:
            return True
        if any(item.job.name == name for item in contributed_jobs(name_)):
            return True
    return False


def verdict_jobs() -> tuple[tuple[str, str], ...]:
    """Each builtin point that declares a verdict job of its own, with the job's name.

    A verdict job runs whatever the jobs it needs did; the judges an
    extension adds join one of these.
    """
    return tuple(
        (point.name, job.name) for point in DECLARED for job in point.jobs if job.always
    )


def judges_refusal(point: str, job: str) -> str:
    """Why judges may not join *job* of *point*; empty when they may.

    Judges join a point's own verdict job alone: they run before it
    collects the run's rows, and a red one stops it before the verdict
    and the stamp.
    """
    known = verdict_jobs()
    if (point, job) in known:
        return ""
    listed = ", ".join(f"the {name} point's {verdict} job" for name, verdict in known)
    return (
        f"names judges on the {point} point's {job} job, which is not a verdict"
        f" job a point declares; judges join {listed}, before it collects the"
        " run's rows"
    )


def contribute_job(
    point: str,
    job: Job,
    *,
    entries: tuple[Entry, ...] = (),
    gates: bool = False,
    extension: str,
) -> None:
    """Add *job* to the builtin *point* on behalf of *extension*, or join one.

    The job sits before the first job of the point that needs it, else
    before the point's verdict job, else last (`composed_points`), so a
    contributed job renders before the jobs that wait for it and where
    a declared one would; a gating job joins the verdict's needs. A
    *job* that ``joins`` names a job the point already has
    (`point_has_job`): its entries follow that job's own, before any
    the job runs last, its plumbing renders on that job, and its needs
    join the needs of a job the point has of its own. Refuses a point
    that is not builtin, a job name the point already has when the job
    does not join, a join of a job the point does not have, a join that
    gates, a name another extension contributed, an entry naming
    another point or job, and a judge on a job that is not the point's
    own verdict job (`judges_refusal`).
    """
    by_name = {declared.name: declared for declared in DECLARED}
    if point not in by_name:
        fail(
            f"{extension} contributes the job {job.name!r} to {point!r}, which is not a"
            f" builtin point; the points are {', '.join(by_name)}"
        )
    for entry in entries:
        if entry.point != point or entry.job != job.name:
            fail(
                f"{extension} contributes the job {job.name!r} to the {point} point"
                f" with an entry for {entry.point}/{entry.job}; an entry names"
                " the job it runs in"
            )
    if any(entry.judges for entry in entries):
        why = judges_refusal(point, job.name)
        if why:
            fail(f"{extension} {why}")
    if job.joins:
        if not point_has_job(point, job.name):
            fail(
                f"{extension} joins the job {job.name!r} of the {point} point,"
                " which has no such job"
            )
        if gates:
            fail(
                f"{extension} joins the job {job.name!r} of the {point} point and"
                " gates; a join adds entries and plumbing, and the verdict's"
                " needs stay the job's own"
            )
        _JOINED_JOBS[(point, job.name, extension)] = JobContribution(
            point, job, tuple(entries), False, extension
        )
        return
    declared_names = [declared.name for declared in by_name[point].jobs]
    if job.name in declared_names:
        fail(
            f"{extension} contributes the job {job.name!r} to the {point} point, which"
            " declares it already"
        )
    other = _CONTRIBUTED_JOBS.get((point, job.name))
    if other is not None and other.extension != extension:
        fail(
            f"{extension} contributes the job {job.name!r} to the {point} point, which"
            f" {other.extension} contributed already"
        )
    _CONTRIBUTED_JOBS[(point, job.name)] = JobContribution(
        point, job, tuple(entries), gates, extension
    )


def snapshot(
    *, initial: bool = False
) -> tuple[
    dict[tuple[str, str], JobContribution],
    dict[tuple[str, str, str], JobContribution],
    Tasks | None,
]:
    """The jobs the extensions added and joined, and the kept task tree, for restore.

    Judges ride their joins' entries. [livery.workshop._points.restore][]
    puts all three back. With *initial*, the state a process starts with
    instead: no job added or joined, and no task tree kept.
    """
    if initial:
        return {}, {}, None
    return dict(_CONTRIBUTED_JOBS), dict(_JOINED_JOBS), MOUNTED


def restore(
    state: tuple[
        dict[tuple[str, str], JobContribution],
        dict[tuple[str, str, str], JobContribution],
        Tasks | None,
    ],
) -> None:
    """Put the added jobs, the joins and the kept task tree back to *state*."""
    global MOUNTED
    added, joined, tree = state
    _CONTRIBUTED_JOBS.clear()
    _CONTRIBUTED_JOBS.update(added)
    _JOINED_JOBS.clear()
    _JOINED_JOBS.update(joined)
    MOUNTED = tree


def contributed_jobs(point: str) -> tuple[JobContribution, ...]:
    """The jobs the extensions contributed to *point*, in contribution order."""
    return tuple(item for item in _CONTRIBUTED_JOBS.values() if item.point == point)


def joined_jobs(point: str) -> tuple[JobContribution, ...]:
    """The joins the extensions made to *point*'s jobs, in contribution order."""
    return tuple(item for item in _JOINED_JOBS.values() if item.point == point)


def contributor_of(point: str, job: str) -> str:
    """The extension that contributed *job* to *point*; the base for its own jobs."""
    item = _CONTRIBUTED_JOBS.get((point, job))
    return item.extension if item is not None else "livery.workshop"


def verdict_needs(point: str) -> tuple[str, ...]:
    """What *point*'s verdict waits for: its declared needs, then the gating jobs."""
    declared = {item.name: item for item in DECLARED}
    verdict = next((job for job in declared[point].jobs if job.always), None)
    needs = tuple(verdict.needs) if verdict is not None else ()
    return needs + tuple(
        item.job.name for item in contributed_jobs(point) if item.gates
    )


def composed_points() -> tuple[Point, ...]:
    """The builtin points with the extensions' contributed jobs in place.

    A contributed job sits before the first job of its point that needs
    it, else before the point's verdict job, else last: a job renders
    before the jobs that wait for it, and where a declared one would.
    The verdict waits for what `verdict_needs` names. A join's needs
    join the needs of the job it joins when the point has that job of
    its own, declared or contributed; the read of a declaration refuses
    needs on a join of a job the point inherits, whose needs are the
    point's it inherits from.
    """
    composed: list[Point] = []
    for point in DECLARED:
        added = [item.job for item in contributed_jobs(point.name)]
        joined = [item.job for item in joined_jobs(point.name)]
        if not added and not joined:
            composed.append(point)
            continue
        jobs = [
            _waiting(
                replace(job, needs=verdict_needs(point.name)) if job.always else job,
                joined,
            )
            for job in point.jobs
        ]
        for job in added:
            needing = [i for i, other in enumerate(jobs) if job.name in other.needs]
            verdict = [i for i, other in enumerate(jobs) if other.always]
            jobs.insert((needing or verdict or [len(jobs)])[0], _waiting(job, joined))
        # A join renders as no job: it rides the point's jobs so every
        # reader of what a job installs, deploys and publishes sees it.
        jobs.extend(joined)
        composed.append(replace(point, jobs=tuple(jobs)))
    return tuple(composed)


def _waiting(job: Job, joins: Sequence[Job]) -> Job:
    """*job* with the needs of each of *joins* that joins it added to its own."""
    extra = [need for join in joins if join.name == job.name for need in join.needs]
    if not extra:
        return job
    return replace(job, needs=tuple(dict.fromkeys((*job.needs, *extra))))


def points(root: Path | None) -> tuple[Point, ...]:
    """Every point *root* has: the builtin four, then the ones its packages contribute.

    ``None`` is a process outside any workspace, which has the builtin
    four alone, with the jobs the mounted extensions contributed in place.
    The whole set is verified as one: a contributed point cannot
    share a file or a name with another.
    """
    builtin = composed_points()
    if root is None:
        verify_points(builtin)
        return builtin
    everything = builtin + tuple(item.point for item in contributed(root))
    verify_points(everything)
    return everything


def emitted_points(root: Path) -> tuple[Point, ...]:
    """`points`, plus a job for each name only the contract's entries give.

    A ``[[ci.schedule]]`` entry in the root contract may name a job its
    point does not declare (`jobs_of` lists it); the shell needs that
    job to call it. The job runs on one runner, the newest gate
    Python, with the checkout depth, token and write grant of the
    point's first job: the root contract is where a grant is decided,
    so this widens nothing a package could reach.
    """
    everything = points(root)
    entries = declared(root)
    out: list[Point] = []
    for point in everything:
        names = {job.name for job in point.jobs} | {point.name}
        names |= {job.name for job in inherited_jobs(point, everything)}
        model = point.jobs[0] if point.jobs else Job(point.name)
        added: list[Job] = []
        for entry in entries:
            if entry.point != point.name or entry.job in names:
                continue
            names.add(entry.job)
            added.append(
                Job(
                    entry.job,
                    fetch=model.fetch,
                    token=model.token,
                    writes=model.writes,
                    note=(
                        f"The {entry.job} job: the [[ci.schedule]] entries"
                        " that name it, on one runner."
                    ),
                )
            )
        out.append(replace(point, jobs=(*point.jobs, *added)) if added else point)
    return tuple(out)


def inherited_jobs(point: Point, everything: tuple[Point, ...]) -> tuple[Job, ...]:
    """The jobs *point* runs before its own, from the point it inherits."""
    if not point.inherits:
        return ()
    return next(other for other in everything if other.name == point.inherits).jobs


def point_by_name(root: Path | None) -> dict[str, Point]:
    """`points`, by name."""
    return {point.name: point for point in points(root)}


def jobs_reading(root: Path, path: str) -> tuple[str, ...]:
    """The jobs whose declared inputs read *path*, each ``<point>/<job>``."""
    from livery.workshop._influence import reads

    return tuple(
        f"{point.name}/{job.name}"
        for point in points(root)
        for job in point.jobs
        if job.inputs is not None and reads(job.inputs, root, path)
    )


def unread_by_the_job(root: Path, point: str, job: str) -> str:
    """The line that skips *job* of *point* when nothing it reads changed, or empty.

    Only a job that declares ``inputs`` skips, and only on a pull
    request's run in a workspace declaring ``[ci] affected-legs``, the
    terms the check legs narrow on: a push, a dispatch, the clock and a
    person's own run always run every entry. The changes are the run's
    own ([livery.workshop.ci_changes][]), read the way the check legs
    read them, and the inputs select as a step's do, so a change to
    the sources of the extension that contributed the job runs it.
    """
    from livery.workshop._extensions import extension_provider
    from livery.workshop._influence import select
    from livery.workshop._packages import discover_packages
    from livery.workshop._quality import ci_changes

    declared = next(
        (item for item in point_by_name(root)[point].jobs if item.name == job), None
    )
    if declared is None or declared.inputs is None:
        return ""
    changes = ci_changes(root)
    if changes is None:
        return ""
    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    provider = extension_provider(contributor_of(point, job), packages)
    if select(declared.inputs, changes, provider=provider).runs:
        return ""
    return (
        f"  {point}/{job}: nothing the job reads changed in this run"
        f" ({len(changes.paths)} path(s) changed); its entries are skipped"
    )


def job_installs(
    root: Path, everything: tuple[Point, ...] | None = None
) -> dict[tuple[str, str], tuple[str, ...]]:
    """What each job installs before it enters, by its point and its name.

    Only a job that names an ``installs`` function has an entry. Its
    packages are sorted and named once, so the rendered file is the
    same on every run. *everything* is the points to read; *root*'s
    when absent.
    """
    everything = everything if everything is not None else points(root)
    return {
        (point.name, job.name): tuple(sorted(set(job.installs(root))))
        for point in everything
        for job in point.jobs
        if job.installs is not None
    }


def job_seams(
    root: Path, everything: tuple[Point, ...] | None = None
) -> dict[tuple[str, str], str]:
    """The seam each deploying job publishes through, by its point and its name.

    Only a job that names a ``deploy`` function has an entry.
    *everything* is the points to read; *root*'s when absent.
    """
    everything = everything if everything is not None else points(root)
    return {
        (point.name, job.name): job.deploy(root)
        for point in everything
        for job in point.jobs
        if job.deploy is not None
    }


def job_runners(
    root: Path, everything: tuple[Point, ...] | None = None
) -> dict[tuple[str, str], tuple[str, ...]]:
    """The runner labels each job's ``labels`` function names, by point and job.

    Only a job that names a ``labels`` function has an entry: the
    labels the function names for *root*, in its order and each once,
    so the rendered file is the same on every run. An empty entry is a
    ``runners`` job this workspace renders no job for. *everything* is
    the points to read; *root*'s when absent.
    """
    everything = everything if everything is not None else points(root)
    return {
        (point.name, job.name): tuple(dict.fromkeys(job.labels(root)))
        for point in everything
        for job in point.jobs
        if job.labels is not None
    }


def events_of(root: Path | None, point: str) -> tuple[str, ...]:
    """The events that start *point*'s runs; refuses a name that is not a point."""
    by_name = point_by_name(root)
    if point not in by_name:
        fail(f"{point!r} is not a point; the points are {', '.join(by_name)}")
    return by_name[point].events


def dispatchable(root: Path | None) -> tuple[str, ...]:
    """The points a person starts by hand: a dispatch entry and no input to supply.

    An optional input, the nightly's ``only``, leaves a point
    dispatchable; a required one, the release's ``ref``, does not.
    """
    return tuple(
        point.name
        for point in points(root)
        if "workflow_dispatch" in point.events
        and not any(item.required for item in point.inputs)
    )


#: The events that trigger each point's runs, in the forges' words.
EVENTS = {point.name: point.events for point in DECLARED}

#: The points a person starts by hand through ``ci.dispatch``: a
#: dispatch entry and no input to supply. The merge point runs on a
#: push alone, and the release wave takes a required input, so the
#: merge point dispatches it through ``workflow.release.dispatch``.
DISPATCHABLE = tuple(
    point.name
    for point in DECLARED
    if "workflow_dispatch" in point.events
    and not any(item.required for item in point.inputs)
)

#: A point whose jobs include another point's: the merge point runs
#: the gate's jobs and its own.
INHERITS = {point.name: point.inherits for point in DECLARED if point.inherits}


def _tracing(root: Path) -> bool:
    """Whether this workspace keeps a trace of what CI does."""
    from livery.workshop._traces import policy

    kept, _why = policy(root)
    return kept.legs


def profile_box() -> AbstractContextManager[Path]:
    """The job's drop box, where every entry leaves its own timeline.

    The plugin owns the directory and its sweeping; this names it to the
    entries through the environment the runner already builds for them,
    which is why nothing here writes this process's own.
    """
    from livery.footman.profile import box

    return box()


def _push_about(root: Path) -> None:
    """Record the commit this run ran on, where the forge files it elsewhere.

    A line and never a verdict, like the trace beside it. Written per
    job because any job of the run can write it and the write merges;
    a run whose own head is the commit it checked out writes nothing.
    """
    from livery.workshop._traces import about

    try:
        line = about(root)
    except Exception as error:
        line = f"profile: what this run ran on was not recorded ({error})"
    if line:
        print(f"  {line}")


def _push_job(root: Path, drop: Path, *, job: str, leg: str) -> None:
    """Write the job's own trace from what its entries left, and push it.

    *leg* is the label this job gave the entries it spawned. This
    process is not one of them, so its own environment never carried
    the label and the push has to be told.

    Every line is printed and none is a verdict: a job's timeline is
    something noticed about the work, never part of deciding it.
    """
    from livery.footman.profile import as_trace, swept
    from livery.workshop._traces import push

    try:
        events, origin = swept(drop)
        if not events:
            return
        written = drop.parent / f"{drop.name}.json"
        written.write_text(as_trace(events, origin=origin), encoding="utf-8")
        line = push(root, written, job=job, leg=leg)
        written.unlink(missing_ok=True)
    except Exception as error:
        line = f"profile: the job's trace was not kept ({error})"
    if line:
        print(f"  {line}")


def trace_file(task: str) -> str:
    """The file an entry's own trace is written to, named after the task.

    One file per entry rather than one per job: the leg's timing row reads
    the gate's own file, and a person reading a runner's working directory
    can tell which entry a trace belongs to.
    """
    from livery.git import slug

    return f"fm-profile-{slug(task)}.json"


#: The gate's own trace, which the leg's timing row reads.
TRACE = trace_file("check")


@dataclass(frozen=True)
class Entry:
    """One scheduled task: at which point and job, which task, which arguments.

    Attributes:
        point: One of `POINTS`.
        job: The job of the point the entry runs in.
        task: The task's dotted address, as the workspace mounts it.
        args: The arguments, each formatted with the run's facts:
            ``{display}`` the job's display name, ``{label}`` the
            leg's label, ``{os}`` and ``{python}`` the matrix values,
            ``{shard}`` the leg's shard spelled ``<i>of<n>`` (``1of1``
            on a leg that is not split), and each of the point's
            dispatch inputs by name (``{ref}`` on the release), empty
            when the run was not dispatched with it.
        source: Where the entry was declared.
        every: The cadence, ``""`` for every run of the point, ``1w``
            for one run a week, ``2w`` for one run every two weeks;
            a run on any other day skips the entry and names the day
            it runs next.
        once: Whether the entry runs on a matrix job's first leg alone,
            the first runner and the first Python, and on that leg's
            first shard alone; every leg and shard otherwise.
        sharded: Whether the entry takes the leg's shard,
            ``--shard=<i>of<n>`` after its arguments, when the leg
            runs as one of several shards; on a leg that runs as one
            shard of one the entry takes nothing more.
        last: Whether the entry runs after every entry an extension
            adds to the job, the janitor's shape; the added entries go
            before the first such entry.
        judges: Whether the entry judges the run before the verdict:
            a judge an extension adds to a point's verdict job runs
            before the job collects the run's rows, and a red one stops
            the job there, as a red builtin entry does.
    """

    point: str
    job: str
    task: str
    args: tuple[str, ...] = ()
    source: str = "builtin"
    every: str = ""
    once: bool = False
    last: bool = False
    judges: bool = False
    sharded: bool = False


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


#: The task that collects the run's rows in the verdict job: the
#: judges the extensions add run before it, so what they leave for
#: the run's row is folded in.
COLLECT = "ci.metrics.collect"

#: The task that drops the run's per-run refs, the last entry of a job
#: that collected: after its verdict, and only once every job that
#: puts a half has completed green.
DROP_TASK = "ci.metrics.drop"

#: The task that ends every job of a point with no verdict job: it
#: puts the job's half from the traces its entries left, collects the
#: run's row, and drops the refs when it is the run's last green job.
RECORD_TASK = "ci.metrics.record"

#: The workshop's own schedule. Every entry runs profiled, so a job's
#: own timeline is the union of the work it did; the gate's check job
#: also records the leg's timing row, and its verdict job collects the
#: run's rows, then judges the jobs it needs. The merge
#: point adds the deploy, the governance reconcile, which classifies
#: its own commit and exits fast when no contract path changed, and
#: the release dispatch, green unless a merged release is unpublished.
BUILTIN: tuple[Entry, ...] = (
    # The walk takes the leg's shard when the leg is one of several, so
    # the steps that split run their share; an unsplit leg calls it bare.
    Entry("gate", "check", "check", sharded=True),
    # The leg's one write, after whatever the extensions add to the
    # job: its timing row and every file their entries left go on its
    # per-run ref together.
    Entry("gate", "check", "ci.leg", ("--job={display}",), last=True),
    # The title check first: it reads the pull request's title from
    # the event payload, is green off a release branch, and refuses a
    # release title the changelogs do not match before anything else
    # is judged. It runs here so the legs never wait for a job of
    # its own. The judges the extensions add run after it, before the
    # collect, so what they leave rides the run's row.
    Entry("gate", "gate", "workflow.release.check-title"),
    # The fold next, before any judge reads the run's refs: a platform
    # that ran as shards has one ref per shard, and the judges, the
    # collect and the stamp read one leg per platform.
    Entry("gate", "gate", "ci.fold"),
    Entry("gate", "gate", COLLECT),
    # The speed judge reads the run's row the collect just put: a
    # suite over its mark for the second run in a row is red here,
    # before the verdict, so the record never names a slow tree green.
    Entry("gate", "gate", "speed.judge"),
    # The verdict waits for the check and for every contributed job that
    # gates; `schedule` writes the composed list into this entry.
    Entry("gate", "gate", "ci.verdict", ("--needs=check",)),
    # After a green verdict only: a red verdict fails the job before
    # this entry, so the record never names a tree a run proved red.
    Entry("gate", "gate", "ci.verified.stamp"),
    # After the stamp, so the forge's merge follows it as closely as
    # it can: a release pull request whose base moved under its set is
    # refused before the merge, where the publish would refuse it
    # after. Green off a release branch. The stamp before it holds
    # either way: it records the tree, and the tree is green.
    Entry("gate", "gate", "workflow.release.check-fresh"),
    # Last of all, after a green verdict and whatever the extensions
    # add: the legs' per-run refs go only once this job has nothing
    # left to read from them, so a red attempt leaves them for its
    # re-run, which runs this job again without its legs.
    Entry("gate", "gate", DROP_TASK, ("--point=gate", "--job={display}"), last=True),
    Entry("merge", "govern", "workflow.configure", ("--if-changed",)),
    # The wave is dispatched from the gate job itself, right after
    # main's own verdict: the verb reads the manifest at HEAD and the
    # receipts on the remote, and is green unless a merged release is
    # unpublished. A job of its own cost a runner's entry for a second
    # of work.
    Entry("merge", "gate", "workflow.release.dispatch"),
    # The janitor after the stamp, on the merge point alone, and after
    # whatever the extensions add to the job: every merge tidies the
    # remote store, and a pull request's run writes nothing it does
    # not own.
    Entry("merge", "gate", "janitor", last=True),
    # The clock's point: the doctor first, so the night's record opens
    # with the runner's and the forge's state, mending what footman
    # can (a runner that keeps its caches across jobs holds the scratch
    # of processes that died there; a finding that stands after the
    # mend is still red), then the whole check with the tests that
    # declare the nightly point selected in, on every python of the
    # matrix.
    Entry("nightly", "nightly", "doctor", ("--fix",)),
    Entry("nightly", "nightly", "check"),
    # The wave, at the squash the dispatch names: the publish that cuts
    # the receipts. The verb decides for itself what the ref holds, and
    # whether a job of the run already built its wheels.
    Entry("release", "publish", "workflow.release.publish", ("--ref={ref}",)),
)


def declared(root: Path) -> tuple[Entry, ...]:
    """The ``[[ci.schedule]]`` entries of *root*'s contract, refusing bad ones.

    Each entry names a ``point`` (one of `points`), a ``task``, and
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
    names = tuple(point.name for point in points(root))
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            fail(f"[[ci.schedule]] entry {index} is not a table")
        point = str(item.get("point", ""))
        task = str(item.get("task", ""))
        if point not in names:
            fail(
                f"[[ci.schedule]] entry {index}: point {point!r} is not a"
                f" point; the points are {', '.join(names)}"
            )
        if not task:
            fail(f"[[ci.schedule]] entry {index} ({point}): names no task")
        # The contract's judge holds the arguments to strings and the
        # cadence to one of `CADENCES`.
        args = item.get("args", [])
        every = str(item.get("every", ""))
        once = bool(item.get("once", False))
        entries.append(
            Entry(
                point,
                str(item.get("job", point)),
                task,
                tuple(args),
                source="workshop.toml",
                every=every,
                once=once,
            )
        )
    return tuple(entries)


def builtin_schedule() -> tuple[Entry, ...]:
    """The builtin entries with the extensions' contributed jobs' entries in place.

    A contributed job's entries follow the declared entries of its
    point's jobs before the verdict, and the verdict's ``--needs``
    names the composed list.
    """
    from dataclasses import replace

    composed: list[Entry] = []
    for entry in BUILTIN:
        if entry.task == "ci.verdict":
            for item in contributed_jobs(entry.point):
                composed.extend(item.entries)
            composed.append(
                replace(
                    entry, args=(f"--needs={','.join(verdict_needs(entry.point))}",)
                )
            )
            continue
        composed.append(entry)
    seen = {(entry.point, entry.job, entry.task) for entry in composed}
    for point in DECLARED:
        if any(job.always for job in point.jobs):
            continue
        for item in contributed_jobs(point.name):
            composed.extend(
                entry
                for entry in item.entries
                if (entry.point, entry.job, entry.task) not in seen
            )
    for point in DECLARED:
        for item in joined_jobs(point.name):
            _join_entries(composed, item)
    return tuple(composed)


def _join_entries(composed: list[Entry], item: JobContribution) -> None:
    """Put *item*'s entries into *composed* where its job runs what it adds.

    Before the first entry of the job that runs last, else after the
    job's last entry, else at the end: the join's entries run after
    the job's own and before its sweep. A judge goes before the job's
    collect (`COLLECT`), so what it leaves for the run's row is folded
    in, and the verdict and the stamp come after it.
    """
    key = (item.point, item.job.name)
    owned = [i for i, e in enumerate(composed) if (e.point, e.job) == key]
    judges = [entry for entry in item.entries if entry.judges]
    if judges:
        at = next((i for i in owned if composed[i].task == COLLECT), None)
        if at is None:
            at = owned[-1] + 1 if owned else len(composed)
        composed[at:at] = judges
        owned = [i for i, e in enumerate(composed) if (e.point, e.job) == key]
    at = next((i for i in owned if composed[i].last), None)
    if at is None:
        at = owned[-1] + 1 if owned else len(composed)
    composed[at:at] = [entry for entry in item.entries if not entry.judges]


def schedule(root: Path) -> tuple[Entry, ...]:
    """Every entry: the builtin ones, the packages' contributed ones, the contract's.

    Then the record each job of a point without a verdict job ends
    with (`recording`), after every entry the contract gave the job.
    """
    scheduled = (
        builtin_schedule()
        + tuple(item.entry for item in contributed(root))
        + declared(root)
    )
    return scheduled + recording(root, scheduled)


def _has_verdict_job(point: Point) -> bool:
    """Whether *point*, or the point it inherits, declares a verdict job."""
    verdicts = {name for name, _job in verdict_jobs()}
    return point.name in verdicts or point.inherits in verdicts


def recording(root: Path, scheduled: tuple[Entry, ...]) -> tuple[Entry, ...]:
    """The record ending every job of each point of *root* with no verdict job.

    A verdict job collects what its point's legs put; a point without
    one (the nightly, the release, every point a package contributes)
    would leave no row of its run, so each of its jobs ends with
    `RECORD_TASK`, which puts the job's own half, collects the run's
    row and drops the per-run refs when it is the run's last green
    job. The jobs are the point's declared ones and any a
    ``[[ci.schedule]]`` entry in *scheduled* names.
    """
    entries: list[Entry] = []
    for point in points(root):
        if _has_verdict_job(point):
            continue
        jobs = [job.name for job in point.jobs]
        for entry in scheduled:
            if entry.point == point.name and entry.job not in jobs:
                jobs.append(entry.job)
        entries.extend(
            Entry(
                point.name,
                job,
                RECORD_TASK,
                (f"--point={point.name}", "--job={display}"),
                last=True,
            )
            for job in jobs
        )
    return tuple(entries)


def half_writers(root: Path, point: str) -> tuple[str, ...]:
    """The jobs of *point* that put a half of the run's row, by their schedule names.

    The check legs of a point with a verdict job (`ci.leg`), and every
    job of a point without one (`RECORD_TASK`). A forge lists a matrix
    job's legs under the job's name with the leg in brackets, so a
    reader matches by prefix.
    """
    return tuple(
        dict.fromkeys(
            entry.job
            for entry in schedule(root)
            if entry.task in ("ci.leg", RECORD_TASK)
            and entry.point in (INHERITS.get(point), point)
        )
    )


def workflow_of(point: str, root: Path | None = None) -> str:
    """The workflow file *point*'s shell is; refuses a name that is not a point."""
    by_name = point_by_name(root)
    if point not in by_name:
        fail(f"{point!r} is not a point; the points are {', '.join(by_name)}")
    return by_name[point].workflow


def jobs_of(root: Path, point: str) -> tuple[str, ...]:
    """The jobs *point* has: declared ones, inherited first, then any an entry names.

    A ``[[ci.schedule]]`` entry may name a job no declaration has; it
    is listed after the declared jobs, in schedule order.
    """
    by_name = point_by_name(root)
    if point not in by_name:
        fail(f"{point!r} is not a point; the points are {', '.join(by_name)}")
    declared_point = by_name[point]
    inherited = by_name[declared_point.inherits].jobs if declared_point.inherits else ()
    names: list[str] = []
    for job in (*inherited, *declared_point.jobs):
        if job.name not in names:
            names.append(job.name)
    for entry in schedule(root):
        if entry.point in (INHERITS.get(point), point) and entry.job not in names:
            names.append(entry.job)
    return tuple(names)


def first_leg(root: Path, point: str, job: str) -> tuple[str, str]:
    """The first leg of *job* at *point*: its first runner and its first Python.

    An axis the job does not have is empty. The check legs fan out
    over ``[ci] runners`` and the gate's Pythons, the nightly over every
    Python on the first runner, a declared job over its own lists. A
    ``runners`` job's call names no leg and a job with no matrix has
    none, so neither has a leg to name.
    """
    from livery.workshop._pythons import gate_pythons, python_matrix

    found = next(
        (item for item in point_by_name(root)[point].jobs if item.name == job), None
    )
    if found is None or not found.matrix:
        return "", ""
    ci = load_contract(root / "workshop.toml").get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    if found.matrix == "legs":
        return runners[0], gate_pythons(root)[0]
    if found.matrix == "pythons":
        return "", python_matrix(root)[0]
    if found.matrix == "declared":
        return (
            found.runners[0] if found.runners else "",
            found.pythons[0] if found.pythons else "",
        )
    return "", ""


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


def scheduled_tasks(root: Path, point: str) -> tuple[str, ...]:
    """Every task *point* runs, across its jobs, in schedule order, once each."""
    found: dict[str, None] = {}
    for job in jobs_of(root, point):
        for entry in entries_for(root, point, job):
            found.setdefault(entry.task, None)
    return tuple(found)


def asked_tasks(root: Path, point: str, only: str) -> tuple[str, ...]:
    """The tasks a dispatch's *only* names, judged against what *point* runs.

    Empty for an empty *only*, which runs the whole schedule.

    Raises:
        Failed: when *only* names a task the point does not run, naming
            the ones it does.
    """
    asked = tuple(
        dict.fromkeys(name.strip() for name in only.split(",") if name.strip())
    )
    if not asked:
        return ()
    runs = scheduled_tasks(root, point)
    unknown = [name for name in asked if name not in runs]
    if unknown:
        fail(
            f"the {point} point runs {', '.join(runs) or 'nothing'}; --only names"
            f" {', '.join(unknown)}, which it does not run"
        )
    return asked


#: The label a pull request carries so the gate runs every runner for
#: it, where ``[ci] pull-request-runners`` names fewer. `fm submit
#: --every-runner` sets it through the forge, and a person sets it on
#: the forge by hand; the gate workflow runs on ``labeled``, its legs
#: job picks the full matrix while the label is there, and the coverage
#: union expects every leg. The repository configuration creates the
#: label (`fm workflow.configure`).
EVERY_RUNNER_LABEL = "ci:every-runner"


def pull_request_runners(ci: dict[str, Any]) -> list[str]:
    """The runners a pull request's check legs fan out to.

    ``[ci] pull-request-runners`` when the contract declares it, else
    every runner ``[ci] runners`` names. A label the runners list does
    not name refuses naming both lists, since a pull request's legs run
    on runners every event runs on; an empty list refuses too.
    """
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    declared: Any = ci.get("pull-request-runners")
    if declared is None:
        return runners
    chosen = [str(runner) for runner in declared]
    strangers = [runner for runner in chosen if runner not in runners]
    if strangers:
        fail(
            f"[ci] pull-request-runners names {', '.join(strangers)}, which [ci]"
            f" runners does not list ({', '.join(runners)}); a pull request's"
            " legs run on runners every event runs on"
        )
    if not chosen:
        fail(
            "[ci] pull-request-runners is empty; name a runner [ci] runners"
            " lists, or remove the key so every runner runs"
        )
    return chosen


def pull_request_legs(root: Path) -> list[str]:
    """Name the check legs a pull request's run produces, ``check-<os>-<python>`` each.

    One per runner the root contract's ``[ci] pull-request-runners``
    names and Python the gate runs; every leg
    [livery.workshop.check_legs][] names when the key is absent. The
    task fails, naming both lists, when the key names a runner
    ``[ci] runners`` does not list, and when it is empty.
    """
    from livery.workshop._pythons import gate_pythons

    contract = load_contract(root / "workshop.toml")
    ci = contract.get("ci") or {}
    pythons = gate_pythons(root)
    return [
        f"check-{runner}-{python}"
        for runner in pull_request_runners(ci)
        for python in pythons
    ]


def check_legs(root: Path) -> list[str]:
    """Name the check legs the gate produces, ``check-<os>-<python>`` each.

    One per runner the root contract names (``[ci] runners``,
    ``ubuntu-latest`` without it) and Python the gate runs: the
    contract's ``[ci] python-versions``, or the newest Python of the
    derived matrix. Each label is spelled as the job runner names the
    leg it runs, the key of the leg's rows on the state store.
    """
    from livery.workshop._pythons import gate_pythons

    contract = load_contract(root / "workshop.toml")
    runners = list((contract.get("ci") or {}).get("runners") or ["ubuntu-latest"])
    pythons = gate_pythons(root)
    return [f"check-{runner}-{python}" for runner in runners for python in pythons]


#: The most shards a runner may take while ``[ci] shards-max`` is absent.
SHARDS_MAX = 4


def shards_max(ci: dict[str, Any]) -> int:
    """The most shards a runner may take: ``[ci] shards-max``, `SHARDS_MAX` when absent.

    A value that is not a whole number, or is below 1, fails naming it.
    """
    declared: Any = ci.get("shards-max")
    if declared is None:
        return SHARDS_MAX
    if isinstance(declared, bool) or not isinstance(declared, int):
        fail(
            f"[ci] shards-max is {declared!r}, which is not a whole number; it is"
            " the most shards a runner may take"
        )
    if declared < 1:
        fail(f"[ci] shards-max is {declared}; the cap is at least 1")
    return declared


def shard_counts(
    ci: dict[str, Any], *, key: str, runners: Sequence[str], listed: str
) -> dict[str, int]:
    """The shards per runner ``[ci] <key>`` gives, one for a runner it does not name.

    *runners* are the labels the legs fan out to and *listed* names the
    key they came from, for the refusal. The task fails, naming the key
    and the runner, when the table names a runner *runners* does not
    hold, gives a count below 1 or above `shards_max`, or gives one
    that is not a whole number.
    """
    cap = shards_max(ci)
    counts = dict.fromkeys(runners, 1)
    declared: Any = ci.get(key)
    if declared is None:
        return counts
    if not isinstance(declared, dict):
        fail(f"[ci] {key} is {declared!r}; it is a table of runner label to count")
    table: dict[str, Any] = declared
    strangers = [runner for runner in table if runner not in runners]
    if strangers:
        fail(
            f"[ci] {key} names {', '.join(strangers)}, which {listed} does not list"
            f" ({', '.join(runners)}); a count belongs to a runner the legs run on"
        )
    for runner, count in table.items():
        if isinstance(count, bool) or not isinstance(count, int):
            fail(
                f"[ci] {key} gives {runner} {count!r}, which is not a whole number;"
                f" a count is from 1 to {cap}"
            )
        if count < 1:
            fail(f"[ci] {key} gives {runner} {count}; a count is at least 1")
        if count > cap:
            fail(
                f"[ci] {key} gives {runner} {count}, above the cap of {cap}"
                " ([ci] shards-max); lower the count or raise the cap"
            )
        counts[runner] = count
    return counts


def _shard_legs(
    runners: Sequence[str], pythons: Sequence[str], counts: Mapping[str, int]
) -> list[str]:
    """One leg per runner and Python, split into shards where the count is above 1."""
    legs: list[str] = []
    for runner in runners:
        count = counts.get(runner, 1)
        for python in pythons:
            leg = f"check-{runner}-{python}"
            if count == 1:
                legs.append(leg)
            else:
                legs.extend(
                    f"{leg}-{Shard(index, count)}" for index in range(1, count + 1)
                )
    return legs


def check_shards(root: Path) -> list[str]:
    """Name the shard legs the gate produces, split as ``[ci] shards`` says.

    One leg per runner and Python [livery.workshop.check_legs][] names,
    spelled as that leg, ``check-<os>-<python>``, for a count of one,
    the count of every runner the table does not name; and
    ``check-<os>-<python>-<i>of<n>`` for each of the ``n`` shards
    otherwise, in order. ``[ci] shards-max`` caps a count, 4 when
    absent. The task fails, naming the key and the runner, when the
    table names a runner ``[ci] runners`` does not list, gives a count
    below 1 or above the cap, or gives one that is not a whole number.
    """
    from livery.workshop._pythons import gate_pythons

    contract = load_contract(root / "workshop.toml")
    ci = contract.get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    counts = shard_counts(ci, key="shards", runners=runners, listed="[ci] runners")
    return _shard_legs(runners, gate_pythons(root), counts)


def pull_request_shards(root: Path) -> list[str]:
    """Name the shard legs a pull request's run produces, split as the contract says.

    One leg per runner and Python [livery.workshop.pull_request_legs][]
    names, split as [livery.workshop.check_shards][] splits the gate's:
    by ``[ci] pull-request-shards``, judged against the pull request's
    runners, or by ``[ci] shards`` when that key is absent. The task
    fails as `check_shards` fails, and as `pull_request_legs` fails.
    """
    from livery.workshop._pythons import gate_pythons

    contract = load_contract(root / "workshop.toml")
    ci = contract.get("ci") or {}
    runners = pull_request_runners(ci)
    if ci.get("pull-request-shards") is None:
        every = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
        counts = shard_counts(ci, key="shards", runners=every, listed="[ci] runners")
    else:
        listed = (
            "[ci] pull-request-runners"
            if ci.get("pull-request-runners") is not None
            else "[ci] runners"
        )
        counts = shard_counts(
            ci, key="pull-request-shards", runners=runners, listed=listed
        )
    return _shard_legs(runners, gate_pythons(root), counts)


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
    shard: str = "",
    spawn: Callable[[list[str], dict[str, str]], int] = _spawn,
) -> None:
    """Run every entry of *job* at *point*, in order; a red entry fails the job.

    A red builtin entry stops the job at once, since the entries after
    it read its verdict, and so does a red judge an extension adds to
    the verdict job; a red entry of the contract's schedule stands
    on its own, so the scheduled entries after it still run, and the
    job fails at the end naming each red one. A dispatch that names
    tasks in the nightly's ``only`` input runs those alone, builtin or
    scheduled, and says what it skipped.

    *os_label* and *python* are the matrix facts the shell passes for
    a matrix job; they format the entries' arguments and name the
    leg. *shard* is the leg's shard, ``2of4``, on a check leg the
    contract splits; empty runs as one shard of one. A count above
    one ends the label and the display name
    (``check-windows-latest-3.14-2of4``, ``check (windows-latest,
    3.14, 2of4)``, and on GitLab ``check: [2of4]``, as it lists a job
    run as ``parallel: matrix``), and an entry declared ``sharded``
    takes ``--shard=2of4``; one shard of one leaves the label, the
    display name and every call as the unsplit leg's. A spelling outside
    ``<i>of<n>`` fails naming it, before any entry runs. An entry
    with ``once`` runs on the first leg's first shard alone, and the
    skip line names the shard when the leg is the first. An entry
    with a cadence runs only on its day, judged by
    `today`, and says when it runs next otherwise. Every entry runs
    under ``--profile``, writing its own trace beside the others, and
    the job's box collects them into one timeline the runner pushes;
    ``[ci] profile = false`` does none of that. Every child's
    environment names the leg in
    `livery.workshop._ci_context.LEG_VARIABLE`, the key of the leg's rows
    and stamps, and the resolved point in
    `livery.workshop._ci_context.POINT_VARIABLE`, which selects the
    tests a point runs. A job that declares ``inputs`` runs no entry on
    a pull request that changed nothing it reads (`unread_by_the_job`).
    *spawn* runs one entry's command in that environment and returns
    its exit code; the default is the runner's own child.
    """
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import SNAPSHOT_VARIABLE

    try:
        split = Shard.parse(shard) if shard else ONE
    except ValueError as error:
        fail(str(error))
    resolved = effective_point(point)
    entries = entries_for(root, resolved, job)
    if resolved != point:
        print(f"  point: {point} on a push is the {resolved} point")
    skip = unread_by_the_job(root, resolved, job)
    if skip:
        print(skip)
        return
    # The display name is the job's name as the forge lists it, since
    # the collect step joins the leg's row with the forge's job by it:
    # GitHub and Gitea show a matrix job as ``check (ubuntu-latest,
    # 3.14)``; GitLab names the job by its key alone, the matrix
    # riding its variables and never its name, except that a job run
    # as ``parallel: matrix`` is listed as ``check: [2of4]``.
    run = run_context()
    # Only the dimensions the matrix actually has: the nightly point runs a
    # matrix of pythons and no runners, and the forge lists that job as
    # ``nightly (3.14)``. Spelling an absent dimension as an empty one gave
    # ``nightly (, 3.14)``, which matched no job the forge had.
    parts = [part for part in (os_label, python) if part]
    # The shard ends the label and the display name only where the leg
    # is one of several: one shard of one is the unsplit leg, spelled
    # as the platform leg every record is keyed by.
    if split.count > 1:
        parts.append(str(split))
    gitlab = run is not None and run.forge == "gitlab"
    if gitlab:
        display = f"{job}: [{split}]" if split.count > 1 else job
    else:
        display = f"{job} ({', '.join(parts)})" if parts else job
    label = "-".join([job, *parts])
    facts = {
        "display": display,
        "label": label,
        "os": os_label,
        "python": python,
        "shard": str(split),
    }
    # A dispatched point's inputs, as the run received them, by name:
    # the release's ``{ref}`` names the squash the wave publishes.
    from livery.workshop._ci_context import dispatch_inputs

    facts.update(
        dispatch_inputs(
            tuple(item.name for item in point_by_name(root)[resolved].inputs)
        )
    )
    asked = asked_tasks(root, resolved, facts.get(ONLY, ""))
    prog = footman.prog()
    # One listing of the state store's namespace for the whole job:
    # every entry reads through it and records what it writes for the
    # entries after it, so the job lists once, not once per entry.
    with contextlib.ExitStack() as scope:
        # Every entry writes its own trace and drops a copy in the job's
        # box, so the job's own timeline is the union of the work it did.
        # `[ci] profile = false` opens no box, passes no flag, and pushes
        # nothing: off costs nothing rather than less.
        drop = scope.enter_context(profile_box()) if _tracing(root) else None
        published = scope.enter_context(
            remote_snapshot(state_store(root), publish=True)
        )
        env = {**os.environ, LEG_VARIABLE: label, POINT_VARIABLE: resolved}
        if published:
            env[SNAPSHOT_VARIABLE] = published
        if drop is not None:
            # Named here and nowhere else: a workspace that keeps no traces
            # never imports the plugin that writes them.
            from livery.footman.profile import DROP

            env[DROP] = str(drop)
        red: list[str] = []
        try:
            for entry in entries:
                if asked and entry.task not in asked:
                    print(
                        f"  {resolved}/{job}: {entry.task} ({entry.source}) is not"
                        " among the tasks this dispatch asked for; skipped"
                    )
                    continue
                if entry.once:
                    first_os, first_python = first_leg(root, resolved, job)
                    leg_is_first = (not os_label or os_label == first_os) and (
                        not python or python == first_python
                    )
                    if not leg_is_first:
                        first = ", ".join(
                            part for part in (first_os, first_python) if part
                        )
                        print(
                            f"  {resolved}/{job}: {entry.task} ({entry.source}) runs"
                            f" once, on the first leg ({first}); skipped"
                        )
                        continue
                    if not split.first:
                        print(
                            f"  {resolved}/{job}: {entry.task} ({entry.source}) runs"
                            f" once, on the first shard ({Shard(1, split.count)});"
                            " skipped"
                        )
                        continue
                if entry.every:
                    is_due, when = due(entry.every, today())
                    if not is_due:
                        print(
                            f"  {resolved}/{job}: {entry.task} ({entry.source}) runs"
                            f" every {entry.every}; next on {when:%Y-%m-%d}, skipped"
                        )
                        continue
                argv = [prog]
                if drop is not None:
                    argv.append(f"--profile={trace_file(entry.task)}")
                argv.append(entry.task)
                argv.extend(arg.format(**facts) for arg in entry.args)
                if entry.sharded and split.count > 1:
                    argv.append(f"--shard={split}")
                print(f"  {resolved}/{job}: {entry.task} ({entry.source})")
                # The record lists the store for itself: a sibling job's
                # half may have landed after this job's listing was
                # taken, and the job's listing never learns a new ref.
                handed = env
                if entry.task == RECORD_TASK:
                    handed = {k: v for k, v in env.items() if k != SNAPSHOT_VARIABLE}
                code = spawn(argv, handed)
                if code != 0:
                    reason = f"{resolved}/{job}: {entry.task} exited {code}"
                    if entry.source == "builtin" or entry.judges:
                        fail(reason)
                    print(f"  {reason}; the scheduled entries after it still run")
                    red.append(reason)
        finally:
            # A red job's timeline is the one most worth having, so the
            # push happens whatever the entries did.
            if drop is not None:
                _push_job(root, drop, job=display, leg=label)
                _push_about(root)
        if red:
            fail("; ".join(red))
    if not entries:
        print(f"  {resolved}/{job}: nothing scheduled")
