"""``fm status`` and the ``ci`` group.

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
(the gate, the nightly) and follows it to its verdict. ``ci.run``
runs one job of a point
(livery.workshop._points), the one verb the emitted shells call;
``ci.verdict`` is the gate job's judgement of the jobs it needs.
``ci.timings`` prints the timing rows the gate writes on
that store (livery.workshop._metrics), and ``ci.balance`` reads the
same rows to predict each platform's wall at one to several shards
and propose the counts (livery.workshop._balance); the hidden
``ci.metrics.collect`` is the gate job's writer ``ci.run`` schedules,
the hidden ``ci.fold`` folds each platform's shards into one leg
before the judges read, and the hidden ``ci.leg`` is each check leg's
one write of its row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery import git
from livery.footman import doc, fail, group, pre_tasks, task
from livery.forge import (
    ForgeError,
    Job,
    Repository,
    Run,
    Unsupported,
)
from livery.workshop._balance import MAX_SHARDS
from livery.workshop._ci_context import state_store
from livery.workshop._extensions import workspace_root
from livery.workshop._points import TRACE
from livery.workshop._verdict import (
    EXIT_CI_FAILED,
    EXIT_PENDING,
    EXIT_TIMEOUT,
    EXIT_UNREACHABLE,
    FollowTimedOut,
    ForgeUnreachable,
    JobWatch,
    Pending,
    classify,
    follow,
    follow_pull_request,
)

ci = group("ci", help="The head commit's CI runs")


@pre_tasks
def remember_mounted_tasks(inv: footman.Invocation) -> None:
    """Keep this run's merged task tree for the declared points' task check.

    A ``[[ci.point]]`` names a task some extension mounts; the check reads
    the tree discovery merged for this invocation, which the
    module-level registry no longer holds by the time a task runs.
    """
    from livery.workshop import _points

    _points.MOUNTED = inv.tasks


def _resolved() -> tuple[Repository, Path]:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    from livery.workshop._forge_lane import this_repository

    return this_repository(root), root


def status_flow(repo: Repository, root: Path) -> int:
    """Print where the branch stands; return that state's exit code."""
    verdict = classify(repo, git.current_branch(root), root)
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
    repo, root = _resolved()
    if workflow:
        from livery.workshop._workflow_state import workflow_states
        from livery.workshop._workflow_tasks import render_workflows

        render_workflows(workflow_states(repo, root))
        return
    if watch:
        follow_pull_request(
            repo, git.current_branch(root), root, interval=interval, timeout=timeout
        )
        return
    code = status_flow(repo, root)
    if code:
        raise SystemExit(code)


def _head_sha(repo: Repository, root: Path) -> str:
    """The commit whose runs matter: the PR's head, else the local HEAD.

    After a push the two agree; the pull request's answer also covers
    a checkout that has moved on locally since the push.
    """
    pr = repo.pr.find_by_head(git.current_branch(root))
    return pr.head_sha if pr is not None else git.head_sha(root)


def rerun_flow(repo: Repository, root: Path, *, failed_only: bool = True) -> None:
    """Re-run the failed runs that hold this branch's verdict.

    The verdict is each workflow's newest run on a commit this branch
    contains. The head commit's runs are the common case, but a
    workflow triggered on an earlier commit (a merged release squash,
    a path-filtered job) keeps its verdict until it re-runs, so
    scoping to the head sha alone would miss it. Each rerun is read
    back: a forge that quietly ignores the request is named, never
    reported as re-running. Off main, a run on a commit main already
    contains is main's verdict (a scheduled nightly, a merge-point
    run): it is named and left for a re-run from main.
    """
    sha = _head_sha(repo, root)
    on_main = git.current_branch(root) == "main"
    newest: dict[str, Run] = {}
    # The newest hundred, asked of the forge so a repository with a
    # long run history stays cheap. A run on a sha this clone does not
    # contain belongs to another branch and is skipped.
    for run in repo.checks.runs(limit=100):
        if run.workflow in newest:
            continue
        if run.head_sha == sha or git.is_ancestor(root, run.head_sha, "HEAD"):
            newest[run.workflow] = run
    failed = [
        run
        for run in newest.values()
        if run.status == "completed" and run.conclusion != "success"
    ]
    from livery.workshop._points import BUILTIN, workflow_of

    release_workflow = workflow_of("release")
    mains = [
        run
        for run in failed
        if not on_main
        and run.head_sha != sha
        and git.is_ancestor(root, run.head_sha, "origin/main")
        and (run.workflow or "").rsplit("/", 1)[-1] != release_workflow
    ]
    for run in mains:
        print(
            f"  {run.workflow} (run {run.id}) failed on {run.head_sha[:10]}, which"
            " main already has: it is main's verdict, so re-run it from main"
        )
    failed = [run for run in failed if run not in mains]
    if not failed and mains:
        return
    if not failed:
        running = [run for run in newest.values() if run.status != "completed"]
        for run in running:
            red = [
                job.name
                for job in repo.checks.jobs(run.id)
                if job.status == "completed" and job.conclusion not in _GREEN
            ]
            so_far = f" ({', '.join(red)} red so far)" if red else ""
            print(
                f"  {run.workflow} (run {run.id}) is still in progress{so_far}: a"
                f" re-run needs its verdict first, `{footman.prog()} ci.status --wait`"
            )
        if not running:
            print(f"  nothing failed on this branch as of {sha[:10]}")
        return
    verdict_jobs = {entry.job for entry in BUILTIN if entry.task == "ci.verdict"}
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
    repo, root = _resolved()
    rerun_flow(repo, root, failed_only=failed_only)


def cancel_flow(repo: Repository, root: Path, *, force: bool = False) -> None:
    """Cancel the head commit's unfinished runs.

    The relief for a wedged queue; ``force`` reaches the runs a plain
    cancel cannot, where the forge grants the capability.
    """
    sha = _head_sha(repo, root)
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
    repo, root = _resolved()
    cancel_flow(repo, root, force=force)


#: The run conclusions that are not red.
_GREEN = ("success", "skipped", "neutral")


def cancel_superseded_runs(repo: Repository, sha: str) -> list[str]:
    """Cancel the runs still moving for *sha*, a commit a newer push has superseded.

    A push that moves a branch's head leaves the old head's runs
    running with nothing left to read them, and on a shared runner
    they delay the new head's. The cancel is best effort: a run the
    forge refuses to cancel (already terminal, cancelling) is named
    and left. Returns one line per run touched.
    """
    lines: list[str] = []
    try:
        runs = repo.checks.runs(head_sha=sha)
    except ForgeError as error:
        return [f"  superseded runs for {sha[:12]}: not read ({error})"]
    for run in runs:
        if run.status == "completed":
            continue
        try:
            repo.checks.cancel_run(run.id)
        except ForgeError as error:
            lines.append(
                f"  superseded run {run.id} for {sha[:12]}: not cancelled ({error})"
            )
            continue
        lines.append(f"  superseded run {run.id} for {sha[:12]}: cancelled")
    return lines


#: How many of a workflow's newest runs a follow or a dispatch reads. A
#: run that started since the last read is among them, however long the
#: history behind them is.
RECENT_RUNS = 20


def point_runs(
    repo: Repository,
    point: str,
    root: Path | None = None,
    *,
    limit: int = RECENT_RUNS,
) -> tuple[Run, ...]:
    """The newest *limit* runs of *point*'s workflow, whatever commit they checked.

    Newest first. Asked of the workflow's own listing for each of the
    point's events, so a forge with a long history answers with one
    page. The workflow's file name is compared here as well (GitHub
    lists the path, Gitea the file; the basename is the comparison). A
    forge that names no workflow on a run (GitLab) answers by event
    alone.
    """
    from livery.workshop._points import events_of, workflow_of

    root = root if root is not None else workspace_root()
    workflow = workflow_of(point, root)
    found: dict[int, Run] = {}
    for event in events_of(root, point):
        for run in repo.checks.runs(event=event, workflow=workflow, limit=limit):
            if not run.workflow or run.workflow.rsplit("/", 1)[-1] == workflow:
                found[run.id] = run
    newest = sorted(found.values(), key=lambda run: run.id, reverse=True)
    return tuple(newest[:limit] if limit else newest)


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

    def read() -> Run | Pending:
        current = next((r for r in point_runs(repo, point) if r.id == run.id), run)
        if current.status == "completed":
            return current
        return Pending(
            f"  waiting: {point} run {current.id} is {current.status}",
            key=current.status,
        )

    try:
        current = follow(
            repo,
            read,
            subject=f"{point} run {run.id} {run.url}",
            interval=interval,
            timeout=timeout,
        )
    except ForgeUnreachable as exc:
        print(exc.line)
        return EXIT_UNREACHABLE
    except FollowTimedOut as exc:
        print(f"  still {exc.last.state} after {timeout:.0f}s; {run.url}")
        return EXIT_TIMEOUT
    word, code = runs_state((current,))
    print(f"  {word}: {point} run {current.id} {current.conclusion} {current.url}")
    if code:
        for job in repo.checks.jobs(current.id):
            if job.conclusion not in _GREEN:
                print(f"    {job.name}: {job.conclusion or job.status}")
    return code


def _short_ref(ref: str) -> str:
    """*ref* as a forge names a run's ref: ``refs/heads/main`` is ``main``."""
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def remote_head(root: Path, ref: str) -> str:
    """The commit branch *ref* names on origin; empty for a tag or a missing branch.

    The commit a dispatched run checks out, read just before the
    dispatch. A tag names one commit for good, so only a branch is
    read. An unreachable origin refuses, naming git's words.
    """
    name = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
    if not name.startswith("refs/heads/"):
        return ""
    try:
        return git.ls_remote(root, name).get(name, "")
    except git.GitError as error:
        fail(
            f"cannot read {ref} on origin, the commit that tells the dispatched"
            f" run from another: {error}"
        )


def _named(run: Run) -> str:
    """One run as a refusal names it: id, ref, commit, page."""
    return f"run {run.id} on {run.ref or '?'} at {run.head_sha[:10]} {run.url}"


def own_runs(
    runs: tuple[Run, ...],
    *,
    seen: set[int],
    started: int | None,
    ref: str,
    head: str,
) -> list[Run]:
    """The runs among *runs* that may be the one a dispatch on *ref* started.

    *started* is the id the forge's answer named, and settles it.
    Without one, a run qualifies when it is a dispatched run, is not
    in *seen* (the listing read just before the dispatch), checked
    out *ref*, and, when *head* names a commit, checked out that
    commit. A run of another ref never qualifies, however new.
    """
    if started is not None:
        return [run for run in runs if run.id == started]
    return [
        run
        for run in runs
        if run.id not in seen
        and run.event == "workflow_dispatch"
        and _short_ref(run.ref) == _short_ref(ref)
        and (not head or run.head_sha == head)
    ]


def dispatch_flow(
    repo: Repository,
    *,
    point: str,
    ref: str,
    head: str = "",
    follow: bool = True,
    interval: float = 15,
    timeout: float = 1800,
    register_timeout: float = 60,
    only: str = "",
) -> int:
    """Start *point* on *ref* by hand; follow it to its verdict; return the exit.

    Refuses a point without a dispatch entry, naming what starts it
    instead. *only* names the scheduled tasks the run takes,
    comma-separated, for a point that takes the input: a rehearsal
    names the loop alone. A name the point does not run, or *only* on
    a point without the input, refuses before anything is dispatched.

    The dispatch is confirmed by its own run: the one the forge's
    answer names, or else the one dispatched run of *ref* that was
    not listed before the dispatch and, when *head* is given, checked
    out that commit, the one *ref* named just before. A run of another
    ref is never followed. Two or more such runs refuse, naming each,
    since the listing cannot tell which one this dispatch started.
    None within *register_timeout* is reported with the verb that
    reads the point later and the dispatched runs of other refs or
    commits that did appear, exit 14. Without *follow* the run is
    named and the exit is 18 while it moves.
    """
    from livery.workshop._points import (
        ONLY,
        asked_tasks,
        dispatchable,
        point_by_name,
        workflow_of,
    )

    root = workspace_root()
    names = point_by_name(root)
    if point not in names:
        fail(f"{point!r} is not a point; the points are {', '.join(names)}")
    starts = dispatchable(root)
    if point not in starts:
        if point == "release":
            fail(
                "the release point has no dispatch entry of its own: the merge"
                f" point dispatches the wave, and `{footman_prog()}"
                " workflow.release.dispatch` does by hand"
            )
        fail(
            f"the {point} point has no dispatch entry: it runs on a push to"
            f" main; `{footman_prog()} ci.dispatch` starts"
            f" {', '.join(starts)}"
        )
    inputs: dict[str, str] = {}
    if only:
        if ONLY not in {item.name for item in names[point].inputs}:
            takes = [
                name
                for name, found in names.items()
                if ONLY in {i.name for i in found.inputs}
            ]
            fail(
                f"the {point} point takes no --only; {', '.join(takes) or 'no point'}"
                " does"
            )
        if root is None:
            fail("--only reads the workspace's schedule: run it inside a workspace")
        asked = asked_tasks(root, point, only)
        inputs[ONLY] = ",".join(asked)
    workflow = workflow_of(point, root)
    seen = {run.id for run in point_runs(repo, point)}
    try:
        started = repo.checks.dispatch(workflow, ref=ref, inputs=inputs or None)
    except Unsupported as exc:
        fail(f"this forge cannot dispatch a workflow: {exc}")
    except ForgeError as exc:
        fail(f"the forge refused the {point} dispatch on {ref}: {exc}")
    running = f", running {inputs[ONLY]} alone" if inputs.get(ONLY) else ""
    at = f" at {head[:10]}" if head else ""
    print(f"  dispatched the {point} point on {ref}{at} ({workflow}){running}")
    last_runs: tuple[Run, ...] = ()

    def registered() -> Run | Pending:
        nonlocal last_runs
        last_runs = point_runs(repo, point)
        own = own_runs(last_runs, seen=seen, started=started, ref=ref, head=head)
        if len(own) > 1:
            fail(
                f"{len(own)} {point} runs started on {ref}{at} after this"
                " dispatch, and the forge's answer named none of them, so it"
                f" cannot tell which one is its own: {'; '.join(map(_named, own))}."
                " Read them on the forge, or dispatch again once no other"
                f" {point} dispatch on {ref} is starting"
            )
        return own[0] if own else Pending()

    # `follow` is this verb's flag; the helper comes in under its own name.
    from livery.workshop._verdict import follow as follow_until

    try:
        run = follow_until(
            repo,
            registered,
            subject=f"the {point} run of {ref}{at}",
            interval=interval,
            timeout=register_timeout,
            quiet=None,
        )
    except ForgeUnreachable as exc:
        fail(exc.line.strip())
    except FollowTimedOut:
        print(
            f"  the dispatch was accepted but no {point} run of {ref}{at}"
            f" appeared within {register_timeout:.0f}s; `{footman_prog()}"
            f" ci.status --point={point}` reads one that appears later"
        )
        others = [
            run
            for run in last_runs
            if run.id not in seen and run.event == "workflow_dispatch"
        ]
        if others:
            print(f"  not its own: {'; '.join(map(_named, others))}")
        return EXIT_TIMEOUT
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
        str, doc("the point to start: gate, nightly, or one a package contributes")
    ] = "nightly",
    ref: Annotated[str, doc("the branch or tag the run checks out")] = "main",
    follow: Annotated[bool, doc("wait for the run's verdict")] = True,
    interval: Annotated[int, doc("poll seconds")] = 15,
    timeout: Annotated[int, doc("deadline seconds for the verdict")] = 1800,
    only: Annotated[
        str,
        doc(
            "the scheduled tasks the run takes, comma-separated, for the"
            " nightly: ci.e2e rehearses the loop alone; empty runs everything"
        ),
    ] = "",
) -> None:
    """Start a point on the forge by hand and follow it to its verdict.

    The gate and the nightly carry a dispatch entry. A dispatched
    gate pays the full gate on *ref*, since the check verb narrows on
    a pull request alone, and its stamp writes a full row for the
    tree it proved; the nightly's run waits for nobody, and this verb
    starts the same run now, so a failure only the nightly meets (a
    floor Python, a release's verification) is reproduced on demand.
    The run followed is the dispatch's own: the one the forge names,
    or the one dispatched run of *ref*, at the commit origin names for
    it, that appeared after the dispatch. Two such runs refuse, naming
    both. Exits green 0, red 13 with the red jobs named, a run that
    never registered or a wait that runs out 14, a forge unreachable
    for five polls in a row 15, and, with ``--no-follow``, 18 while
    the run moves. The merge point runs on a push alone, and the
    release wave is the merge point's to dispatch.
    """
    repo, root = _resolved()
    code = dispatch_flow(
        repo,
        point=point,
        ref=ref,
        head=remote_head(root, ref),
        follow=follow,
        interval=interval,
        timeout=timeout,
        only=only,
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
    root: Path,
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
    is named in one line and exits 15 at once.
    """
    sha = ""
    if point:
        subject = f"the {point} point"
        nothing = f"no {point} runs yet"
    else:
        sha = _head_sha(repo, root)
        subject = sha[:10]
        nothing = f"no runs yet for {sha[:10]}"
    seen_runs: dict[int, str] = {}
    jobs = JobWatch()

    def read() -> tuple[str, int, int]:
        if point:
            runs = point_runs(repo, point, limit=1)
        else:
            runs = repo.checks.runs(head_sha=sha)
        # A line per run when it moves, and a line per job likewise;
        # a poll that changes nothing prints nothing.
        for run in runs:
            if seen_runs.get(run.id) != (run.conclusion or run.status):
                seen_runs[run.id] = run.conclusion or run.status
                print(jobs.stamp() + _run_line(run).lstrip())
        if not point:
            jobs.report(repo, sha)
        word, code = runs_state(runs)
        if not runs:
            print(f"  {nothing}")
        return word, code, len(runs)

    def settled() -> tuple[str, int, int] | Pending:
        answer = read()
        return Pending() if answer[1] == EXIT_PENDING else answer

    if wait:
        try:
            word, code, count = follow(
                repo,
                settled,
                subject=f"the runs for {subject}",
                interval=interval,
                timeout=timeout,
                quiet=None,
            )
        except ForgeUnreachable as exc:
            print(exc.line)
            return EXIT_UNREACHABLE
        except FollowTimedOut:
            print(f"  still pending after {timeout:.0f}s")
            return EXIT_TIMEOUT
    else:
        # One read, no retry: an unreadable forge is named in one line
        # with the retrying form, never a traceback.
        try:
            word, code, count = read()
        except ForgeError as exc:
            print(
                f"  the forge answered {exc} while reading the runs for {subject};"
                f" run again, or `{footman_prog()} ci.status --wait` to retry"
            )
            return EXIT_UNREACHABLE
    print(f"  {word}: {count} run(s) for {subject}")
    return code


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
    repo, root = _resolved()
    code = runs_status_flow(
        repo, root, wait=wait, interval=interval, timeout=timeout, point=point
    )
    if code:
        raise SystemExit(code)


#: The marks of a line that names a failure: pytest's short summary,
#: the runner's error annotations, and a verb's own refusal line.
_FAILURE_MARKS = ("FAILED ", "##[error]", "fm: ")


def failure_lines(log: str, *, limit: int = 40) -> list[str]:
    """The lines of *log* that name a failure, the first *limit* of them.

    A long leg buries pytest's ``FAILED`` summary hundreds of lines
    above its end, behind the coverage table, so a tail alone shows
    the exit code and not the test.
    """
    found = [
        line
        for line in log.splitlines()
        if any(mark in line for mark in _FAILURE_MARKS)
    ]
    return found[:limit]


def logs_flow(
    repo: Repository,
    root: Path,
    *,
    lines: int = 80,
    failed_only: bool = True,
    point: str = "",
) -> None:
    """Print the head commit's job logs, failed jobs first and by default.

    The tail of each log, newest run first, so a red branch explains
    itself without leaving the terminal; the log's failure lines
    (livery.workshop._ci_tasks.failure_lines) come first when the tail
    does not reach them. With *point* the one run read is the newest
    of that point's workflow.
    """
    if point:
        runs = point_runs(repo, point, limit=1)
        subject = f"the {point} point"
    else:
        sha = _head_sha(repo, root)
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
            tail = log.splitlines()[-lines:]
            named = [line for line in failure_lines(log) if line not in tail]
            if named:
                print("    failures named above the tail:")
                for line in named:
                    print(f"    {line}")
                print("    tail:")
            for line in tail:
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
    repo, root = _resolved()
    logs_flow(repo, root, lines=lines, failed_only=failed_only, point=point)


@ci.task(name="run")
def ci_run(
    *,
    point: Annotated[str, doc("the point: gate, merge, nightly, or release")],
    job: Annotated[str, doc("the point's job this runner executes")],
    os: Annotated[str, doc("the matrix runner label, on a matrix job")] = "",
    python: Annotated[str, doc("the matrix python, on a matrix job")] = "",
    shard: Annotated[
        str, doc("the leg's shard, <i>of<n>, on a check leg the contract splits")
    ] = "",
) -> None:
    """Run one job of a point: every task scheduled there, in order.

    The one verb the emitted workflows call, once per job. What the
    job does is data: the workshop's builtin schedule plus the
    contract's ``[[ci.schedule]]`` entries. A push to main promotes
    the gate to the merge point, so the shell spells no decision. On
    a check leg the contract splits, ``--shard=2of4`` names the shard
    this job runs as: it ends the leg's label and the job's display
    name, and the walk runs that shard's share; without it the job
    runs as one shard of one. A person may run a job by hand; the
    tasks that only CI may do refuse or fail open on their own.
    """
    from livery.workshop._points import run_point

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    try:
        run_point(root, point, job, os_label=os, python=python, shard=shard)
    finally:
        note = sweep_tool_store()
        if note:
            print(f"  {note}")


def sweep_tool_store(environ: dict[str, str] | None = None) -> str:
    """Sweep the tool store on a GitHub job, before the post-job save; the note.

    A tool's archive stays in the store as an object once its tree is
    extracted and named, and nothing reaches it again: the tree is what
    a pinned version runs from. The workflow saves the whole store,
    so those objects would ride in every archive and cost every restore.
    Off a GitHub job nothing runs and the note is empty; on Gitea's
    runner, which sets the same variable, shares one store across its
    jobs and saves none, the store is kept whole and the note says so.
    A failed sweep is named and never decides the job.
    """
    import os as _os

    found = _os.environ if environ is None else environ
    if not found.get("GITHUB_ACTIONS"):
        return ""
    if found.get("GITEA_ACTIONS"):
        # Gitea's runner sets GITHUB_ACTIONS for compatibility, shares
        # one store across its jobs and saves no archive, so a sweep
        # there only makes the next job fetch the catalogue again.
        return (
            "tool store: kept whole; this runner shares it across jobs and saves none"
        )
    from livery.strongroom import LockTimeout, StoreError
    from livery.workshop._tools import _home  # pyright: ignore[reportPrivateUsage]

    try:
        report = _home().open_store().sweep()
    except (LockTimeout, StoreError, OSError) as error:
        return f"tool store: sweep failed ({error}); the archive saves unswept"
    return (
        f"tool store: {len(report.removed)} unreached object(s) swept before the save"
    )


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
    from livery.workshop._ci_context import run_context

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


def verified_stamp_flow(root: Path) -> None:
    """Stamp the run's tree when every check leg ran the full gate; print why not."""
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import run_context
    from livery.workshop._verified import stamp_from_metrics

    run = run_context()
    if run is None:
        print("  not a CI run: the verified record is written by CI only")
        return
    with remote_snapshot(state_store(root), fetch=("metrics", "verified")):
        print(stamp_from_metrics(root, run, sha=git.head_sha(root)))


@verified.task(name="stamp")
def ci_verified_stamp() -> None:
    """Record this run's tree as proved green, for the runs that share it.

    Runs in the gate job after the verdict, so only a green run
    reaches it. Every check leg's metrics row must say it ran the
    full gate; a narrowed leg leaves the tree unstamped, and a later
    run of the same tree pays the gate. Fails open loudly: every
    reason is printed and the exit stays 0.
    """
    _repo, root = _resolved()
    verified_stamp_flow(root)


def leg_flow(root: Path, *, job: str, trace: Path) -> None:
    """Put this leg's row and its spool on its per-run ref; red when the write fails."""
    from livery.workshop._ci_context import run_context
    from livery.workshop._metrics import put_leg

    run = run_context()
    if run is None:
        print("  not a CI run: a check leg's row is put by CI only")
        return
    lines, why = put_leg(
        root,
        run,
        job=job,
        label=run.leg,
        trace=trace if trace.is_absolute() else root / trace,
    )
    for line in lines:
        print(line)
    if why:
        fail(
            f"the leg's row could not be put on its per-run ref ({why}): a leg"
            " whose write fails is red, since the gate job would judge it by"
            " what it never put"
        )


@ci.task(name="leg", hidden=True)
def ci_leg(
    *,
    job: Annotated[str, doc("the job's name as the forge lists it")] = "",
    trace: Annotated[Path, doc("the trace the gate wrote")] = Path(TRACE),
) -> None:
    """Put this check leg's row and the files its entries left on its ref, in one write.

    The check leg's last entry. The row is the leg's timing half, read
    from the trace the profiled gate wrote, with the scope the gate
    ran; beside it go the files the leg's other entries left in its
    spool, ``.workshop/leg/``, each under its own name. A trace that
    cannot be read prints its reason and the write goes on without
    the row. A write that fails is red.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    leg_flow(root, job=job, trace=trace)


def fold_flow(root: Path) -> None:
    """Fold each platform's shards on the run's refs into one leg; print every line."""
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import run_context
    from livery.workshop._fold import fold_run

    run = run_context()
    if run is None:
        print("  not a CI run: a platform's shards are folded by CI only")
        return
    with remote_snapshot(state_store(root), fetch=(f"run/{run.run_id}/",)):
        fold_run(root, run)


@ci.task(name="fold", hidden=True)
def ci_fold() -> None:
    """Fold each platform's shards into one check leg on the run's refs.

    Runs in the gate job before the judges, the collect, the verdict
    and the stamp read the run's refs. A platform whose check leg ran
    as several shards has one ref per shard; the fold proves the
    shards are one leg's work (one scope, one test-file set from one
    durations row, every file in exactly one shard, every shard the
    matrix produces present) and puts one ref under the platform
    leg's key, the timing sums added and every other file folded by
    the extension that leaves it, then drops the shards' refs. A
    platform whose shards disagree, or one of whose shards left no
    ref, is red naming the platform, the shards and what differs. A
    run with no sharded platform says so and changes nothing.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    fold_flow(root)


metrics = ci.group("metrics", help="The timing rows CI writes", hidden=True)


def metrics_collect_flow(root: Path, repo: Repository) -> None:
    """Collect the run's rows into the metrics series; print every line."""
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import run_context
    from livery.workshop._metrics import collect

    run = run_context()
    if run is None:
        print("  not a CI run: the run's timing rows are collected by CI only")
        return
    with remote_snapshot(state_store(root), fetch=(f"run/{run.run_id}/", "metrics")):
        for line in collect(root, repo, run, sha=git.head_sha(root)):
            print(line)


@metrics.task(name="collect")
def ci_metrics_collect() -> None:
    """Join the legs' timing rows with the forge's times into the run's file.

    Runs in the gate job before its verdict. Reads the per-run refs
    the legs wrote, asks the forge for the run's jobs and their
    steps, puts the run's file on the metrics series under its
    window, and drops the per-run refs when every completed job is
    green; a red job keeps them, since the forge's re-run of the
    failed jobs runs this gate again without its legs. Fails open
    loudly: every reason is printed and the exit stays 0.
    """
    repo, root = _resolved()
    metrics_collect_flow(root, repo)


profile = ci.group("profile", help="A run as one timeline")


@profile.default
def ci_profile(
    *,
    run: Annotated[str, doc("the run's id; the newest for HEAD by default")] = "",
    from_: Annotated[
        str, doc("walk every run about this commit, and what its merge caused")
    ] = "",
    into: Annotated[str, doc("where the file lands; the contract's own by default")] = (
        ""
    ),
) -> None:
    """Write one CI run as one timeline, to open at ui.perfetto.dev.

    Every job of the run is a track at the forge's own times, its steps
    inside it, and each leg that kept a trace brings its own tasks, steps
    and tests, laid on the run's clock by the origin each side recorded.
    This is what the legs' traces are kept for, and what the window bounds.

    ``--from=<commit>`` writes the chain instead: every run about that
    commit, and the runs of the commit its merge produced. A release is
    two commits and one hop, so the pull request's run, the base's own
    run and the wave land in one file. Any commit-ish a checkout can
    resolve names it, ``HEAD`` included.

    Refuses when no file could be written, naming why: a person asked for
    one. A run whose traces have aged out of the window still assembles from
    the forge's skeleton alone, and says which legs brought nothing.
    """
    from livery.workshop._traces import write_chain, write_run

    repo, root = _resolved()
    where = Path(into) if into else None
    if from_:
        commit = git.sha_of(root, from_)
        if not commit:
            fail(f"--from={from_} names no commit this checkout can resolve")
        path, lines = write_chain(root, repo, commit, into=where)
        for line in lines:
            print(f"  {line}")
        if path is None:
            fail(lines[0] if lines else f"nothing about {commit[:12]}")
        print(f"  profile: {path}")
        return
    path, lines = write_run(
        root,
        repo,
        run_id=run,
        head_sha="" if run else git.head_sha(root),
        into=where,
    )
    for line in lines:
        print(f"  {line}")
    if path is None:
        fail(lines[0] if lines else "no run to assemble")
    print(f"  profile: {path}")


def timings_flow(root: Path, *, since: int, base: int) -> None:
    """Print the rendered timings, then the speed marks beside the newest run."""
    from livery.git import remote_snapshot
    from livery.workshop._metrics import render
    from livery.workshop._speed import render_marks

    with remote_snapshot(state_store(root), fetch=("metrics", "speed/marks")):
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
    and its median and ninetieth percentile over the series' window;
    a platform that ran as shards is one job, its folded row, with a
    line per shard under it giving the shard's wall and queue times
    from the newest run. Then the biggest movers: the recent runs'
    median against the base runs' median. Then the speed marks, each
    package's mark on each leg beside the newest run's time.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    timings_flow(root, since=since, base=base)


def balance_flow(root: Path, *, cap: int) -> None:
    """Print the balancer's window line, its tables and its proposal."""
    from livery.git import remote_snapshot
    from livery.workshop._balance import render

    with remote_snapshot(state_store(root), fetch=("metrics",)):
        for line in render(root, cap=cap):
            print(line)


@ci.task(name="balance")
def ci_balance(
    max_: Annotated[int, doc("the largest shard count a platform may take")] = (
        MAX_SHARDS
    ),
) -> None:
    """Predict each check platform's wall at one to several shards; propose the counts.

    Reads the metrics series the gate job writes; CI is its only
    writer, a local run reads. Per platform, a runner and a Python,
    the newest ten rows whose check jobs ran the full gate and
    succeeded give the medians of four measures: F, the setup and
    teardown the forge timed around the call; O, the longest gate
    step that is not the test step; S, the summed test time over
    every package; and p, the parallelism the test step reached, S
    over its wall. The shards of a platform fold into one reading.
    The wall at n shards is predicted as F + max(O, S / (p n)) and
    the runner seconds as about n F + S / p + O; the table prints
    both for every count up to ``--max``.

    The proposal: the slowest platform, the largest wall at one
    shard, takes the largest count whose every added shard saved at
    least F; every other platform takes the smallest count whose
    wall is within 15% of the slowest platform's, or the cap, named.
    The pull request counts follow the same rule over
    ``[ci] pull-request-runners`` when that list is shorter than
    ``[ci] runners``. The proposal prints as the TOML ``[ci] shards``
    and ``[ci] pull-request-shards`` would hold, with the predicted
    wall of the slowest leg beside its wall at one shard.

    A platform with fewer than three such rows keeps its count and is
    named. A store that cannot be read prints why and proposes
    nothing. Changes nothing: a count lands in the contract by pull
    request.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    balance_flow(root, cap=max_)
