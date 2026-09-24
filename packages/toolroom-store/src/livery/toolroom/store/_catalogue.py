"""The catalogue: what a consumer resolves against, from the index or the records.

A consumer never replays a record. It reads the index, one tree per
tool with the versions in order, each host's deployment and each
version's stub, and resolves against that; the authoring site, which
holds the records that build the index, reads them directly and gets
the same catalogue. An index is read on demand: the pointer first, a
tool's tree when the tool is asked for, a version's hosts when a lock
needs them, so listing eleven locked tools costs eleven trees and
never every version of every tool. Either way a deployment's digest is the digest of
its canonical JSON, so a lock written against the records names the
same deployment a consumer fetches from the index.

Reach for [livery.toolroom.store.Catalogue.of_records][] and
[livery.toolroom.store.Catalogue.of_index][].
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from livery.strongroom import Digest, Entry, FolderSource, HttpSource, Source, Tree
from livery.strongroom import Store as ObjectStore
from livery.toolroom.store._fingerprint import tree_fingerprint
from livery.toolroom.store._home import Home
from livery.toolroom.store._record import (
    Deployment,
    Observation,
    Record,
    RecordError,
    observations,
    records_in,
    resolve,
    surface_at,
)
from livery.toolroom.store._stub import render_observation

POINTER = "pointer.json"
"""The pointer document at an index's root, naming each tool's tree."""

POINTER_SCHEMA = 1
"""The pointer document's shape this reader understands."""

BUILD_FILE = "build.json"
"""The build's record beside the pointer: what it fingerprinted, so a gate can ask."""


class CatalogueError(ValueError):
    """An index or a records directory that cannot be read as a catalogue."""


@dataclass(frozen=True)
class Listed:
    """One tool as the catalogue lists it.

    Attributes:
        name: The tool's name.
        kind: The installer kind, one of the record's kinds.
        description: One line on what the tool is.
        package: What a delegated kind's installer installs, when it
            differs from the name.
        mode: The record's materialisation mode; empty for the kind's default.
        versions: Every version tracked, oldest first, as the record
            orders them.
        hosts: Per version, the host keys the version has an artifact
            for, with the digest of each host's deployment.
    """

    name: str
    kind: str
    description: str
    versions: tuple[str, ...]
    hosts: Mapping[str, Mapping[str, Digest]]
    package: str = ""
    mode: str = ""
    min_version: str = ""


@dataclass(frozen=True)
class Catalogue:
    """Every tool a consumer may require, with what each version resolves to.

    Attributes:
        tools: Tool name to its listing.
    """

    tools: Mapping[str, Listed]
    _deployments: dict[tuple[str, str, str], Deployment] = field(
        default_factory=dict, repr=False, compare=False
    )
    _store: ObjectStore | None = field(default=None, repr=False, compare=False)
    _records: dict[str, Record] = field(default_factory=dict, repr=False, compare=False)

    def listed(self, name: str) -> Listed:
        """The listing of *name*.

        Raises:
            CatalogueError: when the catalogue has no such tool, naming
                what it has.
        """
        found = self.tools.get(name)
        if found is None:
            known = ", ".join(sorted(self.tools)) or "nothing"
            raise CatalogueError(f"no record of {name}; the catalogue lists {known}")
        return found

    def deployment(self, name: str, version: str, host: str) -> Deployment:
        """The deployment of *name* at *version* on *host*.

        From the records it is resolved; from the index it is the blob
        the tool's tree names, fetched through the sources.

        Raises:
            CatalogueError: when the version has no such host, or the
                blob cannot be read.
        """
        listed = self.listed(name)
        digest = listed.hosts.get(version, {}).get(host)
        if digest is None:
            has = ", ".join(listed.hosts.get(version, {})) or "none"
            raise CatalogueError(
                f"{name} {version}: no host {host}; the version has {has}"
            )
        held = self._deployments.get((name, version, host))
        if held is not None:
            return held
        if self._store is None:  # pragma: no cover - records fill every deployment
            raise CatalogueError(f"{name} {version} {host}: no deployment was read")
        try:
            data = json.loads(self._store.fetch(digest).read_bytes())
        except Exception as error:
            raise CatalogueError(
                f"{name} {version} {host}: the deployment {digest} cannot be read:"
                f" {error}"
            ) from error
        found = Deployment.from_json(data, where=f"{name} {version} {host}")
        self._deployments[(name, version, host)] = found
        return found

    def stub(self, name: str, version: str) -> str:
        """The stub of *name* at *version*, rendered from the version's surface.

        From the records the surface is the version's resolved
        observation; from the index it is the version's `observation`
        and `surface` blobs. Either way the text is the same rendering,
        the version's own verbs and options, exactly what a workspace
        locking that version types against.

        Raises:
            CatalogueError: when the version was never read, or its
                blobs cannot be read.
        """
        self.listed(name)
        record = self._records.get(name)
        if record is not None:
            found = surface_at(record, version) if version in record.versions else None
            if found is None:
                read = ", ".join(o.version for o in observations(record)) or "none"
                raise CatalogueError(
                    f"{name} {version}: never read; the versions read are {read}"
                )
            return render_observation(name, found)
        tools = self.tools
        if not isinstance(tools, _LazyTools):  # pragma: no cover - records fill it
            raise CatalogueError(f"{name} {version}: no surface was read")
        return render_observation(name, tools.observation(name, version))

    @classmethod
    def of_records(cls, directory: Path) -> Catalogue:
        """The catalogue of the records under *directory*, the authoring site's.

        Raises:
            RecordError: for a record that does not validate.
        """
        tools: dict[str, Listed] = {}
        deployments: dict[tuple[str, str, str], Deployment] = {}
        records: dict[str, Record] = {}
        for path in records_in(directory):
            record = Record.load(path)
            records[record.name] = record
            hosts: dict[str, dict[str, Digest]] = {}
            for delta in record.deltas:
                hosts[delta.version] = {}
                for host in delta.hosts:
                    deployment = resolve(record, delta.version, host)
                    deployments[(record.name, delta.version, host)] = deployment
                    hosts[delta.version][host] = deployment.digest()
            tools[record.name] = Listed(
                record.name,
                record.kind,
                record.description,
                record.versions,
                hosts,
                package=record.package,
                mode=record.mode,
                min_version=record.min_version,
            )
        return cls(tools, deployments, None, records)

    @classmethod
    def of_index(cls, source: str, *, home: Home, offline: bool = False) -> Catalogue:
        """The catalogue of the index at *source*, a directory or an HTTP base URL.

        The pointer is read from the source; a tool's tree is fetched
        into the home's store through the source when the tool is first
        asked for, and a version's hosts when they are, so a second read
        answers from the machine and a read touches only what it needs.

        Raises:
            CatalogueError: when the pointer cannot be read or is not
                one; a tree the pointer names and cannot be fetched
                refuses when its tool is asked for.
        """
        pointer = _read_pointer(source)
        entries: dict[str, dict[str, Any]] = {}
        for name, entry in sorted(pointer["tools"].items()):
            if not isinstance(entry, dict) or not isinstance(entry.get("tree"), str):
                raise CatalogueError(
                    f"{source}: the pointer's entry for {name} is not one"
                )
            entries[str(name)] = entry
        store = home.open_store(sources=(_source_of(source),), offline=offline)
        return cls(_LazyTools(store, entries, source), {}, store)


class _LazyTools(Mapping[str, Listed]):
    """The pointer's tools, each listed from its tree on first access."""

    def __init__(
        self, store: ObjectStore, entries: dict[str, dict[str, Any]], source: str
    ) -> None:
        self._store = store
        self._entries = entries
        self._source = source
        self._read: dict[str, Listed] = {}
        self._members: dict[str, dict[str, Any]] = {}

    def __getitem__(self, name: str) -> Listed:
        held = self._read.get(name)
        if held is None:
            entry = self._entries[name]
            where = f"{self._source} {name}"
            tree = _tree(self._store, Digest.parse(entry["tree"]), where=where)
            self._members[name] = {member.name: member for member in tree.entries}
            held = self._read[name] = _listed(self._store, name, tree, where=where)
        return held

    def observation(self, name: str, version: str) -> Observation:
        """The version's observation, its surface read from the index.

        Raises:
            CatalogueError: when the version was never read, or its
                blobs cannot be read.
        """
        listed = self[name]
        if version not in listed.versions:
            read = ", ".join(listed.versions) or "none"
            raise CatalogueError(
                f"{name} {version}: never read; the versions read are {read}"
            )
        where = f"{self._source} {name} {version}"
        member = self._members[name].get(version)
        if not isinstance(member, Entry):
            raise CatalogueError(f"{where}: the tree has no entry for {version}")
        parts = {
            p.name: p for p in _tree(self._store, member.digest, where=where).entries
        }
        seen = parts.get("observation")
        surface = parts.get("surface")
        if not isinstance(seen, Entry) or not isinstance(surface, Entry):
            raise CatalogueError(f"{where}: the version was never read")
        try:
            about = json.loads(self._store.fetch(seen.digest).read_bytes())
            verbs = json.loads(self._store.fetch(surface.digest).read_bytes())
        except Exception as error:
            raise CatalogueError(
                f"{where}: the surface cannot be read: {error}"
            ) from error
        if not isinstance(about, dict) or not isinstance(verbs, dict):
            raise CatalogueError(f"{where}: the surface is not one")
        return Observation(
            version,
            str(about.get("date", "")),
            tuple(str(p) for p in about.get("platforms", ())),
            int(about.get("extractor", 0) or 0),
            str(about.get("help", "")),
            verbs,
            {
                str(verb): {
                    str(o): tuple(str(w) for w in who) for o, who in options.items()
                }
                for verb, options in (about.get("absent") or {}).items()
                if isinstance(options, dict)
            },
        )

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


class _LazyHosts(Mapping[str, Mapping[str, Digest]]):
    """Per version the hosts' deployment digests; a version's tree is read on access."""

    def __init__(
        self,
        store: ObjectStore,
        members: dict[str, Any],
        versions: tuple[str, ...],
        where: str,
    ) -> None:
        self._store = store
        self._members = members
        self._versions = versions
        self._where = where
        self._read: dict[str, dict[str, Digest]] = {}

    def __getitem__(self, version: str) -> Mapping[str, Digest]:
        if version not in self._versions:
            raise KeyError(version)
        held = self._read.get(version)
        if held is None:
            held = self._read[version] = _hosts_of(
                self._store, self._members, version, where=self._where
            )
        return held

    def __iter__(self) -> Iterator[str]:
        return iter(self._versions)

    def __len__(self) -> int:
        return len(self._versions)


def read_pointer(source: str) -> dict[str, Any]:
    """The pointer document at *source*, a directory or an HTTP base URL.

    Raises:
        CatalogueError: when no pointer can be read there, or what is
            read is not one.
    """
    return _read_pointer(source)


def build_current(index: Path) -> bool:
    """Whether the index at *index* stands as its build record fingerprints it.

    The build writes `build.json` beside the pointer, naming the records
    directory with a stat fingerprint. Current means the pointer is
    there, the record reads, and the fingerprint stands; anything else,
    an absent record included, is not current. Stats alone are read, so the answer costs
    milliseconds and a caller can skip a build, or the spawn of one,
    without reading a record.
    """
    try:
        held = json.loads((index / BUILD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(held, dict) or not (index / POINTER).is_file():
        return False
    records = held.get("records")
    if not isinstance(records, dict) or not isinstance(records.get("path"), str):
        return False
    return bool(tree_fingerprint([records["path"]]) == records.get("fingerprint"))


def _read_pointer(source: str) -> dict[str, Any]:
    parsed = urlparse(source)
    try:
        if parsed.scheme in ("http", "https"):
            from livery.toolroom.store._engine import download

            text = download(source.rstrip("/") + "/" + POINTER).decode("utf-8")
        else:
            text = (Path(source) / POINTER).read_text(encoding="utf-8")
        found = json.loads(text)
    except (OSError, ValueError) as error:
        raise CatalogueError(f"{source}: no pointer can be read ({error})") from None
    if (
        not isinstance(found, dict)
        or found.get("schema") != POINTER_SCHEMA
        or not isinstance(found.get("tools"), dict)
    ):
        raise CatalogueError(
            f"{source}: {POINTER} is not a pointer document of schema {POINTER_SCHEMA}"
        )
    return found


def _source_of(source: str) -> Source:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return HttpSource(source)
    return FolderSource(Path(source))


def _tree(store: ObjectStore, digest: Digest, *, where: str) -> Tree:
    try:
        return Tree.decode(store.fetch(digest).read_bytes())
    except Exception as error:
        raise CatalogueError(
            f"{where}: the tree {digest} cannot be read: {error}"
        ) from error


def _listed(store: ObjectStore, name: str, tree: Tree, *, where: str) -> Listed:
    by_name = {member.name: member for member in tree.entries}
    axis = by_name.get("tool")
    order = by_name.get("versions")
    if not isinstance(axis, Entry) or not isinstance(order, Entry):
        raise CatalogueError(f"{where}: the tool's tree names no tool or versions")
    try:
        record = Record.from_json(
            json.loads(store.fetch(axis.digest).read_bytes()), where=f"{where} tool"
        )
        versions = json.loads(store.fetch(order.digest).read_bytes())
    except (RecordError, ValueError, OSError) as error:
        raise CatalogueError(
            f"{where}: the tool's axis cannot be read: {error}"
        ) from None
    if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
        raise CatalogueError(f"{where}: the versions blob is not a list of strings")
    hosts = _LazyHosts(store, dict(by_name), tuple(versions), where)
    return Listed(
        name,
        record.kind,
        record.description,
        tuple(versions),
        hosts,
        package=record.package,
        mode=record.mode,
        min_version=record.min_version,
    )


def _hosts_of(
    store: ObjectStore, members: dict[str, Any], version: str, *, where: str
) -> dict[str, Digest]:
    """The hosts' deployment digests of *version*, from its tree under the tool's."""
    member = members.get(version)
    if not isinstance(member, Entry):
        raise CatalogueError(f"{where}: the tree has no entry for {version}")
    found: dict[str, Digest] = {}
    version_tree = _tree(store, member.digest, where=f"{where} {version}")
    for part in version_tree.entries:
        if part.name == "hosts" and isinstance(part, Entry):
            for host in _tree(
                store, part.digest, where=f"{where} {version} hosts"
            ).entries:
                if isinstance(host, Entry):
                    found[host.name] = host.digest
    return found
