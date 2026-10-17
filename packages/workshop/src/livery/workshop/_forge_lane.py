"""Resolve this workspace's repository on its forge.

The workspace contract's ``[forge]`` table names the kind and owner;
the repository name comes from the ``origin`` remote. Tokens resolve
through livery.workshop._tokens: ``FORGE_TOKEN`` (host-qualified
first), and each backend's own documented fallback when neither is
set. Nothing here is inferred from ambient state beyond those
documented lookups.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery import git
from livery.footman import fail
from livery.forge import (
    Forge,
    ForgeError,
    GiteaForge,
    GithubForge,
    GitlabForge,
    PullRequest,
    Repository,
)
from livery.workshop import _stack
from livery.workshop._contract import load_contract
from livery.workshop._tokens import admin_token, forge_token, host_qualifier

# http and https (ports and embedded credentials included), and the
# git@host:owner/name form: the dev rig speaks plain http on a port.
_REMOTE_RE = re.compile(
    r"(?:https?://[^/]+/|git@[^:]+:)(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$"
)

#: A URL's user and password, the part a credential rides in before the host.
_USERINFO_RE = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@]*@", re.IGNORECASE)


def shown_url(url: str) -> str:
    """*url* as a message may show it: any user and password replaced by ``***``.

    A remote's URL can carry a token (``http://oauth2:<token>@host/...``),
    and a refusal that names the URL is printed, logged and recorded. An
    ``ssh`` spelling (``git@host:owner/name``) names a user and no secret,
    and is shown as it is.
    """
    return _USERINFO_RE.sub(r"\g<scheme>***@", url)


def forge_repository(root: Path) -> str:
    """This workspace's repository name on its forge: the name ``origin`` points at.

    The remote's URL in either spelling, ``https://host/owner/name`` or
    ``git@host:owner/name``, with or without ``.git``.

    Raises:
        Failed: when *root* has no ``origin`` remote, or its URL names
            no owner and name.
    """
    try:
        url = git.remote_url(root)
    except git.GitError as error:
        fail(f"no origin remote:\n{error}")
    match = _REMOTE_RE.search(url)
    if match is None:
        fail(f"cannot read owner/name from origin url {shown_url(url)!r}")
    return match.group("name")


def _connect(kind: str, url: str, token: str | None) -> Forge:
    """One backend, connected; ``None`` lets its own resolution decide.

    The one place a workshop verb connects, so a refusal reads the
    same from every verb: when the lane resolved nothing and the
    backend found nothing either, the backend's own words (its
    variable, its CLI) are followed by the workshop's names, so the
    reader learns both ladders at once.

    Raises:
        ForgeError: when the backend cannot connect; a caller that
            falls open catches it, and a verb lets it stand as the
            refusal.
    """
    try:
        if kind == "github":
            return GithubForge.connect(url=url, token=token)
        if kind == "gitea":
            return GiteaForge.connect(url=url, token=token)
        if kind == "gitlab":
            return GitlabForge.connect(url=url, token=token)
    except ForgeError as error:
        if token is not None or "credential" not in str(error):
            raise
        qualifier = host_qualifier(kind, url)
        names = "FORGE_TOKEN" + (f" or FORGE_TOKEN__{qualifier}" if qualifier else "")
        raise ForgeError(f"{error}; the workshop reads {names} first") from error
    fail(f"unknown forge kind {kind!r}: use github, gitea, or gitlab")


def this_forge(root: Path) -> Forge:
    """The workspace's forge, per the contract's ``[forge]`` table.

    The token is ``FORGE_TOKEN`` (host-qualified first); with neither
    set, the backend's own documented fallback decides.

    Returns:
        The connected [livery.forge.Forge][].
    """
    contract = load_contract(root / "workshop.toml")
    forge_table = contract.get("forge") or {}
    kind = str(forge_table.get("kind", ""))
    url = str(forge_table.get("url", ""))
    if not kind:
        fail("workshop.toml [forge] must carry kind and owner")
    token, _ = forge_token(kind, url)
    return _connect(kind, url, token or None)


def admin_forge(root: Path) -> tuple[Forge, str]:
    """The forge for an admin verb, and the variable that armed it.

    Least privilege by split tokens: the everyday verbs never read
    the admin name, and the admin verbs (configure, the post-abort
    reconcile) resolve ``FORGE_ADMIN_TOKEN`` (host-qualified first)
    with a fallback to the everyday token; the fallback keeps a solo
    developer whose one token already administers working with
    nothing extra. The second value names the admin variable used,
    "" for the fallback, so a refusal can teach the missing grant.
    """
    contract = load_contract(root / "workshop.toml")
    table = contract.get("forge") or {}
    kind = str(table.get("kind", ""))
    url = str(table.get("url", ""))
    token, var = admin_token(kind, url)
    if not var.startswith("FORGE_ADMIN_TOKEN"):
        return this_forge(root), ""
    return _connect(kind, url, token), var


def admin_repository(root: Path) -> tuple[Repository, str]:
    """The repository bound to the admin ladder's forge."""
    contract = load_contract(root / "workshop.toml")
    owner = str((contract.get("forge") or {}).get("owner", ""))
    if not owner:
        fail("workshop.toml [forge] must carry kind and owner")
    forge, var = admin_forge(root)
    return forge.repository(owner, forge_repository(root)), var


def this_repository(root: Path) -> Repository:
    """The workspace's repository, per the contract and the remote."""
    contract = load_contract(root / "workshop.toml")
    owner = str((contract.get("forge") or {}).get("owner", ""))
    if not owner:
        fail("workshop.toml [forge] must carry kind and owner")
    return this_forge(root).repository(owner, forge_repository(root))


def forge_kind(root: Path) -> str:
    """The forge the workspace's contract names, ``github``, ``gitea`` or ``gitlab``.

    Empty when the contract names none, which the fake forge speaks for,
    and when there is no contract to read.
    """
    path = root / "workshop.toml"
    if not path.is_file():
        return ""
    contract = load_contract(path)
    return str((contract.get("forge") or {}).get("kind", ""))


def pull_request_for(repo: Repository, root: Path, branch: str) -> PullRequest | None:
    """The pull request *branch* is or was the head of, or None.

    By head branch first, then by the branch's head commit: a forge
    that deletes the head branch on merge may clear the merged pull
    request's head ref, and the commit persists after the branch is
    gone. A pull request found by the commit is the branch's own only
    when its head branch is *branch* or empty, and never while
    *branch* is a stacked branch still at the commit it started from:
    that commit is the parent's head, so the pull request found is the
    parent's. A forge that cannot be asked raises; the caller decides
    what an unanswered question means, never this function.
    """
    pr = repo.pr.find_by_head(branch, state="all")
    if pr is not None:
        return pr
    sha = git.any_head(root, branch)
    if not sha:
        return None
    pr = repo.pr.find_by_head_sha(sha)
    if pr is None or (pr.head_branch and pr.head_branch != branch):
        return None
    _parent, start = _stack.stack(root, branch)
    return None if start == sha else pr
