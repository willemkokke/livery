"""``fm status``, the ``ci`` group, and ``fm doctor``.

``status`` says where the current branch's pull request stands and
exits that state's code (0 while nothing is wrong); ``--watch``
follows instead of reading once. The ``ci`` group acts on the head
commit's runs: ``ci.rerun`` re-runs the failed jobs, ``ci.cancel``
cancels what is still moving (the relief for a wedged queue),
``ci.status`` says where the head commit's runs stand and exits that
state's code, so a script can wait on main after a merge, and
``ci.logs`` prints the job logs, the one read that stays here so
logs reach an agent through fm; both read the newest run of a point
instead under ``--point``, which is how the nightly's verdict reaches
a person. ``ci.dispatch`` starts a point that has a dispatch entry
(the nightly) and follows it to its verdict. ``ci.run`` runs one job of a point
(livery.workshop._points), the one verb the emitted shells call;
``ci.verdict`` is the gate job's judgement of the jobs it needs.
``ci.timings`` prints the timing rows the gate writes on
that store (livery.workshop._metrics); the hidden ``ci.metrics.collect``
is the gate job's writer ``ci.run`` schedules, and a check leg's row
rides ``coverage.leg``'s one write. ``doctor`` says who you are,
which server this is, and what it grants.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import doc, fail, group, task
from livery.forge import (
    Capability,
    Forge,
    ForgeError,
    Job,
    Repository,
    Run,
    Unsupported,
)
from livery.workshop._contract import load_contract
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._verdict import (
    EXIT_CI_FAILED,
    EXIT_PENDING,
    EXIT_TIMEOUT,
    EXIT_UNREACHABLE,
    Transient,
    classify,
    follow,
)

ci = group("ci", help="The head commit's CI runs")

_CAPABILITIES: tuple[Capability, ...] = (
    "auto_merge",
    "force_cancel",
    "required_contexts",
    "ci_secrets",
    "schedule_events",
)


def _resolved() -> tuple[Repository, GitOps]:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    from livery.workshop._forge_lane import this_repository

    return this_repository(root), GitOps(root)


def status_flow(repo: Repository, git: GitOps) -> int:
    """Print where the branch stands; return that state's exit code."""
    verdict = classify(repo, git.current_branch(), git)
    print(f"  {verdict.state}: {verdict.detail}")
    return verdict.exit_code


@task
def status(
    watch: Annotated[bool, doc("follow until it lands or needs a person")] = False,
    workflow: Annotated[bool, doc("the reserved workflows instead")] = False,
    interval: Annotated[int, doc("watch poll seconds")] = 15,
    timeout: Annotated[int, doc("watch deadline seconds")] = 1800,
) -> None:
    """Say where the branch's pull request stands; exit that state's code.

    Exit 0 covers merged, in flight, and no pull request; each blocker
    state keeps its own stable code (see livery.workshop._verdict), and
    the printed sentence always says what to do next. ``--watch``
    follows instead of reading once: the same classification, polled
    until the branch lands or a person is needed. ``--workflow``
    reads the reserved workflows instead, one line each with its
    state, author, and, for a mid-publish release, the members whose
    receipt tag is already cut.
    """
    repo, git = _resolved()
    if workflow:
        from livery.workshop._workflow_state import workflow_states
        from livery.workshop._workflow_tasks import render_workflows

        render_workflows(workflow_states(repo, git))
        return
    if watch:
        follow(repo, git.current_branch(), git, interval=interval, timeout=timeout)
        return
    code = status_flow(repo, git)
    if code:
        raise SystemExit(code)


def _head_sha(repo: Repository, git: GitOps) -> str:
    """The commit whose runs matter: the PR's head, else the local HEAD.

    After a push the two agree; the pull request's answer also covers
    a checkout that has moved on locally since the push.
    """
    pr = repo.pr.find_by_head(git.current_branch())
    return pr.head_sha if pr is not None else git.head_sha()


def rerun_flow(repo: Repository, git: GitOps, *, failed_only: bool = True) -> None:
    """Re-run the failed runs that hold this branch's verdict.

    The verdict is each workflow's newest run on a commit this branch
    contains. The head commit's runs are the common case, but a
    workflow triggered on an earlier commit (a merged release squash,
    a path-filtered job) keeps its verdict until it re-runs, so
    scoping to the head sha alone would miss it. Each rerun is read
    back: a forge that quietly ignores the request is named, never
    reported as re-running.
    """
    sha = _head_sha(repo, git)
    newest: dict[str, Run] = {}
    # Newest first; the scan is capped so a repository with a long
    # run history stays cheap. A run on a sha this clone does not
    # contain belongs to another branch and is skipped.
    for run in repo.checks.runs()[:100]:
        if run.workflow in newest:
            continue
        if run.head_sha == sha or git.is_ancestor(run.head_sha, "HEAD"):
            newest[run.workflow] = run
    failed = [
        run
        for run in newest.values()
        if run.status == "completed" and run.conclusion != "success"
    ]
    if not failed:
        print(f"  nothing failed on this branch as of {sha[:10]}")
        return
    from livery.workshop._points import BUILTIN, workflow_of

    release_workflow = workflow_of("release")
    verdict_jobs = {entry.job for entry in BUILTIN if entry.task == "ci.verdict"}
    on_main = git.current_branch() == "main"
    for run in failed:
        workflow = (run.workflow or "").rsplit("/", 1)[-1]
        # A release wave is the train's: its recovery re-dispatches
        # at the squash with the right driver, which a rerun of the
        # same run cannot do. On main it is this branch's verdict.
        if workflow == release_workflow and not on_main:
            print(
                f"  {workflow} (run {run.id}) is a release wave, left to the"
                f" train: `{footman.prog()} workflow.release <set>` re-dispatches it"
            )
            continue
        whole = not failed_only
        if failed_only:
            # The verdict job judges the legs' rows, which the first
            # attempt's collect dropped; re-run alone it can only fail.
            red = [
                job.name
                for job in repo.checks.jobs(run.id)
                if job.status == "completed" and job.conclusion not in _GREEN
            ]
            if red and all(job in verdict_jobs for job in red):
                whole = True
                print(
                    f"  {workflow} (run {run.id}): only the verdict job failed,"
                    " and it judges the legs' rows; the whole run is asked to run again"
                )
        repo.checks.rerun(run.id, failed_only=not whole)
        after = next(
            (
                candidate
                for candidate in repo.checks.runs(head_sha=run.head_sha)
                if candidate.id == run.id
            ),
            None,
        )
        if after is not None and after.status == "completed":
            print(
                f"  {run.workflow} (run {run.id}): the forge did not"
                f" restart it; still {after.conclusion}"
            )
        else:
            print(f"  re-running {run.workflow} (run {run.id})")


@ci.task(name="rerun")
def ci_rerun(
    failed_only: Annotated[bool, doc("re-run only the failed jobs")] = True,
) -> None:
    """Re-run the head commit's failed runs."""
    repo, git = _resolved()
    rerun_flow(repo, git, failed_only=failed_only)


def cancel_flow(repo: Repository, git: GitOps, *, force: bool = False) -> None:
    """Cancel the head commit's unfinished runs.

    The relief for a wedged queue; ``force`` reaches the runs a plain
    cancel cannot, where the forge grants the capability.
    """
    sha = _head_sha(repo, git)
    cancelled = 0
    for run in repo.checks.runs(head_sha=sha):
        if run.status != "completed":
            repo.checks.cancel_run(run.id, force=force)
            print(f"  cancelled {run.workflow} (run {run.id})")
            cancelled += 1
    if not cancelled:
        print(f"  nothing running for {sha[:10]}")


@ci.task(name="cancel")
def ci_cancel(
    force: Annotated[bool, doc("force-cancel a wedged run")] = False,
) -> None:
    """Cancel the head commit's unfinished runs."""
    repo, git = _resolved()
    cancel_flow(repo, git, force=force)


#: The run conclusions that are not red.
_GREEN = ("success", "skipped", "neutral")


def point_runs(repo: Repository, point: str) -> tuple[Run, ...]:
    """The runs of *point*'s workflow, newest first, whatever commit they checked.

    Read by the point's events so the listing stays short on a forge
    with a long history, then kept by the workflow's file name
    (GitHub lists the path, Gitea the file; the basename is the
    comparison). A forge that names no workflow on a run (GitLab)
    answers by event alone.
    """
    from livery.workshop._points import EVENTS, workflow_of

    workflow = workflow_of(point)
    found: dict[int, Run] = {}
    for event in EVENTS[point]:
        for run in repo.checks.runs(event=event):
            if not run.workflow or run.workflow.rsplit("/", 1)[-1] == workflow:
                found[run.id] = run
    return tuple(sorted(found.values(), key=lambda run: run.id, reverse=True))


def _run_line(run: Run) -> str:
    """One run: workflow, conclusion or status, event, commit, page."""
    return (
        f"  {run.workflow:14} {run.conclusion or run.status:12}"
        f" {run.event:17} {run.head_sha[:10]} {run.url}"
    )


def follow_run(
    repo: Repository,
    point: str,
    run: Run,
    *,
    interval: float = 15,
    timeout: float = 1800,
) -> int:
    """Follow *run* of *point* to its verdict; return that state's exit.

    Progress prints on state changes and every minute. Green is 0;
    red is 13 with the red jobs named; a wait that runs out is 14; a
    forge unreachable past the transient budget is 15.
    """
    import time

    deadline = time.monotonic() + timeout
    transient = Transient(interval=interval)
    last = ""
    quiet_since = time.monotonic()
    while True:
        try:
            current = next((r for r in point_runs(repo, point) if r.id == run.id), run)
        except ForgeError as exc:
            if transient.note(exc):
                print(transient.giving_up(f"{point} run {run.id} {run.url}"))
                return EXIT_UNREACHABLE
            time.sleep(interval)
            continue
        transient.reset()
        if current.status == "completed":
            word, code = runs_state((current,))
            print(
                f"  {word}: {point} run {current.id} {current.conclusion} {current.url}"
            )
            if code:
                for job in repo.checks.jobs(current.id):
                    if job.conclusion not in _GREEN:
                        print(f"    {job.name}: {job.conclusion or job.status}")
            return code
        if current.status != last or time.monotonic() - quiet_since >= 60:
            last = current.status
            quiet_since = time.monotonic()
            print(f"  waiting: {point} run {current.id} is {current.status}")
        if time.monotonic() >= deadline:
            print(f"  still {current.status} after {timeout:.0f}s; {current.url}")
            return EXIT_TIMEOUT
        time.sleep(interval)


def dispatch_flow(
    repo: Repository,
    *,
    point: str,
    ref: str,
    follow: bool = True,
    interval: float = 15,
    timeout: float = 1800,
    register_timeout: float = 60,
) -> int:
    """Start *point* on *ref* by hand; follow it to its verdict; return the exit.

    Refuses a point without a dispatch entry, naming what starts it
    instead. The dispatch is confirmed by the run that appears: one
    the forge accepted but never listed within *register_timeout* is
    reported with the verb that reads it later, exit 14. Without
    *follow* the run is named and the exit is 18 while it moves.
    """
    import time

    from livery.workshop._points import DISPATCHABLE, POINTS, workflow_of

    if point not in POINTS:
        fail(f"{point!r} is not a point; the points are {', '.join(POINTS)}")
    if point not in DISPATCHABLE:
        if point == "release":
            fail(
                "the release point has no dispatch entry of its own: the merge"
                f" point dispatches the wave, and `{footman_prog()}"
                " workflow.release.dispatch` does by hand"
            )
        fail(
            f"the {point} point has no dispatch entry: it runs on its own event"
            f" (a pull request, a push to main); `{footman_prog()} ci.dispatch`"
            f" starts {', '.join(DISPATCHABLE)}"
        )
    workflow = workflow_of(point)
    seen = {run.id for run in point_runs(repo, point)}
    try:
        repo.checks.dispatch(workflow, ref=ref)
    except Unsupported as exc:
        fail(f"this forge cannot dispatch a workflow: {exc}")
    except ForgeError as exc:
        fail(f"the forge refused the {point} dispatch on {ref}: {exc}")
    print(f"  dispatched the {point} point on {ref} ({workflow})")
    deadline = time.monotonic() + register_timeout
    while True:
        new = [run for run in point_runs(repo, point) if run.id not in seen]
        if new:
            run = new[0]
            break
        if time.monotonic() >= deadline:
            print(
                f"  the dispatch was accepted but no {point} run appeared within"
                f" {register_timeout:.0f}s; `{footman_prog()} ci.status"
                f" --point={point}` reads one that appears later"
            )
            return EXIT_TIMEOUT
        time.sleep(interval)
    print(f"  {point}: run {run.id} {run.url}")
    if not follow:
        return runs_state((run,))[1]
    return follow_run(repo, point, run, interval=interval, timeout=timeout)


def footman_prog() -> str:
    """The runner's name, for a remedy in a message."""
    import livery.footman as footman

    return footman.prog()


@ci.task(name="dispatch")
def ci_dispatch(
    point: Annotated[
        str, doc("the point to start; nightly is the one with an entry")
    ] = "nightly",
    ref: Annotated[str, doc("the branch or tag the run checks out")] = "main",
    follow: Annotated[bool, doc("wait for the run's verdict")] = True,
    interval: Annotated[int, doc("poll seconds")] = 15,
    timeout: Annotated[int, doc("deadline seconds for the verdict")] = 1800,
) -> None:
    """Start a point on the forge by hand and follow it to its verdict.

    The nightly is the point with a dispatch entry: the clock's run
    waits for nobody, and this verb starts the same run now, on
    *ref*, so a failure only the nightly meets (a floor Python, a
    replay) is reproduced on demand. Exits green 0, red 13 with the
    red jobs named, a run that never registered or a wait that runs
    out 14, a forge unreachable for five polls in a row 15, and,
    with ``--no-follow``, 18 while the run moves. The gate and the
    merge point run on their own events, and the release wave is the
    merge point's to dispatch.
    """
    repo, _git = _resolved()
    code = dispatch_flow(
        repo,
        point=point,
        ref=ref,
        follow=follow,
        interval=interval,
        timeout=timeout,
    )
    if code:
        raise SystemExit(code)


def runs_state(runs: tuple[Run, ...]) -> tuple[str, int]:
    """The one word for *runs* together and its exit: green 0, pending 18, red 13.

    No run at all is pending: the forge may not have registered the
    push yet, and a script waiting on it keeps waiting. A run still
    moving is pending whatever the others concluded. Red is any run
    that completed with a conclusion that is not green.
    """
    if not runs or any(run.status != "completed" for run in runs):
        return "pending", EXIT_PENDING
    if any(run.conclusion not in _GREEN for run in runs):
        return "red", EXIT_CI_FAILED
    return "green", 0


def runs_status_flow(
    repo: Repository,
    git: GitOps,
    *,
    wait: bool = False,
    interval: float = 15,
    timeout: float = 1800,
    point: str = "",
) -> int:
    """Print the head commit's runs and their state; return that state's exit.

    One line per run, workflow, status or conclusion, event, commit,
    and page, then the word for all of them. With *point* the one run
    read is the newest of that point's workflow, whatever commit it
    checked: the nightly's verdict, which no commit of a branch
    carries. With *wait* the read repeats every *interval* seconds
    while the state is pending, until *timeout*, which exits 14; a
    transport error under *wait* is a retry, and a run of them that
    reaches the budget exits 15. Without *wait* an unreadable forge
    is the caller's error.
    """
    import time

    sha = ""
    if point:
        subject = f"the {point} point"
        nothing = f"no {point} runs yet"
    else:
        sha = _head_sha(repo, git)
        subject = sha[:10]
        nothing = f"no runs yet for {sha[:10]}"
    deadline = time.monotonic() + timeout
    transient = Transient(interval=interval)
    while True:
        try:
            if point:
                runs = point_runs(repo, point)[:1]
            else:
                runs = repo.checks.runs(head_sha=sha)
        except ForgeError as exc:
            if not wait:
                raise
            if transient.note(exc):
                print(transient.giving_up(f"the runs for {subject}"))
                return EXIT_UNREACHABLE
            time.sleep(interval)
            continue
        transient.reset()
        for run in runs:
            print(_run_line(run))
        word, code = runs_state(runs)
        if not runs:
            print(f"  {nothing}")
        if code != EXIT_PENDING or not wait:
            print(f"  {word}: {len(runs)} run(s) for {subject}")
            return code
        if time.monotonic() >= deadline:
            print(f"  still pending after {timeout:.0f}s")
            return EXIT_TIMEOUT
        time.sleep(interval)


@ci.task(name="status")
def ci_status(
    wait: Annotated[bool, doc("poll until the runs are no longer pending")] = False,
    interval: Annotated[int, doc("poll seconds under --wait")] = 15,
    timeout: Annotated[int, doc("deadline seconds under --wait")] = 1800,
    point: Annotated[str, doc("read the newest run of this point instead")] = "",
) -> None:
    """Say where the head commit's runs stand; exit that state's code.

    Green exits 0, red 13, pending 18, a wait that runs out 14, and
    a forge unreachable for five polls in a row under the wait 15,
    so a script can ask whether main's push has finished after a
    merge. The head is the pull request's when the branch has one,
    else the local HEAD, so on main after a pull it is the merge.
    ``--point=nightly`` reads the newest nightly run instead, by
    workflow rather than by commit, so a failure only the nightly
    meets reaches a person here.
    """
    repo, git = _resolved()
    code = runs_status_flow(
        repo, git, wait=wait, interval=interval, timeout=timeout, point=point
    )
    if code:
        raise SystemExit(code)


def logs_flow(
    repo: Repository,
    git: GitOps,
    *,
    lines: int = 80,
    failed_only: bool = True,
    point: str = "",
) -> None:
    """Print the head commit's job logs, failed jobs first and by default.

    The tail of each log, newest run first, so a red branch explains
    itself without leaving the terminal. With *point* the one run
    read is the newest of that point's workflow.
    """
    if point:
        runs = point_runs(repo, point)[:1]
        subject = f"the {point} point"
    else:
        sha = _head_sha(repo, git)
        runs = repo.checks.runs(head_sha=sha)
        subject = sha[:10]
    if not runs:
        print(f"  no {point} runs" if point else f"  no runs for {subject}")
        return
    printed = 0
    for run in runs:
        for job in repo.checks.jobs(run.id):
            failed = job.conclusion not in ("", "success", "skipped")
            if failed_only and not failed:
                continue
            state = job.conclusion or job.status
            print(f"  {run.workflow} / {job.name}: {state}")
            try:
                log = repo.checks.job_log(job.id)
            except ForgeError as exc:
                # A running job's log is not stored yet; github answers
                # 404 from its blob store until the job completes.
                print(f"    log not available yet ({exc.status or 'error'})")
                continue
            for line in log.splitlines()[-lines:]:
                print(f"    {line}")
            printed += 1
    if not printed:
        which = "failed " if failed_only else ""
        print(f"  no {which}jobs for {subject}")


@ci.task(name="logs")
def ci_logs(
    lines: Annotated[int, doc("log lines per job, from the tail")] = 80,
    failed_only: Annotated[bool, doc("only jobs that did not succeed")] = True,
    point: Annotated[str, doc("read the newest run of this point instead")] = "",
) -> None:
    """Print the head commit's job logs, failed jobs by default.

    ``--point=nightly`` prints the newest nightly run's logs instead.
    """
    repo, git = _resolved()
    logs_flow(repo, git, lines=lines, failed_only=failed_only, point=point)


@ci.task(name="run")
def ci_run(
    *,
    point: Annotated[str, doc("the point: gate, merge, nightly, or release")],
    job: Annotated[str, doc("the point's job this runner executes")],
    os: Annotated[str, doc("the matrix runner label, on a matrix job")] = "",
    python: Annotated[str, doc("the matrix python, on a matrix job")] = "",
) -> None:
    """Run one job of a point: every task scheduled there, in order.

    The one verb the emitted workflows call, once per job. What the
    job does is data: the workshop's builtin schedule plus the
    contract's ``[[ci.schedule]]`` entries. A push to main promotes
    the gate to the merge point, so the shell spells no decision. A
    person may run a job by hand; the tasks that only CI may do
    refuse or fail open on their own.
    """
    from livery.workshop._points import run_point

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    run_point(root, point, job, os_label=os, python=python)


def _conclusion_words(repo: Repository, run: Run, job: Job) -> str:
    """A red job's conclusion, with the run that superseded a cancelled one named."""
    words = job.conclusion or job.status
    if job.conclusion != "cancelled":
        return words
    from livery.workshop._runs import successor

    found = successor(repo, run)
    if found is None:
        return "cancelled; no newer run for this head or its pull request is known"
    where = (
        "for the same head"
        if found.same_head
        else f"for the moved head {found.run.head_sha[:12]}"
    )
    return f"cancelled, superseded by run {found.run.id} {where}"


def verdict_flow(repo: Repository, *, needs: tuple[str, ...], job: str) -> list[str]:
    """The red jobs of this run among *needs*, and any other red job; empty is green.

    A needed job is matched by name or, for a matrix job, by its
    display name's prefix (``check (ubuntu-latest, 3.14)`` answers to
    ``check``). A needed job the forge does not list at all counts as
    red: the verdict never passes on a job it could not see.
    """
    from livery.workshop._state import run_context

    run = run_context()
    if run is None:
        return ["not a CI run: the verdict reads the run's jobs from the forge"]
    runs = repo.checks.runs(head_sha=run.head_sha) if run.head_sha else ()
    forge_run = next((r for r in runs if str(r.id) == run.run_id), None)
    if forge_run is None:
        return [f"the forge lists no run {run.run_id} for {run.head_sha[:12]}"]
    jobs = repo.checks.jobs(forge_run.id)
    red: list[str] = []
    for needed in needs:
        matching = [
            j for j in jobs if j.name == needed or j.name.startswith(f"{needed} (")
        ]
        if not matching:
            red.append(f"{needed}: not among the run's jobs")
        for found in matching:
            if found.status != "completed" or found.conclusion != "success":
                red.append(f"{found.name}: {_conclusion_words(repo, forge_run, found)}")
    for found in jobs:
        if found.name == job or any(line.startswith(found.name) for line in red):
            continue
        if found.status == "completed" and found.conclusion not in (
            "success",
            "skipped",
        ):
            red.append(f"{found.name}: {found.conclusion}")
    return red


@ci.task(name="verdict", hidden=True)
def ci_verdict(
    *,
    needs: Annotated[str, doc("the jobs this verdict requires, comma-separated")] = "",
) -> None:
    """Judge the run: green when every needed job succeeded, red naming the rest.

    Runs in the gate job, the one required context, after the jobs
    it needs have completed. It asks the forge for the run's jobs, so
    the YAML carries no expression over them; a job it cannot see is
    red, never assumed.
    """
    import os as _os

    repo, _ = _resolved()
    names = tuple(name.strip() for name in needs.split(",") if name.strip())
    red = verdict_flow(repo, needs=names, job=_os.environ.get("GITHUB_JOB", "gate"))
    if red:
        fail("the run is red:\n  " + "\n  ".join(red))
    print(f"  green: {', '.join(names) or 'every job'} succeeded")


verified = ci.group("verified", help="The trees a green gate proved", hidden=True)


def verified_stamp_flow(root: Path, git: GitOps) -> None:
    """Stamp the run's tree when every check leg ran the full gate; print why not."""
    from livery.workshop._state import remote_snapshot, run_context
    from livery.workshop._verified import stamp_from_metrics

    run = run_context()
    if run is None:
        print("  not a CI run: the verified record is written by CI only")
        return
    with remote_snapshot(root, fetch=("metrics", "verified")):
        print(stamp_from_metrics(root, run, sha=git.head_sha()))


@verified.task(name="stamp")
def ci_verified_stamp() -> None:
    """Record this run's tree as proved green, for the runs that share it.

    Runs in the gate job after the verdict, so only a green run
    reaches it. Every check leg's metrics row must say it ran the
    full gate; a narrowed leg leaves the tree unstamped, and a later
    run of the same tree pays the gate. Fails open loudly: every
    reason is printed and the exit stays 0.
    """
    _repo, git = _resolved()
    verified_stamp_flow(git.root, git)


metrics = ci.group("metrics", help="The timing rows CI writes", hidden=True)


def metrics_collect_flow(root: Path, repo: Repository, git: GitOps) -> None:
    """Collect the run's rows into the metrics series; print every line."""
    from livery.workshop._metrics import collect
    from livery.workshop._state import remote_snapshot, run_context

    run = run_context()
    if run is None:
        print("  not a CI run: the run's timing rows are collected by CI only")
        return
    with remote_snapshot(root, fetch=(f"run/{run.run_id}/", "metrics")):
        for line in collect(root, repo, run, sha=git.head_sha()):
            print(line)


@metrics.task(name="collect")
def ci_metrics_collect() -> None:
    """Join the legs' timing rows with the forge's times into the run's file.

    Runs in the gate job before its verdict. Reads the per-run refs
    the legs wrote, asks the forge for the run's jobs and their
    steps, puts the run's file on the metrics series under its
    window, and drops the per-run refs. Fails open loudly: every
    reason is printed and the exit stays 0.
    """
    repo, git = _resolved()
    metrics_collect_flow(git.root, repo, git)


def timings_flow(root: Path, *, since: int, base: int) -> None:
    """Print the rendered timings, then the speed marks beside the newest run."""
    from livery.workshop._metrics import render
    from livery.workshop._speed import render_marks
    from livery.workshop._state import remote_snapshot

    with remote_snapshot(root, fetch=("metrics", "speed/marks")):
        for line in render(root, since=since, base=base):
            print(line)
        for line in render_marks(root):
            print(line)


@ci.task(name="timings")
def ci_timings(
    since: Annotated[int, doc("the recent runs the movers judge")] = 1,
    base: Annotated[int, doc("the runs before them the movers compare against")] = 20,
) -> None:
    """Print the CI timings: per job and metric the latest, p50, p90, and the movers.

    Reads the metrics series the gate job writes; CI is its only
    writer, a local run reads. Per job, every metric's latest value
    and its median and ninetieth percentile over the series' window,
    then the biggest movers: the recent runs' median against the
    base runs' median. Then the speed marks, each package's mark on
    each leg beside the newest run's time.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    timings_flow(root, since=since, base=base)


def doctor_flow(forge: Forge) -> None:
    """Print identity, server, and capabilities for *forge*."""
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is not None:
        from livery.workshop._governance import unknown_owners
        from livery.workshop._tokens import admin_token

        contract = load_contract(root / "workshop.toml")
        owner = str((contract.get("forge") or {}).get("owner", ""))
        kind = str((contract.get("forge") or {}).get("kind", ""))
        url = str((contract.get("forge") or {}).get("url", ""))
        _, admin_var = admin_token(kind, url)
        if admin_var.startswith("FORGE_ADMIN_TOKEN"):
            print(f"  admin ladder: {admin_var} set")
        elif admin_var:
            print(
                f"  admin ladder: FORGE_ADMIN_TOKEN unset ({admin_var} is the fallback)"
            )
        else:
            print(
                "  admin ladder: FORGE_ADMIN_TOKEN unset (everyday"
                " token is the fallback)"
            )
        try:
            unknown = unknown_owners(root, forge, owner)
        except Exception as error:
            print(f"  owners: not checked ({error})")
        else:
            if unknown:
                for entry in unknown:
                    print(f"  owners: unknown {entry}")
            else:
                print("  owners: every declared owner exists on the forge")

    print(f"  {forge.whoami()} on {forge.server_version()}")
    granted = [name for name in _CAPABILITIES if forge.supports(name)]
    missing = [name for name in _CAPABILITIES if name not in granted]
    if granted:
        print(f"  grants: {', '.join(granted)}")
    if missing:
        print(f"  missing: {', '.join(missing)}")
    else:
        print("  every capability the workshop can use is granted")


@task
def doctor() -> None:
    """Say who you are, which server this is, and what it grants."""
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    from livery.workshop._env_tasks import missing_host_tools
    from livery.workshop._forge_lane import this_forge

    for tool in missing_host_tools(root):
        print(
            f"  host: {tool} MISSING (a compiler the present package"
            " kinds need; install the platform toolchain)"
        )
    doctor_flow(this_forge(root))
