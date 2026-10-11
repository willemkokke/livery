"""The workshop's doctors, registered under footman's ``footman.doctors`` group.

Two: the forge doctor, which reaches the forge and says who you are
there, which server it is, what it grants, whether every declared owner
exists and which admin token ladder is set; and the host doctor, which
reads the machine, the compilers the present kinds need, the tools the
host serves, and the installed extensions the contract does not list.
Each returns findings ([livery.footman.Finding][]); every fix here is
a person's, so none carries a repair. `fm doctor` prints them, footman's
own first.
"""

from __future__ import annotations

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
    """The host doctor: the kinds' compilers, the host's tools, unlisted extensions."""
    from livery.workshop._env_tasks import missing_host_tools, served_by
    from livery.workshop._extensions import available_extensions, extension_names
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
                "MISSING, a compiler the present package kinds need",
                "install the platform toolchain",
            )
        )
    if not missing:
        findings.append(
            Finding("host: every compiler the present kinds need is present")
        )
    hosted = [
        f"{name} ({served_by(receipt)})"
        for name, receipt in sorted(receipts(root).items())
        if receipt.source == "host"
    ]
    if hosted:
        findings.append(Finding(f"tools: served by the host: {', '.join(hosted)}"))
    for module, dist in available_extensions(extension_names(root)):
        named = f" ({dist})" if dist else ""
        # Information, not a fault: an extension a wheel ships that this
        # workspace has no use for is the common case.
        findings.append(
            Finding(
                f"extensions: {module}{named} is installed and not listed; it does"
                " nothing until [workspace] extensions names it"
            )
        )
    return findings
