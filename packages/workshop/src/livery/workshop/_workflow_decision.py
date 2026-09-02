"""The pure decision: given the state, what does a workflow driver do?

Every reserved-branch workflow shares one lifecycle: cut a
``workflow/<name>`` branch, do the work, open a squash PR, arm, and
merge (a release then publishes). The engine
(livery.workshop._workflow_engine.run_workflow) gathers the inputs,
asks ``workflow_decision``, and acts; each driver plugs in only
its do-the-work and after-the-merge steps.

The decision is a pure function so every branch is table-testable.
Order is load-bearing and stated inline. Every STOP message teaches:
it names what stopped the driver and lists the options with when
each applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from livery.workshop._workflow_state import (
    WORKFLOW_PREFIX,
    WorkflowKind,
    WorkflowState,
    WorkflowStatus,
)

#: Live (non-terminal) states: a workflow here is still someone's.
ACTIVE = frozenset(
    {
        WorkflowState.IN_PROGRESS,
        WorkflowState.AWAITING_REVIEW,
        WorkflowState.PREPARING,
    }
)


class WorkflowAction(Enum):
    """What a driver does next; the same vocabulary for every kind."""

    START = "start"
    ARM = "arm"
    RETRY = "retry"
    REOPEN = "reopen"
    REPREPARE = "reprepare"  # the base moved in the set's paths
    TIDY_THEN_START = "tidy_then_start"
    MERGE_DEFAULT = "merge_default"
    STOP = "stop"


@dataclass(frozen=True)
class WorkflowDecision:
    """The chosen action, a line to announce or teach, and a tidy target.

    ``tidy_target`` names WHICH workflow a TIDY_THEN_START removes: a
    finished other under coexistence, or None for the driver's own
    leftover. Without it the engine could not tell somebody else's
    leftover from its own live branch, and tidying the wrong one
    closes a live PR.
    """

    action: WorkflowAction
    message: str = ""
    tidy_target: WorkflowStatus | None = None


_DIRTY = (
    "The working tree has uncommitted changes, and a workflow must not"
    " guess whether they belong to it. Commit them (they ride along),"
    " or discard them, then run this again."
)


def _noun(kind: WorkflowKind) -> str:
    return "release" if kind is WorkflowKind.RELEASE else "update"


def workflow_decision(
    *,
    kind: WorkflowKind,
    name: str,
    wf: WorkflowStatus,
    members: tuple[str, ...],
    branch: str,
    dirty: bool,
    behind_default: int,
    default_branch: str,
    current_user: str,
    others: tuple[WorkflowStatus, ...] = (),
) -> WorkflowDecision:
    """Decide what the *name* driver does next. Pure, no I/O.

    *wf* is this driver's own detected workflow (NONE when absent);
    *others* are the coexisting workflows on different names;
    *members* are a release driver's package directories, used for the
    intersection refusal. Order matters: the wrong-branch refusal,
    then the fail-safe and human stops, then coexistence, then the
    automatic actions keyed on the detector's state.
    """
    own_branch = f"{WORKFLOW_PREFIX}{name}"
    if branch and branch not in (default_branch, own_branch):
        return WorkflowDecision(
            WorkflowAction.STOP,
            f"A {_noun(kind)} runs from {default_branch} or {own_branch}, and"
            f" you are on {branch}. Switch to {default_branch} to run it"
            f" (or to {own_branch} to resume this one), then run it again.",
        )

    if wf.state is WorkflowState.UNKNOWN:
        return WorkflowDecision(
            WorkflowAction.STOP,
            f"The forge cannot be reached to read the {_noun(kind)} state, so"
            " nothing here may act on a guess. Retry when the forge answers;"
            f" `fm status --workflow` shows what is known meanwhile.",
        )

    if wf.author and current_user and wf.author != current_user and wf.state in ACTIVE:
        return WorkflowDecision(
            WorkflowAction.STOP,
            f"A {_noun(kind)} on {name} is in flight, run by {wf.author}: it"
            " is theirs to drive. Coordinate with them, watch it with"
            " `fm status --workflow`, or abort it deliberately with"
            f" `fm workflow.abort {name} --force` if you both agree it is"
            " dead.",
        )

    # A second release whose set intersects an in-flight release
    # refuses immediately, from read-only state, nothing to undo. An
    # UNKNOWN other still refuses: absence cannot be proven from a
    # blip. Disjoint sets coexist.
    if kind is WorkflowKind.RELEASE and members:
        for other in others:
            if other.kind is not WorkflowKind.RELEASE:
                continue
            live = other.state in ACTIVE or other.state is WorkflowState.UNKNOWN
            overlap = sorted(set(members) & set(other.members))
            if live and overlap:
                who = other.author or "someone"
                return WorkflowDecision(
                    WorkflowAction.STOP,
                    f"Release {other.name} ({who}) is in flight and shares"
                    f" {', '.join(overlap)} with this set. Options: wait for"
                    " it to land; release the disjoint remainder now"
                    " (drop the shared packages from your set); or, if it is"
                    f" yours and dead, `fm workflow.abort {other.name}`.",
                )

    # A finished other leftover is tidied so branches never
    # accumulate; a live other coexists; an UNKNOWN other is left
    # strictly alone (never tear down on a state we could not read).
    for other in others:
        if other.name == name or not other.name:
            continue
        if other.state in ACTIVE or other.state is WorkflowState.UNKNOWN:
            continue
        if dirty:
            return WorkflowDecision(WorkflowAction.STOP, _DIRTY)
        return WorkflowDecision(
            WorkflowAction.TIDY_THEN_START,
            f"A finished {other.name} is lying around; tidying it, then"
            f" starting {name}.",
            tidy_target=other,
        )

    if wf.state is not WorkflowState.NONE and wf.name and wf.name != name:
        return WorkflowDecision(
            WorkflowAction.STOP,
            f"{wf.name} was passed as this driver's own workflow: a caller"
            " bug, not a state a person can fix.",
        )

    # A dirty tree is ambiguous, except a PREPARING update resumed on
    # its own branch: the renderer leaves conflict markers there, and
    # re-running the verb is how a person resumes after resolving
    # them. From any other branch the dirt is somebody's unrelated
    # work, and preparing would carry it onto the workflow branch.
    resumable = (
        kind is WorkflowKind.UPDATE
        and wf.state is WorkflowState.PREPARING
        and branch == own_branch
    )
    if dirty and not resumable:
        return WorkflowDecision(WorkflowAction.STOP, _DIRTY)

    # A completed leftover is behind by construction (the squash), so
    # tidy before the behind check, which would otherwise merge the
    # base into a branch about to be deleted.
    if wf.state is WorkflowState.SUCCEEDED:
        return WorkflowDecision(
            WorkflowAction.TIDY_THEN_START,
            f"The last {_noun(kind)} completed; tidying the leftover and"
            " starting the next one.",
        )

    # The base moved in the set's own paths: the stamped derivation is
    # a claim about history that no longer holds, so re-prepare before
    # anything arms. Routed before the behind check because the remedy
    # differs: MERGE_DEFAULT integrates, REPREPARE re-derives.
    if wf.blocker.name == "STALE_SET" and wf.state in ACTIVE:
        return WorkflowDecision(
            WorkflowAction.REPREPARE,
            "The base moved in this set's own paths since prepare, so the"
            " stamped versions and entries are stale. Re-deriving on the"
            " moved base and re-submitting.",
        )

    if behind_default > 0 and not dirty:
        return WorkflowDecision(
            WorkflowAction.MERGE_DEFAULT,
            f"{behind_default} commit(s) behind {default_branch}; merging it in.",
        )

    if wf.state is WorkflowState.AWAITING_REVIEW:
        return WorkflowDecision(
            WorkflowAction.ARM, f"Arming the ready {_noun(kind)} PR."
        )

    if wf.state is WorkflowState.FAILED:
        if wf.reopenable:
            return WorkflowDecision(
                WorkflowAction.REOPEN,
                f"Reopening the closed {_noun(kind)} PR and re-submitting.",
            )
        return WorkflowDecision(
            WorkflowAction.RETRY, "Re-submitting after the previous failure."
        )

    if wf.state is WorkflowState.IN_PROGRESS:
        return WorkflowDecision(
            WorkflowAction.RETRY,
            f"A {_noun(kind)} is already in progress; re-submitting is the"
            " idempotent check on it.",
        )

    return WorkflowDecision(WorkflowAction.START)
