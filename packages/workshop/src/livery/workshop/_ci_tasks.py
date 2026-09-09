"""``fm status``, the ``ci`` group, and ``fm doctor``.

``status`` says where the current branch's pull request stands and
exits that state's code (0 while nothing is wrong); ``--watch``
follows instead of reading once. The ``ci`` group acts on the head
commit's runs: ``ci.rerun`` re-runs the failed jobs, ``ci.cancel``
cancels what is still moving (the relief for a wedged queue), and
``ci.logs`` prints the job logs, the one read that stays here so
logs reach an agent through fm. ``ci.run`` runs one job of a point
(livery.workshop._points), the one verb the emitted shells call;
``ci.verdict`` is the gate job's judgement of the jobs it needs.
``ci.janitor`` sweeps the CI state store (livery.workshop._state):
the per-run refs a cancelled run left behind, and each series'
window. ``ci.timings`` prints the timing rows the gate writes on
that store (livery.workshop._metrics); the two hidden ``ci.metrics``
verbs are the writers ``ci.run`` schedules. ``doctor`` says who you
are, which server this is, and what it grants.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from livery.footman import doc, fail, group, task
from livery.forge import Capability, Forge, ForgeError, Repository, Run
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._verdict import classify, follow

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
    for run in failed:
        repo.checks.rerun(run.id, failed_only=failed_only)
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


def logs_flow(
    repo: Repository, git: GitOps, *, lines: int = 80, failed_only: bool = True
) -> None:
    """Print the head commit's job logs, failed jobs first and by default.

    The tail of each log, newest run first, so a red branch explains
    itself without leaving the terminal.
    """
    sha = _head_sha(repo, git)
    runs = repo.checks.runs(head_sha=sha)
    if not runs:
        print(f"  no runs for {sha[:10]}")
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
        print(f"  no {which}jobs for {sha[:10]}")


@ci.task(name="logs")
def ci_logs(
    lines: Annotated[int, doc("log lines per job, from the tail")] = 80,
    failed_only: Annotated[bool, doc("only jobs that did not succeed")] = True,
) -> None:
    """Print the head commit's job logs, failed jobs by default."""
    repo, git = _resolved()
    logs_flow(repo, git, lines=lines, failed_only=failed_only)


def janitor_flow(root: Path, *, older_than_hours: float) -> None:
    """Sweep the state store and print every line of what happened."""
    from datetime import timedelta

    from livery.workshop._state import sweep

    for line in sweep(root, older_than=timedelta(hours=older_than_hours)):
        print(line)


@ci.task(name="janitor")
def ci_janitor(
    older_than: Annotated[
        float, doc("hours a per-run ref may live before it counts as orphaned")
    ] = 6.0,
) -> None:
    """Sweep the CI state store: orphaned per-run refs, and the windows.

    A per-run ref outlives its run only when the run was cancelled or
    died before its gate job could read and delete it; any older than
    ``--older-than`` hours is dropped. Every declared series is
    trimmed to its window. Idempotent: re-running is the recovery
    procedure, and a second sweep finds nothing to do.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    janitor_flow(root, older_than_hours=older_than)


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
                red.append(f"{found.name}: {found.conclusion or found.status}")
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


metrics = ci.group("metrics", help="The timing rows CI writes", hidden=True)


def metrics_leg_flow(root: Path, *, job: str, label: str, trace: Path) -> None:
    """Put the leg's timing row, or print why it could not."""
    from livery.workshop._metrics import put_leg
    from livery.workshop._state import run_context

    run = run_context()
    if run is None:
        print("  not a CI run: the leg's timing row is written by CI only")
        return
    why = put_leg(root, run, job=job, label=label, trace=trace)
    print(f"  {job}: {why or f'timing row recorded for run {run.run_id}'}")


@metrics.task(name="leg")
def ci_metrics_leg(
    *,
    job: Annotated[str, doc("the job's name as the forge lists it")],
    label: Annotated[str, doc("the per-run ref segment for this leg")],
    trace: Annotated[Path, doc("the trace the profiled gate wrote")] = Path(
        "fm-profile.json"
    ),
) -> None:
    """Record this leg's timings on its per-run ref for the gate job to collect.

    Runs at the end of every check leg, whatever the gate's verdict.
    Fails open loudly: a missing trace or a store fault prints its
    reason and the exit stays 0, so a timing row never reddens a leg.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    metrics_leg_flow(root, job=job, label=label, trace=trace)


def metrics_collect_flow(root: Path, repo: Repository, git: GitOps) -> None:
    """Collect the run's rows into the metrics series; print every line."""
    from livery.workshop._metrics import collect
    from livery.workshop._state import run_context

    run = run_context()
    if run is None:
        print("  not a CI run: the run's timing rows are collected by CI only")
        return
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
    """Print the rendered timings."""
    from livery.workshop._metrics import render

    for line in render(root, since=since, base=base):
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
    base runs' median.
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
        import tomllib

        from livery.workshop._governance import unknown_owners
        from livery.workshop._tokens import admin_token

        contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
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
