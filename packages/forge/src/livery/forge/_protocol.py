"""The protocols every backend implements and every workflow calls.

livery.forge.Forge is one server: identity, capabilities, and the
repositories it hosts. livery.forge.Repository is one repository on
that server, with its operations grouped the way the workflows name
them: ``pr``, ``checks``, ``issue``, ``release``. livery.forge.Registry
is a package index, kept apart because a forge and a registry are only
sometimes the same server.

Four rules hold across every method:

- One handle. A pull request, issue, run, or job is identified by the
  number or id the protocol returned for it. A backend whose server
  keeps two identities (GitLab's iid and global id) translates at the
  boundary and never leaks the other one.
- Listings are complete or they raise. A method that returns a
  sequence returns everything the query matches. When a backend cannot
  read to the end (a pagination cap, a truncated answer), it raises
  livery.forge.ForgeError instead of returning a prefix, because "not
  in this list" is an answer callers act on.
- Probe before acting. Every verb is safe to re-run, and re-running a
  workflow is its recovery procedure. Where an operation is not
  idempotent on the server (creating a release, opening a pull
  request), the protocol pairs it with the read that makes the caller's
  probe-then-act loop idempotent.
- Capabilities, not pretence. Where forges differ, the
  difference is a named livery.forge.Capability. A backend asked for
  an operation it declined by name raises livery.forge.Unsupported;
  callers that can degrade probe livery.forge.Forge.supports first.

Failures carry the server's own words: every livery.forge.ForgeError
message quotes what the server said, and no failure is ever reduced to
a bare boolean.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from livery.forge._types import (
    Capability,
    Codeowners,
    CodeownersEntry,
    CombinedStatus,
    Issue,
    Job,
    Protection,
    PullRequest,
    Release,
    RepoConfig,
    RepoInfo,
    Review,
    Run,
    ScheduleEvent,
    StateFilter,
)


class PullRequests(Protocol):
    """One repository's pull requests: open, find, merge, arm, comment.

    GitLab calls them merge requests; the operations map one to one
    and the protocol keeps one vocabulary.
    """

    def open(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """Open a pull request from *head* into *base* and return it.

        Raises livery.forge.ForgeError when an open pull request for
        *head* already exists; find and reuse it with
        livery.forge.PullRequests.find_by_head first.
        """
        ...

    def find_by_head(
        self, branch: str, *, state: StateFilter = "open"
    ) -> PullRequest | None:
        """The pull request whose head branch is *branch*, or None.

        Reliable for open pull requests. A merged pull request whose
        head branch was auto-deleted may no longer carry the branch
        name on some forges, so "None" here does not prove no such
        pull request ever existed: find a finished one by
        livery.forge.PullRequests.find_by_head_sha instead.
        """
        ...

    def find_by_head_sha(self, sha: str) -> PullRequest | None:
        """The pull request whose head commit is *sha*, or None.

        The head sha outlives the head branch, so this finds a merged
        pull request after the branch is gone.
        """
        ...

    def get(self, number: int) -> PullRequest | None:
        """The pull request *number*, or None when it does not exist."""
        ...

    def update_title(self, number: int, title: str) -> None:
        """Retitle pull request *number*.

        The title is the review-facing name and, on a squash-only
        repository, the merge armed after this call carries it as the
        commit subject.
        """
        ...

    def close(self, number: int) -> None:
        """Close pull request *number* without merging."""
        ...

    def reopen(self, number: int) -> None:
        """Reopen the closed, unmerged pull request *number*.

        Reopening reuses the pull request, history and discussion
        intact, rather than opening a duplicate.
        """
        ...

    def merge_now(self, number: int, *, title: str, message: str = "") -> None:
        """Merge pull request *number* immediately.

        *title* is the squash commit subject, *message* its body. A
        refusal raises livery.forge.ForgeError with the status a
        caller branches on: 405 when the checks are not green, 409 on
        a conflict. A caller that can wait arms instead
        (livery.forge.PullRequests.arm).

        Merging an already merged pull request is success: re-running
        a verb is its recovery procedure, and backends whose forge
        refuses the second merge absorb that refusal after verifying
        the pull request really merged.
        """
        ...

    def arm(self, number: int, *, title: str, message: str = "") -> None:
        """Schedule pull request *number* to merge when its checks go green.

        *title* is the squash commit subject the merge will carry,
        *message* its body. Arming an already armed pull request
        replaces the schedule, title included. The merge itself
        happens server-side: the caller observes it through
        livery.forge.Checks.status and livery.forge.PullRequests.get,
        never performs it. Push to an armed pull request only after
        livery.forge.PullRequests.disarm: a push into a live schedule
        races the merge, and the merge can take the pre-push head.

        Raises livery.forge.Unsupported on a forge without the
        ``auto_merge`` capability.
        """
        ...

    def disarm(self, number: int) -> bool:
        """Cancel a scheduled merge on pull request *number*.

        Returns True when a schedule was cancelled and False when there
        was nothing to cancel. Idempotent: disarming an unarmed pull
        request is not an error.
        """
        ...

    def is_armed(self, number: int) -> bool:
        """Whether pull request *number* currently has a merge scheduled.

        A pull request that left the open state reads unarmed, however
        it left it.
        """
        ...

    def reviews(self, number: int) -> tuple[Review, ...]:
        """The submitted reviews on pull request *number*.

        Only verdicts arrive (approved, changes requested, commented);
        drafts never do. Complete or raising, like every listing.
        """
        ...

    def schedule_events(self, number: int) -> tuple[ScheduleEvent, ...]:
        """The merge-scheduling history of pull request *number*.

        Oldest first. The record that shows a created-then-lost
        schedule. Raises livery.forge.Unsupported on a forge without
        the ``schedule_events`` capability; ask
        livery.forge.Forge.supports first.
        """
        ...

    def comment(self, number: int, body: str) -> None:
        """Post *body* as a comment on pull request *number*.

        The evidence channel: conflict reports and failure summaries
        that the pull request body cannot carry land here.
        """
        ...


class Checks(Protocol):
    """One repository's CI: the combined verdict, runs, jobs, and logs.

    GitHub and Gitea run workflows, GitLab runs pipelines. A run here
    is either; livery.forge.Run says how the two map.
    """

    def status(self, sha: str) -> CombinedStatus:
        """The one CI verdict for commit *sha*.

        Polling this is the observation model: a workflow that armed a
        merge watches the verdict here and observes the merge through
        livery.forge.PullRequests.get. A commit nothing has reported
        for answers ``none``, never ``pending``.
        """
        ...

    def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
        """The repository's runs, newest first.

        *head_sha* and *event* filter when given. The tuple is
        complete for the query or the call raises; a truncated listing
        is never returned as the answer.
        """
        ...

    def jobs(self, run: int) -> tuple[Job, ...]:
        """Every job of run *run*."""
        ...

    def job_log(self, job: int) -> str:
        """The raw log text of job *job*.

        The failure evidence: triage reads this, quotes it verbatim,
        and never reduces it to a boolean.
        """
        ...

    def rerun(self, run: int, *, failed_only: bool = True) -> None:
        """Re-run *run*: its failed jobs, or all of them.

        ``failed_only`` is the cheap and usually correct choice: a
        green matrix leg does not need re-running to re-test a red
        one. Raises livery.forge.ForgeError while the run is still in
        progress, because there is nothing to re-run yet.
        """
        ...

    def cancel_run(self, run: int, *, force: bool = False) -> None:
        """Cancel *run* and its jobs.

        Required on every forge: superseded pushes cancel their stale
        runs, a dispatch storm is withdrawn, a starving queue is
        relieved by this verb instead of a wait. Raises
        livery.forge.ForgeError when the run is already terminal, so a
        caller re-running its workflow probes the run's status first.

        ``force=True`` marks the jobs cancelled immediately and
        discards later runner reports, for a run whose runner stopped
        answering. It is capability-gated: a forge without
        ``force_cancel`` raises livery.forge.Unsupported naming the
        capability, and plain cancel remains available.
        """
        ...

    def dispatch(
        self, workflow: str, *, ref: str, inputs: Mapping[str, str] | None = None
    ) -> None:
        """Trigger *workflow* on *ref*.

        *workflow* is the workflow file name as GitHub and Gitea
        address one. GitLab has one pipeline definition per
        repository: its backend triggers the pipeline on *ref* and
        passes *workflow* through as a variable for the pipeline's
        rules to route on.
        """
        ...


class Releases(Protocol):
    """One repository's releases, addressed by tag."""

    def create(
        self, tag: str, *, name: str, body: str = "", prerelease: bool = False
    ) -> Release:
        """Create the release for *tag* and return it.

        Raises livery.forge.ForgeError when *tag* already has a
        release. A re-run therefore probes with
        livery.forge.Releases.get first and skips the create: that
        pair is the idempotent whole.
        """
        ...

    def get(self, tag: str) -> Release | None:
        """The release for *tag*, or None when the tag has none.

        The probe that makes release creation safe to re-run.
        """
        ...


class Issues(Protocol):
    """One repository's issues: text in both directions.

    An issue's body is carried in full on create, get, list, and
    search, because the body is the work order in issue-driven
    workflows and the marker text in deduplication probes.
    """

    def create(
        self,
        title: str,
        *,
        body: str = "",
        labels: tuple[str, ...] = (),
        assignee: str = "",
    ) -> Issue:
        """Open an issue and return it.

        *labels* are label names; ensure they exist first with
        livery.forge.Repository.configure. A creator that must not
        file duplicates searches for its marker text with
        livery.forge.Issues.search before creating.
        """
        ...

    def get(self, number: int) -> Issue | None:
        """The issue *number*, body included, or None when it does not exist.

        The existence probe every write to an issue runs first: the
        subject of a write is verified, never assumed.
        """
        ...

    def list(self, *, state: StateFilter = "open") -> tuple[Issue, ...]:
        """The repository's issues in *state*. Pull requests never appear."""
        ...

    def search(
        self,
        text: str,
        *,
        state: StateFilter = "open",
        labels: tuple[str, ...] = (),
    ) -> tuple[Issue, ...]:
        """The issues whose title or body contains *text*.

        *labels* narrows to issues carrying every named label. The
        deduplication probe: an unattended creator searches for its
        marker text and label before it files.
        """
        ...

    def assign(self, number: int, assignee: str) -> None:
        """Add *assignee* to the issue's assignees.

        Adds rather than replaces, so a colleague's assignment
        survives; how many assignees an issue may carry is workspace
        policy, enforced by the caller, because the forges' own
        limits differ (GitLab's free tier carries one).
        """
        ...

    def unassign(self, number: int) -> None:
        """Remove the authenticated user from the issue's assignees.

        Only the caller's own assignment: a colleague's stays. Not
        being assigned is a no-op, so re-running is the recovery.
        """
        ...

    def assigned_to_me(self) -> tuple[Issue, ...]:
        """The open issues assigned to the authenticated user."""
        ...

    def comment(self, number: int, body: str) -> None:
        """Post *body* as a comment on issue *number*."""
        ...

    def close(self, number: int) -> None:
        """Close issue *number*; closing a closed issue is a no-op.

        Idempotent so re-running is the recovery. The reason belongs
        in a comment posted before the close: every forge keeps the
        thread, none has a first-class close reason worth abstracting.
        """
        ...


class Repository(Protocol):
    """One repository on one forge.

    A cheap value bound to the owner and name, obtained from
    livery.forge.Forge.repository. The operation groups mirror the
    workflow vocabulary: ``pr.open``, ``checks.status``,
    ``issue.search``, ``release.get``.
    """

    @property
    def owner(self) -> str:
        """The user, organisation, or group path that owns the repository."""
        ...

    @property
    def name(self) -> str:
        """The repository name within the owner."""
        ...

    @property
    def pr(self) -> PullRequests:
        """The pull request operations."""
        ...

    @property
    def checks(self) -> Checks:
        """The CI operations."""
        ...

    @property
    def issue(self) -> Issues:
        """The issue operations."""
        ...

    @property
    def release(self) -> Releases:
        """The release operations."""
        ...

    def configure(self, config: RepoConfig) -> None:
        """Assert the repository settings *config* states.

        Idempotent drift repair: fields left None are untouched,
        stated fields are made true whether or not they already were.
        Project birth and release aftercare run the same call.

        Raises livery.forge.Unsupported when a stated field needs a
        capability the forge declines by name, ``required_contexts``
        being the known case.
        """
        ...

    def tags(self) -> tuple[str, ...]:
        """Every tag name on the repository.

        Complete or raising, like every listing: release trains probe
        "does this tag exist" here, and a truncated answer would turn
        that probe into a guess.
        """
        ...

    def branch_exists(self, branch: str) -> bool:
        """Whether *branch* exists on the repository."""
        ...

    def protection(self, branch: str) -> Protection | None:
        """The protection configured for *branch*, or None when none is.

        The read side of livery.forge.RepoConfig, normalised per
        livery.forge.Protection: a flag the forge cannot express reads
        as its inert value. Reading protection may need an
        administrating token on some forges; a refusal raises
        livery.forge.ForgeError for the caller to degrade on.
        """
        ...

    def web_url(self) -> str:
        """The repository's home page address.

        Every ``*_url`` method builds strings from what the backend
        already knows; nothing goes on the wire, and the address is
        not probed for existence.
        """
        ...

    def pr_url(self, number: int) -> str:
        """The address of pull request *number*."""
        ...

    def issue_url(self, number: int) -> str:
        """The address of issue *number*."""
        ...

    def commit_url(self, sha: str) -> str:
        """The address of commit *sha*."""
        ...

    def compare_url(self, base: str, head: str) -> str:
        """The address comparing *base* to *head* (refs or shas)."""
        ...

    def tag_url(self, tag: str) -> str:
        """The address of *tag*'s release or tag view."""
        ...

    def delete_branch(self, branch: str) -> None:
        """Delete *branch* from the repository.

        Idempotent: a branch already gone is success, not an error.
        The abort path's cleanup; merge-path deletion is repository
        configuration (``delete_branch_on_merge``), not this call.
        """
        ...


class Forge(Protocol):
    """One forge server: identity, capabilities, and its repositories.

    Construction is a backend concern, not part of the protocol. Each
    backend states the server and resolves its token once, and a token
    belongs to exactly one host: a foreign host is never sent it.
    """

    def whoami(self) -> str:
        """The authenticated user's login name.

        The first probe of anything unattended: a wrong-host token
        dies here, not mid-workflow.
        """
        ...

    def server_version(self) -> str:
        """The server's version string.

        Backends with a version floor check it here and raise
        livery.forge.Unsupported naming the version when the server
        predates an operation.
        """
        ...

    def supports(self, capability: Capability) -> bool:
        """Whether this forge offers *capability*.

        The honesty valve: a caller that can degrade asks here first,
        and a backend never pretends. The names are the
        livery.forge.Capability vocabulary.
        """
        ...

    def repository(self, owner: str, name: str) -> Repository:
        """The view onto one repository. Cheap, no network."""
        ...

    def members(self, owner: str) -> tuple[str, ...]:
        """The login names under *owner*, an organisation or group.

        A personal namespace answers exactly its one login: the
        governance declarations validate against this listing, and a
        solo repository's only member is its owner.
        """
        ...

    def teams(self, owner: str) -> tuple[str, ...]:
        """The team names under *owner*, as the forge spells them.

        GitHub and Gitea answer team slugs; GitLab's teams are its
        subgroups and it answers group paths. A personal namespace
        has none and answers empty.
        """
        ...

    def codeowners(self, entries: tuple[CodeownersEntry, ...]) -> Codeowners:
        """Render *entries* as this forge's codeowners file.

        Pure string building, nothing on the wire: the canonical
        location and syntax are the forge's, the declarations are
        neutral. What a dialect cannot express (a per-path approval
        count outside GitLab's sections) is approximated and named
        in the result's notes, never dropped silently.
        """
        ...

    def user_url(self, login: str) -> str:
        """The address of *login*'s profile page.

        String building from the server the backend is bound to;
        nothing goes on the wire and the login is not verified.
        """
        ...

    def create_repo(
        self,
        owner: str,
        name: str,
        *,
        private: bool = True,
        description: str = "",
    ) -> Repository:
        """Create the repository and return its view.

        The repository is initialised with a default branch. Raises
        livery.forge.ForgeError when it already exists; ensure-exists
        callers probe with livery.forge.Forge.get_repo first. On
        GitLab, *owner* may be a group path.
        """
        ...

    def get_repo(self, owner: str, name: str) -> RepoInfo | None:
        """The repository's settings, or None when it does not exist.

        The probe that makes repository creation safe to re-run.
        """
        ...

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete the repository.

        Idempotent: a repository already gone is success, not an
        error.
        """
        ...


class Registry(Protocol):
    """One package index, apart from the forge.

    A forge and a registry are only sometimes the same server, so
    "which versions of this name are published" is its own one-method
    interface, with a backend per ecosystem.
    """

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of package *name*, oldest first.

        An unpublished name answers the empty tuple; an unreachable
        index raises livery.forge.ForgeError. The release train's
        "does the index see it yet" probe.
        """
        ...
