"""The one conformance suite every backend must pass, the fake included.

The suite is data plus a harness seam: livery.forge.testing.SCENARIOS
is the data, a tuple of named livery.forge.testing.Scenario records,
and each backend supplies a livery.forge.testing.ForgeDriver so the
scenarios can drive the world around the protocol (pushes, tags, CI
settling) in that backend's own way. A test harness runs every
applicable scenario against every driver it has; the scenarios
themselves never change per backend, which is what makes passing them
mean something.

The driver seam is shaped by what a real forge can actually be made to
do. CI's verdict is decided by the commit that was pushed, so the
scenario states the intended outcome at push time and the driver
arranges it (the fake stores it; a real driver pushes a commit whose
seeded workflow produces it). Blocking lives only in the driver:
``settle`` and ``await_run`` return immediately on the fake and poll
on a real forge, and no scenario ever sleeps.

Scenarios assert the contract and only the contract: where forges may
legitimately differ, the scenario either probes
livery.forge.Forge.supports or does not look.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._protocol import Forge, Repository
from livery.forge._types import Capability, Label, RepoConfig

Outcome: TypeAlias = Literal["success", "failure", "hang"]
"""What CI will do with a pushed commit.

``success`` and ``failure`` are verdicts the run reaches once settled.
``hang`` is a held run: it stays live until the scenario either
cancels it or settles it, and settling releases it to success. The
held run is what makes the pending state, cancellation, and arming
deterministic on every backend, however fast its runners are.
"""


class ForgeDriver(Protocol):
    """What a backend's test rig supplies to run the shared scenarios.

    The protocol under test only observes the forge; the driver moves
    the world the protocol observes. The fake moves dictionaries; a
    real driver creates commits, seeds workflow files, and polls. The
    scenarios cannot tell the difference, which is the point.
    """

    @property
    def forge(self) -> Forge:
        """The forge under test."""
        ...

    def unused_repo_name(self) -> tuple[str, str]:
        """An owner and name no repository has yet."""
        ...

    def fresh_repo(self) -> Repository:
        """A new repository with an initialised default branch.

        The repository carries a dispatchable CI workflow the
        scenarios address as ``conf.yml``, however the backend spells
        that underneath.
        """
        ...

    def push(
        self,
        repo_owner: str,
        repo_name: str,
        branch: str,
        *,
        outcome: Outcome = "success",
    ) -> str:
        """Push *branch* (creating it when new) and return its head sha.

        CI starts on the pushed commit and will reach *outcome* once
        settled; a ``hang`` outcome stays live until cancelled. A real
        driver arranges this with the workflow it seeds (the branch or
        commit carries the outcome); the fake stores it on the run.
        """
        ...

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head."""
        ...

    def settle(self, repo_owner: str, repo_name: str, sha: str) -> None:
        """Return once every run for *sha* is terminal.

        A held (``hang``) run is released and concludes success; the
        other outcomes conclude as pushed. The fake applies the
        outcome; a real driver releases its hold and polls.
        """
        ...

    def await_run(
        self, repo_owner: str, repo_name: str, *, head_sha: str = "", event: str = ""
    ) -> int:
        """The id of the one run matching the filters, once it exists.

        Runs appear asynchronously on a real forge; this blocks until
        the matching run is listed. Exactly one run must match.
        """
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

    def await_mergeable(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Return once the forge has computed the pull request's mergeability.

        A forge recomputes mergeability asynchronously after a pull
        request opens, and merging or arming inside that window is
        refused with a 405. Scenarios wait here before either verb;
        workflows retry on the 405 instead, which is the same
        contract from the other side.
        """
        ...

    def await_merged(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Return once the merge of *number* and its aftermath have landed.

        A scheduled merge is performed by the forge after the checks
        go green, and even an immediate merge finishes its aftermath
        (the configured source branch deletion) asynchronously; the
        caller only ever observes both. This is the driver-side bound
        on that observation.
        """
        ...

    def await_issue(
        self, repo_owner: str, repo_name: str, number: int, *, assignee: str = ""
    ) -> None:
        """Return once listings serve issue *number* in its current state.

        Issue listings are eventually consistent on some forges (about
        nine seconds behind on github.com), so a scenario that creates
        or assigns and then reads through a listing waits here first.
        When *assignee* is given, the served row must carry it.
        """
        ...

    def required_context(self) -> str:
        """How this forge spells the seeded workflow's check context.

        The string branch protection must name for the gate job:
        forges spell it differently (a bare job name, or
        workflow / job (event)), and the spelling is rig knowledge.
        """
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


def _addresses(driver: ForgeDriver) -> None:
    """The address family builds each forge's own path shapes.

    Compared by path, not by host: a server's configured external
    address is authoritative for its web links and may differ from the
    API base the backend derives from, so only the shapes below are
    the protocol's promise.
    """
    repo = driver.fresh_repo()
    driver.push(repo.owner, repo.name, "feature")
    base = _default_branch(driver, repo)
    pr = repo.pr.open("feature", base, "feat: addresses", "")
    home = repo.web_url()
    assert home.endswith(f"{repo.owner}/{repo.name}")
    for built in (
        repo.pr_url(pr.number),
        repo.issue_url(1),
        repo.commit_url("0" * 40),
        repo.compare_url(base, "feature"),
        repo.tag_url("v1.2.3"),
    ):
        assert built.startswith(f"{home}/")
    # The pull request's own address, as the forge reports it, carries
    # the path the built one must reproduce.
    assert pr.url.endswith(repo.pr_url(pr.number).removeprefix(home))
    assert driver.forge.user_url("someone").endswith("someone")


def _governance_reads(driver: ForgeDriver) -> None:
    """author, protection, and the schedule history read back honestly.

    Reviews are exercised against the fake only: submitting one needs
    a second account, and the mapping is a thin normalisation each
    backend's unit shape covers.
    """
    repo = driver.fresh_repo()
    # A held run keeps the checks pending: GitHub refuses to schedule
    # auto-merge on an already-green pull request (it would merge, not
    # schedule), so the arm below needs an unfinished check.
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    base = _default_branch(driver, repo)
    pr = repo.pr.open("feature", base, "feat: governance", "")
    assert pr.author == driver.forge.whoami()
    if driver.forge.supports("required_contexts"):
        repo.configure(RepoConfig(required_contexts=("gate",)))
        protection = repo.protection(base)
        assert protection is not None
        assert "gate" in protection.required_contexts
    if driver.forge.supports("schedule_events"):
        if driver.forge.supports("auto_merge"):
            repo.configure(RepoConfig(allow_auto_merge=True))
            driver.await_mergeable(repo.owner, repo.name, pr.number)
            repo.pr.arm(pr.number, title="feat: governance")
            repo.pr.disarm(pr.number)
            driver.settle(repo.owner, repo.name, sha)
            kinds = [event.kind for event in repo.pr.schedule_events(pr.number)]
            assert "scheduled" in kinds
    else:
        try:
            repo.pr.schedule_events(pr.number)
        except Unsupported:
            pass
        else:
            raise AssertionError(
                "schedule_events must refuse by name without the capability"
            )
    assert repo.pr.reviews(pr.number) == () or all(
        review.author for review in repo.pr.reviews(pr.number)
    )


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
    contexts = (driver.required_context(),)
    repo.configure(RepoConfig(required_contexts=contexts))
    repo.configure(RepoConfig(required_contexts=contexts))


def _configure_secrets(driver: ForgeDriver) -> None:
    """Secrets and variables store idempotently where supported.

    Write-only by design: no protocol verb reads a secret back, so the
    assertion is that both writes succeed twice.
    """
    repo = driver.fresh_repo()
    config = RepoConfig(
        secrets={"CONF_SECRET": "a secret value"},
        variables={"CONF_VARIABLE": "a plain value"},
    )
    repo.configure(config)
    repo.configure(config)


def _configure_secrets_declined(driver: ForgeDriver) -> None:
    """Secrets are declined by name where the backend cannot store them."""
    repo = driver.fresh_repo()
    try:
        repo.configure(RepoConfig(secrets={"CONF_SECRET": "a secret value"}))
    except Unsupported as exc:
        assert "ci_secrets" in str(exc)
    else:
        raise AssertionError("a forge without ci_secrets must raise Unsupported")


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
    """Open, find_by_head, and get agree; duplicates are refused."""
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
    driver.await_mergeable(repo.owner, repo.name, pr.number)
    repo.pr.merge_now(pr.number, title="feat: merge")
    driver.await_merged(repo.owner, repo.name, pr.number)
    merged = repo.pr.get(pr.number)
    assert merged is not None
    assert merged.merged
    assert merged.state == "closed"
    assert not repo.branch_exists("feature")
    by_sha = repo.pr.find_by_head_sha(sha)
    assert by_sha is not None
    assert by_sha.number == pr.number
    repo.pr.merge_now(pr.number, title="feat: merge")  # idempotent re-run
    still = repo.pr.get(pr.number)
    assert still is not None
    assert still.merged


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


def _armable(driver: ForgeDriver) -> Repository:
    """A fresh repository configured so arming is available.

    GitHub only arms a pull request something blocks, so where the
    forge can name required contexts the repository is protected with
    the seeded workflow's context; elsewhere allowing auto-merge is
    enough.
    """
    repo = driver.fresh_repo()
    if driver.forge.supports("required_contexts"):
        repo.configure(
            RepoConfig(
                allow_auto_merge=True,
                required_contexts=(driver.required_context(),),
            )
        )
    else:
        repo.configure(RepoConfig(allow_auto_merge=True))
    return repo


def _arm_disarm(driver: ForgeDriver) -> None:
    """Arm records a schedule, disarm cancels it, both observably."""
    repo = _armable(driver)
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: arm", "")
    driver.await_mergeable(repo.owner, repo.name, pr.number)
    assert not repo.pr.is_armed(pr.number)
    repo.pr.arm(pr.number, title="feat: arm")
    assert repo.pr.is_armed(pr.number)
    assert repo.pr.disarm(pr.number)
    assert not repo.pr.is_armed(pr.number)
    assert not repo.pr.disarm(pr.number)  # idempotent, and says so
    run = driver.await_run(repo.owner, repo.name, head_sha=sha)
    repo.checks.cancel_run(run)  # leave nothing hanging


def _armed_pr_merges_on_green(driver: ForgeDriver) -> None:
    """An armed pull request merges when its checks go green, server-side."""
    repo = _armable(driver)
    # A held run keeps CI live while the arm lands (GitLab refuses a
    # schedule with no live pipeline); settling releases it to green.
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    pr = repo.pr.open("feature", _default_branch(driver, repo), "feat: auto", "")
    driver.await_mergeable(repo.owner, repo.name, pr.number)
    repo.pr.arm(pr.number, title="feat: auto")
    driver.settle(repo.owner, repo.name, sha)
    driver.await_merged(repo.owner, repo.name, pr.number)
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
    live_sha = driver.push(repo.owner, repo.name, "live", outcome="hang")
    driver.await_run(repo.owner, repo.name, head_sha=live_sha)
    assert repo.checks.status(live_sha).state == "pending"
    green_sha = driver.push(repo.owner, repo.name, "green")
    driver.settle(repo.owner, repo.name, green_sha)
    green = repo.checks.status(green_sha)
    assert green.state == "success"
    assert green.contexts >= 1
    red_sha = driver.push(repo.owner, repo.name, "red", outcome="failure")
    driver.settle(repo.owner, repo.name, red_sha)
    assert repo.checks.status(red_sha).state == "failure"
    live_run = driver.await_run(repo.owner, repo.name, head_sha=live_sha)
    repo.checks.cancel_run(live_run)  # leave nothing hanging
    driver.settle(repo.owner, repo.name, live_sha)


def _runs_jobs_log(driver: ForgeDriver) -> None:
    """Runs filters and orders newest first; jobs and job_log read through."""
    repo = driver.fresh_repo()
    first = driver.push(repo.owner, repo.name, "one", outcome="failure")
    second = driver.push(repo.owner, repo.name, "two")
    driver.settle(repo.owner, repo.name, first)
    driver.settle(repo.owner, repo.name, second)
    listed = repo.checks.runs()
    assert len(listed) >= 2
    ids = [run.id for run in listed]
    assert ids == sorted(ids, reverse=True), "runs must list newest first"
    assert {first, second} <= {run.head_sha for run in listed}
    for_first = repo.checks.runs(head_sha=first)
    assert len(for_first) == 1
    run = for_first[0]
    assert run.status == "completed"
    assert run.conclusion == "failure"
    jobs = repo.checks.jobs(run.id)
    assert jobs, "a run has jobs"
    assert jobs[0].conclusion == "failure"
    assert repo.checks.job_log(jobs[0].id) != ""


def _rerun(driver: ForgeDriver) -> None:
    """Rerun re-runs a completed run; a live run is refused."""
    repo = driver.fresh_repo()
    live_sha = driver.push(repo.owner, repo.name, "live", outcome="hang")
    live_run = driver.await_run(repo.owner, repo.name, head_sha=live_sha)
    try:
        repo.checks.rerun(live_run)
    except ForgeError:
        pass
    else:
        raise AssertionError("re-running a live run must raise")
    repo.checks.cancel_run(live_run)
    driver.settle(repo.owner, repo.name, live_sha)
    flaky_sha = driver.push(repo.owner, repo.name, "flaky", outcome="failure")
    driver.settle(repo.owner, repo.name, flaky_sha)
    flaky_run = driver.await_run(repo.owner, repo.name, head_sha=flaky_sha)
    repo.checks.rerun(flaky_run)
    driver.settle(repo.owner, repo.name, flaky_sha)
    settled = repo.checks.runs(head_sha=flaky_sha)[0]
    assert settled.status == "completed", "a re-run run settles again"


def _cancel_run(driver: ForgeDriver) -> None:
    """cancel_run cancels a live run; a terminal run is refused."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    run = driver.await_run(repo.owner, repo.name, head_sha=sha)
    repo.checks.cancel_run(run)
    driver.settle(repo.owner, repo.name, sha)  # cancellation lands async
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
    """Force-cancel works where the capability is declared."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    run = driver.await_run(repo.owner, repo.name, head_sha=sha)
    repo.checks.cancel_run(run, force=True)
    driver.settle(repo.owner, repo.name, sha)
    cancelled = repo.checks.runs(head_sha=sha)[0]
    assert cancelled.conclusion == "cancelled"


def _cancel_run_force_declined(driver: ForgeDriver) -> None:
    """Force is declined by name where unsupported; plain cancel still works."""
    repo = driver.fresh_repo()
    sha = driver.push(repo.owner, repo.name, "feature", outcome="hang")
    run = driver.await_run(repo.owner, repo.name, head_sha=sha)
    try:
        repo.checks.cancel_run(run, force=True)
    except Unsupported as exc:
        assert "force_cancel" in str(exc)
    else:
        raise AssertionError("force on an unsupporting forge must raise")
    repo.checks.cancel_run(run)
    driver.settle(repo.owner, repo.name, sha)
    assert repo.checks.runs(head_sha=sha)[0].conclusion == "cancelled"


def _dispatch(driver: ForgeDriver) -> None:
    """Dispatch queues a run on the named ref; a missing ref is refused."""
    repo = driver.fresh_repo()
    repo.checks.dispatch("conf.yml", ref=_default_branch(driver, repo))
    driver.await_run(repo.owner, repo.name, event="workflow_dispatch")
    try:
        repo.checks.dispatch("conf.yml", ref="no-such-ref")
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
    got = repo.issue.get(issue.number)  # the direct get is not a listing
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
    other = repo.issue.create("two", body="unrelated")
    driver.await_issue(repo.owner, repo.name, with_marker.number)
    driver.await_issue(repo.owner, repo.name, other.number)
    numbers = [issue.number for issue in repo.issue.list()]
    assert with_marker.number in numbers
    assert len(numbers) >= 2
    hits = repo.issue.search("marker: drift-2026", labels=("nightly",))
    assert [issue.number for issue in hits] == [with_marker.number]
    assert repo.issue.search("no such text") == ()


def _issue_assign(driver: ForgeDriver) -> None:
    """Assign adds; unassign removes only the caller; both re-runnable."""
    repo = driver.fresh_repo()
    issue = repo.issue.create("work", body="the order")
    me = driver.forge.whoami()
    repo.issue.assign(issue.number, me)
    repo.issue.assign(issue.number, me)  # already assigned: a no-op
    driver.await_issue(repo.owner, repo.name, issue.number, assignee=me)
    mine = repo.issue.assigned_to_me()
    assert issue.number in [i.number for i in mine]
    repo.issue.unassign(issue.number)
    repo.issue.unassign(issue.number)  # not assigned: a no-op
    fetched = repo.issue.get(issue.number)
    assert fetched is not None and me not in fetched.assignees
    try:
        repo.issue.assign(issue.number + 999, me)
    except ForgeError:
        pass
    else:
        raise AssertionError("assigning a missing issue must raise")


def _issue_close(driver: ForgeDriver) -> None:
    """Close closes, is idempotent, and a missing number is refused."""
    repo = driver.fresh_repo()
    issue = repo.issue.create("done with this", body="the order")
    repo.issue.comment(issue.number, "closed: wontfix - superseded")
    repo.issue.close(issue.number)
    repo.issue.close(issue.number)  # already closed: a no-op
    fetched = repo.issue.get(issue.number)
    assert fetched is not None and fetched.state == "closed"
    assert issue.number not in [row.number for row in repo.issue.list(state="open")]
    try:
        repo.issue.close(issue.number + 999)
    except ForgeError:
        pass
    else:
        raise AssertionError("closing a missing issue must raise")


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
    Scenario("addresses", _addresses),
    Scenario("governance-reads", _governance_reads),
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
    Scenario("configure-secrets", _configure_secrets, requires=("ci_secrets",)),
    Scenario(
        "configure-secrets-declined",
        _configure_secrets_declined,
        forbids=("ci_secrets",),
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
    Scenario("issue-close", _issue_close),
)
"""Every conformance scenario, in the order the groups are documented."""
