"""submit, status, ci, and abort on the fake, with a real git repository.

Every flow runs in-process against livery.forge.testing.FakeForge and
a temporary clone of a bare origin. The git seam is subclassed so a
push also reaches the fake (and, unless a test says otherwise,
settles CI immediately), which is the whole apparatus: no threads, no
sleeps, no network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge import StateFilter
from livery.forge.testing import FakeForge, Outcome
from livery.workshop._ci_tasks import cancel_flow, doctor_flow, rerun_flow, status_flow
from livery.workshop._git_ops import GitOps
from livery.workshop._submit import (
    Plan,
    abort_flow,
    merge_now_flow,
    prepare,
    resolve_closes,
    submit_flow,
    with_closes,
)
from livery.workshop._verdict import (
    EXIT_BEHIND,
    EXIT_CI_FAILED,
    EXIT_CONFLICTS,
    EXIT_DISARMED,
    EXIT_STALLED,
    EXIT_TIMEOUT,
    classify,
    follow,
)

OWNER, NAME = "willemkokke", "livery"

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


class SubmitGit(GitOps):
    """The git seam wired to the fake: a push reaches both sides."""

    def __init__(
        self, root: Path, fake: FakeForge, *, auto_settle: bool = True
    ) -> None:
        super().__init__(root)
        self.fake = fake
        self.auto_settle = auto_settle
        self.outcome: Outcome = "success"

    def push(self, branch: str) -> None:
        super().push(branch)
        sha = self.fake.push(OWNER, NAME, branch, outcome=self.outcome)
        # The fake mints its own shas; the local head is not consulted.
        if self.auto_settle:
            self.fake.settle(OWNER, NAME, sha)


@pytest.fixture
def rig(tmp_path: Path) -> tuple[FakeForge, SubmitGit]:
    """A bare origin, a clone on a feature branch, and the fake."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    _git(clone, "config", "user.email", "test@livery.local")
    _git(clone, "config", "user.name", "Livery Test")
    (clone / "seed.txt").write_text("seed\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")
    _git(clone, "checkout", "-b", "feat/1-first")
    (clone / "work.txt").write_text("work\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: the first change")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="test")
    return fake, SubmitGit(clone, fake)


def _repo(fake: FakeForge):
    return fake.repository(OWNER, NAME)


def _submit(fake: FakeForge, git: SubmitGit, **kwargs: object):
    defaults: dict[str, object] = {
        "gate": False,
        "interval": 0,
        "timeout": 5,
    }
    defaults.update(kwargs)
    return submit_flow(_repo(fake), git, **defaults)  # type: ignore[arg-type]


def test_submit_opens_arms_and_merges(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    number = _submit(fake, git, armed=True)
    pr = _repo(fake).pr.get(number)
    assert pr is not None and pr.merged
    assert pr.title == "feat: the first change"


def test_a_second_submit_reuses_the_pr(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    first = _submit(fake, git, armed=False, follow_to_verdict=False)
    (git.root / "work.txt").write_text("more\n")
    _git(git.root, "commit", "-am", "feat: more work")
    second = _submit(fake, git, armed=False, follow_to_verdict=False)
    assert first == second
    repo = _repo(fake)
    assert len([p for p in [repo.pr.get(first)] if p]) == 1


def test_an_unarmed_green_submit_parks_cleanly(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # Deliberately unarmed and green is the run finishing its job, so
    # it returns instead of raising; 11 is reserved for an armed submit
    # whose schedule went missing.
    fake, git = rig
    number = _submit(fake, git, armed=False)
    pr = _repo(fake).pr.get(number)
    assert pr is not None and pr.state == "open" and not pr.merged


def test_red_ci_names_the_failing_job(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    git.outcome = "failure"
    with pytest.raises(SystemExit) as caught:
        _submit(fake, git, armed=True)
    assert caught.value.code == EXIT_CI_FAILED
    number = 1
    verdict = classify(_repo(fake), "feat/1-first", git)
    assert verdict.exit_code == EXIT_CI_FAILED
    assert "ci.yml" in verdict.detail
    assert verdict.pr_number == number


def test_self_heal_integrates_a_conflicting_base(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # Another clone advances main with a conflicting edit; the submit
    # classifies 10 on the green PR, integrates, and re-submits. The
    # conflict is real, so the heal stops on the merge for a person.
    fake, git = rig
    other = git.root.parent / "other"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other")
    _git(other, "config", "user.email", "other@livery.local")
    _git(other, "config", "user.name", "Other")
    (other / "work.txt").write_text("their conflicting line\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: their change")
    _git(other, "push", "origin", "main")
    with pytest.raises(_FAILURES) as caught:
        _submit(fake, git, armed=False)
    message = str(caught.value)
    assert "resolve it, commit" in message  # the heal engaged and handed over


def test_self_heal_integrates_a_clean_advance(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # A non-conflicting advance of main: with the strict reading the
    # branch is merely behind, which an unarmed PR reports as 11 and
    # an armed one heals through 17; here the conflict probe stays
    # quiet and the flow ends disarmed after the push.
    fake, git = rig
    other = git.root.parent / "other"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other")
    _git(other, "config", "user.email", "other@livery.local")
    _git(other, "config", "user.name", "Other")
    (other / "elsewhere.txt").write_text("independent\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: independent change")
    _git(other, "push", "origin", "main")
    number = _submit(fake, git, armed=False)
    pr = _repo(fake).pr.get(number)
    assert pr is not None and pr.state == "open"
    assert git.behind_base("main") > 0  # behind alone never parks a submit red


def test_the_lost_arm_schedule_is_retried(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    fake.faults.lose_arm_schedule = 1
    number = _submit(fake, git, armed=True)
    pr = _repo(fake).pr.get(number)
    assert pr is not None and pr.merged


def test_a_wedged_queue_times_out_and_cancel_is_the_relief(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    fake, git = rig
    git.auto_settle = False
    with pytest.raises(SystemExit) as caught:
        _submit(fake, git, armed=True, timeout=0.2)
    assert caught.value.code == EXIT_TIMEOUT
    cancel_flow(_repo(fake), git)  # the relief: the queued run cancels
    runs = _repo(fake).checks.runs()
    assert all(run.status == "completed" for run in runs)


def test_slow_status_reads_keep_the_watch_in_flight(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    fake, git = rig
    fake.faults.slow_status_reads = 2
    number = _submit(fake, git, armed=False)  # polled through the window
    assert _repo(fake).pr.get(number) is not None


def test_merge_now_rides_out_the_405_window(
    rig: tuple[FakeForge, SubmitGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, git = rig
    monkeypatch.setattr("time.sleep", lambda _s: None)
    _submit(fake, git, armed=False, follow_to_verdict=False)
    fake.faults.merge_405_window = 2
    merge_now_flow(_repo(fake), "feat/1-first")
    pr = _repo(fake).pr.get(1)
    assert pr is not None and pr.merged


def test_disarm_before_push_and_the_merged_refusal(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    repo = _repo(fake)
    repo.pr.arm(1, title="feat: the first change")  # green: merges now
    merged_pr = repo.pr.get(1)
    assert merged_pr is not None and merged_pr.merged
    (git.root / "work.txt").write_text("late fixup\n")
    _git(git.root, "commit", "-am", "feat: late fixup")
    with pytest.raises(_FAILURES) as caught:
        _submit(fake, git, armed=False, follow_to_verdict=False)
    assert "already merged" in str(caught.value)


def test_closes_resolution_guards(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, _ = rig
    repo = _repo(fake)
    assert resolve_closes(repo, "feat/9-thing", 0) is None  # absent: dropped
    issue = repo.issue.create("the work order", body="the order")
    assert resolve_closes(repo, f"feat/{issue.number}-thing", 0) == issue.number
    with pytest.raises(_FAILURES):
        resolve_closes(repo, "feat/1-first", 999)  # explicit and absent: refused
    assert with_closes(with_closes("body", 5), 5).count("Closes #5") == 1


def test_prepare_validates_branch_and_title(rig: tuple[FakeForge, SubmitGit]) -> None:
    _, git = rig
    plan = prepare(git)
    assert plan == Plan(
        branch="feat/1-first",
        base="main",
        title="feat: the first change",
        body="",
        title_given=False,
    )
    with pytest.raises(_FAILURES):
        prepare(git, title="no convention here")
    _git(git.root, "checkout", "-b", "wrong-shape")
    with pytest.raises(_FAILURES):
        prepare(git)


def test_an_ambiguous_title_default_refuses_on_first_open(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # Two commits ahead: no commit subject may name the new PR. The
    # refusal lists the subjects; --title opens; and once the PR
    # exists, a defaulted re-submit is fine because the default is
    # inert there.
    fake, git = rig
    (git.root / "work.txt").write_text("second\n")
    _git(git.root, "commit", "-am", "feat: the second commit")
    with pytest.raises(_FAILURES) as caught:
        _submit(fake, git, armed=False, follow_to_verdict=False)
    assert "2 commits ahead" in str(caught.value)
    assert "the first change" in str(caught.value)
    number = _submit(
        fake,
        git,
        armed=False,
        follow_to_verdict=False,
        title="feat: the whole intent",
    )
    (git.root / "work.txt").write_text("third\n")
    _git(git.root, "commit", "-am", "feat: a fixup")
    assert (
        _submit(fake, git, armed=False, follow_to_verdict=False) == number
    )  # defaulted re-submit: reused, title kept
    pr = _repo(fake).pr.get(number)
    assert pr is not None and pr.title == "feat: the whole intent"


def test_a_given_title_updates_the_reused_pr(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    _submit(
        fake,
        git,
        armed=False,
        follow_to_verdict=False,
        title="feat: the better title",
    )
    pr = _repo(fake).pr.get(1)
    assert pr is not None and pr.title == "feat: the better title"


def test_abort_is_idempotent(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    repo = _repo(fake)
    abort_flow(repo, git, "feat/1-first", "main")
    pr = repo.pr.get(1)
    assert pr is not None and pr.state == "closed" and not pr.merged
    assert not repo.branch_exists("feat/1-first")
    abort_flow(repo, git, "feat/1-first", "main")  # second run: nothing left


def test_status_and_rerun_and_doctor(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    assert status_flow(_repo(fake), git) == 0  # no PR yet
    git.outcome = "failure"
    _submit(fake, git, armed=False, follow_to_verdict=False)
    assert status_flow(_repo(fake), git) == EXIT_CI_FAILED
    rerun_flow(_repo(fake), git)  # the failed run re-queues
    # The fake's shas are its own; classify by branch still answers.
    doctor_flow(fake)
    assert fake.whoami()


def test_the_stalled_and_behind_verdicts_discriminate(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # The fake's server never loses an evaluation, so the two verdicts
    # that need green+armed+unmerged run against a stub that answers
    # like a forge which has.
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    repo = _repo(fake)
    real_pr = repo.pr

    class StubPRs:
        def find_by_head(self, branch: str, state: StateFilter = "open"):
            return real_pr.find_by_head(branch, state=state)

        def find_by_head_sha(self, sha: str):
            return real_pr.find_by_head_sha(sha)

        def is_armed(self, number: int) -> bool:
            return True

        def get(self, number: int):
            return real_pr.get(number)

    class StubRepo:
        pr = StubPRs()
        checks = repo.checks

    stalled = classify(StubRepo(), "feat/1-first", git, grace_spent=True)  # type: ignore[arg-type]
    assert stalled.exit_code == EXIT_STALLED
    other = git.root.parent / "other"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other")
    _git(other, "config", "user.email", "other@livery.local")
    _git(other, "config", "user.name", "Other")
    (other / "elsewhere.txt").write_text("independent\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: independent change")
    _git(other, "push", "origin", "main")
    behind = classify(StubRepo(), "feat/1-first", git, grace_spent=True)  # type: ignore[arg-type]
    assert behind.exit_code == EXIT_BEHIND


def test_a_disarmed_verdict_still_raises_for_the_watcher(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # The classifier's code is unchanged: fm status and ci.watch still
    # answer 11 for a parked PR; only a deliberately unarmed submit
    # treats it as its own finish line.
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    with pytest.raises(SystemExit) as caught:
        follow(_repo(fake), "feat/1-first", git, interval=0, timeout=1)
    assert caught.value.code == EXIT_DISARMED
    verdict = classify(_repo(fake), "feat/1-first", git)
    assert "parked unarmed" in verdict.detail


def test_follow_returns_the_merged_verdict(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    number = _submit(fake, git, armed=True)
    verdict = follow(_repo(fake), "feat/1-first", git, interval=0, timeout=1)
    assert verdict.state == "merged" and verdict.pr_number == number


def test_a_merge_in_flight_wins_over_a_stale_arming_read(
    rig: tuple[FakeForge, SubmitGit],
) -> None:
    # Forge evidence (livery PR #21): the server merges between the
    # open-PR read and the arming read, so the consumed schedule looks
    # like "green and not armed". The re-read must answer merged.
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    repo = _repo(fake)
    real_pr = repo.pr

    class RacingPRs:
        def find_by_head(self, branch: str, state: StateFilter = "open"):
            pr = real_pr.find_by_head(branch, state="all")
            if pr is not None and not pr.merged:
                return pr
            # Replay the stale open-PR read the race produces.
            from dataclasses import replace

            return None if pr is None else replace(pr, merged=False, state="open")

        def find_by_head_sha(self, sha: str):
            return real_pr.find_by_head_sha(sha)

        def is_armed(self, number: int) -> bool:
            return False

        def get(self, number: int):
            return real_pr.get(number)

    class RacingRepo:
        pr = RacingPRs()
        checks = repo.checks

    real_pr.arm(1, title="feat: the first change")  # green: merges now
    verdict = classify(RacingRepo(), "feat/1-first", git)  # type: ignore[arg-type]
    assert verdict.state == "merged" and verdict.exit_code == 0


def test_conflicts_classify_before_arming(rig: tuple[FakeForge, SubmitGit]) -> None:
    fake, git = rig
    _submit(fake, git, armed=False, follow_to_verdict=False)
    other = git.root.parent / "other"
    _git(git.root.parent, "clone", str(git.root.parent / "origin.git"), "other")
    _git(other, "config", "user.email", "other@livery.local")
    _git(other, "config", "user.name", "Other")
    (other / "work.txt").write_text("their conflicting line\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: their change")
    _git(other, "push", "origin", "main")
    verdict = classify(_repo(fake), "feat/1-first", git)
    assert verdict.exit_code == EXIT_CONFLICTS
