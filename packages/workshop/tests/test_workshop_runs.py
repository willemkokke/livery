"""A cancelled run names its successor; the watchers judge and follow accordingly."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from livery.forge import ForgeError, Repository, Run
from livery.forge.testing import FakeForge
from livery.workshop import _e2e, _runs, _submit
from livery.workshop._ci_tasks import verdict_flow

OWNER, NAME = "acme", "thing"
A, B = "a" * 40, "b" * 40


@pytest.fixture
def rig() -> tuple[FakeForge, Repository]:
    fake = FakeForge()
    repo = fake.create_repo(OWNER, NAME, private=False)
    return fake, repo


def _run(repo: Repository, run_id: int) -> Run:
    return next(run for run in repo.checks.runs() if run.id == run_id)


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


# --- the cases with nobody to name come first ---------------------------------


def test_a_cancelled_run_with_no_successor_says_so(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    (run,) = repo.checks.runs()
    repo.checks.cancel_run(run.id)
    cancelled = _run(repo, run.id)
    assert _runs.successor(repo, cancelled) is None
    assert _runs.successor(repo, cancelled, branch="feat/x") is None
    assert "no newer run" in _runs.supersession_note(repo, cancelled)
    assert (
        _runs.supersession_note(
            repo,
            _run(repo, run.id).__class__(
                **{**cancelled.__dict__, "conclusion": "success"}
            ),
        )
        == ""
    )


def test_a_forge_that_cannot_answer_names_no_successor(
    rig: tuple[FakeForge, Repository], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    (run,) = repo.checks.runs()
    repo.checks.cancel_run(run.id)

    def _down(**kwargs: object) -> tuple[Run, ...]:
        raise ForgeError("down", status=503)

    monkeypatch.setattr(repo.checks, "runs", _down)
    assert _runs.successor(repo, _run_from(repo, run.id, fake)) is None


def _run_from(repo: Repository, run_id: int, fake: FakeForge) -> Run:
    # The fake's run listing is patched away in the test above; rebuild the run.
    state = fake._repos[(OWNER, NAME)].runs[
        run_id
    ]  # the fake's own state is the surface
    return Run(
        id=state.id,
        workflow=state.workflow,
        head_sha=state.head_sha,
        event=state.event,
        status=state.status,
        conclusion=state.conclusion,
        url="",
    )


# --- the twin and the moved head ------------------------------------------------


def test_a_twin_for_the_same_head_is_the_successor(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    fake.push(OWNER, NAME, "feat/x", sha=A)
    older, newer = sorted(repo.checks.runs(), key=lambda run: run.id)
    repo.checks.cancel_run(older.id)
    fake.settle(OWNER, NAME, A)
    found = _runs.successor(repo, _run(repo, older.id))
    assert found is not None and found.same_head and found.run.id == newer.id
    note = _runs.supersession_note(repo, _run(repo, older.id))
    assert (
        note == f"cancelled, superseded by run {newer.id} for the same head (success)"
    )


def test_a_moved_head_is_found_through_the_watched_branch(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    repo.pr.open("feat/x", "main", "a change")
    (old,) = repo.checks.runs(head_sha=A)
    repo.checks.cancel_run(old.id)
    fake.push(OWNER, NAME, "feat/x", sha=B)
    fake.settle(OWNER, NAME, B)
    # Without the branch the old head finds nothing: the forge files the
    # pull request under its current head only.
    assert _runs.successor(repo, _run(repo, old.id)) is None
    found = _runs.successor(repo, _run(repo, old.id), branch="feat/x")
    assert found is not None and not found.same_head and found.run.head_sha == B
    assert "for the moved head bbbbbbbbbbbb" in _runs.supersession_note(
        repo, _run(repo, old.id), branch="feat/x"
    )


# --- the loop's watcher ----------------------------------------------------------


def test_the_watcher_fails_a_cancellation_nobody_took_over(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    (run,) = repo.checks.runs()
    repo.checks.cancel_run(run.id)
    message = _refusal(lambda: _e2e.judge_runs(repo, repo.checks.runs()))
    assert "red runs on the loop: ci.yml" in message


def test_the_watcher_reports_a_green_twin_and_fails_a_red_one(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    fake.push(OWNER, NAME, "feat/x", sha=A)
    older, newer = sorted(repo.checks.runs(), key=lambda run: run.id)
    repo.checks.cancel_run(older.id)
    fake.settle(OWNER, NAME, A)
    lines = _e2e.judge_runs(repo, repo.checks.runs(head_sha=A))
    assert any(
        f"superseded by run {newer.id} for the same head (success)" in line
        for line in lines
    )
    # A red twin: the cancelled run is red by its successor's verdict.
    fake.push(OWNER, NAME, "feat/y", sha=B, outcome="failure")
    fake.push(OWNER, NAME, "feat/y", sha=B, outcome="failure")
    first, _second = sorted(repo.checks.runs(head_sha=B), key=lambda run: run.id)
    repo.checks.cancel_run(first.id)
    fake.settle(OWNER, NAME, B)
    assert "red runs on the loop" in _refusal(
        lambda: _e2e.judge_runs(repo, repo.checks.runs(head_sha=B))
    )


def test_the_watcher_fails_when_the_watched_head_moved(
    rig: tuple[FakeForge, Repository],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    repo.pr.open("feat/x", "main", "a change")
    (old,) = repo.checks.runs(head_sha=A)
    repo.checks.cancel_run(old.id)
    fake.push(OWNER, NAME, "feat/x", sha=B)
    fake.settle(OWNER, NAME, B)
    message = _refusal(
        lambda: _e2e.judge_runs(repo, repo.checks.runs(head_sha=A), branch="feat/x")
    )
    assert "red runs on the loop" in message


# --- the verdict and the submit's follow ----------------------------------------


def test_the_verdict_names_the_run_that_superseded_a_cancelled_job(
    rig: tuple[FakeForge, Repository], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    fake.push(OWNER, NAME, "feat/x", sha=A)
    older, newer = sorted(repo.checks.runs(), key=lambda run: run.id)
    repo.checks.cancel_run(older.id)
    fake.settle(OWNER, NAME, A)
    for name in ("GITHUB_EVENT_PATH", "GITEA_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", str(older.id))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/1/merge")
    monkeypatch.setenv("GITHUB_SHA", A)
    red = verdict_flow(repo, needs=("gate",), job="verdict")
    assert red == [f"gate: cancelled, superseded by run {newer.id} for the same head"]


def test_the_follow_says_when_the_head_moved_and_whose_push_it_was(
    rig: tuple[FakeForge, Repository],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake, repo = rig
    fake.push(OWNER, NAME, "feat/x", sha=A)
    pr = repo.pr.open("feat/x", "main", "a change")
    monkeypatch.setattr(_submit, "_HOLD_POLL", 0.0)
    real_status = repo.checks.status

    def _moving(sha: str, new: str = B):
        # The head moves under the wait: the first poll finds the old
        # head still pending and pushes the new one behind its back.
        if sha == A:
            fake.push(OWNER, NAME, "feat/x", sha=new)
            fake.settle(OWNER, NAME, new)
        return real_status(sha)

    monkeypatch.setattr(repo.checks, "status", _moving)
    _submit._wait_for_verdict(repo, pr.number, local_head=B)
    out = capsys.readouterr().out
    assert f"PR #{pr.number}: the head moved from {A[:12]} to {B[:12]}" in out
    assert "this clone's own push" in out and "following run" in out
    # A head this clone did not push is named as such.
    fake.push(OWNER, NAME, "feat/x", sha=A)
    monkeypatch.setattr(repo.checks, "status", lambda sha: _moving(sha, new="c" * 40))
    _submit._wait_for_verdict(repo, pr.number, local_head=B)
    assert "pushed from elsewhere, not this clone's HEAD" in capsys.readouterr().out
