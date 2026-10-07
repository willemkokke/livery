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

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so a program
# that mounts the forge's tasks, or reads one type, never pays for the
# three backends it does not touch.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.forge import testing as testing
    from livery.forge._errors import ForgeError as ForgeError
    from livery.forge._errors import RateLimited as RateLimited
    from livery.forge._errors import Unsupported as Unsupported
    from livery.forge._gitea import GiteaForge as GiteaForge
    from livery.forge._gitea import gitea_configured_host as gitea_configured_host
    from livery.forge._gitea import gitea_is_configured_host as gitea_is_configured_host
    from livery.forge._github import GithubForge as GithubForge
    from livery.forge._gitlab import GitlabForge as GitlabForge
    from livery.forge._gitlab import gitlab_configured_host as gitlab_configured_host
    from livery.forge._gitlab import (
        gitlab_is_configured_host as gitlab_is_configured_host,
    )
    from livery.forge._merge_state import MergeCategory as MergeCategory
    from livery.forge._merge_state import MergeState as MergeState
    from livery.forge._merge_state import (
        classify_gitea_merge_refusal as classify_gitea_merge_refusal,
    )
    from livery.forge._merge_state import (
        classify_github_mergeable_state as classify_github_mergeable_state,
    )
    from livery.forge._merge_state import (
        classify_gitlab_detailed_status as classify_gitlab_detailed_status,
    )
    from livery.forge._merge_state import (
        classify_merge_refusal as classify_merge_refusal,
    )
    from livery.forge._merge_state import merge_state as merge_state
    from livery.forge._protocol import Checks as Checks
    from livery.forge._protocol import Forge as Forge
    from livery.forge._protocol import Issues as Issues
    from livery.forge._protocol import PullRequests as PullRequests
    from livery.forge._protocol import Registry as Registry
    from livery.forge._protocol import Releases as Releases
    from livery.forge._protocol import Repository as Repository
    from livery.forge._protocol import Schedules as Schedules
    from livery.forge._registry import SimpleRegistry as SimpleRegistry
    from livery.forge._types import Asset as Asset
    from livery.forge._types import Capability as Capability
    from livery.forge._types import CheckState as CheckState
    from livery.forge._types import Codeowners as Codeowners
    from livery.forge._types import CodeownersEntry as CodeownersEntry
    from livery.forge._types import CombinedStatus as CombinedStatus
    from livery.forge._types import Comment as Comment
    from livery.forge._types import Conclusion as Conclusion
    from livery.forge._types import Issue as Issue
    from livery.forge._types import Job as Job
    from livery.forge._types import Label as Label
    from livery.forge._types import Protection as Protection
    from livery.forge._types import PullRequest as PullRequest
    from livery.forge._types import RateBudget as RateBudget
    from livery.forge._types import RegistryKind as RegistryKind
    from livery.forge._types import Release as Release
    from livery.forge._types import RepoConfig as RepoConfig
    from livery.forge._types import RepoInfo as RepoInfo
    from livery.forge._types import Review as Review
    from livery.forge._types import ReviewState as ReviewState
    from livery.forge._types import Run as Run
    from livery.forge._types import RunStatus as RunStatus
    from livery.forge._types import Schedule as Schedule
    from livery.forge._types import ScheduleEvent as ScheduleEvent
    from livery.forge._types import ScheduleEventKind as ScheduleEventKind
    from livery.forge._types import StateFilter as StateFilter
    from livery.forge._types import Step as Step

__version__ = "0.5.0"

__all__ = [
    "Asset",
    "Capability",
    "CheckState",
    "Checks",
    "Codeowners",
    "CodeownersEntry",
    "CombinedStatus",
    "Comment",
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
    "MergeCategory",
    "MergeState",
    "Protection",
    "PullRequest",
    "PullRequests",
    "RateBudget",
    "RateLimited",
    "Registry",
    "RegistryKind",
    "Release",
    "Releases",
    "RepoConfig",
    "RepoInfo",
    "Repository",
    "Review",
    "ReviewState",
    "Run",
    "RunStatus",
    "Schedule",
    "ScheduleEvent",
    "ScheduleEventKind",
    "Schedules",
    "SimpleRegistry",
    "StateFilter",
    "Step",
    "Unsupported",
    "__version__",
    "classify_gitea_merge_refusal",
    "classify_github_mergeable_state",
    "classify_gitlab_detailed_status",
    "classify_merge_refusal",
    "gitea_configured_host",
    "gitea_is_configured_host",
    "gitlab_configured_host",
    "gitlab_is_configured_host",
    "merge_state",
    "testing",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "Asset": "livery.forge._types",
    "Capability": "livery.forge._types",
    "CheckState": "livery.forge._types",
    "Checks": "livery.forge._protocol",
    "Codeowners": "livery.forge._types",
    "CodeownersEntry": "livery.forge._types",
    "CombinedStatus": "livery.forge._types",
    "Comment": "livery.forge._types",
    "Conclusion": "livery.forge._types",
    "Forge": "livery.forge._protocol",
    "ForgeError": "livery.forge._errors",
    "GiteaForge": "livery.forge._gitea",
    "GithubForge": "livery.forge._github",
    "GitlabForge": "livery.forge._gitlab",
    "Issue": "livery.forge._types",
    "Issues": "livery.forge._protocol",
    "Job": "livery.forge._types",
    "Label": "livery.forge._types",
    "MergeCategory": "livery.forge._merge_state",
    "MergeState": "livery.forge._merge_state",
    "Protection": "livery.forge._types",
    "PullRequest": "livery.forge._types",
    "PullRequests": "livery.forge._protocol",
    "Registry": "livery.forge._protocol",
    "RegistryKind": "livery.forge._types",
    "Release": "livery.forge._types",
    "Releases": "livery.forge._protocol",
    "RepoConfig": "livery.forge._types",
    "RepoInfo": "livery.forge._types",
    "RateBudget": "livery.forge._types",
    "RateLimited": "livery.forge._errors",
    "Repository": "livery.forge._protocol",
    "Review": "livery.forge._types",
    "ReviewState": "livery.forge._types",
    "Run": "livery.forge._types",
    "RunStatus": "livery.forge._types",
    "Schedule": "livery.forge._types",
    "ScheduleEvent": "livery.forge._types",
    "ScheduleEventKind": "livery.forge._types",
    "Schedules": "livery.forge._protocol",
    "SimpleRegistry": "livery.forge._registry",
    "StateFilter": "livery.forge._types",
    "Step": "livery.forge._types",
    "Unsupported": "livery.forge._errors",
    "classify_gitea_merge_refusal": "livery.forge._merge_state",
    "classify_github_mergeable_state": "livery.forge._merge_state",
    "classify_gitlab_detailed_status": "livery.forge._merge_state",
    "classify_merge_refusal": "livery.forge._merge_state",
    "gitea_configured_host": "livery.forge._gitea",
    "gitea_is_configured_host": "livery.forge._gitea",
    "gitlab_configured_host": "livery.forge._gitlab",
    "gitlab_is_configured_host": "livery.forge._gitlab",
    "merge_state": "livery.forge._merge_state",
}


#: The public packages beneath the root, served whole on first use.
_PACKAGES = ("testing",)


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    import importlib

    if name in _PACKAGES:
        value: object = importlib.import_module(f"livery.forge.{name}")
    else:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module 'livery.forge' has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
