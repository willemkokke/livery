"""One interface to GitHub, Gitea, and GitLab.

The protocols are the whole surface: livery.forge.Forge for one
server, livery.forge.Repository for one repository on it, and
livery.forge.Registry for one package index. Every verb exists because
a development workflow uses it; a verb no workflow uses is removed.
The protocols are a draft until every backend passes the one
conformance suite in livery.forge.testing; then they freeze.

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
from livery.forge._protocol import (
    Checks,
    Forge,
    Issues,
    PullRequests,
    Registry,
    Releases,
    Repository,
)
from livery.forge._types import (
    Capability,
    CheckState,
    CombinedStatus,
    Conclusion,
    Issue,
    Job,
    Label,
    PullRequest,
    Release,
    RepoConfig,
    RepoInfo,
    Run,
    RunStatus,
    StateFilter,
)

__version__ = "0.0.1"

__all__ = [
    "Capability",
    "CheckState",
    "Checks",
    "CombinedStatus",
    "Conclusion",
    "Forge",
    "ForgeError",
    "GiteaForge",
    "Issue",
    "Issues",
    "Job",
    "Label",
    "PullRequest",
    "PullRequests",
    "Registry",
    "Release",
    "Releases",
    "RepoConfig",
    "RepoInfo",
    "Repository",
    "Run",
    "RunStatus",
    "StateFilter",
    "Unsupported",
    "__version__",
    "gitea_configured_host",
    "gitea_is_configured_host",
]
