"""One interface to GitHub, Gitea, and GitLab.

The protocols are the whole surface: livery.forge.Forge for one
server, livery.forge.Repository for one repository on it, and
livery.forge.Registry for one package index. Every verb exists because
a development workflow uses it; a verb no workflow uses is removed.
The protocols are frozen: every backend, the verified fake included,
passes the one conformance suite in livery.forge.testing, and a change
to a verb now is a compatibility event, not a draft edit.

Runtime dependencies are the standard library and nothing else; a
workspace test enforces it.
"""

from __future__ import annotations

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._gitea import (
    GiteaForge,
    gitea_configured_host,
    gitea_is_configured_host,
)
from livery.forge._github import GithubForge
from livery.forge._gitlab import (
    GitlabForge,
    gitlab_configured_host,
    gitlab_is_configured_host,
)
from livery.forge._protocol import (
    Checks,
    Forge,
    Issues,
    PullRequests,
    Registry,
    Releases,
    Repository,
)
from livery.forge._registry import SimpleRegistry
from livery.forge._types import (
    Capability,
    CheckState,
    Codeowners,
    CodeownersEntry,
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
    RunStatus,
    ScheduleEvent,
    ScheduleEventKind,
    StateFilter,
)

__version__ = "0.1.0"

__all__ = [
    "Capability",
    "CheckState",
    "Checks",
    "Codeowners",
    "CodeownersEntry",
    "CombinedStatus",
    "Conclusion",
    "Forge",
    "ForgeError",
    "GiteaForge",
    "GithubForge",
    "GitlabForge",
    "Issue",
    "Issues",
    "Job",
    "Label",
    "Protection",
    "PullRequest",
    "PullRequests",
    "Registry",
    "Release",
    "Releases",
    "RepoConfig",
    "RepoInfo",
    "Repository",
    "Review",
    "ReviewState",
    "Run",
    "RunStatus",
    "ScheduleEvent",
    "ScheduleEventKind",
    "SimpleRegistry",
    "StateFilter",
    "Unsupported",
    "__version__",
    "gitea_configured_host",
    "gitea_is_configured_host",
    "gitlab_configured_host",
    "gitlab_is_configured_host",
]
