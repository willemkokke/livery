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
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, cast

from livery.footman import fail
from livery.workshop._contract import load_contract

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.forge import RegistryKind

#: The env cascade's declaration variables, per kind. The python
#: pair predates the ladder and stays: addresses in the committed
#: env are the machine-facing declaration the CI rung materialises.
_ENV_VARS = {
    "python": ("PYTHON_REGISTRY_URL", "PYTHON_PUBLISH_INDEX"),
    "conan": ("CONAN_REMOTE_URL",),
    "container": ("CONTAINER_REGISTRY",),
}

#: The ecosystem default, where one exists: the read index and the
#: upload endpoint. The publish step refuses an empty address, so a
#: deliberate PyPI publish arrives there as this rung's explicit URL;
#: trusted publishing or a token carries the credential.
_ECOSYSTEM = {
    "python": ("https://pypi.org/simple", "https://upload.pypi.org/legacy/"),
}

#: The env cascade's credential variable, per kind. Read on every
#: rung, because the credential is independent of where the address
#: came from: a declared or default URL may still need auth. Secrets
#: never ride the contract's [registries] table.
_CREDENTIAL_VARS = {
    "python": "PYTHON_REGISTRY_TOKEN",
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
        releases: True when the artifacts ride the forge's own
            releases, attached to each member's receipt tag. The
            route a conan package takes on a forge that hosts no
            conan registry; `url` is then the repository's page, for
            printing.
        token: The registry credential, for reads and uploads alike:
            the kind's credential variable when set, else the forge
            lane's token when the forge rung answered. Empty reads
            anonymously and lets the publisher's own resolution
            decide.
    """

    kind: str
    url: str
    publish_url: str = ""
    local: bool = False
    token: str = ""
    releases: bool = False

    @classmethod
    def from_table(cls, table: Mapping[str, object]) -> RegistryTarget:
        """The target a ``registry-target`` table holds.

        The engine gives each step of the ``publish`` phase the registry
        it resolved as the ``registry-target`` key: a table of this
        class's fields by name, which the step reads back with this
        constructor. The table holds ``kind`` and ``url``, and a field
        it leaves out takes its default.

        Args:
            table: Each field of the target by its name.

        Returns:
            The target the table holds.

        Raises:
            ValueError: when the table is not a registry target: it has
                no ``kind`` or no ``url``, a value has another type than
                its field, or a key names no field. The message names
                the field and what it takes, and never a value, since
                ``token`` is a credential.
        """
        names = tuple(field.name for field in fields(cls))
        unknown = [name for name in table if name not in names]
        if unknown:
            raise ValueError(
                f"not a registry target: the table holds {', '.join(unknown)};"
                f" a registry target holds {', '.join(names)}"
            )
        for field in fields(cls):
            if field.default is MISSING and field.name not in table:
                raise ValueError(
                    f"not a registry target: the table has no {field.name},"
                    " which every registry target holds"
                )
        return cls(
            kind=_text(table, "kind"),
            url=_text(table, "url"),
            publish_url=_text(table, "publish_url"),
            local=_flag(table, "local"),
            token=_text(table, "token"),
            releases=_flag(table, "releases"),
        )


def _text(table: Mapping[str, object], name: str) -> str:
    """*table*'s string at *name*; empty when the table has none."""
    value = table.get(name, "")
    if not isinstance(value, str):
        raise ValueError(_wrong_type(name, "str", value))
    return value


def _flag(table: Mapping[str, object], name: str) -> bool:
    """*table*'s boolean at *name*; false when the table has none."""
    value = table.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(_wrong_type(name, "bool", value))
    return value


def _wrong_type(name: str, wanted: str, value: object) -> str:
    """The refusal of a field whose value is not a *wanted*, naming no value."""
    return (
        f"not a registry target: {name} takes a {wanted}, and the table's"
        f" {name} is of type {type(value).__name__}"
    )


def target_table(target: RegistryTarget) -> dict[str, object]:
    """*target* as the ``publish`` phase's ``registry-target``: its fields by name."""
    return {
        "kind": target.kind,
        "url": target.url,
        "publish_url": target.publish_url,
        "local": target.local,
        "token": target.token,
        "releases": target.releases,
    }


def _is_local(value: str) -> bool:
    if value.startswith("file://"):
        return True
    return "://" not in value and (value.startswith(("/", "./", "~")) or ":\\" in value)


def _normalise(value: str) -> str:
    if value.startswith("file://"):
        return value.removeprefix("file://")
    return value


def registry(root: Path, kind: str) -> RegistryTarget:
    """The registry the workspace at *root* reads and publishes *kind* artifacts at.

    *kind* is ``python``, ``conan`` or ``container``. The first rung
    that names one wins: the environment's variables for the kind, the
    contract's ``[registries]`` table, the forge's own registry of the
    kind, then the ecosystem's default.

    Raises:
        Failed: when *kind* is not an artifact registry kind, naming
            the three.
    """
    return resolve_registry(root, kind)


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
    token = os.environ.get(_CREDENTIAL_VARS.get(kind, ""), "")
    declared_read = os.environ.get(env_vars[0], "")
    declared_publish = os.environ.get(env_vars[1], "") if len(env_vars) > 1 else ""
    if not declared_read and not declared_publish:
        contract = load_contract(root / "workshop.toml")
        table = contract.get("registries") or {}
        entry = table.get(kind) if isinstance(table, dict) else None
        if isinstance(entry, str):
            declared_read = entry
        elif isinstance(entry, dict):
            declared_read = str(entry.get("url", ""))
            declared_publish = str(entry.get("publish", ""))
    if declared_read or declared_publish:
        read = declared_read or declared_publish
        if not token:
            # A declaration naming the forge's own registry keeps the
            # forge lane token: committing the address must not lose
            # the credential the forge rung would have carried. A
            # foreign address never inherits it.
            resolved = _forge_registry(root, kind)
            if resolved is not None:
                forge_url, lane_token = resolved
                if forge_url and (
                    read.startswith(forge_url) or declared_publish.startswith(forge_url)
                ):
                    token = lane_token
        return RegistryTarget(
            kind=kind,
            url=_normalise(read),
            publish_url=_normalise(declared_publish),
            local=_is_local(read),
            token=token,
        )
    resolved = _forge_registry(root, kind)
    if resolved is None and kind == "conan":
        # A forge with no conan registry still has releases, and a
        # conan package is a file: each member's caches ride its own
        # receipt tag. The consumer reads them back through the same
        # forge, so nothing outside the repository is involved.
        return _forge_releases(root)
    if resolved is not None:
        forge_url, lane_token = resolved
        if kind == "python":
            # The forge-registry shape: the base takes uploads and
            # serves the simple index under /simple.
            return RegistryTarget(
                kind=kind,
                url=f"{forge_url}/simple",
                publish_url=forge_url,
                token=token or lane_token,
            )
        return RegistryTarget(kind=kind, url=forge_url, token=token or lane_token)
    default = _ECOSYSTEM.get(kind)
    if default is not None:
        return RegistryTarget(
            kind=kind, url=default[0], publish_url=default[1], token=token
        )
    fail(
        f"no {kind} registry resolves: nothing declared (env or the"
        " [registries] table), this forge hosts none, and the kind has"
        " no ecosystem default. Declare one, a local folder included."
    )


def _forge_releases(root: Path) -> RegistryTarget:
    """The conan target on a forge whose releases carry the caches.

    The address is the repository itself: the publisher attaches each
    host's saved cache to the member's release, and the probe reads
    the same listing. The refusal names what to declare when this
    workspace has no forge either.
    """
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._tokens import forge_token

    contract = load_contract(root / "workshop.toml")
    forge_table = contract.get("forge") or {}
    try:
        repository = this_repository(root)
    except BaseException as error:
        fail(
            f"no conan registry resolves: nothing declared (env or the"
            f" [registries] table), and this forge's releases did not"
            f" answer either ({error}). Declare [registries.conan] in"
            " workshop.toml, a local folder included."
        )
    token, _ = forge_token(
        str(forge_table.get("kind", "")), str(forge_table.get("url", ""))
    )
    return RegistryTarget(
        kind="conan",
        url=repository.web_url(),
        releases=True,
        token=token,
    )


def _forge_registry(root: Path, kind: str) -> tuple[str, str] | None:
    """The forge's registry of *kind* and the lane token, or None.

    The token is the lane's own (``FORGE_TOKEN``, host-qualified
    first), because the forge's registry authenticates with the same
    credential as its API. It can be empty: a backend that resolves
    its token through its own dialect never surfaces it here, and the
    read then rides anonymously, which a public owner serves.
    """
    from livery.forge import Unsupported
    from livery.workshop._forge_lane import this_forge
    from livery.workshop._tokens import forge_token

    contract = load_contract(root / "workshop.toml")
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
        url = forge.registry_url(registry_kind, owner)
    except Unsupported:
        return None
    forge_kind = str(forge_table.get("kind", ""))
    lane_token, _ = forge_token(forge_kind, str(forge_table.get("url", "")))
    if not lane_token:
        # The connection is the one source of the resolved
        # credential: the backend read its own dialect variable at
        # connect time, and the registry authenticates with the same
        # lane.
        lane_token = forge.token
    return url, lane_token
