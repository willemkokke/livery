"""The shared reserved-branch engine and its one kind-specific seam.

Releasing and updating are the same shape underneath: cut a
``workflow/<name>`` branch, do some work, submit through
livery.workshop._submit.submit_flow, and either publish or just
merge. This module runs that shared lifecycle; a
:class:`WorkflowDriver` plugs in only ``prepare`` (do the work) and
``on_merged`` (a release publishes and tags; an update does
nothing). Re-running the engine at any point is the recovery: the
decision layer reads the state and does the right thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from footman import fail

from livery.forge import ForgeError, Repository
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._workflow_decision import (
    WorkflowAction,
    workflow_decision,
)
from livery.workshop._workflow_state import (
    WorkflowKind,
    WorkflowState,
    WorkflowStatus,
    workflow_states,
)

#: A release in one of these states parks a coexisting update's arm.
#: UNKNOWN counts: a gather blip must not read as "no release running".
PARKS_UPDATES = frozenset(
    {
        WorkflowState.IN_PROGRESS,
        WorkflowState.AWAITING_REVIEW,
        WorkflowState.UNKNOWN,
    }
)


@dataclass(frozen=True)
class Submission:
    """What a driver's prepare hands back for the shared submit.

    None from prepare means nothing to submit (already up to date, or
    the work completed on its own).
    """

    title: str
    body: str


class WorkflowDriver(Protocol):
    """The only kind-specific seam; everything else is shared.

    Structural, so a driver provides these members and inherits
    nothing. ``branch`` is always ``workflow/<name>``: one active
    workflow per name, the branch name is the identity, different
    names coexist.
    """

    name: str  # "release/forge+workshop" | "update/templates" | ...
    kind: WorkflowKind
    armed: bool
    members: tuple[str, ...]  # a release's package directories

    @property
    def branch(self) -> str:
        """The reserved branch, ``workflow/<name>``."""
        ...

    @property
    def base(self) -> str:
        """The branch the PR merges into."""
        ...

    def prepare(self) -> Submission | None:
        """Do the work; idempotent, called fresh and for recovery."""
        ...

    def on_merged(self) -> None:
        """After the merge: a release publishes and tags; else nothing."""
        ...


def run_workflow(
    driver: WorkflowDriver,
    repo: Repository,
    git: GitOps,
    *,
    current_user: str | None = None,
) -> None:
    """Drive *driver* one step: gather, decide, act.

    Loops for MERGE_DEFAULT (re-decide on the fresh state) and for a
    TIDY_THEN_START that removed another workflow's leftover, each
    other name tidied at most once so the loop cannot spin. STOP
    raises with its teaching. Everything else terminates.
    """
    tidied: set[str] = set()
    while True:
        states = workflow_states(repo, git, base=driver.base)
        wf = next(
            (s for s in states if s.name == driver.name),
            WorkflowStatus(kind=None, state=WorkflowState.NONE),
        )
        others = tuple(s for s in states if s.name != driver.name)
        decision = workflow_decision(
            kind=driver.kind,
            name=driver.name,
            wf=wf,
            members=driver.members,
            branch=git.current_branch(),
            dirty=not git.is_clean(),
            behind_default=_behind_default(git, driver.base),
            default_branch=driver.base,
            current_user=(_forge_user(repo) if current_user is None else current_user),
            others=others,
        )
        action = decision.action

        # A bare repeat call into your own armed in-flight workflow:
        # the idempotent submit disarms whenever the arm choice is
        # off, so a status-check re-run would take the live schedule
        # away while announcing a retry. Say what is true and stop.
        if (
            action is WorkflowAction.RETRY
            and wf.state is WorkflowState.IN_PROGRESS
            and wf.armed is not False
            and not driver.armed
        ):
            fail(
                f"{driver.name} is already in flight and armed. Follow it"
                " with `fm status --watch --workflow`, or re-run with"
                " --armed to re-assert the schedule deliberately."
            )

        message = decision.message
        if action is WorkflowAction.ARM and not driver.armed:
            message = (
                f"The ready {driver.name} PR stays unarmed; pass --armed to"
                " arm auto-merge."
            )
        if message:
            print(f"  {message}")

        if action is WorkflowAction.STOP:
            raise SystemExit(f"  {decision.message}")

        if action is WorkflowAction.MERGE_DEFAULT:
            _merge_default(git, driver.base)
            continue

        if action is WorkflowAction.TIDY_THEN_START:
            target = decision.tidy_target
            tidy_leftover(repo, git, target or wf, base=driver.base)
            if target is not None and target.name not in tidied:
                tidied.add(target.name)
                continue
        elif action is WorkflowAction.REOPEN:
            _reopen(repo, driver.branch)

        # The parking rule: while any release is live, a coexisting
        # update submits UNARMED, and the note teaches the re-run
        # (which also raises floors) before arming. The verify
        # backstop holds even when someone arms anyway.
        arm = driver.armed
        if arm and driver.kind is not WorkflowKind.RELEASE:
            parked_by = [
                other.name
                for other in others
                if other.kind is WorkflowKind.RELEASE and other.state in PARKS_UPDATES
            ]
            if parked_by:
                arm = False
                print(
                    f"  Release {parked_by[0]} is in flight: submitting"
                    " unarmed. After it lands, re-run `fm workflow.update`"
                    " (the re-run also raises floors to the fresh release),"
                    " then arm."
                )

        submission = driver.prepare()
        if submission is None:
            print("  nothing to submit: everything is already current")
            return
        from livery.workshop._submit import submit_flow

        # The engine submits and reads the immediate outcome; the
        # long watch belongs to the drivers' own surfaces, which
        # know their done oracle (a release watches its receipts).
        submit_flow(
            repo,
            git,
            title=submission.title,
            body=submission.body,
            base=driver.base,
            armed=arm,
            armed_reason="the workflow engine",
            gate=False,
            follow_to_verdict=False,
        )
        if _merged(repo, git, driver.branch):
            driver.on_merged()
        return


def tidy_leftover(
    repo: Repository, git: GitOps, wf: WorkflowStatus, *, base: str
) -> None:
    """Tear down a finished workflow's branches; safe, it is merged.

    *wf* is the decision's tidy target, never assumed to be the
    driver's own branch: passing a live branch here would close its
    live PR, the exact mistake the target field prevents.
    """
    if wf.branch:
        from livery.workshop._submit import teardown_branch

        teardown_branch(repo, git, wf.branch, base)


def _merged(repo: Repository, git: GitOps, branch: str) -> bool:
    """Whether *branch*'s PR has merged, asked of the forge."""
    try:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            pr = repo.pr.find_by_head_sha(git.any_head(branch))
        return pr is not None and pr.merged
    except ForgeError:
        return False


def _forge_user(repo: Repository) -> str:
    """Who this invocation is, in the forge's namespace.

    The forge identity, because a workflow's author comes from the
    PR; a local git name is a different namespace and on a bot
    checkout can never match. Offline answers empty, which the
    decision treats as "cannot compare", never as a refusal.
    """
    try:
        from livery.workshop._forge_lane import this_forge
        from livery.workshop._layers import workspace_root

        root = workspace_root()
        if root is None:
            return ""
        return this_forge(root).whoami()
    except Exception:
        return ""


def _behind_default(git: GitOps, base: str) -> int:
    """Commits on origin/*base* not in HEAD; 0 when none or offline."""
    try:
        git.fetch()
        return git.behind_base(base)
    except GitError:
        return 0


def _merge_default(git: GitOps, base: str) -> None:
    """Merge origin/*base* in; a conflict stops with the resolution path."""
    try:
        git.integrate(base)
    except GitError as exc:
        raise SystemExit(
            f"  The merge from {base} stopped on a conflict. Resolve it,"
            f" commit, and run the workflow again to resume.\n{exc}"
        ) from None


def _reopen(repo: Repository, branch: str) -> None:
    """Reopen the closed PR for *branch*, reusing it over a duplicate."""
    pr = repo.pr.find_by_head(branch, state="all")
    if pr is not None and not pr.merged and pr.state == "closed":
        repo.pr.reopen(pr.number)
        print(f"  reopened PR #{pr.number}")
