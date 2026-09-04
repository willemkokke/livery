"""Where each artifact kind's registry lives: the resolution ladder.

One resolution for every artifact kind the ecosystem publishes or
probes, so no caller derives a registry address on its own. The
ladder, per kind: the declaration wins (the env cascade's variables
first, machine truth over committed; then the contract's
``[registries]`` table), else the forge's own hosted registry of
that kind, else the ecosystem default where one exists (pypi.org
for python), else a refusal naming the kind and the rungs it tried.
A declaration may be a local folder or share: every kind publishes
to a path, so a workspace can work with no registry server at all.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from footman import fail

if TYPE_CHECKING:
    from livery.forge import RegistryKind

#: The env cascade's declaration variables, per kind. The python
#: pair predates the ladder and stays: addresses in the committed
#: env are the machine-facing declaration the CI rung materialises.
_ENV_VARS = {
    "python": ("PYTHON_REGISTRY_URL", "PYTHON_PUBLISH_INDEX"),
    "conan": ("CONAN_REMOTE_URL",),
    "container": ("CONTAINER_REGISTRY",),
}

#: The ecosystem default, where one exists.
_ECOSYSTEM = {
    "python": ("https://pypi.org/simple", ""),
}


@dataclass(frozen=True)
class RegistryTarget:
    """One resolved registry for one artifact kind.

    Attributes:
        kind: Which artifact registry this is.
        url: The registry's address: the read index for python (the
            publish endpoint is ``publish_url``), the remote for
            conan, the reference prefix for container. A local path
            when the declaration was a folder.
        publish_url: Where python dists upload; empty means the
            publisher's own default resolution (uv publish and
            trusted publishing on pypi.org). Unused by other kinds.
        local: True when the target is a folder or share rather
            than a server.
    """

    kind: str
    url: str
    publish_url: str = ""
    local: bool = False


def _is_local(value: str) -> bool:
    if value.startswith("file://"):
        return True
    return "://" not in value and (value.startswith(("/", "./", "~")) or ":\\" in value)


def _normalise(value: str) -> str:
    if value.startswith("file://"):
        return value.removeprefix("file://")
    return value


def resolve_registry(root: Path, kind: str) -> RegistryTarget:
    """The registry for *kind*, through the ladder; refusal teaches.

    The rungs, in order: the env cascade's variables (machine truth
    over committed declarations), the contract's ``[registries]``
    table, the forge's own registry of the kind, the ecosystem
    default. A python declaration may split read and publish
    addresses; the other kinds carry one.
    """
    env_vars = _ENV_VARS.get(kind)
    if env_vars is None:
        fail(f"{kind!r} is not an artifact registry kind: python, conan, container")
    declared_read = os.environ.get(env_vars[0], "")
    declared_publish = os.environ.get(env_vars[1], "") if len(env_vars) > 1 else ""
    if not declared_read and not declared_publish:
        contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
        table = contract.get("registries") or {}
        entry = table.get(kind) if isinstance(table, dict) else None
        if isinstance(entry, str):
            declared_read = entry
        elif isinstance(entry, dict):
            declared_read = str(entry.get("url", ""))
            declared_publish = str(entry.get("publish", ""))
    if declared_read or declared_publish:
        read = declared_read or declared_publish
        return RegistryTarget(
            kind=kind,
            url=_normalise(read),
            publish_url=_normalise(declared_publish),
            local=_is_local(read),
        )
    forge_url = _forge_registry(root, kind)
    if forge_url is not None:
        if kind == "python":
            # The forge-registry shape: the base takes uploads and
            # serves the simple index under /simple.
            return RegistryTarget(
                kind=kind, url=f"{forge_url}/simple", publish_url=forge_url
            )
        return RegistryTarget(kind=kind, url=forge_url)
    default = _ECOSYSTEM.get(kind)
    if default is not None:
        return RegistryTarget(kind=kind, url=default[0], publish_url=default[1])
    fail(
        f"no {kind} registry resolves: nothing declared (env or the"
        " [registries] table), this forge hosts none, and the kind has"
        " no ecosystem default. Declare one, a local folder included."
    )


def _forge_registry(root: Path, kind: str) -> str | None:
    """The forge's own registry of *kind*, or None where it declines."""
    from livery.forge import Unsupported
    from livery.workshop._forge_lane import this_forge

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    forge_table = contract.get("forge") or {}
    owner = str(forge_table.get("owner", ""))
    if not owner:
        return None
    try:
        forge = this_forge(root)
    except BaseException as error:
        # No reachable forge is a rung that does not answer, not a
        # resolution failure: the ladder continues, and the reason is
        # printed rather than swallowed.
        print(f"  the forge rung did not answer: {error}")
        return None
    # resolve_registry validated the kind at its boundary; the cast
    # states that proof for the checkers.
    registry_kind = cast("RegistryKind", kind)
    try:
        return forge.registry_url(registry_kind, owner)
    except Unsupported:
        return None
