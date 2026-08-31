"""The classified verdict on a branch's pull request.

One read of the forge plus two local git probes, folded into a
livery.workshop._verdict.Verdict that names the state and the exit
code a caller branches on. The codes are stable interface: skills,
hooks, and humans read them, so a new state gets a new code, never a
reused one.

Exit codes:
    10: the merge would conflict; integrate the base and re-submit.
    11: nothing will merge it; the pull request is not armed.
    12: the pull request is closed unmerged.
    13: CI is red; the message names the failing job.
    14: still in flight when the watch deadline passed.
    15: the forge was unreachable past the transient budget.
    16: green and armed with no merge; the server lost the evaluation.
    17: the head is behind the base; integrate and re-submit.

A required-review blocker has no code: the frozen livery.forge
protocol carries no review surface, so a review-blocked pull request
reads as 16 and the message says what to check.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from livery.forge import ForgeError, Repository
from livery.workshop._git_ops import GitOps

EXIT_CONFLICTS = 10
EXIT_DISARMED = 11
EXIT_CLOSED = 12
EXIT_CI_FAILED = 13
EXIT_TIMEOUT = 14
EXIT_UNREACHABLE = 15
EXIT_STALLED = 16
EXIT_BEHIND = 17

#: Consecutive unreadable polls before the watch gives up with 15.
_UNREACHABLE_BUDGET = 5

#: Green-and-armed polls the watch grants the server to merge before
#: probing for behind/conflicts and calling the evaluation lost.
#: Forge evidence (livery PR #35): github.com's auto-merge can take
#: over a minute after green, and three 15s polls declared it stalled
#: 25 seconds before the merge landed.
_MERGE_GRACE_POLLS = 8


@dataclass(frozen=True)
class Verdict:
    """One classified read of a branch's pull request.

    Attributes:
        state: A short name: ``merged``, ``in-flight``, ``conflicts``,
            ``disarmed``, ``closed``, ``ci-failed``, ``stalled``,
            ``behind``, ``no-pr``.
        exit_code: The stable code for the state; 0 when nothing is
            wrong (``merged``, ``in-flight``, ``no-pr``).
        detail: One sentence for a person, naming the evidence.
        pr_number: The pull request, when one exists.
    """

    state: str
    exit_code: int
    detail: str
    pr_number: int = 0


def _failing_job(repo: Repository, head_sha: str) -> str:
    """The first failing job's name for *head_sha*, or empty."""
    for run in repo.checks.runs(head_sha=head_sha):
        if run.conclusion in ("failure", "cancelled"):
            for job in repo.checks.jobs(run.id):
                if job.conclusion in ("failure", "cancelled"):
                    return f"{run.workflow}: {job.name} ({job.conclusion})"
            return f"{run.workflow} ({run.conclusion})"
    return ""


def classify(
    repo: Repository, branch: str, git: GitOps, *, grace_spent: bool = False
) -> Verdict:
    """One read, classified.

    *grace_spent* is the watcher's signal that a green-and-armed pull
    request has already been given time to merge, so the local probes
    (behind, conflicts) may now decide between 10, 16, and 17. A
    single un-watched read never reports 16: the server may simply not
    have evaluated yet.
    """
    pr = repo.pr.find_by_head(branch, state="all")
    if pr is None:
        sha = git.head_sha()
        merged = repo.pr.find_by_head_sha(sha)
        if merged is not None and merged.merged:
            return Verdict("merged", 0, f"PR #{merged.number} merged", merged.number)
        return Verdict("no-pr", 0, f"no pull request for {branch}")
    if pr.merged:
        return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
    if pr.state == "closed":
        return Verdict(
            "closed",
            EXIT_CLOSED,
            f"PR #{pr.number} is closed unmerged; `fm submit` after reopening,"
            " or start over",
            pr.number,
        )
    status = repo.checks.status(pr.head_sha)
    if status.state == "failure":
        failing = _failing_job(repo, pr.head_sha)
        detail = failing or "CI is red"
        return Verdict("ci-failed", EXIT_CI_FAILED, detail, pr.number)
    if status.state in ("pending", "none"):
        return Verdict(
            "in-flight", 0, f"CI {status.state} for {pr.head_sha[:10]}", pr.number
        )
    # Green. A conflict blocks the merge whatever the arming state, so
    # it is classified first; behind matters only once an armed pull
    # request has been given time, because without strict protection an
    # armed behind head still merges.
    git.fetch()
    if git.conflicts_with_base(pr.base_branch):
        return Verdict(
            "conflicts",
            EXIT_CONFLICTS,
            f"PR #{pr.number} conflicts with {pr.base_branch}",
            pr.number,
        )
    if not repo.pr.is_armed(pr.number):
        # Forge evidence (livery PR #21): a merge in flight consumes the
        # schedule between the open-PR read and this one, so an armed
        # merge can read as "green and not armed". Re-read before
        # reporting: merged wins over any blocker derived from stale
        # reads.
        current = repo.pr.get(pr.number)
        if current is not None and current.merged:
            return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
        return Verdict(
            "disarmed",
            EXIT_DISARMED,
            f"PR #{pr.number} is green and parked unarmed; arm it with"
            " `fm submit --armed`, or merge it with `fm workflow.merge-now`",
            pr.number,
        )
    if not grace_spent:
        return Verdict("in-flight", 0, "green and armed; awaiting the merge", pr.number)
    if git.behind_base(pr.base_branch) > 0:
        return Verdict(
            "behind",
            EXIT_BEHIND,
            f"PR #{pr.number} is behind {pr.base_branch}, and protection"
            " blocks outdated branches",
            pr.number,
        )
    current = repo.pr.get(pr.number)
    if current is not None and current.merged:
        return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
    return Verdict(
        "stalled",
        EXIT_STALLED,
        f"PR #{pr.number} is green and armed with no merge: the server lost"
        " the evaluation (or a required review is missing)",
        pr.number,
    )


def follow(
    repo: Repository,
    branch: str,
    git: GitOps,
    *,
    interval: float = 15,
    timeout: float = 1800,
) -> Verdict:
    """Watch until a terminal verdict, or raise SystemExit with its code.

    Returns the ``merged`` verdict on success. Every terminal blocker
    (10-13, 16, 17) raises SystemExit carrying its code, after
    printing the detail; the deadline raises 14 and an unreadable
    forge past the transient budget raises 15.
    """
    deadline = time.monotonic() + timeout
    unreachable = 0
    green_polls = 0
    last_state = ""
    while True:
        try:
            grace_spent = green_polls >= _MERGE_GRACE_POLLS
            verdict = classify(repo, branch, git, grace_spent=grace_spent)
        except ForgeError as exc:
            unreachable += 1
            print(f"  forge unreachable ({unreachable}/{_UNREACHABLE_BUDGET}): {exc}")
            if unreachable >= _UNREACHABLE_BUDGET:
                raise SystemExit(EXIT_UNREACHABLE) from None
            time.sleep(interval)
            continue
        unreachable = 0
        if verdict.state != last_state:
            print(f"  {verdict.state}: {verdict.detail}")
            last_state = verdict.state
        if verdict.state == "merged":
            return verdict
        if verdict.exit_code:
            raise SystemExit(verdict.exit_code)
        if verdict.state == "in-flight" and verdict.detail.startswith("green"):
            green_polls += 1
        else:
            green_polls = 0
        if time.monotonic() >= deadline:
            print(f"  still in flight after {timeout:.0f}s")
            raise SystemExit(EXIT_TIMEOUT)
        time.sleep(interval)
