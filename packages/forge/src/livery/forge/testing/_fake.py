"""An in-memory forge that answers from tables and injects faults on demand.

livery.forge.testing.FakeForge implements livery.forge.Forge without a
server: every operation reads and writes plain dictionaries, so a test
that would take minutes against a container runs in microseconds. The
fake is verified, not trusted: the same conformance suite the real
backends must pass runs against it, so behaving like the contract is a
tested property.

Beyond the protocol, the fake is the world: driver methods simulate
what the git lane and the CI runners would do (a push, a tag, a run
finishing), and livery.forge.testing.Faults injects the quirks the
real forges have taught, deterministically. Each fault mode has its
line in the package's quirks list and a regression test that fails
without it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._protocol import Checks, Issues, PullRequests, Releases, Repository
from livery.forge._types import (
    Capability,
    CombinedStatus,
    Conclusion,
    Issue,
    Job,
    Label,
    Protection,
    PullRequest,
    Release,
    RepoConfig,
    RepoInfo,
    Review,
    ReviewState,
    Run,
    ScheduleEvent,
    StateFilter,
)
from livery.forge.testing._conformance import Outcome

_ALL_CAPABILITIES: tuple[Capability, ...] = (
    "auto_merge",
    "force_cancel",
    "required_contexts",
    "ci_secrets",
    "schedule_events",
)


@dataclass
class Faults:
    """Deterministic fault injection, one attribute per known forge quirk.

    Counters arm a fault for the next N triggering calls and count
    down as they fire; a flag holds until cleared. Nothing here is
    random: a test arms exactly the fault it needs and the fake
    misbehaves exactly that often.

    Attributes:
        lose_arm_schedule: The next N calls to
            livery.forge.PullRequests.arm succeed silently without
            recording a schedule, so the pull request reads unarmed
            afterwards. The lost-auto-merge-schedule quirk.
        merge_405_window: The next N calls to
            livery.forge.PullRequests.merge_now raise
            livery.forge.ForgeError with status 405, as a forge does
            while a mergeability recompute is in flight.
        wedge_status_queue: While True, runs never leave the queued
            state: a finished run is not recorded, so the combined
            status stays pending forever. Cancelling the run is the
            relief, as it is on a real wedged queue.
        slow_status_reads: The next N calls to
            livery.forge.Checks.status answer as if nothing had
            reported for the commit, as a forge does in the window
            after a push before statuses appear.
    """

    lose_arm_schedule: int = 0
    merge_405_window: int = 0
    wedge_status_queue: bool = False
    slow_status_reads: int = 0


@dataclass
class _PullRequestState:
    number: int
    title: str
    body: str
    state: Literal["open", "closed"]
    merged: bool
    head_branch: str
    head_sha: str
    base_branch: str
    comments: list[str] = field(default_factory=list)
    author: str = "fake-user"
    reviews: list[Review] = field(default_factory=list)
    events: list[ScheduleEvent] = field(default_factory=list)


@dataclass
class _IssueState:
    number: int
    title: str
    body: str
    state: Literal["open", "closed"]
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    comments: list[str] = field(default_factory=list)


@dataclass
class _RunState:
    id: int
    workflow: str
    head_sha: str
    event: str
    status: Literal["queued", "running", "completed"]
    conclusion: Conclusion
    job_id: int
    outcome: Outcome = "success"
    log: str = ""


@dataclass
class _RepoState:
    owner: str
    name: str
    private: bool
    description: str
    default_branch: str = "main"
    squash_only: bool = False
    delete_branch_on_merge: bool = False
    allow_auto_merge: bool = False
    required_contexts: tuple[str, ...] = ()
    secrets: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    labels: dict[str, Label] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    prs: dict[int, _PullRequestState] = field(default_factory=dict)
    armed: dict[int, tuple[str, str]] = field(default_factory=dict)
    issues: dict[int, _IssueState] = field(default_factory=dict)
    releases: dict[str, Release] = field(default_factory=dict)
    runs: dict[int, _RunState] = field(default_factory=dict)
    protections: dict[str, Protection] = field(default_factory=dict)
    next_pr: int = 1
    next_issue: int = 1


class FakeForge:
    """A livery.forge.Forge that keeps everything in memory.

    The protocol half behaves as the conformance suite demands. The
    driver half simulates the world around the protocol: pushes, tags,
    and CI runs finishing. Faults are injected through the ``faults``
    attribute; see livery.forge.testing.Faults.

    Pull request and issue numbers count in separate spaces, as GitLab
    keeps them, so code that mixes the two fails here first.

    Args:
        user: What livery.forge.Forge.whoami answers.
        version: What livery.forge.Forge.server_version answers.
        capabilities: What livery.forge.Forge.supports answers True
            for. Defaults to every known capability; pass a subset to
            model a forge that declines some by name.
    """

    def __init__(
        self,
        *,
        user: str = "fake-user",
        version: str = "1.0.0",
        capabilities: tuple[Capability, ...] = _ALL_CAPABILITIES,
    ) -> None:
        """Build an empty forge for *user* with *capabilities*."""
        self.faults: Faults = Faults()
        self._user = user
        self._version = version
        self._capabilities = frozenset(capabilities)
        self._repos: dict[tuple[str, str], _RepoState] = {}
        self._next_sha = 1
        self._next_run = 1
        self._next_job = 1

    # -- the protocol half -------------------------------------------------

    def whoami(self) -> str:
        """The configured user's login name."""
        return self._user

    def server_version(self) -> str:
        """The configured version string."""
        return self._version

    def supports(self, capability: Capability) -> bool:
        """Whether *capability* was granted at construction."""
        return capability in self._capabilities

    def repository(self, owner: str, name: str) -> Repository:
        """The view onto one repository. Cheap, no existence check."""
        return _FakeRepository(self, owner, name)

    def user_url(self, login: str) -> str:
        """The address of *login*'s profile under the ``fake://`` scheme."""
        return f"fake://{login}"

    def set_protection(
        self, owner: str, name: str, branch: str, protection: Protection
    ) -> None:
        """Test helper: what Repository.protection answers for *branch*."""
        self._require_repo(owner, name).protections[branch] = protection

    def review(
        self, owner: str, name: str, number: int, author: str, state: ReviewState
    ) -> None:
        """Test helper: record a submitted review on a pull request."""
        pr = self._require_pr(self._require_repo(owner, name), number)
        pr.reviews.append(Review(author=author, state=state))

    def create_repo(
        self,
        owner: str,
        name: str,
        *,
        private: bool = True,
        description: str = "",
    ) -> Repository:
        """Create the repository with an initialised default branch."""
        if (owner, name) in self._repos:
            raise ForgeError(
                f"repository {owner}/{name} already exists",
                status=409,
                method="POST",
                endpoint=f"/repos/{owner}/{name}",
            )
        state = _RepoState(
            owner=owner, name=name, private=private, description=description
        )
        state.branches[state.default_branch] = self._sha()
        self._repos[(owner, name)] = state
        return self.repository(owner, name)

    def get_repo(self, owner: str, name: str) -> RepoInfo | None:
        """The repository's settings, or None when it does not exist."""
        state = self._repos.get((owner, name))
        if state is None:
            return None
        return RepoInfo(
            owner=state.owner,
            name=state.name,
            default_branch=state.default_branch,
            private=state.private,
        )

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete the repository; a repository already gone is success."""
        self._repos.pop((owner, name), None)

    # -- the driver half ---------------------------------------------------

    def push(
        self,
        owner: str,
        name: str,
        branch: str,
        *,
        outcome: Outcome = "success",
        sha: str = "",
    ) -> str:
        """Simulate a git push: a new head sha on *branch*, CI starting.

        Creates the branch when it is new, moves its head to *sha*
        (a rig pairing the fake with a real repository passes the
        real commit, so sha-keyed reads agree across the seam) or a
        fresh deterministic one, and queues one run for that sha, as
        a push trigger would. The run stays queued until
        livery.forge.testing.FakeForge.settle applies *outcome* (a
        ``hang`` outcome is released to success there, or cancelled
        through the protocol). Returns the new head sha.
        """
        state = self._require_repo(owner, name)
        sha = sha or self._sha()
        state.branches[branch] = sha
        self._start_run(state, sha, workflow="ci.yml", event="push", outcome=outcome)
        return sha

    def create_tag(self, owner: str, name: str, tag: str) -> None:
        """Simulate a tag push: *tag* at the default branch's head."""
        state = self._require_repo(owner, name)
        state.tags[tag] = state.branches[state.default_branch]

    def settle(self, owner: str, name: str, sha: str) -> None:
        """Simulate CI settling: every run for *sha* reaches its outcome.

        A held (``hang``) run is released and concludes success, per
        the driver contract. While
        livery.forge.testing.Faults.wedge_status_queue holds, the
        results are silently dropped and the runs stay queued, which
        is what a wedged queue does to finished jobs. Settle again
        after clearing the fault.
        """
        state = self._require_repo(owner, name)
        if self.faults.wedge_status_queue:
            return
        for run_state in state.runs.values():
            if run_state.head_sha != sha or run_state.status == "completed":
                continue
            conclusion: Conclusion = (
                "success" if run_state.outcome == "hang" else run_state.outcome
            )
            run_state.status = "completed"
            run_state.conclusion = conclusion
            run_state.log = f"job {run_state.job_id} concluded {conclusion}"
        self._settle(state)

    def comment_bodies(
        self, owner: str, name: str, number: int, *, kind: Literal["pr", "issue"]
    ) -> tuple[str, ...]:
        """The comments posted on one pull request or issue, oldest first."""
        state = self._require_repo(owner, name)
        if kind == "pr":
            return tuple(self._require_pr(state, number).comments)
        return tuple(self._require_issue(state, number).comments)

    # -- internals ---------------------------------------------------------

    def _sha(self) -> str:
        sha = f"{self._next_sha:040x}"
        self._next_sha += 1
        return sha

    def _start_run(
        self,
        state: _RepoState,
        sha: str,
        *,
        workflow: str,
        event: str,
        outcome: Outcome = "success",
    ) -> _RunState:
        run = _RunState(
            id=self._next_run,
            workflow=workflow,
            head_sha=sha,
            event=event,
            status="queued",
            conclusion="",
            job_id=self._next_job,
            outcome=outcome,
        )
        self._next_run += 1
        self._next_job += 1
        state.runs[run.id] = run
        return run

    def _require_repo(self, owner: str, name: str) -> _RepoState:
        state = self._repos.get((owner, name))
        if state is None:
            raise ForgeError(
                f"no repository {owner}/{name} on this forge",
                status=404,
                endpoint=f"/repos/{owner}/{name}",
            )
        return state

    def _require_pr(self, state: _RepoState, number: int) -> _PullRequestState:
        pr = state.prs.get(number)
        if pr is None:
            raise ForgeError(
                f"no pull request {number} in {state.owner}/{state.name}",
                status=404,
            )
        return pr

    def _require_issue(self, state: _RepoState, number: int) -> _IssueState:
        issue = state.issues.get(number)
        if issue is None:
            raise ForgeError(
                f"no issue {number} in {state.owner}/{state.name}",
                status=404,
            )
        return issue

    def _require_run(self, state: _RepoState, run: int) -> _RunState:
        run_state = state.runs.get(run)
        if run_state is None:
            raise ForgeError(f"no run {run} in {state.owner}/{state.name}", status=404)
        return run_state

    def _derived_status(self, state: _RepoState, sha: str) -> CombinedStatus:
        runs = [run for run in state.runs.values() if run.head_sha == sha]
        if not runs:
            return CombinedStatus(state="none", contexts=0)
        if any(run.status != "completed" for run in runs):
            return CombinedStatus(state="pending", contexts=len(runs))
        if all(run.conclusion in ("success", "skipped") for run in runs):
            return CombinedStatus(state="success", contexts=len(runs))
        return CombinedStatus(state="failure", contexts=len(runs))

    def _settle(self, state: _RepoState) -> None:
        """Merge every armed pull request whose checks are green.

        The server-side half of auto-merge, run after every state
        change a real forge would evaluate the schedule on.
        """
        for number in list(state.armed):
            pr = state.prs[number]
            if pr.state != "open":
                state.armed.pop(number)
                continue
            # Auto-merge judges the branch's current head, as the real
            # forges do; the stored sha may predate a re-push.
            head = state.branches.get(pr.head_branch, pr.head_sha)
            if self._derived_status(state, head).state == "success":
                state.armed.pop(number)
                self._merge(state, pr)

    def _merge(self, state: _RepoState, pr: _PullRequestState) -> None:
        # Freeze the head at the merge: an open pull request tracks its
        # branch, and this is the moment tracking stops.
        pr.head_sha = state.branches.get(pr.head_branch, pr.head_sha)
        pr.state = "closed"
        pr.merged = True
        pr.events.append(ScheduleEvent(kind="merged", actor="fake-user"))
        if state.delete_branch_on_merge:
            state.branches.pop(pr.head_branch, None)
            # The strictest conforming behaviour: a forge may clear the
            # head ref once the branch is auto-deleted, so the fake
            # does, and nothing downstream can rely on finding a merged
            # pull request by branch name.
            pr.head_branch = ""


class _FakeRepository:
    """The livery.forge.Repository view onto one FakeForge repository."""

    def __init__(self, fake: FakeForge, owner: str, name: str) -> None:
        self._owner = owner
        self._name = name
        self.pr: PullRequests = _FakePullRequests(fake, owner, name)
        self.checks: Checks = _FakeChecks(fake, owner, name)
        self.issue: Issues = _FakeIssues(fake, owner, name)
        self.release: Releases = _FakeReleases(fake, owner, name)
        self._fake = fake

    @property
    def owner(self) -> str:
        """The owner the view is bound to."""
        return self._owner

    @property
    def name(self) -> str:
        """The repository name the view is bound to."""
        return self._name

    def configure(self, config: RepoConfig) -> None:
        """Apply the stated fields; leave None fields untouched."""
        state = self._fake._require_repo(self._owner, self._name)
        if config.required_contexts is not None and not self._fake.supports(
            "required_contexts"
        ):
            raise Unsupported(
                "this forge cannot name required check contexts in branch"
                " protection (capability: required_contexts)"
            )
        if config.secrets is not None and not self._fake.supports("ci_secrets"):
            raise Unsupported(
                "this forge cannot store CI secrets (capability: ci_secrets)"
            )
        if config.default_branch is not None:
            state.default_branch = config.default_branch
            state.branches.setdefault(config.default_branch, self._fake._sha())
        if config.squash_only is not None:
            state.squash_only = config.squash_only
        if config.delete_branch_on_merge is not None:
            state.delete_branch_on_merge = config.delete_branch_on_merge
        if config.allow_auto_merge is not None:
            state.allow_auto_merge = config.allow_auto_merge
        if config.required_contexts is not None:
            state.required_contexts = config.required_contexts
            # Asserting contexts creates the branch protection, as it
            # does on the real forges, so the protection read answers.
            existing = state.protections.get(state.default_branch)
            state.protections[state.default_branch] = Protection(
                required_approvals=existing.required_approvals if existing else 0,
                require_codeowner_review=(
                    existing.require_codeowner_review if existing else None
                ),
                block_on_outdated=existing.block_on_outdated if existing else False,
                block_on_rejected=existing.block_on_rejected if existing else False,
                required_contexts=config.required_contexts,
            )
        if config.secrets is not None:
            state.secrets.update(config.secrets)
        if config.variables is not None:
            state.variables.update(config.variables)
        if config.labels is not None:
            for label in config.labels:
                state.labels[label.name] = label

    def tags(self) -> tuple[str, ...]:
        """Every tag name, in creation order."""
        state = self._fake._require_repo(self._owner, self._name)
        return tuple(state.tags)

    def branch_exists(self, branch: str) -> bool:
        """Whether *branch* exists."""
        state = self._fake._require_repo(self._owner, self._name)
        return branch in state.branches

    def protection(self, branch: str) -> Protection | None:
        """The protection set through FakeForge.set_protection, or None."""
        state = self._fake._require_repo(self._owner, self._name)
        return state.protections.get(branch)

    def web_url(self) -> str:
        """The repository's home page under the ``fake://`` scheme."""
        return f"fake://{self._owner}/{self._name}"

    def pr_url(self, number: int) -> str:
        """The address of pull request *number*."""
        return f"{self.web_url()}/pulls/{number}"

    def issue_url(self, number: int) -> str:
        """The address of issue *number*."""
        return f"{self.web_url()}/issues/{number}"

    def commit_url(self, sha: str) -> str:
        """The address of commit *sha*."""
        return f"{self.web_url()}/commit/{sha}"

    def compare_url(self, base: str, head: str) -> str:
        """The address comparing *base* to *head*."""
        return f"{self.web_url()}/compare/{base}...{head}"

    def tag_url(self, tag: str) -> str:
        """The address of *tag*'s release view."""
        return f"{self.web_url()}/releases/tag/{tag}"

    def delete_branch(self, branch: str) -> None:
        """Delete *branch*; a branch already gone is success."""
        state = self._fake._require_repo(self._owner, self._name)
        state.branches.pop(branch, None)


class _FakePullRequests:
    """The pull request operations of one FakeForge repository."""

    def __init__(self, fake: FakeForge, owner: str, name: str) -> None:
        self._fake = fake
        self._owner = owner
        self._name = name

    def _state(self) -> _RepoState:
        return self._fake._require_repo(self._owner, self._name)

    def _as_pull_request(self, pr: _PullRequestState) -> PullRequest:
        # An open pull request's head follows its branch, as on the real
        # forges; a merged or closed one keeps the sha it ended with.
        head_sha = pr.head_sha
        if pr.state == "open" and not pr.merged:
            head_sha = self._state().branches.get(pr.head_branch, pr.head_sha)
        return PullRequest(
            number=pr.number,
            title=pr.title,
            body=pr.body,
            state=pr.state,
            merged=pr.merged,
            head_branch=pr.head_branch,
            head_sha=head_sha,
            base_branch=pr.base_branch,
            url=f"fake://{self._owner}/{self._name}/pulls/{pr.number}",
            author=pr.author,
        )

    def open(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """Open a pull request from *head* into *base*."""
        state = self._state()
        for branch in (head, base):
            if branch not in state.branches:
                raise ForgeError(
                    f"branch {branch} does not exist in {self._owner}/{self._name}",
                    status=422,
                )
        for pr in state.prs.values():
            if pr.state == "open" and pr.head_branch == head:
                raise ForgeError(
                    f"an open pull request for {head} already exists: #{pr.number}",
                    status=409,
                )
        pr = _PullRequestState(
            number=state.next_pr,
            title=title,
            body=body,
            state="open",
            merged=False,
            head_branch=head,
            head_sha=state.branches[head],
            base_branch=base,
        )
        state.next_pr += 1
        state.prs[pr.number] = pr
        return self._as_pull_request(pr)

    def find_by_head(
        self, branch: str, *, state: StateFilter = "open"
    ) -> PullRequest | None:
        """The pull request whose head branch is *branch*, or None."""
        for pr in self._state().prs.values():
            if pr.head_branch == branch and _matches(pr.state, state):
                return self._as_pull_request(pr)
        return None

    def find_by_head_sha(self, sha: str) -> PullRequest | None:
        """The pull request whose head commit is *sha*, or None."""
        for pr in self._state().prs.values():
            if pr.head_sha == sha:
                return self._as_pull_request(pr)
        return None

    def get(self, number: int) -> PullRequest | None:
        """The pull request *number*, or None."""
        pr = self._state().prs.get(number)
        return None if pr is None else self._as_pull_request(pr)

    def update_title(self, number: int, title: str) -> None:
        """Retitle pull request *number*."""
        self._fake._require_pr(self._state(), number).title = title

    def close(self, number: int) -> None:
        """Close pull request *number*; closing a closed one is success."""
        state = self._state()
        pr = self._fake._require_pr(state, number)
        if pr.state == "open":
            # Tracking stops here, as at a merge.
            pr.head_sha = state.branches.get(pr.head_branch, pr.head_sha)
            pr.events.append(ScheduleEvent(kind="closed", actor="fake-user"))
        pr.state = "closed"

    def reopen(self, number: int) -> None:
        """Reopen the closed, unmerged pull request *number*."""
        pr = self._fake._require_pr(self._state(), number)
        if pr.merged:
            raise ForgeError(
                f"pull request {number} has merged and cannot reopen",
                status=422,
            )
        pr.state = "open"

    def merge_now(self, number: int, *, title: str, message: str = "") -> None:
        """Merge pull request *number* immediately; a merged one is success."""
        state = self._state()
        pr = self._fake._require_pr(state, number)
        if pr.merged:
            return
        if pr.state != "open":
            raise ForgeError(f"pull request {number} is not open", status=405)
        if self._fake.faults.merge_405_window > 0:
            self._fake.faults.merge_405_window -= 1
            raise ForgeError(
                f"pull request {number} is not mergeable yet:"
                " a mergeability recompute is in flight",
                status=405,
            )
        if (
            state.required_contexts
            and self._fake._derived_status(state, pr.head_sha).state != "success"
        ):
            raise ForgeError(
                f"pull request {number} has required checks that are not green",
                status=405,
            )
        pr.title = title or pr.title
        self._fake._merge(state, pr)
        state.armed.pop(number, None)

    def arm(self, number: int, *, title: str, message: str = "") -> None:
        """Schedule pull request *number* to merge when green."""
        state = self._state()
        pr = self._fake._require_pr(state, number)
        if not self._fake.supports("auto_merge"):
            raise Unsupported(
                "this forge cannot schedule a merge (capability: auto_merge)"
            )
        if pr.state != "open":
            raise ForgeError(f"pull request {number} is not open", status=405)
        if self._fake.faults.lose_arm_schedule > 0:
            self._fake.faults.lose_arm_schedule -= 1
            return
        state.armed[number] = (title, message)
        pr.events.append(ScheduleEvent(kind="scheduled", actor="fake-user"))
        self._fake._settle(state)

    def disarm(self, number: int) -> bool:
        """Cancel a scheduled merge; True when one was cancelled."""
        state = self._state()
        pr = self._fake._require_pr(state, number)
        cancelled = state.armed.pop(number, None) is not None
        if cancelled:
            pr.events.append(ScheduleEvent(kind="unscheduled", actor="fake-user"))
        return cancelled

    def is_armed(self, number: int) -> bool:
        """Whether pull request *number* has a merge scheduled."""
        state = self._state()
        pr = self._fake._require_pr(state, number)
        return number in state.armed and pr.state == "open"

    def reviews(self, number: int) -> tuple[Review, ...]:
        """The submitted reviews on pull request *number*."""
        return tuple(self._fake._require_pr(self._state(), number).reviews)

    def schedule_events(self, number: int) -> tuple[ScheduleEvent, ...]:
        """The merge-scheduling history, oldest first."""
        if not self._fake.supports("schedule_events"):
            raise Unsupported(
                "this forge keeps no readable scheduling history"
                " (capability: schedule_events)"
            )
        return tuple(self._fake._require_pr(self._state(), number).events)

    def comment(self, number: int, body: str) -> None:
        """Post *body* on pull request *number*."""
        self._fake._require_pr(self._state(), number).comments.append(body)


class _FakeChecks:
    """The CI operations of one FakeForge repository."""

    def __init__(self, fake: FakeForge, owner: str, name: str) -> None:
        self._fake = fake
        self._owner = owner
        self._name = name

    def _state(self) -> _RepoState:
        return self._fake._require_repo(self._owner, self._name)

    def status(self, sha: str) -> CombinedStatus:
        """The combined verdict for *sha*, faults applied."""
        state = self._state()
        if self._fake.faults.slow_status_reads > 0:
            self._fake.faults.slow_status_reads -= 1
            return CombinedStatus(state="none", contexts=0)
        return self._fake._derived_status(state, sha)

    def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
        """The repository's runs, newest first."""
        state = self._state()
        selected = [
            run
            for run in state.runs.values()
            if (not head_sha or run.head_sha == head_sha)
            and (not event or run.event == event)
        ]
        selected.sort(key=lambda run: run.id, reverse=True)
        return tuple(
            Run(
                id=run.id,
                workflow=run.workflow,
                head_sha=run.head_sha,
                event=run.event,
                status=run.status,
                conclusion=run.conclusion,
                url=f"fake://{self._owner}/{self._name}/runs/{run.id}",
            )
            for run in selected
        )

    def jobs(self, run: int) -> tuple[Job, ...]:
        """Every job of run *run*: one, in this fake."""
        run_state = self._fake._require_run(self._state(), run)
        return (
            Job(
                id=run_state.job_id,
                name="gate",
                status=run_state.status,
                conclusion=run_state.conclusion,
            ),
        )

    def job_log(self, job: int) -> str:
        """The log text of job *job*."""
        state = self._state()
        for run in state.runs.values():
            if run.job_id == job:
                return run.log
        raise ForgeError(f"no job {job} in {self._owner}/{self._name}", status=404)

    def rerun(self, run: int, *, failed_only: bool = True) -> None:
        """Reset the completed run *run* to queued for another attempt."""
        run_state = self._fake._require_run(self._state(), run)
        if run_state.status != "completed":
            raise ForgeError(
                f"run {run} is still in progress: nothing to re-run yet",
                status=409,
            )
        run_state.status = "queued"
        run_state.conclusion = ""
        run_state.log = ""

    def cancel_run(self, run: int, *, force: bool = False) -> None:
        """Cancel run *run*; ``force`` needs the force_cancel capability."""
        if force and not self._fake.supports("force_cancel"):
            raise Unsupported(
                "this forge cannot force-cancel a run"
                " (capability: force_cancel); plain cancel is available"
            )
        state = self._state()
        run_state = self._fake._require_run(state, run)
        if run_state.status == "completed":
            raise ForgeError(
                f"run {run} is already terminal and cannot be cancelled",
                status=409,
            )
        run_state.status = "completed"
        run_state.conclusion = "cancelled"
        run_state.log = f"job {run_state.job_id} cancelled"
        self._fake._settle(state)

    def dispatch(
        self, workflow: str, *, ref: str, inputs: Mapping[str, str] | None = None
    ) -> None:
        """Queue a run of *workflow* at *ref*."""
        state = self._state()
        sha = state.branches.get(ref) or state.tags.get(ref)
        if sha is None:
            raise ForgeError(
                f"ref {ref} does not exist in {self._owner}/{self._name}",
                status=404,
            )
        self._fake._start_run(state, sha, workflow=workflow, event="workflow_dispatch")


class _FakeIssues:
    """The issue operations of one FakeForge repository."""

    def __init__(self, fake: FakeForge, owner: str, name: str) -> None:
        self._fake = fake
        self._owner = owner
        self._name = name

    def _state(self) -> _RepoState:
        return self._fake._require_repo(self._owner, self._name)

    def _as_issue(self, issue: _IssueState) -> Issue:
        return Issue(
            number=issue.number,
            title=issue.title,
            body=issue.body,
            state=issue.state,
            labels=issue.labels,
            assignees=issue.assignees,
            url=f"fake://{self._owner}/{self._name}/issues/{issue.number}",
        )

    def create(
        self,
        title: str,
        *,
        body: str = "",
        labels: tuple[str, ...] = (),
        assignee: str = "",
    ) -> Issue:
        """Open an issue; its labels must already exist on the repository."""
        state = self._state()
        for label in labels:
            if label not in state.labels:
                raise ForgeError(
                    f"label {label} does not exist in"
                    f" {self._owner}/{self._name}: configure it first",
                    status=422,
                )
        issue = _IssueState(
            number=state.next_issue,
            title=title,
            body=body,
            state="open",
            labels=labels,
            assignees=(assignee,) if assignee else (),
        )
        state.next_issue += 1
        state.issues[issue.number] = issue
        return self._as_issue(issue)

    def get(self, number: int) -> Issue | None:
        """The issue *number*, body included, or None."""
        issue = self._state().issues.get(number)
        return None if issue is None else self._as_issue(issue)

    def list(self, *, state: StateFilter = "open") -> tuple[Issue, ...]:
        """The issues in *state*, oldest first."""
        return tuple(
            self._as_issue(issue)
            for issue in self._state().issues.values()
            if _matches(issue.state, state)
        )

    def search(
        self,
        text: str,
        *,
        state: StateFilter = "open",
        labels: tuple[str, ...] = (),
    ) -> tuple[Issue, ...]:
        """The issues whose title or body contains *text*."""
        return tuple(
            self._as_issue(issue)
            for issue in self._state().issues.values()
            if _matches(issue.state, state)
            and (text in issue.title or text in issue.body)
            and all(label in issue.labels for label in labels)
        )

    def assign(self, number: int, assignee: str) -> None:
        """Make *assignee* the single assignee of issue *number*."""
        issue = self._fake._require_issue(self._state(), number)
        issue.assignees = (assignee,)

    def assigned_to_me(self) -> tuple[Issue, ...]:
        """The open issues assigned to the authenticated user."""
        me = self._fake.whoami()
        return tuple(
            self._as_issue(issue)
            for issue in self._state().issues.values()
            if issue.state == "open" and me in issue.assignees
        )

    def comment(self, number: int, body: str) -> None:
        """Post *body* on issue *number*."""
        self._fake._require_issue(self._state(), number).comments.append(body)


class _FakeReleases:
    """The release operations of one FakeForge repository."""

    def __init__(self, fake: FakeForge, owner: str, name: str) -> None:
        self._fake = fake
        self._owner = owner
        self._name = name

    def _state(self) -> _RepoState:
        return self._fake._require_repo(self._owner, self._name)

    def create(
        self, tag: str, *, name: str, body: str = "", prerelease: bool = False
    ) -> Release:
        """Create the release for the existing tag *tag*."""
        state = self._state()
        if tag not in state.tags:
            raise ForgeError(
                f"tag {tag} does not exist in {self._owner}/{self._name}:"
                " push the tag before releasing it",
                status=404,
            )
        if tag in state.releases:
            raise ForgeError(
                f"tag {tag} already has a release: probe with release.get"
                " before creating",
                status=409,
            )
        release = Release(
            tag=tag,
            name=name,
            body=body,
            prerelease=prerelease,
            url=f"fake://{self._owner}/{self._name}/releases/{tag}",
        )
        state.releases[tag] = release
        return release

    def get(self, tag: str) -> Release | None:
        """The release for *tag*, or None."""
        return self._state().releases.get(tag)


def _matches(state: Literal["open", "closed"], wanted: StateFilter) -> bool:
    """Whether an item in *state* passes the *wanted* filter."""
    return wanted == "all" or state == wanted


class FakeDriver:
    """The livery.forge.testing.ForgeDriver over a FakeForge.

    The reference driver: it makes the conformance scenarios runnable
    with no server at all, and shows a real backend's test rig what it
    must supply.

    Args:
        fake: The forge to drive. A fresh full-capability one when
            omitted.
    """

    def __init__(self, fake: FakeForge | None = None) -> None:
        """Bind the driver to *fake*, creating one when omitted."""
        self.fake: FakeForge = fake if fake is not None else FakeForge()
        self._counter = 0

    @property
    def forge(self) -> FakeForge:
        """The forge under test."""
        return self.fake

    def unused_repo_name(self) -> tuple[str, str]:
        """An owner and name no repository has yet."""
        self._counter += 1
        return ("acme", f"repo-{self._counter}")

    def fresh_repo(self) -> Repository:
        """A new repository with an initialised default branch."""
        owner, name = self.unused_repo_name()
        return self.fake.create_repo(owner, name)

    def push(
        self,
        repo_owner: str,
        repo_name: str,
        branch: str,
        *,
        outcome: Outcome = "success",
    ) -> str:
        """Push *branch* with *outcome* and return its new head sha."""
        return self.fake.push(repo_owner, repo_name, branch, outcome=outcome)

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head."""
        self.fake.create_tag(repo_owner, repo_name, tag)

    def settle(self, repo_owner: str, repo_name: str, sha: str) -> None:
        """Apply every pushed outcome for *sha*; immediate on the fake."""
        self.fake.settle(repo_owner, repo_name, sha)

    def await_run(
        self, repo_owner: str, repo_name: str, *, head_sha: str = "", event: str = ""
    ) -> int:
        """The one run matching the filters; the fake never waits.

        Raises AssertionError unless exactly one run matches, which is
        the driver contract.
        """
        runs = self.fake.repository(repo_owner, repo_name).checks.runs(
            head_sha=head_sha, event=event
        )
        assert len(runs) == 1, f"expected one matching run, found {len(runs)}"
        return runs[0].id

    def comment_bodies(
        self,
        repo_owner: str,
        repo_name: str,
        number: int,
        *,
        kind: Literal["pr", "issue"],
    ) -> tuple[str, ...]:
        """The comments on one pull request or issue, oldest first."""
        return self.fake.comment_bodies(repo_owner, repo_name, number, kind=kind)

    def await_mergeable(self, repo_owner: str, repo_name: str, number: int) -> None:
        """The fake computes mergeability synchronously: nothing to await."""
        self.fake._require_pr(self.fake._require_repo(repo_owner, repo_name), number)

    def await_merged(self, repo_owner: str, repo_name: str, number: int) -> None:
        """The fake merges inside settle: nothing to await."""
        self.fake._require_pr(self.fake._require_repo(repo_owner, repo_name), number)

    def await_issue(
        self, repo_owner: str, repo_name: str, number: int, *, assignee: str = ""
    ) -> None:
        """The fake's listings are immediate: only verify the state."""
        issue = self.fake._require_issue(
            self.fake._require_repo(repo_owner, repo_name), number
        )
        assert not assignee or assignee in issue.assignees

    def required_context(self) -> str:
        """The fake reports checks under the plain job name."""
        return "gate"
