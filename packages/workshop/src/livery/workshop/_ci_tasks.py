"""``fm status``, the ``ci`` group, and ``fm doctor``.

``status`` says where the current branch's pull request stands and
exits that state's code (0 while nothing is wrong); ``--watch``
follows instead of reading once. The ``ci`` group acts on the head
commit's runs: ``ci.rerun`` re-runs the failed jobs, ``ci.cancel``
cancels what is still moving (the relief for a wedged queue), and
``ci.logs`` prints the job logs, the one read that stays here so
logs reach an agent through fm. ``doctor`` says who you are, which
server this is, and what it grants.
"""

from __future__ import annotations

from typing import Annotated

from footman import doc, fail, group, task

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
