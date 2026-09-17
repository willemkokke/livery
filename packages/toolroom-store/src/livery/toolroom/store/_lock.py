"""The lock: one version per tool for the repository, resolved against the catalogue.

A requirement is a tool's name with a floor, declared at one of three
sites; the sites' requirements union, and the lock takes for each tool
the newest version the catalogue lists that satisfies every floor and
resolves on every locked host. A version of a downloaded kind resolves
on a host when it has that host's artifact; a delegated kind resolves
everywhere its installer does. A requirement that cannot be satisfied
refuses naming the tool, each floor and its site, and for a host that
no eligible version has, the first version that has it.

The lock is a file in the repository, `tools.lock`, so every checkout
and every runner installs the same version; it moves only through a
lock or an upgrade, never on its own.

Reach for [livery.toolroom.store.Requirement][],
[livery.toolroom.store.resolve_lock][] and [livery.toolroom.store.Lock][].
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.strongroom import Digest
from livery.toolroom.store._catalogue import Catalogue, CatalogueError, Listed
from livery.toolroom.store._record import DOWNLOAD_KINDS, HOSTS, version_key
from livery.toolroom.tools import version_tuple

LOCK_FILE = "tools.lock"
"""The lock's name at the repository root."""

LOCK_SCHEMA = 1
"""The lock's shape. Bumped when a reader must know."""

_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.\-]+)\s*(?:>=\s*(?P<floor>\S+))?\s*$"
)


class LockError(ValueError):
    """A requirement the catalogue cannot satisfy; the message names why."""


@dataclass(frozen=True)
class Requirement:
    """One site's requirement of a tool: a floor, or any version.

    Attributes:
        name: The tool's name.
        floor: The lowest version that satisfies; empty for any.
        site: Where the requirement was declared, for a refusal.
    """

    name: str
    floor: str = ""
    site: str = ""

    @classmethod
    def parse(cls, text: str, *, site: str = "") -> Requirement:
        """A requirement from its spelling, `name` or `name>=floor`.

        Raises:
            LockError: for a spelling that is neither.
        """
        match = _REQUIREMENT.match(text)
        if match is None:
            raise LockError(
                f"{site or 'a requirement'}: {text!r} is not a requirement;"
                " spell it `name` or `name>=floor`"
            )
        return cls(match["name"], match["floor"] or "", site)

    def __str__(self) -> str:
        return f"{self.name}>={self.floor}" if self.floor else self.name

    def satisfied_by(self, version: str) -> bool:
        """Whether *version* is at or above the floor."""
        return not self.floor or version_tuple(version) >= version_tuple(self.floor)


@dataclass(frozen=True)
class Locked:
    """One tool as the lock holds it.

    Attributes:
        version: The version locked.
        hosts: Per locked host the deployment's digest; empty for a
            delegated kind, whose installer resolves the host.
    """

    version: str
    hosts: dict[str, Digest] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """The entry as a JSON object."""
        return {
            "version": self.version,
            "hosts": {host: str(digest) for host, digest in sorted(self.hosts.items())},
        }


@dataclass(frozen=True)
class Lock:
    """The repository's lock: the hosts it locks for and one version per tool.

    Attributes:
        hosts: The host keys every locked download resolves on.
        tools: Tool name to its locked version and deployments.
    """

    hosts: tuple[str, ...]
    tools: dict[str, Locked]

    def to_json(self) -> dict[str, Any]:
        """The lock as a JSON object, tools in name order."""
        return {
            "schema": LOCK_SCHEMA,
            "hosts": list(self.hosts),
            "tools": {name: self.tools[name].to_json() for name in sorted(self.tools)},
        }

    def save(self, path: Path) -> None:
        """Write the lock to *path*, indented, with a trailing newline."""
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Lock:
        """The lock at *path*.

        Raises:
            LockError: when the file is not a lock of this schema,
                naming the file.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LockError(f"{path}: not a lock ({error})") from None
        if not isinstance(data, dict) or data.get("schema") != LOCK_SCHEMA:
            raise LockError(f"{path}: not a lock of schema {LOCK_SCHEMA}")
        hosts = data.get("hosts")
        tools = data.get("tools")
        if (
            not isinstance(hosts, list)
            or not all(isinstance(h, str) and h in HOSTS for h in hosts)
            or not isinstance(tools, dict)
        ):
            raise LockError(
                f"{path}: the lock names hosts outside the six, or no tools"
            )
        locked: dict[str, Locked] = {}
        for name, entry in tools.items():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("version"), str)
                or not isinstance(entry.get("hosts"), dict)
            ):
                raise LockError(f"{path}: the entry for {name} is not one")
            try:
                digests = {
                    str(host): Digest.parse(str(digest))
                    for host, digest in entry["hosts"].items()
                }
            except ValueError as error:
                raise LockError(f"{path}: {name}: {error}") from None
            locked[str(name)] = Locked(entry["version"], digests)
        return cls(tuple(hosts), locked)


def resolve_lock(
    catalogue: Catalogue,
    requirements: Iterable[Requirement],
    *,
    hosts: Iterable[str],
    keep: Lock | None = None,
    upgrade: Iterable[str] = (),
) -> Lock:
    """The lock for *requirements* against *catalogue*, on *hosts*.

    Each tool takes the newest version that satisfies every floor
    declared for it and resolves on every host. A tool *keep* already
    locks stays at its version when that version still satisfies and
    resolves, unless it is named in *upgrade*; a tool no requirement
    names any more leaves the lock.

    Raises:
        LockError: naming the first requirement that cannot be met:
            a tool the catalogue does not list, a floor above every
            version, or a host no eligible version has.
    """
    locked_hosts = tuple(hosts)
    for host in locked_hosts:
        if host not in HOSTS:
            raise LockError(f"host {host!r} is not one of {', '.join(HOSTS)}")
    by_tool: dict[str, list[Requirement]] = {}
    for requirement in requirements:
        by_tool.setdefault(requirement.name, []).append(requirement)
    moving = set(upgrade)
    tools: dict[str, Locked] = {}
    for name in sorted(by_tool):
        wants = by_tool[name]
        try:
            listed = catalogue.listed(name)
        except CatalogueError as error:
            sites = (
                ", ".join(sorted({w.site for w in wants if w.site})) or "a requirement"
            )
            raise LockError(f"{error}; required by {sites}") from None
        held = keep.tools.get(name) if keep and name not in moving else None
        if held is not None and _eligible(listed, held.version, wants, locked_hosts):
            tools[name] = _locked(listed, held.version, locked_hosts)
            continue
        tools[name] = _locked(
            listed, _newest(listed, wants, locked_hosts), locked_hosts
        )
    return Lock(locked_hosts, tools)


def _eligible(
    listed: Listed, version: str, wants: list[Requirement], hosts: tuple[str, ...]
) -> bool:
    return (
        version in listed.versions
        and all(want.satisfied_by(version) for want in wants)
        and _resolves(listed, version, hosts)
    )


def _resolves(listed: Listed, version: str, hosts: tuple[str, ...]) -> bool:
    if listed.kind not in DOWNLOAD_KINDS:
        return True
    return all(host in listed.hosts.get(version, {}) for host in hosts)


def _newest(listed: Listed, wants: list[Requirement], hosts: tuple[str, ...]) -> str:
    """The newest version satisfying every floor and every host, or a refusal."""
    ordered = sorted(listed.versions, key=version_key)
    floors = [want for want in wants if want.floor]
    satisfying = [v for v in ordered if all(want.satisfied_by(v) for want in wants)]
    if not satisfying:
        declared = "; ".join(
            f"{want} ({want.site or 'a requirement'})" for want in floors
        )
        raise LockError(
            f"{listed.name}: no version satisfies {declared}; the newest version"
            f" listed is {ordered[-1] if ordered else 'none'}"
        )
    resolving = [v for v in satisfying if _resolves(listed, v, hosts)]
    if resolving:
        return resolving[-1]
    for host in hosts:
        if not any(host in listed.hosts.get(v, {}) for v in satisfying):
            first = next((v for v in ordered if host in listed.hosts.get(v, {})), None)
            if first is None:
                raise LockError(
                    f"{listed.name}: no version has host {host}; the lock covers"
                    f" {', '.join(hosts)}"
                )
            raise LockError(
                f"{listed.name}: no version satisfying"
                f" {', '.join(str(w) for w in floors) or 'the requirement'} has host"
                f" {host}; the first version that has it is {first}"
            )
    raise LockError(  # pragma: no cover - every host is had by some version above
        f"{listed.name}: no version resolves on every locked host"
    )


def _locked(listed: Listed, version: str, hosts: tuple[str, ...]) -> Locked:
    if listed.kind not in DOWNLOAD_KINDS:
        return Locked(version)
    return Locked(version, {host: listed.hosts[version][host] for host in hosts})
