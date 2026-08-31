"""Resolve this workspace's repository on its forge.

The workspace contract's ``[forge]`` table names the kind and owner;
the repository name comes from the ``origin`` remote. Tokens resolve
the way each backend's ``connect`` documents (``GITHUB_TOKEN`` and
``gh auth token`` for GitHub, the corresponding variables for Gitea
and GitLab). Nothing here is inferred from ambient state beyond
those documented lookups.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from footman import fail

from livery.forge import Forge, GiteaForge, GithubForge, GitlabForge, Repository

_REMOTE_RE = re.compile(
    r"(?:https://|git@)(?P<host>[^/:]+)[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$"
)


def remote_repo_name(root: Path) -> str:
    """The repository name the ``origin`` remote points at."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"no origin remote:\n{result.stdout}{result.stderr}")
    match = _REMOTE_RE.search(result.stdout.strip())
    if match is None:
        fail(f"cannot read owner/name from origin url {result.stdout.strip()!r}")
    return match.group("name")


def this_forge(root: Path) -> Forge:
    """The workspace's forge, per the contract's ``[forge]`` table."""
    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    forge_table = contract.get("forge") or {}
    kind = str(forge_table.get("kind", ""))
    url = str(forge_table.get("url", ""))
    if not kind:
        fail("livery.toml [forge] must carry kind and owner")
    if kind == "github":
        return GithubForge.connect(url=url)
    if kind == "gitea":
        return GiteaForge.connect(url=url)
    if kind == "gitlab":
        return GitlabForge.connect(url=url)
    fail(f"unknown forge kind {kind!r}: use github, gitea, or gitlab")


def this_repository(root: Path) -> Repository:
    """The workspace's repository, per the contract and the remote."""
    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    owner = str((contract.get("forge") or {}).get("owner", ""))
    if not owner:
        fail("livery.toml [forge] must carry kind and owner")
    return this_forge(root).repository(owner, remote_repo_name(root))
