"""The catalogue: what a consumer resolves against, from the index or the records.

A consumer never replays a record. It reads the index, one tree per
tool with the versions in order, each host's deployment and each
version's stub, and resolves against that; the authoring site, which
holds the records that build the index, reads them directly and gets
the same catalogue. Either way a deployment's digest is the digest of
its canonical JSON, so a lock written against the records names the
same deployment a consumer fetches from the index.

Reach for [livery.toolroom.store.Catalogue.of_records][] and
[livery.toolroom.store.Catalogue.of_index][].
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from livery.strongroom import Digest, Entry, FolderSource, HttpSource, Source, Tree
from livery.strongroom import Store as ObjectStore
from livery.toolroom.store._home import Home
from livery.toolroom.store._record import (
    TOOL_FILE,
    Deployment,
    Record,
    RecordError,
    resolve,
)

POINTER = "pointer.json"
"""The pointer document at an index's root, naming each tool's tree."""

POINTER_SCHEMA = 1
"""The pointer document's shape this reader understands."""


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
        stubs: Per version read, the digest of its stub in the index;
            empty for a catalogue read from the records.
    """

    name: str
    kind: str
    description: str
    versions: tuple[str, ...]
    hosts: dict[str, dict[str, Digest]]
    stubs: dict[str, Digest] = field(default_factory=dict)
    package: str = ""
    mode: str = ""
    min_version: str = ""


@dataclass(frozen=True)
class Catalogue:
    """Every tool a consumer may require, with what each version resolves to.

    Attributes:
        tools: Tool name to its listing.
    """

    tools: dict[str, Listed]
    _deployments: dict[tuple[str, str, str], Deployment] = field(
        default_factory=dict, repr=False, compare=False
    )
    _store: ObjectStore | None = field(default=None, repr=False, compare=False)

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

    @classmethod
    def of_records(cls, directory: Path) -> Catalogue:
        """The catalogue of the records under *directory*, the authoring site's.

        Raises:
            RecordError: for a record that does not validate.
        """
        tools: dict[str, Listed] = {}
        deployments: dict[tuple[str, str, str], Deployment] = {}
        for path in sorted(directory.iterdir()):
            if not path.is_dir() or not (path / TOOL_FILE).is_file():
                continue
            record = Record.load(path)
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
        return cls(tools, deployments)

    @classmethod
    def of_index(cls, source: str, *, home: Home, offline: bool = False) -> Catalogue:
        """The catalogue of the index at *source*, a directory or an HTTP base URL.

        The pointer is read from the source, and every tree it names is
        fetched into the home's store through the source, so a second
        read answers from the machine.

        Raises:
            CatalogueError: when the pointer cannot be read or is not
                one, or a tree the pointer names cannot be fetched.
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
        tools: dict[str, Listed] = {}
        for name, entry in entries.items():
            tree = _tree(store, Digest.parse(entry["tree"]), where=f"{source} {name}")
            tools[name] = _listed(store, name, tree, entry, where=f"{source} {name}")
        return cls(tools, {}, store)


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


def _listed(
    store: ObjectStore, name: str, tree: Tree, entry: dict[str, Any], *, where: str
) -> Listed:
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
    hosts: dict[str, dict[str, Digest]] = {}
    for version in versions:
        hosts[version] = {}
        member = by_name.get(version)
        if not isinstance(member, Entry):
            raise CatalogueError(f"{where}: the tree has no entry for {version}")
        version_tree = _tree(store, member.digest, where=f"{where} {version}")
        for part in version_tree.entries:
            if part.name == "hosts" and isinstance(part, Entry):
                for host in _tree(
                    store, part.digest, where=f"{where} {version} hosts"
                ).entries:
                    if isinstance(host, Entry):
                        hosts[version][host.name] = host.digest
    stubs: dict[str, Digest] = {}
    named = entry.get("stubs")
    if isinstance(named, str):
        for stub in _tree(store, Digest.parse(named), where=f"{where} stubs").entries:
            if isinstance(stub, Entry):
                stubs[stub.name] = stub.digest
    return Listed(
        name,
        record.kind,
        record.description,
        tuple(versions),
        hosts,
        stubs,
        package=record.package,
        mode=record.mode,
        min_version=record.min_version,
    )
