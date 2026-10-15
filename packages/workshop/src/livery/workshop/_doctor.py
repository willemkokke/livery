"""The workshop's doctors, registered under footman's ``footman.doctors`` group.

Four: the forge doctor, which reaches the forge and says who you are
there, which server it is, what it grants, whether every declared owner
exists and which admin token ladder is set; the host doctor, which
reads the machine, the compilers the workspace's packages need, the
tools the host serves, the listed extensions that are dormant, and the
installed extensions the contract does not list; the uv cache doctor,
which measures uv's cache against the workspace's limit; and the ssh
doctor, which says whether the git plugin shares one connection per
host and names a `ControlMaster` entry of the person's own. Each
returns findings ([livery.footman.Finding][]). Only the finding of a
cache over its limit carries a repair, the prune `fm doctor --fix`
runs; every other fix is a person's. `fm doctor` prints them,
footman's own first.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from livery.footman import Finding
from livery.forge import Capability, Forge
from livery.workshop._contract import load_contract
from livery.workshop._extensions import workspace_root

#: The forge capabilities the workshop can use; the doctor says which are granted.
CAPABILITIES: tuple[Capability, ...] = (
    "auto_merge",
    "force_cancel",
    "required_contexts",
    "ci_secrets",
    "schedule_events",
)


def forge_findings(forge: Forge, root: Path | None) -> list[Finding]:
    """The forge doctor's findings for *forge*; *root*'s contract is read when given."""
    findings: list[Finding] = []
    if root is not None:
        from livery.workshop._governance import unknown_owners
        from livery.workshop._tokens import admin_token

        contract = load_contract(root / "workshop.toml")
        owner = str((contract.get("forge") or {}).get("owner", ""))
        kind = str((contract.get("forge") or {}).get("kind", ""))
        url = str((contract.get("forge") or {}).get("url", ""))
        _, admin_var = admin_token(kind, url)
        if admin_var.startswith("FORGE_ADMIN_TOKEN"):
            findings.append(Finding(f"admin ladder: {admin_var} set"))
        elif admin_var:
            findings.append(
                Finding(
                    "admin ladder: FORGE_ADMIN_TOKEN unset"
                    f" ({admin_var} is the fallback)"
                )
            )
        else:
            findings.append(
                Finding(
                    "admin ladder: FORGE_ADMIN_TOKEN unset"
                    " (everyday token is the fallback)"
                )
            )
        try:
            unknown = unknown_owners(root, forge, owner)
        except Exception as error:
            findings.append(Finding(f"owners: not checked ({error})"))
        else:
            for entry in unknown:
                findings.append(
                    Finding(
                        "owners",
                        f"unknown {entry}",
                        "add the user or team on the forge, or drop it from the"
                        " contract's owners",
                    )
                )
            if not unknown:
                findings.append(
                    Finding("owners: every declared owner exists on the forge")
                )
    findings.append(Finding(f"{forge.whoami()} on {forge.server_version()}"))
    granted = [name for name in CAPABILITIES if forge.supports(name)]
    missing = [name for name in CAPABILITIES if name not in granted]
    if granted:
        findings.append(Finding(f"grants: {', '.join(granted)}"))
    if missing:
        findings.append(
            Finding(
                "grants",
                f"missing: {', '.join(missing)}",
                "a token or a plan that grants them; the workshop works around"
                " each one it lacks",
            )
        )
    else:
        findings.append(Finding("every capability the workshop can use is granted"))
    return findings


def forge(**facts: Any) -> list[Finding]:
    """The forge doctor: who you are on the forge, what it is, what it grants."""
    from livery.workshop._forge_lane import this_forge

    root = workspace_root()
    if root is None:
        return [Finding("forge: no workspace here, not checked")]
    return forge_findings(this_forge(root), root)


def host(**facts: Any) -> list[Finding]:
    """The host doctor: compilers, the host's tools, dormant and unlisted extensions."""
    from livery.workshop._env_tasks import missing_host_tools, served_by
    from livery.workshop._extensions import (
        available_extensions,
        composes_per_package,
        dormancy,
        extension_names,
    )
    from livery.workshop._tools import receipts

    root = workspace_root()
    if root is None:
        return [Finding("host: no workspace here, not checked")]
    findings: list[Finding] = []
    missing = list(missing_host_tools(root))
    for tool in missing:
        findings.append(
            Finding(
                f"host: {tool}",
                "MISSING, a compiler the workspace's packages need",
                "install the platform toolchain",
            )
        )
    if not missing:
        findings.append(
            Finding("host: every compiler the workspace's packages need is present")
        )
    hosted = [
        f"{name} ({served_by(receipt)})"
        for name, receipt in sorted(receipts(root).items())
        if receipt.source == "host"
    ]
    if hosted:
        findings.append(Finding(f"tools: served by the host: {', '.join(hosted)}"))
    for name, waits in dormancy(root).items():
        # Information, not a fault: a listing kept for a contributor
        # still to come is a statement of intent.
        findings.append(Finding(f"extensions: {name}: dormant, {waits}"))
    for module, dist in available_extensions(extension_names(root)):
        # Information, not a fault: an extension a wheel ships that this
        # workspace has no use for is the common case.
        findings.append(
            Finding(unlisted(module, dist, per_package=composes_per_package(module)))
        )
    return findings


def unlisted(module: str, dist: str, *, per_package: bool) -> str:
    """The doctor's line for an installed extension the workspace lists nowhere.

    Either list takes any extension. The line names `[workspace]
    extensions`, and a package's `extensions` too when *per_package*
    says the extension composes something in a package that holds it
    ([livery.workshop._extensions.composes_per_package][]).
    """
    named = f" ({dist})" if dist else ""
    places = "[workspace] extensions"
    if per_package:
        places += " or a package's extensions"
    return (
        f"extensions: {module}{named} is installed and not listed; it does"
        f" nothing until {places} names it"
    )


def ssh(**facts: Any) -> list[Finding]:
    """The ssh doctor: the shared connection, and a person's own multiplexing."""
    import livery.footman as footman
    from livery import git

    del facts
    findings: list[Finding] = []
    config = Path.home() / ".ssh" / "config"
    if config.is_file():
        lines = [
            line.strip()
            for line in config.read_text("utf-8", errors="replace").splitlines()
            if line.strip().lower().startswith(("controlmaster", "controlpath"))
        ]
        if lines:
            findings.append(
                Finding(
                    f"ssh: {config} multiplexes on its own ({'; '.join(lines)}); the"
                    " git plugin's command adds a master beside it"
                )
            )
    socket_dir = git.ssh_sockets(footman.data_dir())
    environment, reason = git.ssh_environment(
        socket_dir,
        configured=git.config_get(footman.cwd(), "core.sshCommand"),
    )
    if reason:
        findings.append(
            Finding(reason, "", f"delete {socket_dir / 'unsupported'} to try again")
        )
    elif not environment:
        findings.append(
            Finding("ssh: a command of your own stands; the plugin adds nothing")
        )
    else:
        findings.append(
            Finding(f"ssh: one connection per host, sockets in {socket_dir}")
        )
    return findings


#: The size uv's cache may reach before the doctor reports it, when the
#: contract sets no `[workspace] uv-cache-limit`.
UV_CACHE_LIMIT = "10G"

_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """The bytes a size such as ``10G`` or ``500M`` names.

    Raises:
        ValueError: for any other shape, naming the accepted one.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT])B?\s*", text, flags=re.I)
    if match is None:
        raise ValueError(
            f"{text!r} is not a size: a number with K, M, G or T, like 10G"
        )
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


def show_size(size: int) -> str:
    """A size in the largest unit that keeps one decimal, ``47.2G``."""
    for unit in ("T", "G", "M", "K"):
        if size >= _UNITS[unit]:
            return f"{size / _UNITS[unit]:.1f}{unit}"
    return f"{size}B"


def _tree_size(root: Path) -> int:
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def uv_cache_dir() -> Path:
    """Where uv keeps its cache on this machine, as the locked uv answers."""
    from livery.toolroom import tools

    result = tools.uv.opts(nofail=True, recorded=False)("cache", "dir")
    if result.code != 0:
        raise RuntimeError(
            f"uv cache dir exited {result.code}: {result.stderr.strip()}"
        )
    return Path(result.stdout.strip())


def prune_uv_cache() -> None:
    """Drop the cache entries nothing references, through the locked uv."""
    from livery.toolroom import tools

    tools.uv.opts(recorded=False)("cache", "prune")


def uv_cache(**facts: Any) -> list[Finding]:
    """The uv cache doctor: its size against the limit the workspace sets."""
    root = workspace_root()
    if root is None:
        return [Finding("uv cache: no workspace here, not checked")]
    workspace = load_contract(root / "workshop.toml").get("workspace") or {}
    declared = str(workspace.get("uv-cache-limit", UV_CACHE_LIMIT))
    try:
        limit = parse_size(declared)
    except ValueError as error:
        return [
            Finding(
                "uv cache",
                f"[workspace] uv-cache-limit: {error}",
                "set it to a size such as 10G in workshop.toml",
            )
        ]
    try:
        cache = uv_cache_dir()
    except RuntimeError as error:
        return [Finding("uv cache", str(error), "fm sync installs the locked uv")]
    size = _tree_size(cache) if cache.is_dir() else 0
    if size <= limit:
        return [Finding(f"uv cache: {show_size(size)}, under the {declared} limit")]
    return [
        Finding(
            "uv cache",
            f"{show_size(size)}, over the {declared} limit",
            "fm doctor --fix runs `uv cache prune`; `uv cache clean` empties it;"
            " raise [workspace] uv-cache-limit in workshop.toml if this machine"
            " needs a larger one",
            prune_uv_cache,
        )
    ]
