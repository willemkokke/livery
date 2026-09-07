"""The merge-state classifiers: every documented value, loud unknowns."""

from __future__ import annotations

import pytest

from livery.forge import (
    CombinedStatus,
    ForgeError,
    classify_gitea_merge_refusal,
    classify_github_mergeable_state,
    classify_gitlab_detailed_status,
)
from livery.forge._merge_state import _GITLAB_DETAILED, MERGE_STATES


def _combined(state: str) -> CombinedStatus:
    return CombinedStatus(state=state, contexts=0 if state == "none" else 1)  # type: ignore[arg-type]


def test_an_unmapped_answer_refuses_loudly_never_waits() -> None:
    # The refusal first: a state outside the map is a software
    # update, not a runtime guess, and nothing may wait on it.
    for raiser in (
        lambda: classify_gitlab_detailed_status("security_policy_violations"),
        lambda: classify_github_mergeable_state("mystery_state"),
        lambda: classify_gitea_merge_refusal(
            "the repository is being migrated",
            combined=_combined("success"),
            item_state="open",
            merged=False,
        ),
    ):
        with pytest.raises(ForgeError) as caught:
            raiser()
        text = str(caught.value)
        assert "never" in text and "map" in text


def test_every_documented_gitlab_value_maps() -> None:
    # The whole published enumeration, the unstageable values
    # included: mapping complete is the contract, staging them is
    # separate later work.
    for value in _GITLAB_DETAILED:
        state = classify_gitlab_detailed_status(value)
        assert state.state in MERGE_STATES
        assert state.native == value
    assert classify_gitlab_detailed_status("checking").category == "in-progress"
    assert classify_gitlab_detailed_status("ci_still_running").category == "in-progress"
    assert classify_gitlab_detailed_status("need_rebase").state == "behind"
    assert classify_gitlab_detailed_status("not_approved").category == "recoverable"
    assert (
        classify_gitlab_detailed_status("jira_association_missing").state
        == "reference-missing"
    )


def test_gitlab_not_open_splits_by_the_merged_flag() -> None:
    merged = classify_gitlab_detailed_status("not_open", merged=True)
    assert merged.category == "success"
    closed = classify_gitlab_detailed_status("not_open", item_state="closed")
    assert closed.category == "terminal"


def test_every_documented_github_value_maps() -> None:
    for value in (
        "unknown",
        "dirty",
        "behind",
        "blocked",
        "unstable",
        "draft",
        "has_hooks",
    ):
        state = classify_github_mergeable_state(value)
        assert state.state in MERGE_STATES
        assert state.native == value
    assert classify_github_mergeable_state("unknown").category == "in-progress"
    assert classify_github_mergeable_state("dirty").state == "conflict"
    assert classify_github_mergeable_state("has_hooks").category == "recoverable"
    assert classify_github_mergeable_state("draft", item_state="closed").category == (
        "terminal"
    )
    assert (
        classify_github_mergeable_state(
            "draft", item_state="closed", merged=True
        ).category
        == "success"
    )


def test_gitea_prose_classifies_by_the_cross_read() -> None:
    # One sentence, three states: the combined status tells Gitea's
    # required-checks refusal apart.
    native = "Not all required status checks successful"
    red = classify_gitea_merge_refusal(
        native, combined=_combined("failure"), item_state="open", merged=False
    )
    assert red.state == "checks-red" and red.category == "recoverable"
    running = classify_gitea_merge_refusal(
        native, combined=_combined("pending"), item_state="open", merged=False
    )
    assert running.state == "ci-running" and running.category == "in-progress"
    unreported = classify_gitea_merge_refusal(
        native, combined=_combined("none"), item_state="open", merged=False
    )
    assert unreported.state == "ci-running"
    settling = classify_gitea_merge_refusal(
        native, combined=_combined("success"), item_state="open", merged=False
    )
    assert settling.state == "contexts-settling"
    assert settling.category == "in-progress"
    assert settling.native == native


def test_gitea_recompute_and_identity_states() -> None:
    computing = classify_gitea_merge_refusal(
        "Please try again later",
        combined=_combined("success"),
        item_state="open",
        merged=False,
    )
    assert computing.state == "computing" and computing.category == "in-progress"
    walked = classify_gitea_merge_refusal(
        "already merged",
        combined=_combined("success"),
        item_state="closed",
        merged=True,
    )
    assert walked.category == "success"
    closed = classify_gitea_merge_refusal(
        "pull request 9 is not open",
        combined=_combined("success"),
        item_state="closed",
        merged=False,
    )
    assert closed.category == "terminal"


def test_gitea_policy_prose_maps() -> None:
    for native, state in (
        ("cannot merge a work in progress pull request", "draft"),
        ("does not have enough approvals", "approvals-missing"),
        ("the head branch is behind the base branch", "behind"),
        ("merge conflict detected", "conflict"),
        ("this merge style is not allowed", "merge-method-not-allowed"),
    ):
        classified = classify_gitea_merge_refusal(
            native, combined=_combined("success"), item_state="open", merged=False
        )
        assert classified.state == state, native
        assert classified.category == "recoverable"
