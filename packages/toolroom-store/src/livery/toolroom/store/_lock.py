"""The lock: one version per tool for the repository, resolved against the catalogue.

A requirement is a tool's name with a floor, declared at one of the
sites, and may name the hosts it applies to; the sites' requirements
union, and the lock takes for each tool the newest version the
catalogue lists that satisfies every floor and resolves on every
host the tool is required on. A version of a downloaded kind resolves
on a host when it has that host's artifact; a delegated kind resolves
everywhere its installer does. A requirement that cannot be satisfied
refuses naming the tool, each floor and its site, and for a host that
no eligible version has, the first version that has it. A tool
required on some of the locked hosts alone is locked on those, and
the entry says which; one whose scope names no locked host is not
locked at all.

The lock is a file in the repository, `tools.lock`, so every checkout
and every runner installs the same version; it moves only through a
lock or an upgrade, never on its own.

Reach for [livery.toolroom.store.Requirement][],
[livery.toolroom.store.resolve_lock][] and [livery.toolroom.store.Lock][].
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from livery.strongroom import Digest
from livery.toolroom.store._catalogue import Catalogue, CatalogueError, Listed
from livery.toolroom.store._record import DOWNLOAD_KINDS, HOSTS, version_key
from livery.toolroom.store._requirement import Scope, Spec, SpecError
from livery.toolroom.tools import version_tuple

LOCK_FILE = "tools.lock"
"""The lock's name at the repository root."""

LOCK_SCHEMA = 1
"""The lock's shape. Bumped when a reader must know."""

GRAPHS = "tools.graphs"
"""The directory beside the lock holding one resolved graph per delegated tool."""


class LockError(ValueError):
    """A requirement the catalogue cannot satisfy; the message names why."""


@dataclass(frozen=True)
class Requirement:
    """One site's requirement of a tool: a floor, or any version, on some hosts or all.

    Attributes:
        name: The tool's name.
        floor: The lowest version that satisfies; empty for any.
        site: Where the requirement was declared, for a refusal.
        hosts: The hosts the requirement applies to, each a platform
            (``windows``, every locked host of that platform) or a
            host key (``windows-x64``); empty for every locked host.
        optional: Whether ``?`` marks it optional: locked where the
            catalogue can serve it, never refused.
        exclude: The platforms and host keys ``!`` removes from its
            hosts.
    """

    name: str
    floor: str = ""
    site: str = ""
    hosts: tuple[str, ...] = ()
    optional: bool = False
    exclude: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str, *, site: str = "") -> Requirement:
        """A requirement from its spelling, ``name?>=floor@scope``.

        Every part after the name is optional.

        ``?`` marks it optional. The scope after ``@`` is comma-separated
        platforms or host keys, each excluded with a leading ``!``:
        ``dotnet_coverage@windows``, ``tea>=1.1@linux,macos-arm``,
        ``docker?@!windows-arm``. The grammar is
        [livery.toolroom.store.Spec][]'s; a tool requirement takes
        no options.

        Raises:
            LockError: for a spelling that is none of these, or a
                scope naming no host or a token that is neither a
                platform nor a host key.
        """
        where = site or "a requirement"
        try:
            spec = Spec.parse(text, where=where)
        except SpecError as error:
            raise LockError(str(error)) from None
        if spec.options:
            raise LockError(
                f"{where}: {text!r} names options; a tool requirement takes"
                " none, spell it `name?>=floor@scope`"
            )
        return cls(
            spec.name,
            spec.floor,
            site,
            spec.scope.include,
            spec.optional,
            spec.scope.exclude,
        )

    def __str__(self) -> str:
        return str(
            Spec(
                self.name,
                optional=self.optional,
                floor=self.floor,
                scope=Scope(self.hosts, self.exclude),
            )
        )

    def satisfied_by(self, version: str) -> bool:
        """Whether *version* is at or above the floor."""
        return not self.floor or version_tuple(version) >= version_tuple(self.floor)

    def on(self, locked: Iterable[str]) -> tuple[str, ...]:
        """The hosts of *locked* this requirement applies to, in their order."""
        return Scope(self.hosts, self.exclude).hosts(locked)


@dataclass(frozen=True)
class Graph:
    """The resolved graph of a delegated tool's version, as the lock names it.

    A delegated kind installs a graph, not a file: the package named
    is one of many its installer resolves, and the transitive part
    moves between resolves at one version. The graph pins all of it,
    in the installer's own format, in a file beside the lock; the
    lock carries its name and its digest, so the file that installs
    is the file that was resolved.

    Attributes:
        file: The file's name under `GRAPHS`, beside the lock.
        digest: The file's bytes, so a graph edited by hand is caught.
        by: What resolved it, name and version, for the day an
            install refuses and someone must know what wrote it.
    """

    file: str
    digest: Digest
    by: str = ""

    def to_json(self) -> dict[str, Any]:
        """The graph as a JSON object."""
        return {"file": self.file, "digest": str(self.digest), "by": self.by}

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Graph:
        """The graph in *value*.

        Raises:
            LockError: when it is not one, naming *where*.
        """
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("file"), str)
            or not value["file"]
            or not isinstance(value.get("digest"), str)
        ):
            raise LockError(f"{where}: the graph is not one")
        try:
            digest = Digest.parse(value["digest"])
        except ValueError as error:
            raise LockError(f"{where}: {error}") from None
        return cls(value["file"], digest, str(value.get("by", "")))


@dataclass(frozen=True)
class Locked:
    """One tool as the lock holds it.

    Attributes:
        version: The version locked.
        hosts: Per host the tool is locked on, the deployment's
            digest; empty for a delegated kind, whose installer
            resolves the host.
        graph: The resolved graph of a delegated kind's version, or
            None for a downloaded kind and for a delegated one locked
            before a graph was written for it.
        on: The locked hosts the tool is required on, when they are
            fewer than the lock's; empty when it is required on every
            one.
        allow_host: Whether a copy of the tool already on the machine
            may serve instead of the locked version, when it satisfies
            the requirement's floor; the workspace's `host-allowed`
            list says so, and the entry carries `allow-host` so every
            checkout and CI agree on which tools may vary.
        optional: Whether every site that requires the tool marks it
            optional, so a host it is not locked on lacks it by design.
    """

    version: str
    hosts: dict[str, Digest] = field(default_factory=dict)
    graph: Graph | None = None
    on: tuple[str, ...] = ()
    allow_host: bool = False
    optional: bool = False

    def applies(self, host: str) -> bool:
        """Whether the tool is locked for *host*."""
        return not self.on or host in self.on

    def to_json(self) -> dict[str, Any]:
        """The entry as a JSON object."""
        out: dict[str, Any] = {
            "version": self.version,
            "hosts": {host: str(digest) for host, digest in sorted(self.hosts.items())},
        }
        if self.graph is not None:
            out["graph"] = self.graph.to_json()
        if self.on:
            out["on"] = list(self.on)
        if self.allow_host:
            out["allow-host"] = True
        if self.optional:
            out["optional"] = True
        return out


@dataclass(frozen=True)
class Lock:
    """The repository's lock: the hosts it locks for and one version per tool.

    Attributes:
        hosts: The host keys every locked download resolves on.
        tools: Tool name to its locked version and deployments.
        notes: What the resolution left out and why: an optional tool
            the catalogue could not serve. Not written to the file.
    """

    hosts: tuple[str, ...]
    tools: dict[str, Locked]
    notes: tuple[str, ...] = field(default=(), compare=False)

    def on_host(self, host: str) -> tuple[str, ...]:
        """The tools locked for *host*, in name order."""
        return tuple(
            name for name in sorted(self.tools) if self.tools[name].applies(host)
        )

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
            raw = entry.get("graph")
            graph = (
                None if raw is None else Graph.from_json(raw, where=f"{path}: {name}")
            )
            scope = entry.get("on", [])
            if not isinstance(scope, list) or not all(
                isinstance(h, str) and h in hosts for h in scope
            ):
                raise LockError(
                    f"{path}: {name}: `on` names a host outside the lock's"
                    f" ({', '.join(hosts)})"
                )
            allowed = entry.get("allow-host", False)
            if not isinstance(allowed, bool):
                raise LockError(
                    f"{path}: {name}: `allow-host` is {allowed!r}, not true or false"
                )
            optional = entry.get("optional", False)
            if not isinstance(optional, bool):
                raise LockError(
                    f"{path}: {name}: `optional` is {optional!r}, not true or false"
                )
            locked[str(name)] = Locked(
                entry["version"], digests, graph, tuple(scope), allowed, optional
            )
        return cls(tuple(hosts), locked)


def resolve_lock(
    catalogue: Catalogue,
    requirements: Iterable[Requirement],
    *,
    hosts: Iterable[str],
    keep: Lock | None = None,
    upgrade: Iterable[str] = (),
    host_allowed: Iterable[str] = (),
) -> Lock:
    """The lock for *requirements* against *catalogue*, on *hosts*.

    Each tool takes the newest version that satisfies every floor
    declared for it and resolves on every host it is required on: the
    union of its requirements' scopes, the whole of *hosts* for one
    with no scope. A tool required on fewer hosts than the lock's is
    locked on those and its entry names them; one whose scopes reach
    no locked host is left out. A tool *keep* already locks stays at
    its version when that version still satisfies and resolves,
    unless it is named in *upgrade*; a tool no requirement names any
    more leaves the lock. A tool named in *host_allowed* carries the
    allowance in its entry, whatever *keep* said of it: the allowance
    is the sites' current word, never a kept one.

    An optional requirement (``?``) is never refused. Its hosts join a
    tool's entry where the version the required sites chose has an
    artifact; a tool every site marks optional takes the newest
    version satisfying its floors that resolves somewhere, is locked
    where it resolves, and its entry says ``optional``. What is left
    out, a host or a whole tool, is named in the lock's ``notes``.

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
    allowed = set(host_allowed)
    tools: dict[str, Locked] = {}
    notes: list[str] = []
    for name in sorted(by_tool):
        wants = by_tool[name]
        required = [want for want in wants if not want.optional]
        must = tuple(
            host
            for host in locked_hosts
            if any(host in want.on(locked_hosts) for want in required)
        )
        may = tuple(
            host
            for host in locked_hosts
            if host not in must
            and any(host in want.on(locked_hosts) for want in wants if want.optional)
        )
        if not must and not may:
            continue
        sites = ", ".join(sorted({w.site for w in wants if w.site})) or "a requirement"
        try:
            listed = catalogue.listed(name)
        except CatalogueError as error:
            if not required:
                notes.append(f"{name}: optional and not catalogued ({error}); left out")
                continue
            raise LockError(f"{error}; required by {sites}") from None
        held = keep.tools.get(name) if keep and name not in moving else None
        if required:
            version = (
                held.version
                if held is not None and _eligible(listed, held.version, wants, must)
                else _newest(listed, wants, must)
            )
        else:
            found = _newest_anywhere(listed, wants, may, held)
            if found is None:
                notes.append(
                    f"{name}: optional, and no version satisfying"
                    f" {', '.join(str(w) for w in wants)} resolves on"
                    f" {', '.join(may)}; left out"
                )
                continue
            version = found
        reach = (*must, *(host for host in may if _resolves(listed, version, (host,))))
        on = tuple(host for host in locked_hosts if host in reach)
        absent = [host for host in may if host not in on]
        if absent:
            notes.append(
                f"{name}: optional, {version} has no artifact for"
                f" {', '.join(absent)}; left out there"
            )
        scope = on if on != locked_hosts else ()
        tools[name] = replace(
            _locked(listed, version, on, scope),
            allow_host=name in allowed,
            optional=not required,
        )
    return Lock(locked_hosts, tools, tuple(notes))


def _newest_anywhere(
    listed: Listed,
    wants: list[Requirement],
    hosts: tuple[str, ...],
    held: Locked | None,
) -> str | None:
    """For an optional tool: the version to lock, or None when none resolves anywhere.

    The version *held* stands while it still satisfies and resolves on
    a host it was locked on; otherwise the newest version satisfying
    every floor that resolves on at least one of *hosts*.
    """

    def reaches(version: str) -> bool:
        return any(_resolves(listed, version, (host,)) for host in hosts)

    if (
        held is not None
        and held.version in listed.versions
        and reaches(held.version)
        and all(want.satisfied_by(held.version) for want in wants)
    ):
        return held.version
    ordered = sorted(listed.versions, key=version_key)
    for version in reversed(ordered):
        if all(want.satisfied_by(version) for want in wants) and reaches(version):
            return version
    return None


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


def _locked(
    listed: Listed, version: str, hosts: tuple[str, ...], scope: tuple[str, ...]
) -> Locked:
    if listed.kind not in DOWNLOAD_KINDS:
        return Locked(version, on=scope)
    return Locked(
        version, {host: listed.hosts[version][host] for host in hosts}, on=scope
    )
