"""The values the protocol speaks: literal vocabularies and frozen records.

Every record is immutable and forge-neutral. A backend translates its
server's shapes into these at the boundary and never lets a native
field leak through. Numbers identify pull requests, issues, runs, and
jobs; each kind numbers its own space, so a pull request number must
never be used as an issue number or the reverse. GitLab keeps the
spaces separate, so the protocol does too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

Capability: TypeAlias = Literal[
    "auto_merge", "force_cancel", "required_contexts", "ci_secrets"
]
"""What livery.forge.Forge.supports answers for, by name.

- ``auto_merge``: the forge can schedule a merge that fires when the
  checks go green (livery.forge.PullRequests.arm).
- ``force_cancel``: the forge can cancel a run whose runner stopped
  answering (``force=True`` on livery.forge.Checks.cancel_run).
- ``required_contexts``: branch protection can name the check contexts
  that must pass before a merge.
- ``ci_secrets``: livery.forge.RepoConfig.secrets can be stored
  through the backend. GitHub's backend declines: its secrets API
  demands sealed-box encryption the standard library cannot provide,
  and no workflow stores a secret there, because trusted publishing
  replaces tokens on GitHub.
"""

CheckState: TypeAlias = Literal["none", "pending", "success", "failure"]
"""The combined verdict for one commit.

``none`` means nothing has reported for the commit at all, which must
stay distinguishable from ``pending``: a commit no run ever started on
looks identical to a live run otherwise, and a poll loop would wait on
it forever.
"""

RunStatus: TypeAlias = Literal["queued", "running", "completed"]
"""Where a run is in its life. Only ``completed`` runs carry a conclusion."""

Conclusion: TypeAlias = Literal["", "success", "failure", "cancelled", "skipped"]
"""How a completed run or job ended. Empty until the run completes."""

StateFilter: TypeAlias = Literal["open", "closed", "all"]
"""Which pull requests or issues a listing includes."""

ItemState: TypeAlias = Literal["open", "closed"]
"""Where a pull request or issue is: open, or closed in any way."""


@dataclass(frozen=True)
class RepoInfo:
    """What livery.forge.Forge.get_repo reports about an existing repository.

    Attributes:
        owner: The user, organisation, or group path that owns the
            repository.
        name: The repository name within the owner.
        default_branch: The branch pull requests target by default.
        private: True when the repository is not publicly visible.
    """

    owner: str
    name: str
    default_branch: str
    private: bool


@dataclass(frozen=True)
class Label:
    """One issue label, spoken by name everywhere.

    The protocol never exposes label ids: backends that key labels by
    id resolve the name at the boundary.

    Attributes:
        name: The label text, unique within the repository.
        color: Six hex digits, no leading ``#``.
        description: One line saying when the label applies.
    """

    name: str
    color: str
    description: str = ""


@dataclass(frozen=True)
class RepoConfig:
    """The desired repository settings livery.forge.Repository.configure asserts.

    Every field defaults to None, which means "leave this setting as it
    is". Configure applies only the fields a caller states, so one
    config value can repair one drifted setting without touching the
    rest.

    Attributes:
        default_branch: The branch pull requests target by default.
        squash_only: True allows only squash merges.
        delete_branch_on_merge: True deletes a pull request's head
            branch when it merges.
        allow_auto_merge: True lets a merge be scheduled to fire when
            the checks go green.
        required_contexts: The check contexts the default branch's
            protection requires before a merge. Setting this on a
            forge without the ``required_contexts`` capability raises
            livery.forge.Unsupported.
        secrets: CI secrets to store, by name. Write-only: no protocol
            operation reads a secret back. Setting this on a forge
            without the ``ci_secrets`` capability raises
            livery.forge.Unsupported.
        variables: Plain CI variables to store, by name.
        labels: The labels the repository must offer. Labels already
            present keep their issues; labels absent from this tuple
            are left alone, never deleted.
    """

    default_branch: str | None = None
    squash_only: bool | None = None
    delete_branch_on_merge: bool | None = None
    allow_auto_merge: bool | None = None
    required_contexts: tuple[str, ...] | None = None
    secrets: Mapping[str, str] | None = None
    variables: Mapping[str, str] | None = None
    labels: tuple[Label, ...] | None = None


@dataclass(frozen=True)
class PullRequest:
    """One pull request, as the forge reports it.

    Attributes:
        number: The number a person sees in the forge's own UI. On
            GitLab this is the merge request iid, never the global id.
        title: The pull request title.
        body: The pull request description, in full.
        state: ``open`` or ``closed``. A merged pull request is
            ``closed`` with ``merged`` True.
        merged: True when the pull request has merged.
        head_branch: The branch the pull request proposes. May be empty
            on a merged pull request whose head branch was deleted;
            find a merged pull request by
            livery.forge.PullRequests.find_by_head_sha instead.
        head_sha: The head commit. Persists after the branch is gone.
        base_branch: The branch the pull request targets.
        url: The pull request's page, for printing to a person.
    """

    number: int
    title: str
    body: str
    state: ItemState
    merged: bool
    head_branch: str
    head_sha: str
    base_branch: str
    url: str = ""


@dataclass(frozen=True)
class CombinedStatus:
    """The one CI answer for a commit, whatever the forge calls it underneath.

    GitHub folds check runs and commit statuses into it, Gitea
    aggregates commit statuses, GitLab reports the commit's latest
    pipeline. The caller sees one verdict either way.

    Attributes:
        state: The combined verdict. ``none`` when nothing has
            reported for the commit, which is not ``pending``: see
            livery.forge.CheckState.
        contexts: How many checks the verdict aggregates. Zero exactly
            when ``state`` is ``none``.
    """

    state: CheckState
    contexts: int


@dataclass(frozen=True)
class Run:
    """One CI run: a workflow run on GitHub or Gitea, a pipeline on GitLab.

    Attributes:
        id: The identifier every ``livery.forge.Checks`` method takes.
            Backends that also number runs per repository translate;
            the protocol speaks one handle.
        workflow: The workflow that ran, as the forge names it. A
            workflow file name on GitHub and Gitea; empty on GitLab,
            which has one pipeline definition per repository.
        head_sha: The commit the run checked.
        event: What triggered the run, in the forge's own vocabulary.
        status: Where the run is in its life.
        conclusion: How the run ended. Empty until ``status`` is
            ``completed``.
        url: The run's page, for printing to a person.
    """

    id: int
    workflow: str
    head_sha: str
    event: str
    status: RunStatus
    conclusion: Conclusion
    url: str = ""


@dataclass(frozen=True)
class Job:
    """One job of a run.

    Attributes:
        id: The identifier livery.forge.Checks.job_log takes.
        name: The job name as the workflow declares it.
        status: Where the job is in its life.
        conclusion: How the job ended. Empty until ``status`` is
            ``completed``.
    """

    id: int
    name: str
    status: RunStatus
    conclusion: Conclusion


@dataclass(frozen=True)
class Release:
    """One release, addressed by its tag.

    Attributes:
        tag: The tag the release describes. The tag is the identity;
            the release is its prose.
        name: The display name.
        body: The release notes, in full.
        prerelease: True when the release is marked as a prerelease.
        url: The release's page, for printing to a person.
    """

    tag: str
    name: str
    body: str
    prerelease: bool = False
    url: str = ""


@dataclass(frozen=True)
class Issue:
    """One issue, body included.

    Attributes:
        number: The number a person sees in the forge's own UI. On
            GitLab this is the issue iid, never the global id.
        title: The issue title.
        body: The issue text, in full. The body is the work order for
            issue-driven workflows, so it is always carried, never
            truncated.
        state: ``open`` or ``closed``.
        labels: The label names on the issue.
        assignees: The login names assigned to the issue.
        url: The issue's page, for printing to a person.
    """

    number: int
    title: str
    body: str
    state: ItemState
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    url: str = ""
