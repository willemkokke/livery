"""Where each reserved-branch workflow sits in its lifecycle.

A workflow lives on a reserved branch whose name is its identity:
``workflow/release/<members>`` (the set's directory names, sorted,
joined with ``+``) or ``workflow/update/<slug>``. Different names
coexist; this reader tells the truth per branch and never arbitrates
between them, which is the decision layer's job
(livery.workshop._workflow_decision.workflow_decision).

Classification is a pure function (:func:`classify`) of gathered
signals, so every mapping is testable with no I/O; the gathering
(:func:`gather`) isolates the network and answers None when the
forge cannot be read. UNKNOWN is a first-class state, not an error:
a blip must never read as "nothing running", so everything that
mutates refuses on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from livery.forge import ForgeError, Repository, Unsupported
from livery.workshop._git_ops import GitOps

#: Reserved workflow branches live under this prefix.
WORKFLOW_PREFIX = "workflow/"


class WorkflowKind(Enum):
    """Which reserved-branch workflow a branch names."""

    RELEASE = "release"
    UPDATE = "update"


class WorkflowState(Enum):
    """Where a workflow sits in its lifecycle.

    UNKNOWN means the forge could not be read: callers that mutate
    treat it like a live state and refuse, never assuming "nothing
    running" from a blip.
    """

    NONE = "none"
    PREPARING = "preparing"  # local branch, never submitted
    IN_PROGRESS = "in_progress"  # armed PR, or publish still running
    AWAITING_REVIEW = "awaiting_review"  # unarmed PR: waits for a person
    FAILED = "failed"  # CI red, or closed unmerged
    SUCCEEDED = "succeeded"  # merged (and, for a release, tagged)
    UNKNOWN = "unknown"


class Blocker(Enum):
    """Why a workflow will not land by itself, data beside the state."""

    NONE = "none"
    CONFLICTS = "conflicts"
    CI_FAILING = "ci_failing"
    DISARMED = "disarmed"
    CLOSED = "closed"
    BEHIND_BASE = "behind_base"
    REVIEW_REQUIRED = "review_required"
    STALE_SET = "stale_set"  # the base moved in the release set's paths


#: States ``workflow.abort`` tears down without --force: nothing is
#: running or can complete on its own. IN_PROGRESS and AWAITING_REVIEW
#: need force; UNKNOWN always refuses (never tear down on a blip).
TEARDOWN_SAFE = frozenset(
    {
        WorkflowState.NONE,
        WorkflowState.PREPARING,
        WorkflowState.FAILED,
        WorkflowState.SUCCEEDED,
    }
)


@dataclass(frozen=True)
class WorkflowStatus:
    """The detector's verdict for one workflow.

    Attributes:
        kind: Release or update; None on the NONE placeholder.
        state: The lifecycle state.
        name: The workflow name, the branch without its prefix
            (``release/forge+workshop``).
        branch: The reserved branch (``workflow/<name>``).
        members: A release's package directory names; empty for an
            update.
        detail: A short hint (the failing leg, an unreadable-arm
            note).
        reopenable: FAILED from a closed-unmerged PR; reopening is an
            option.
        author: Who opened the PR, the forge's login namespace.
        armed: True = a schedule is live; False = confirmed unarmed;
            None = unreadable, which watches like armed (a false
            "disarmed" is the proven wrong exit).
        blocker: Why it will not land by itself.
        pr_number: The workflow's PR, when one exists.
        head_sha: The commit whose CI matters.
        ci_state: The combined CI verdict for that commit.
        tagged: A release's members whose receipt tag is already cut.
    """

    kind: WorkflowKind | None
    state: WorkflowState
    name: str = ""
    branch: str = ""
    members: tuple[str, ...] = ()
    detail: str = ""
    reopenable: bool = False
    author: str = ""
    armed: bool | None = False
    blocker: Blocker = Blocker.NONE
    pr_number: int = 0
    head_sha: str = ""
    ci_state: str = ""
    tagged: tuple[str, ...] = ()


def kind_of(name: str) -> WorkflowKind:
    """The kind a workflow *name* declares by its first segment."""
    return (
        WorkflowKind.RELEASE
        if name.split("/", 1)[0] == "release"
        else WorkflowKind.UPDATE
    )


def members_of(name: str) -> tuple[str, ...]:
    """A release name's package directories; empty for an update."""
    if kind_of(name) is not WorkflowKind.RELEASE:
        return ()
    _, _, joined = name.partition("/")
    return tuple(part for part in joined.split("+") if part)


@dataclass(frozen=True)
class Signals:
    """The gathered inputs :func:`classify` maps to a state."""

    kind: WorkflowKind
    remote_branch: bool
    pr_state: str  # "none" | "open" | "closed" | "merged"
    ci_state: str  # "success" | "pending" | "failure" | "none" | ""
    tagged: tuple[str, ...] = ()  # release members with a receipt tag
    members: tuple[str, ...] = ()
    armed: bool | None = False
    mergeable: bool | None = None
    detail: str = ""
    author: str = ""
    pr_number: int = 0
    head_sha: str = ""
    stale_set: bool = False  # base moved in the set's paths


def classify(sig: Signals) -> WorkflowState:
    """Map gathered *sig* to a lifecycle state. Pure, no I/O."""
    if sig.pr_state == "none":
        return (
            WorkflowState.IN_PROGRESS if sig.remote_branch else WorkflowState.PREPARING
        )
    if sig.pr_state == "open":
        if sig.ci_state == "failure":
            return WorkflowState.FAILED
        # An unreadable arm (None) watches like armed: reporting a
        # live arm as absent is the proven wrong verdict.
        return (
            WorkflowState.AWAITING_REVIEW
            if sig.armed is False
            else WorkflowState.IN_PROGRESS
        )
    if sig.pr_state == "closed":
        return WorkflowState.FAILED
    # merged
    if sig.kind is WorkflowKind.UPDATE:
        return WorkflowState.SUCCEEDED
    # A release is done when every member's receipt tag is cut; the
    # tags are the ledger, so partial success reads IN_PROGRESS with
    # the cut members listed, and a red publish run reads FAILED.
    if sig.members and set(sig.tagged) >= set(sig.members):
        return WorkflowState.SUCCEEDED
    if sig.ci_state == "failure":
        return WorkflowState.FAILED
    return WorkflowState.IN_PROGRESS


def blocker(sig: Signals) -> Blocker:
    """Why the workflow will not land by itself. Pure, no I/O.

    CONFLICTS first (fixing them re-triggers CI anyway), then CI red,
    then the stale set (re-preparing also re-runs CI, so it outranks
    the arm question), then unarmed.
    """
    if sig.pr_state == "closed":
        return Blocker.CLOSED
    if sig.pr_state != "open":
        return Blocker.NONE
    if sig.mergeable is False:
        return Blocker.CONFLICTS
    if sig.ci_state == "failure":
        return Blocker.CI_FAILING
    if sig.stale_set:
        return Blocker.STALE_SET
    if sig.armed is False:
        return Blocker.DISARMED
    return Blocker.NONE


def base_moved_in_paths(git: GitOps, base: str, paths: tuple[str, ...]) -> bool:
    """Whether ``origin/<base>`` carries commits HEAD lacks touching *paths*.

    The staleness probe for a release set: pure local git, empty
    *paths* is never stale. Movement outside the set's paths is
    harmless by construction and reads False.
    """
    if not paths:
        return False
    out = git.log_paths(f"HEAD..origin/{base}", paths)
    return bool(out)


def gather(
    repo: Repository,
    git: GitOps,
    name: str,
    *,
    base: str = "main",
) -> Signals | None:
    """Gather one workflow's signals, or None when the forge is unreadable."""
    branch = f"{WORKFLOW_PREFIX}{name}"
    kind = kind_of(name)
    members = members_of(name)
    try:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            head = git.any_head(branch)
            if head:
                pr = repo.pr.find_by_head_sha(head)
        remote_branch = repo.branch_exists(branch)
    except (ForgeError, Unsupported, OSError):
        return None

    if pr is None:
        return Signals(
            kind=kind,
            remote_branch=remote_branch,
            pr_state="none",
            ci_state="",
            members=members,
        )

    if pr.merged:
        pr_state = "merged"
    elif pr.state == "closed":
        pr_state = "closed"
    else:
        pr_state = "open"

    armed: bool | None = False
    mergeable: bool | None = None
    ci_state = ""
    detail = ""
    head_sha = pr.head_sha
    tagged: tuple[str, ...] = ()
    stale = False

    try:
        if pr_state == "open":
            try:
                armed = repo.pr.is_armed(pr.number)
            except (ForgeError, Unsupported):
                armed = None
                detail = "arming state unreadable"
            status = repo.checks.status(pr.head_sha)
            ci_state = status.state
            if kind is WorkflowKind.RELEASE:
                stale = base_moved_in_paths(
                    git, base, tuple(f"packages/{m}" for m in members)
                )
        elif pr_state == "merged" and kind is WorkflowKind.RELEASE:
            tags = set(repo.tags())
            # The receipt: each member's tag, whatever version it
            # carries, present on the repository after this merge.
            tagged = tuple(
                m for m in members if any(t.startswith(f"packages/{m}/v") for t in tags)
            )
            if set(tagged) < set(members):
                status = repo.checks.status(pr.head_sha)
                ci_state = status.state
    except (ForgeError, Unsupported, OSError):
        return None

    return Signals(
        kind=kind,
        remote_branch=remote_branch,
        pr_state=pr_state,
        ci_state=ci_state,
        tagged=tagged,
        members=members,
        armed=armed,
        mergeable=mergeable,
        detail=detail,
        author=pr.author,
        pr_number=pr.number,
        head_sha=head_sha,
        stale_set=stale,
    )


def status_of(sig: Signals | None, name: str) -> WorkflowStatus:
    """One workflow's :class:`WorkflowStatus` from its gathered signals."""
    kind = kind_of(name)
    branch = f"{WORKFLOW_PREFIX}{name}"
    if sig is None:
        return WorkflowStatus(
            kind=kind, state=WorkflowState.UNKNOWN, name=name, branch=branch
        )
    return WorkflowStatus(
        kind=kind,
        state=classify(sig),
        name=name,
        branch=branch,
        members=sig.members,
        detail=sig.detail,
        reopenable=(sig.pr_state == "closed"),
        author=sig.author,
        armed=sig.armed,
        blocker=blocker(sig),
        pr_number=sig.pr_number,
        head_sha=sig.head_sha,
        ci_state=sig.ci_state,
        tagged=sig.tagged,
    )


def active_workflow_names(repo: Repository, git: GitOps) -> tuple[str, ...]:
    """The distinct workflow names in play, local and remote united.

    The union, never local-else-remote: a colleague's live release
    exists only on the remote, and a local leftover of your own would
    hide it. The remote probe degrades to the local list when the
    forge is unreachable.
    """
    local = set(git.local_branches(WORKFLOW_PREFIX))
    try:
        remote = set(git.remote_branches(WORKFLOW_PREFIX))
    except Exception:
        remote = set()
    return tuple(sorted(b.removeprefix(WORKFLOW_PREFIX) for b in local | remote))


def workflow_states(
    repo: Repository, git: GitOps, *, base: str = "main"
) -> tuple[WorkflowStatus, ...]:
    """Every active workflow, each classified on its own signals.

    Coexistence is legitimate; multiple names are states to report,
    not a broken invariant. Releases sort first, then by name.
    """
    states = [
        status_of(gather(repo, git, name, base=base), name)
        for name in active_workflow_names(repo, git)
    ]
    states.sort(key=lambda wf: (wf.kind is not WorkflowKind.RELEASE, wf.name))
    return tuple(states)
