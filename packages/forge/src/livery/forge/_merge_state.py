"""Classify a merge refusal into one state, whatever the forge said.

Every forge holds a merge for its own reasons in its own words:
GitLab publishes an enum (``detailed_merge_status``), GitHub
publishes another (``mergeable_state``), and Gitea answers 405 with
prose. This module maps all three onto one vocabulary, so a caller
decides by category, never by forge dialect:

- ``in-progress``: the forge has not decided yet; following through
  resolves it.
- ``recoverable``: someone can act, and re-running afterwards
  merges; the message says what would recover it.
- ``terminal``: the instruction is void (the pull request is closed
  without merging).
- ``success``: it is merged, including discovered merged.

A forge answer outside the map raises [livery.forge.ForgeError][]
naming the native words: the map is updated in software, never
guessed at runtime, and an unmapped state must not hang a wait.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from livery.forge._errors import ForgeError

if TYPE_CHECKING:
    from livery.forge._types import CombinedStatus, ItemState

MergeCategory: TypeAlias = Literal["in-progress", "recoverable", "terminal", "success"]
"""What a caller does with a state: follow, act, stop, or walk past."""


@dataclass(frozen=True)
class MergeState:
    """One classified merge hold.

    Attributes:
        state: The union vocabulary member (``computing``,
            ``checks-red``, ``behind``, ...).
        category: What a caller does with it; see
            [livery.forge.MergeCategory][].
        message: One user-facing sentence: the situation, and for a
            recoverable state what would recover it.
        native: The forge's own words, verbatim; empty when the
            forge expressed the state as data rather than prose.
    """

    state: str
    category: MergeCategory
    message: str
    native: str = ""


#: The union vocabulary: every state any supported forge can hold a
#: merge in, with its category and user-facing message. Mapped
#: complete from each forge's documentation, including states that
#: are hard to stage; an answer outside this table is a software
#: update, never a runtime guess.
MERGE_STATES: dict[str, tuple[MergeCategory, str]] = {
    "computing": (
        "in-progress",
        "the forge is still computing mergeability",
    ),
    "ci-running": (
        "in-progress",
        "required checks are still running",
    ),
    "approvals-syncing": (
        "in-progress",
        "the forge is syncing approvals",
    ),
    "contexts-settling": (
        "in-progress",
        "the checks read green and the forge's protection view is catching up",
    ),
    "checks-red": (
        "recoverable",
        "required checks are red: fix the failure and push",
    ),
    "behind": (
        "recoverable",
        "the head is behind the base: integrate, then merge again",
    ),
    "conflict": (
        "recoverable",
        "the branches conflict: resolve the conflicts and push",
    ),
    "draft": (
        "recoverable",
        "the pull request is a draft: mark it ready",
    ),
    "approvals-missing": (
        "recoverable",
        "required approvals are missing: an approver must approve",
    ),
    "changes-requested": (
        "recoverable",
        "a reviewer requested changes: they must lift the request",
    ),
    "discussions-open": (
        "recoverable",
        "open discussions remain: answer or resolve them",
    ),
    "blocked-by-dependency": (
        "recoverable",
        "another merge request must merge first",
    ),
    "external-checks-pending": (
        "recoverable",
        "an external status check must report success",
    ),
    "source-branch-missing": (
        "recoverable",
        "the source branch is missing or empty: push the work",
    ),
    "reference-missing": (
        "recoverable",
        "a required issue reference is missing: add it to the title or description",
    ),
    "merge-method-not-allowed": (
        "recoverable",
        "this merge method is not allowed here: use the repository's method",
    ),
    "hooks-refused": (
        "recoverable",
        "a server-side hook refused the merge",
    ),
    "contexts-misreported": (
        "recoverable",
        "a required check context is reported by nothing: fix the"
        " required context's name to match what CI reports",
    ),
    "closed": (
        "terminal",
        "the pull request is closed without merging",
    ),
    "merged": (
        "success",
        "already merged",
    ),
}


def merge_state(state: str, native: str = "") -> MergeState:
    """The vocabulary member *state*, carrying *native* verbatim."""
    category, message = MERGE_STATES[state]
    return MergeState(state=state, category=category, message=message, native=native)


def _unmapped(forge: str, native: str) -> ForgeError:
    return ForgeError(
        f"{forge} held a merge in a state this classifier has never"
        f" seen: {native!r}. The merge-state map needs this answer"
        " added (a software update, with a quirks line); nothing"
        " waits on an unmapped state."
    )


#: GitLab's published ``detailed_merge_status`` enumeration, mapped
#: complete, the unstageable values included.
_GITLAB_DETAILED: dict[str, str] = {
    "checking": "computing",
    "unchecked": "computing",
    "preparing": "computing",
    "approvals_syncing": "approvals-syncing",
    "ci_still_running": "ci-running",
    "ci_must_pass": "checks-red",
    "external_status_checks": "external-checks-pending",
    "conflict": "conflict",
    "need_rebase": "behind",
    "commits_status": "source-branch-missing",
    "blocked_status": "blocked-by-dependency",
    "discussions_not_resolved": "discussions-open",
    "draft_status": "draft",
    "not_approved": "approvals-missing",
    "requested_changes": "changes-requested",
    "jira_association_missing": "reference-missing",
}


def classify_gitlab_detailed_status(
    value: str, *, item_state: ItemState = "open", merged: bool = False
) -> MergeState:
    """GitLab's ``detailed_merge_status`` value as a merge state.

    ``mergeable`` never reaches here: it is the go, not a hold.
    ``not_open`` splits by the merged flag.
    """
    if value == "not_open":
        return merge_state("merged" if merged else "closed", value)
    mapped = _GITLAB_DETAILED.get(value)
    if mapped is None:
        raise _unmapped("gitlab", value)
    return merge_state(mapped, value)


#: GitHub's published ``mergeable_state`` (GraphQL
#: ``MergeStateStatus``) enumeration, mapped complete. ``clean``
#: never reaches the classifier: it is the go, not a hold.
_GITHUB_MERGEABLE_STATE: dict[str, str] = {
    "unknown": "computing",
    "dirty": "conflict",
    "behind": "behind",
    "blocked": "checks-red",
    "unstable": "ci-running",
    "draft": "draft",
    "has_hooks": "hooks-refused",
}


def classify_github_mergeable_state(
    value: str, *, item_state: ItemState = "open", merged: bool = False
) -> MergeState:
    """GitHub's ``mergeable_state`` value as a merge state.

    ``blocked`` folds required checks and missing approvals
    together, which is as fine as GitHub's own data gets on this
    path; the message stays the checks one, and the native value
    rides along for the person reading.
    """
    if item_state == "closed":
        return merge_state("merged" if merged else "closed", value)
    mapped = _GITHUB_MERGEABLE_STATE.get(value)
    if mapped is None:
        raise _unmapped("github", value)
    return merge_state(mapped, value)


def classify_gitea_merge_refusal(
    native: str,
    *,
    combined: CombinedStatus,
    item_state: ItemState,
    merged: bool,
) -> MergeState:
    """Gitea's 405 prose as a merge state; the inputs disambiguate.

    Gitea has no state field, so the message plus the checks API
    decide. "Please try again later" is the mergeability recompute.
    "Not all required status checks successful" is three states in
    one sentence: red checks, checks still running, or the beat
    where the protection view trails a green run; the combined
    status tells them apart. A caller entering ``contexts-settling``
    should first verify every required context has a reporter,
    because a context nothing reports never settles
    (``contexts-misreported``); that cross-check needs the caller's
    knowledge of reported context names.
    """
    if merged:
        return merge_state("merged", native)
    if item_state == "closed":
        return merge_state("closed", native)
    lowered = native.lower()
    if "try again later" in lowered:
        return merge_state("computing", native)
    if "status checks" in lowered:
        if combined.state == "failure":
            return merge_state("checks-red", native)
        if combined.state in ("pending", "none"):
            return merge_state("ci-running", native)
        return merge_state("contexts-settling", native)
    if "work in progress" in lowered:
        return merge_state("draft", native)
    if "approv" in lowered:
        return merge_state("approvals-missing", native)
    if "review" in lowered and "reject" in lowered:
        return merge_state("changes-requested", native)
    if "behind" in lowered or "out-of-date" in lowered:
        return merge_state("behind", native)
    if "conflict" in lowered:
        return merge_state("conflict", native)
    if "merge style" in lowered or "merge method" in lowered:
        return merge_state("merge-method-not-allowed", native)
    raise _unmapped("gitea", native)


def classify_merge_refusal(
    kind: str,
    native: str,
    *,
    combined: CombinedStatus,
    item_state: ItemState,
    merged: bool,
) -> MergeState:
    """Classify a merge-endpoint refusal by the forge *kind*.

    The identity states resolve first on every forge: a merged pull
    request is success whatever the words, a closed one terminal.
    Gitea's prose classifier handles ``gitea`` and the empty kind
    (the fake speaks Gitea's words). GitHub and GitLab classify from
    their published state fields on the read path instead, wired
    when their lanes are; a merge-endpoint refusal from them raises
    as unmapped rather than being guessed at.
    """
    if merged:
        return merge_state("merged", native)
    if item_state == "closed":
        return merge_state("closed", native)
    if kind in ("gitea", ""):
        return classify_gitea_merge_refusal(
            native, combined=combined, item_state=item_state, merged=merged
        )
    raise _unmapped(kind, native)
