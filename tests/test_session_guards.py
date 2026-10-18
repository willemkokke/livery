"""Every test session keeps its guards, in the workspace and in a release leg.

footman's pytest plugin points the runner's directories away and guards
the task registry, livery-git's gives git a config of the session's own,
and the workshop's guards ride footman's ``footman.test-guards`` group.
A release leg installs one member's wheel with its ``test`` extra and
what those require, so a member that tests against the workshop must
reach footman and livery-git through its requirements; and a project
with footman alone still gets footman's guards.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytest_plugins = ("pytester",)

ROOT = Path(__file__).resolve().parents[1]

#: The name a requirement starts with.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _normal(name: str) -> str:
    """A distribution name as an index compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements(lines: list[str]) -> set[str]:
    """The distributions a list of requirement lines names."""
    found: set[str] = set()
    for line in lines:
        match = _NAME.match(line.strip())
        if match:
            found.add(_normal(match.group(0)))
    return found


def _members() -> dict[str, tuple[set[str], set[str]]]:
    """Each member's distribution, to what it requires and what its test extra adds."""
    manifests = [*ROOT.glob("packages/*/pyproject.toml")]
    manifests += ROOT.glob("packages/*/*/pyproject.toml")
    members: dict[str, tuple[set[str], set[str]]] = {}
    for manifest in manifests:
        project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
        extras = project.get("optional-dependencies", {})
        members[_normal(project["name"])] = (
            _requirements(project.get("dependencies", [])),
            _requirements(extras.get("test", [])),
        )
    return members


def _leg(member: str, members: dict[str, tuple[set[str], set[str]]]) -> set[str]:
    """The members a leg of *member* installs: it, its test extra, what they require."""
    runtime, test = members[member]
    pending = [member, *runtime, *test]
    installed: set[str] = set()
    while pending:
        name = pending.pop()
        if name in installed or name not in members:
            continue
        installed.add(name)
        pending.extend(members[name][0])
    return installed


def test_every_leg_that_installs_the_workshop_installs_footman_and_git() -> None:
    members = _members()
    legs = {member: _leg(member, members) for member in members}
    with_workshop = sorted(m for m, leg in legs.items() if "livery-workshop" in leg)
    assert "livery-extensions-lfs" in with_workshop
    missing = [
        member
        for member in with_workshop
        if not {"livery-footman", "livery-git"} <= legs[member]
    ]
    assert missing == []


def test_a_session_without_livery_git_keeps_footman_s_guards_and_no_git_config(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A project with footman and no livery-git: the session points the
    # runner's directories away and fails a test that leaks into the
    # registry, and leaves git's global config as the outer environment
    # set it, here unset.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    for suffix in ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR"):
        monkeypatch.delenv(f"FOOTMAN_{suffix}", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    cache_home = str(tmp_path / "cache-home")
    pytester.makepyfile(
        f"""
        import os
        from pathlib import Path

        from livery.footman import root_group

        def test_directories_away_and_no_git_config():
            assert "GIT_CONFIG_GLOBAL" not in os.environ
            for suffix in ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR"):
                value = Path(os.environ[f"FOOTMAN_{{suffix}}"])
                assert value.is_dir() and value.is_relative_to({cache_home!r})

        def test_leaks():
            root_group.tasks["leaked"] = object()
        """
    )
    base = pytester.path / "basetemp"
    result = pytester.runpytest(
        "-p", "no:cacheprovider", "-p", "no:livery-git-isolation", f"--basetemp={base}"
    )
    result.assert_outcomes(passed=2, errors=1)
