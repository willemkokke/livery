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
import tomllib
from pathlib import Path

import toolroom
from footman import fail

from livery.forge import Forge, GiteaForge, GithubForge, GitlabForge, Repository

_REMOTE_RE = re.compile(
    r"(?:https://|git@)(?P<host>[^/:]+)[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$"
)


def remote_repo_name(root: Path) -> str:
    """The repository name the ``origin`` remote points at."""
    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "remote", "get-url", "origin"
    )
    if result.code != 0:
        fail(f"no origin remote:\n{result.stdout}{result.stderr}")
    match = _REMOTE_RE.search(result.stdout.strip())
    if match is None:
        fail(f"cannot read owner/name from origin url {result.stdout.strip()!r}")
    return match.group("name")


def this_forge(root: Path) -> Forge:
    """The workspace's forge, per the contract's ``[forge]`` table."""
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    forge_table = contract.get("forge") or {}
    kind = str(forge_table.get("kind", ""))
    url = str(forge_table.get("url", ""))
    if not kind:
        fail("workshop.toml [forge] must carry kind and owner")
    if kind == "github":
        return GithubForge.connect(url=url)
    if kind == "gitea":
        return GiteaForge.connect(url=url)
    if kind == "gitlab":
        return GitlabForge.connect(url=url)
    fail(f"unknown forge kind {kind!r}: use github, gitea, or gitlab")


_ADMIN_VARS = {
    "github": "GITHUB_ADMIN_TOKEN",
    "gitea": "GITEA_ADMIN_TOKEN",
    "gitlab": "GITLAB_ADMIN_TOKEN",
}


def admin_forge(root: Path) -> tuple[Forge, str]:
    """The forge for an admin verb, and the variable that armed it.

    Least privilege by split tokens: the everyday verbs never read
    the admin variable, and the admin verbs (configure, the
    post-abort reconcile) resolve admin-first with a fallback to the
    everyday token; the fallback keeps a solo developer whose one
    token already administers working with nothing extra. The second
    value names the admin variable used, "" for the fallback, so a
    refusal can teach the missing grant.
    """
    import os

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    table = contract.get("forge") or {}
    kind = str(table.get("kind", ""))
    url = str(table.get("url", ""))
    var = _ADMIN_VARS.get(kind, "")
    token = os.environ.get(var, "") if var else ""
    if not token:
        return this_forge(root), ""
    if kind == "github":
        return GithubForge.connect(url=url, token=token), var
    if kind == "gitea":
        return GiteaForge.connect(url=url, token=token), var
    return GitlabForge.connect(url=url, token=token), var


def admin_repository(root: Path) -> tuple[Repository, str]:
    """The repository bound to the admin ladder's forge."""
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    owner = str((contract.get("forge") or {}).get("owner", ""))
    if not owner:
        fail("workshop.toml [forge] must carry kind and owner")
    forge, var = admin_forge(root)
    return forge.repository(owner, remote_repo_name(root)), var


def this_repository(root: Path) -> Repository:
    """The workspace's repository, per the contract and the remote."""
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    owner = str((contract.get("forge") or {}).get("owner", ""))
    if not owner:
        fail("workshop.toml [forge] must carry kind and owner")
    return this_forge(root).repository(owner, remote_repo_name(root))
