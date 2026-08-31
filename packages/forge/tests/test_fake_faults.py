"""Each fault mode injects its quirk deterministically.

Every test here fails if the fake's fault handling is removed: the
first half of each asserts the misbehaviour itself, the second half
asserts the recovery verb the workflows rely on. Each fault has its
line in ``packages/forge/docs/quirks.md``.
"""

from __future__ import annotations

import pytest

from livery.forge import ForgeError, PullRequest, Repository
from livery.forge.testing import FakeDriver


def _repo_with_open_pr(driver: FakeDriver) -> tuple[Repository, PullRequest, str]:
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", "main", "feat: change", "")
    return repo, pr, sha


def test_a_lost_arm_schedule_reads_unarmed_and_rearming_recovers() -> None:
    driver = FakeDriver()
    repo, pr, _ = _repo_with_open_pr(driver)
    driver.fake.faults.lose_arm_schedule = 1
    repo.pr.arm(pr.number, title="feat: change")
    # The quirk: the forge accepted the arm and recorded nothing.
    assert not repo.pr.is_armed(pr.number)
    repo.pr.arm(pr.number, title="feat: change")
    assert repo.pr.is_armed(pr.number)


def test_the_405_window_refuses_merges_then_passes() -> None:
    driver = FakeDriver()
    repo, pr, _ = _repo_with_open_pr(driver)
    driver.fake.faults.merge_405_window = 2
    for _ in range(2):
        with pytest.raises(ForgeError) as refusal:
            repo.pr.merge_now(pr.number, title="feat: change")
        assert refusal.value.status == 405
    repo.pr.merge_now(pr.number, title="feat: change")
    merged = repo.pr.get(pr.number)
    assert merged is not None
    assert merged.merged


def test_a_wedged_status_queue_holds_pending_until_cancel_relieves_it() -> None:
    driver = FakeDriver()
    repo, _, sha = _repo_with_open_pr(driver)
    run = repo.checks.runs(head_sha=sha)[0].id
    driver.fake.faults.wedge_status_queue = True
    driver.settle(repo.owner, repo.name, sha)
    # The quirk: CI finished, and the queue never applied the result.
    assert repo.checks.status(sha).state == "pending"
    repo.checks.cancel_run(run)
    assert repo.checks.status(sha).state == "failure"


def test_slow_status_reads_answer_none_before_the_truth() -> None:
    driver = FakeDriver()
    repo, _, sha = _repo_with_open_pr(driver)
    driver.settle(repo.owner, repo.name, sha)
    driver.fake.faults.slow_status_reads = 2
    # The quirk: freshly pushed commits read as unreported for a while,
    # so a poller must treat "none" as "keep waiting", never "no CI".
    assert repo.checks.status(sha).state == "none"
    assert repo.checks.status(sha).state == "none"
    assert repo.checks.status(sha).state == "success"
