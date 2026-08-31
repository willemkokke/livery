"""The one conformance suite every backend must pass, the fake included.

The suite is data plus a harness seam: livery.forge.testing.SCENARIOS
is the data, a tuple of named livery.forge.testing.Scenario records,
and each backend supplies a livery.forge.testing.ForgeDriver so the
scenarios can drive the world around the protocol (pushes, tags, CI
finishing) in that backend's own way. A test harness runs every
applicable scenario against every driver it has; the scenarios
themselves never change per backend, which is what makes passing them
mean something.

Scenarios assert the contract and only the contract: where forges may
legitimately differ, the scenario either probes
livery.forge.Forge.supports or does not look.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._protocol import Forge, Repository
from livery.forge._types import Capability, Conclusion, Label, RepoConfig


class ForgeDriver(Protocol):
    """What a backend's test rig supplies to run the shared scenarios.

    The protocol under test only observes the forge; the driver moves
    the world the protocol observes. The fake moves dictionaries, a
    container driver runs git pushes and posts CI results, and the
    scenarios cannot tell the difference. Every method is cheap on the
    fake and honest on a real backend, however slow.
    """

    @property
    def forge(self) -> Forge:
        """The forge under test."""
        ...

    def unused_repo_name(self) -> tuple[str, str]:
        """An owner and name no repository has yet."""
        ...

    def fresh_repo(self) -> Repository:
        """A new repository with an initialised default branch."""
        ...

    def push(self, repo_owner: str, repo_name: str, branch: str) -> str:
        """Push *branch* (creating it when new) and return its head sha.

        CI starts on the pushed commit, as a push trigger would start
        it.
        """
        ...

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head."""
        ...

    def finish_run(
        self, repo_owner: str, repo_name: str, run: int, conclusion: Conclusion
    ) -> None:
        """Drive CI to finish *run* with *conclusion*."""
        ...

    def comment_bodies(
        self,
        repo_owner: str,
        repo_name: str,
        number: int,
        *,
        kind: Literal["pr", "issue"],
    ) -> tuple[str, ...]:
        """The comments on one pull request or issue, oldest first."""
        ...


@dataclass(frozen=True)
class Scenario:
    """One conformance case: a name, its capability gates, and its body.

    Attributes:
        name: The scenario's stable identifier, used as the test id.
        run: The body. Raises AssertionError when the backend breaks
            the contract.
        requires: Capabilities the forge must support for the scenario
            to apply.
        forbids: Capabilities the forge must NOT support: the scenario
            asserts the honest decline.
    """

    name: str
    run: Callable[[ForgeDriver], None]
    requires: tuple[Capability, ...] = ()
    forbids: tuple[Capability, ...] = ()

    def applies_to(self, forge: Forge) -> bool:
        """Whether *forge*'s capabilities put this scenario in scope."""
        return all(forge.supports(c) for c in self.requires) and not any(
            forge.supports(c) for c in self.forbids
        )


def _run_id_for(repo: Repository, sha: str) -> int:
    """The id of the one run CI started for *sha*."""
    runs = repo.checks.runs(head_sha=sha)
    assert len(runs) == 1, f"expected one run for {sha}, found {len(runs)}"
    return runs[0].id


def _identity(driver: ForgeDriver) -> None:
    """Whoami and server_version answer, non-empty."""
    assert driver.forge.whoami() != ""
    assert driver.forge.server_version() != ""


def _repo_lifecycle(driver: ForgeDriver) -> None:
    """Create, get, delete: probe-before-act works in both directions."""
    forge = driver.forge
    owner, name = driver.unused_repo_name()
    assert forge.get_repo(owner, name) is None
    forge.create_repo(owner, name)
    info = forge.get_repo(owner, name)
    assert info is not None
    assert info.owner == owner
    assert info.name == name
    assert info.default_branch != ""
    try:
        forge.create_repo(owner, name)
    except ForgeError:
        pass
    else:
        raise AssertionError("creating an existing repository must raise")
    forge.delete_repo(owner, name)
    assert forge.get_repo(owner, name) is None
    forge.delete_repo(owner, name)  # idempotent


def _branches(driver: ForgeDriver) -> None:
    """branch_exists tracks pushes; delete_branch is idempotent."""
    repo = driver.fresh_repo()
    assert not repo.branch_exists("feature")
    driver.push(repo.owner, repo.name, "feature")
    assert repo.branch_exists("feature")
    repo.delete_branch("feature")
    assert not repo.branch_exists("feature")
    repo.delete_branch("feature")  # idempotent


def _tags(driver: ForgeDriver) -> None:
    """Tags lists every tag pushed."""
    repo = driver.fresh_repo()
    assert repo.tags() == ()
    driver.create_tag(repo.owner, repo.name, "packages/forge/v0.0.1")
    assert "packages/forge/v0.0.1" in repo.tags()


def _configure_is_idempotent(driver: ForgeDriver) -> None:
    """Configure applies stated fields and re-applies without error."""
    repo = driver.fresh_repo()
    config = RepoConfig(
        squash_only=True,
        delete_branch_on_merge=True,
        allow_auto_merge=True,
        labels=(Label(name="bug", color="ee0701", description="a defect"),),
    )
    repo.configure(config)
    repo.configure(config)  # re-running is the recovery procedure


def _configure_required_contexts(driver: ForgeDriver) -> None:
    """Configure names required contexts where the forge supports it."""
    repo = driver.fresh_repo()
    repo.configure(RepoConfig(required_contexts=("gate",)))
    repo.configure(RepoConfig(required_contexts=("gate",)))


def _configure_required_contexts_declined(driver: ForgeDriver) -> None:
    """Configure declines required contexts by name, never silently."""
    repo = driver.fresh_repo()
    try:
        repo.configure(RepoConfig(required_contexts=("gate",)))
    except Unsupported as exc:
        assert "required_contexts" in str(exc)
    else:
        raise AssertionError("a forge without required_contexts must raise Unsupported")


def _pr_open_find_get(driver: ForgeDriver) -> None:
    """open, find_by_head, and get agree; duplicates are refused."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    base = _default_branch(driver, repo)
    pr = repo.pr.open("feature", base, "feat: a change", "the body")
    assert pr.state == "open"
    assert pr.head_branch == "feature"
    assert pr.head_sha == sha
    found = repo.pr.find_by_head("feature")
    assert found is not None
    assert found.number == pr.number
    got = repo.pr.get(pr.number)
    assert got is not None
    assert got.title == "feat: a change"
    assert got.body == "the body"
    assert repo.pr.get(pr.number + 999) is None
    assert repo.pr.find_by_head("no-such-branch") is None
    try:
        repo.pr.open("feature", base, "feat: again", "")
    except ForgeError:
        pass
    else:
        raise AssertionError("a second open PR for one head must be refused")


def _pr_title_close_reopen(driver: ForgeDriver) -> None:
    """update_title sticks; close and reopen reuse the pull request."""
    repo = driver.fresh_repo()
    driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: first", "")
    repo.pr.update_title(pr.number, "feat: retitled")
    got = repo.pr.get(pr.number)
    assert got is not None
    assert got.title == "feat: retitled"
    repo.pr.close(pr.number)
    closed = repo.pr.get(pr.number)
    assert closed is not None
    assert closed.state == "closed"
    assert not closed.merged
    assert repo.pr.find_by_head("feature") is None
    assert repo.pr.find_by_head("feature", state="all") is not None
    repo.pr.reopen(pr.number)
    reopened = repo.pr.get(pr.number)
    assert reopened is not None
    assert reopened.state == "open"


def _pr_merge_now(driver: ForgeDriver) -> None:
    """merge_now merges; the merged PR is found by head sha, branch gone."""
    repo = driver.fresh_repo()
    repo.configure(RepoConfig(delete_branch_on_merge=True))
    sha = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: merge", "")
    repo.pr.merge_now(pr.number, title="feat: merge")
    merged = repo.pr.get(pr.number)
    assert merged is not None
    assert merged.merged
    assert merged.state == "closed"
    assert not repo.branch_exists("feature")
    by_sha = repo.pr.find_by_head_sha(sha)
    assert by_sha is not None
    assert by_sha.number == pr.number
    try:
        repo.pr.merge_now(pr.number, title="feat: merge")
    except ForgeError:
        pass
    else:
        raise AssertionError("merging a merged pull request must raise")


def _pr_comment(driver: ForgeDriver) -> None:
    """Comment lands on the pull request; a missing number is refused."""
    repo = driver.fresh_repo()
    driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: c", "")
    repo.pr.comment(pr.number, "the evidence")
    bodies = driver.comment_bodies(repo.owner, repo.name, pr.number, kind="pr")
    assert "the evidence" in bodies
    try:
        repo.pr.comment(pr.number + 999, "lost")
    except ForgeError:
        pass
    else:
        raise AssertionError("commenting on a missing pull request must raise")


def _arm_disarm(driver: ForgeDriver) -> None:
    """Arm records a schedule, disarm cancels it, both observably."""
    repo = driver.fresh_repo()
    driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: arm", "")
    assert not repo.pr.is_armed(pr.number)
    repo.pr.arm(pr.number, title="feat: arm")
    assert repo.pr.is_armed(pr.number)
    assert repo.pr.disarm(pr.number)
    assert not repo.pr.is_armed(pr.number)
    assert not repo.pr.disarm(pr.number)  # idempotent, and says so


def _armed_pr_merges_on_green(driver: ForgeDriver) -> None:
    """An armed pull request merges when its checks go green, server-side."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: auto", "")
    repo.pr.arm(pr.number, title="feat: auto")
    driver.finish_run(repo.owner, repo.name, _run_id_for(repo, sha), "success")
    merged = repo.pr.get(pr.number)
    assert merged is not None
    assert merged.merged, "the armed pull request must merge on green"
    assert not repo.pr.is_armed(pr.number), "a merged pull request reads unarmed"


def _status_progression(driver: ForgeDriver) -> None:
    """Status distinguishes none, pending, success, and failure."""
    repo = driver.fresh_repo()
    nothing = repo.checks.status("0" * 40)
    assert nothing.state == "none"
    assert nothing.contexts == 0
    sha = driver.push(repo.owner, repo.name, "green")
    assert repo.checks.status(sha).state == "pending"
    driver.finish_run(repo.owner, repo.name, _run_id_for(repo, sha), "success")
    green = repo.checks.status(sha)
    assert green.state == "success"
    assert green.contexts >= 1
    red_sha = driver.push(repo.owner, repo.name, "red")
    driver.finish_run(repo.owner, repo.name, _run_id_for(repo, red_sha), "failure")
    assert repo.checks.status(red_sha).state == "failure"


def _runs_jobs_log(driver: ForgeDriver) -> None:
    """Runs filters and orders newest first; jobs and job_log read through."""
    repo = driver.fresh_repo()
    first = driver.push(repo.owner, repo.name, "one")
    second = driver.push(repo.owner, repo.name, "two")
    listed = repo.checks.runs()
    assert len(listed) >= 2
    assert listed[0].head_sha == second, "runs must list newest first"
    for_first = repo.checks.runs(head_sha=first)
    assert len(for_first) == 1
    run = for_first[0]
    driver.finish_run(repo.owner, repo.name, run.id, "failure")
    jobs = repo.checks.jobs(run.id)
    assert jobs, "a run has jobs"
    assert jobs[0].conclusion == "failure"
    assert repo.checks.job_log(jobs[0].id) != ""


def _rerun(driver: ForgeDriver) -> None:
    """Rerun resets a completed run; a live run is refused."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    run = _run_id_for(repo, sha)
    try:
        repo.checks.rerun(run)
    except ForgeError:
        pass
    else:
        raise AssertionError("re-running a live run must raise")
    driver.finish_run(repo.owner, repo.name, run, "failure")
    repo.checks.rerun(run)
    rerun = repo.checks.runs(head_sha=sha)[0]
    assert rerun.status != "completed", "a re-run run is live again"


def _cancel_run(driver: ForgeDriver) -> None:
    """cancel_run cancels a live run; a terminal run is refused."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    run = _run_id_for(repo, sha)
    repo.checks.cancel_run(run)
    cancelled = repo.checks.runs(head_sha=sha)[0]
    assert cancelled.status == "completed"
    assert cancelled.conclusion == "cancelled"
    try:
        repo.checks.cancel_run(run)
    except ForgeError:
        pass
    else:
        raise AssertionError("cancelling a terminal run must raise")


def _cancel_run_forced(driver: ForgeDriver) -> None:
    """force-cancel works where the capability is declared."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    repo.checks.cancel_run(_run_id_for(repo, sha), force=True)
    cancelled = repo.checks.runs(head_sha=sha)[0]
    assert cancelled.conclusion == "cancelled"


def _cancel_run_force_declined(driver: ForgeDriver) -> None:
    """Force is declined by name where unsupported; plain cancel still works."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature")
    run = _run_id_for(repo, sha)
    try:
        repo.checks.cancel_run(run, force=True)
    except Unsupported as exc:
        assert "force_cancel" in str(exc)
    else:
        raise AssertionError("force on an unsupporting forge must raise")
    repo.checks.cancel_run(run)
    assert repo.checks.runs(head_sha=sha)[0].conclusion == "cancelled"


def _dispatch(driver: ForgeDriver) -> None:
    """Dispatch queues a run on the named ref; a missing ref is refused."""
    repo = driver.fresh_repo()
    repo.checks.dispatch("nightly.yml", ref=_default_branch(driver, repo))
    dispatched = repo.checks.runs(event="workflow_dispatch")
    assert dispatched, "dispatch must queue a run"
    try:
        repo.checks.dispatch("nightly.yml", ref="no-such-ref")
    except ForgeError:
        pass
    else:
        raise AssertionError("dispatching on a missing ref must raise")


def _release_by_tag(driver: ForgeDriver) -> None:
    """Get probes by tag; create refuses a tag that already has a release."""
    repo = driver.fresh_repo()
    tag = "packages/forge/v0.0.1"
    driver.create_tag(repo.owner, repo.name, tag)
    assert repo.release.get(tag) is None
    created = repo.release.create(tag, name="livery-forge 0.0.1", body="notes")
    assert created.tag == tag
    got = repo.release.get(tag)
    assert got is not None
    assert got.name == "livery-forge 0.0.1"
    assert got.body == "notes"
    try:
        repo.release.create(tag, name="again")
    except ForgeError:
        pass
    else:
        raise AssertionError("a second release for one tag must be refused")


def _issue_text_both_ways(driver: ForgeDriver) -> None:
    """Create carries title, body, labels, assignee; get returns the body."""
    repo = driver.fresh_repo()
    repo.configure(RepoConfig(labels=(Label(name="nightly", color="0000ee"),)))
    me = driver.forge.whoami()
    issue = repo.issue.create(
        "nightly: replay failed",
        body="the work order, in full",
        labels=("nightly",),
        assignee=me,
    )
    got = repo.issue.get(issue.number)
    assert got is not None
    assert got.title == "nightly: replay failed"
    assert got.body == "the work order, in full"
    assert got.labels == ("nightly",)
    assert got.assignees == (me,)
    assert repo.issue.get(issue.number + 999) is None


def _issue_list_and_search(driver: ForgeDriver) -> None:
    """List filters by state; search matches body text and labels."""
    repo = driver.fresh_repo()
    repo.configure(RepoConfig(labels=(Label(name="nightly", color="0000ee"),)))
    with_marker = repo.issue.create(
        "one", body="marker: drift-2026", labels=("nightly",)
    )
    repo.issue.create("two", body="unrelated")
    numbers = [issue.number for issue in repo.issue.list()]
    assert with_marker.number in numbers
    assert len(numbers) >= 2
    hits = repo.issue.search("marker: drift-2026", labels=("nightly",))
    assert [issue.number for issue in hits] == [with_marker.number]
    assert repo.issue.search("no such text") == ()


def _issue_assign(driver: ForgeDriver) -> None:
    """Assign sets the single assignee; assigned_to_me reads it back."""
    repo = driver.fresh_repo()
    issue = repo.issue.create("work", body="the order")
    me = driver.forge.whoami()
    repo.issue.assign(issue.number, me)
    mine = repo.issue.assigned_to_me()
    assert issue.number in [i.number for i in mine]
    try:
        repo.issue.assign(issue.number + 999, me)
    except ForgeError:
        pass
    else:
        raise AssertionError("assigning a missing issue must raise")


def _issue_comment(driver: ForgeDriver) -> None:
    """Comment lands on the issue; a missing number is refused."""
    repo = driver.fresh_repo()
    issue = repo.issue.create("work", body="the order")
    repo.issue.comment(issue.number, "evidence the body cannot carry")
    bodies = driver.comment_bodies(repo.owner, repo.name, issue.number, kind="issue")
    assert "evidence the body cannot carry" in bodies
    try:
        repo.issue.comment(issue.number + 999, "lost")
    except ForgeError:
        pass
    else:
        raise AssertionError("commenting on a missing issue must raise")


def _default_branch(driver: ForgeDriver, repo: Repository) -> str:
    """The repository's default branch, read through the protocol."""
    info = driver.forge.get_repo(repo.owner, repo.name)
    assert info is not None
    return info.default_branch


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("identity", _identity),
    Scenario("repo-lifecycle", _repo_lifecycle),
    Scenario("branches", _branches),
    Scenario("tags", _tags),
    Scenario("configure-is-idempotent", _configure_is_idempotent),
    Scenario(
        "configure-required-contexts",
        _configure_required_contexts,
        requires=("required_contexts",),
    ),
    Scenario(
        "configure-required-contexts-declined",
        _configure_required_contexts_declined,
        forbids=("required_contexts",),
    ),
    Scenario("pr-open-find-get", _pr_open_find_get),
    Scenario("pr-title-close-reopen", _pr_title_close_reopen),
    Scenario("pr-merge-now", _pr_merge_now),
    Scenario("pr-comment", _pr_comment),
    Scenario("arm-disarm", _arm_disarm, requires=("auto_merge",)),
    Scenario(
        "armed-pr-merges-on-green",
        _armed_pr_merges_on_green,
        requires=("auto_merge",),
    ),
    Scenario("status-progression", _status_progression),
    Scenario("runs-jobs-log", _runs_jobs_log),
    Scenario("rerun", _rerun),
    Scenario("cancel-run", _cancel_run),
    Scenario("cancel-run-forced", _cancel_run_forced, requires=("force_cancel",)),
    Scenario(
        "cancel-run-force-declined",
        _cancel_run_force_declined,
        forbids=("force_cancel",),
    ),
    Scenario("dispatch", _dispatch),
    Scenario("release-by-tag", _release_by_tag),
    Scenario("issue-text-both-ways", _issue_text_both_ways),
    Scenario("issue-list-and-search", _issue_list_and_search),
    Scenario("issue-assign", _issue_assign),
    Scenario("issue-comment", _issue_comment),
)
"""Every conformance scenario, in the order the groups are documented."""
