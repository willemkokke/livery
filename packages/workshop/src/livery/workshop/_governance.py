"""Repository governance from the contracts: owners, the file, the config.

Each package's ``livery.toml`` names who reviews it (``[owners]``
with ``users``, ``teams``, and ``approvals``); the root contract's
own ``[owners]`` guard the governance declarations themselves, so
raising a reviewer count is a reviewed merge like any change. This
module turns those declarations into the forge's CODEOWNERS file (a
managed generated artifact the drift gate compares offline), into
the repository settings ``configure`` asserts, and into the
generated post-merge workflow that applies them, so nobody carries
forge-specific knowledge in their head.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from footman import fail

from livery.forge import Codeowners, CodeownersEntry, Forge, RepoConfig


def _owners_table(contract: dict[str, Any]) -> dict[str, Any]:
    return dict(contract.get("owners") or {})


def owners_of(contract: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """A contract's ``(users, teams, approvals)`` owner declaration.

    Approvals default to zero: declared owners document and route
    review without gating the merge until a count is asked for,
    which is what a solo maintainer needs (a forge that forbids
    self-approval deadlocks a solo repo on any required review).
    """
    table = _owners_table(contract)
    users = tuple(str(name) for name in table.get("users", []) if str(name).strip())
    teams = tuple(str(name) for name in table.get("teams", []) if str(name).strip())
    approvals = int(table.get("approvals", 0))
    return users, teams, approvals


def offline_forge(root: Path) -> Forge:
    """The contract's forge backend with no credential attached.

    The codeowners rendering is pure string building, so the drift
    gate may hold a backend offline; nothing built here may be used
    for a network call.
    """
    from livery.forge import GiteaForge, GithubForge, GitlabForge

    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    table = contract.get("forge") or {}
    kind = str(table.get("kind", ""))
    url = str(table.get("url", ""))
    if kind == "github":
        return GithubForge.connect(url=url, token="")
    if kind == "gitea":
        return GiteaForge.connect(url=url or "http://offline.invalid", token="")
    if kind == "gitlab":
        return GitlabForge.connect(url=url or "http://offline.invalid", token="")
    fail(f"unknown forge kind {kind!r}: use github, gitea, or gitlab")


def governance_entries(root: Path) -> tuple[CodeownersEntry, ...]:
    """The neutral ownership declarations, root guard first.

    The root contract's owners guard the root ``livery.toml`` and
    the rendered codeowners file itself (config-as-code guarded by
    itself); each package's owners guard its directory. A workspace
    with no owner declarations anywhere answers empty, and no file
    is generated.
    """
    from livery.workshop._packages import discover_packages

    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    forge_table = contract.get("forge") or {}
    owner = str(forge_table.get("owner", ""))
    if not str(forge_table.get("kind", "")):
        # No forge declared: nothing to render a dialect for, and a
        # bare rig must not be forced to invent one.
        return ()

    def _qualified(users: tuple[str, ...], teams: tuple[str, ...]) -> tuple[str, ...]:
        return users + tuple(f"{owner}/{team}" for team in teams)

    entries: list[CodeownersEntry] = []
    root_users, root_teams, root_approvals = owners_of(contract)
    rendered_path = offline_forge(root).codeowners(()).path
    if root_users or root_teams:
        guard = _qualified(root_users, root_teams)
        entries.append(
            CodeownersEntry(
                path="/livery.toml", owners=guard, min_approvals=root_approvals
            )
        )
        entries.append(
            CodeownersEntry(
                path=f"/{rendered_path}", owners=guard, min_approvals=root_approvals
            )
        )
    if (root / "packages").is_dir():
        for package in discover_packages(root):
            contract_file = package.directory / "livery.toml"
            users, teams, approvals = owners_of(
                tomllib.loads(contract_file.read_text("utf-8"))
            )
            if not (users or teams):
                continue
            entries.append(
                CodeownersEntry(
                    path=f"/{package.path}/",
                    owners=_qualified(users, teams),
                    min_approvals=approvals,
                )
            )
    return tuple(entries)


def codeowners_file(root: Path) -> Codeowners | None:
    """The rendered codeowners artifact, or None with no declarations."""
    entries = governance_entries(root)
    if not entries:
        return None
    return offline_forge(root).codeowners(entries)


def governance_paths(root: Path) -> tuple[str, ...]:
    """The paths whose change means governance may need re-applying."""
    paths = ["livery.toml", "packages/*/livery.toml"]
    rendered = codeowners_file(root)
    if rendered is not None:
        paths.append(rendered.path)
    return tuple(paths)


def governance_config(root: Path) -> RepoConfig:
    """The approvals half of the repository settings.

    The repo-wide count is the highest any declaration asks, because
    GitHub and Gitea express one count through protection; GitLab's
    per-path sections carry the rest, where a paid tier enforces
    them. No declarations anywhere means no approval requirement.
    """
    entries = governance_entries(root)
    if not entries:
        return RepoConfig()
    highest = max(entry.min_approvals for entry in entries)
    # A count of zero asserts zero and requires no codeowner review
    # either: on a forge that forbids self-approval, a codeowner
    # requirement alone still deadlocks a solo repo, and ownership
    # stays documented in the file.
    return RepoConfig(
        min_approvals=highest,
        require_codeowner_review=highest > 0,
    )


def unknown_owners(root: Path, forge: Forge, owner: str) -> tuple[str, ...]:
    """Declared owners the forge does not know, for configure's check.

    Users are checked against ``members(owner)``, teams against
    ``teams(owner)``; the answers are live listings, so this runs
    only where the forge is already being spoken to, never on the
    merge path.
    """
    members = set(forge.members(owner))
    teams = set(forge.teams(owner))
    missing: list[str] = []
    for entry in governance_entries(root):
        for name in entry.owners:
            _, slash, team = name.partition("/")
            if slash:
                if team not in teams and name not in teams:
                    missing.append(f"team {name} (declared on {entry.path})")
            elif name not in members:
                missing.append(f"user {name} (declared on {entry.path})")
    return tuple(dict.fromkeys(missing))
