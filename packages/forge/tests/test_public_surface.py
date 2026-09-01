"""The public surface is what each __init__ declares; everything else is private."""

from __future__ import annotations

from pathlib import Path

import livery.forge
import livery.forge.testing


def test_the_surface_is_declared() -> None:
    assert livery.forge.__all__ == [
        "Capability",
        "CheckState",
        "Checks",
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
        "StateFilter",
        "Unsupported",
        "__version__",
        "gitea_configured_host",
        "gitea_is_configured_host",
        "gitlab_configured_host",
        "gitlab_is_configured_host",
    ]


def test_the_testing_surface_is_declared() -> None:
    assert livery.forge.testing.__all__ == [
        "FORMAT",
        "REDACTED",
        "SCENARIOS",
        "VOLATILE",
        "Cassette",
        "CassetteError",
        "Exchange",
        "FakeDriver",
        "FakeForge",
        "Faults",
        "ForgeDriver",
        "Outcome",
        "RecordingOpener",
        "ReplayOpener",
        "Scenario",
        "UrlOpener",
    ]


def test_every_other_module_is_underscore_named() -> None:
    package = Path(livery.forge.__file__).parent
    for module in package.rglob("*.py"):
        if module.name != "__init__.py":
            assert module.name.startswith("_"), (
                f"{module.name} is public by name: modules are private, "
                "and public names are re-exported by __init__"
            )
