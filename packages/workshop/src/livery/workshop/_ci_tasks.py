"""``fm status``, the ``ci`` group, and ``fm doctor``.

``status`` prints one classified read of the current branch's pull
request and exits its verdict code (0 while nothing is wrong). The
``ci`` group works on the head commit's runs: ``ci.watch`` follows to
the classified verdict, ``ci.rerun`` re-runs the failed jobs,
``ci.cancel`` cancels what is still moving (the relief for a wedged
queue). ``doctor`` answers who you are, what the server is, and which
capabilities this forge grants.
"""

from __future__ import annotations

from typing import Annotated

from footman import doc, fail, group, task

from livery.forge import Capability, Forge, Repository
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._verdict import classify, follow

ci = group("ci", help="The head commit's CI runs")

_CAPABILITIES: tuple[Capability, ...] = (
    "auto_merge",
    "force_cancel",
    "required_contexts",
    "ci_secrets",
)


def _resolved() -> tuple[Repository, GitOps]:
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    from livery.workshop._forge_lane import this_repository

    return this_repository(root), GitOps(root)


def status_flow(repo: Repository, git: GitOps) -> int:
    """One classified read, printed; returns the verdict's exit code."""
    verdict = classify(repo, git.current_branch(), git)
    print(f"  {verdict.state}: {verdict.detail}")
    return verdict.exit_code


@task
def status() -> None:
    """Print the branch's classified verdict; exit its code.

    Exit 0 covers merged, in-flight, and no-PR; the blocker codes are
    stable interface (see livery.workshop._verdict).
    """
    repo, git = _resolved()
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
    """Re-run the head commit's runs (failed jobs only by default)."""
    sha = _head_sha(repo, git)
    runs = repo.checks.runs(head_sha=sha)
    if not runs:
        print(f"  no runs for {sha[:10]}")
        return
    for run in runs:
        if run.status == "completed" and run.conclusion != "success":
            repo.checks.rerun(run.id, failed_only=failed_only)
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


@ci.task(name="watch")
def ci_watch(
    interval: Annotated[int, doc("poll seconds")] = 15,
    timeout: Annotated[int, doc("deadline seconds")] = 1800,
) -> None:
    """Follow the branch to its classified verdict (see fm status)."""
    repo, git = _resolved()
    follow(repo, git.current_branch(), git, interval=interval, timeout=timeout)


def doctor_flow(forge: Forge) -> None:
    """Print identity, server, and capabilities for *forge*."""
    print(f"  whoami: {forge.whoami()}")
    print(f"  server: {forge.server_version()}")
    for capability in _CAPABILITIES:
        verdict = "yes" if forge.supports(capability) else "no"
        print(f"  {capability}: {verdict}")


@task
def doctor() -> None:
    """Who you are, what the server is, what it grants (W12)."""
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    from livery.workshop._forge_lane import this_forge

    doctor_flow(this_forge(root))
