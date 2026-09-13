"""The engine: a pinned tool installed from verified tiers, linked, emitted, fetched.

`ensure` lands a spec's artifact by its pin through the store's
sources, and through the origin URL unless the store is offline;
extracts it, hoists the root, places a binary as its exe, applies the
shims, collects the directory as a tree, moves `tools/<name>@<version>`
to it write-once, and views the tree at the home's tool directory. A
second `ensure` is a probe. `link` fills a checkout's bin directory
with one link per executable and removes only links it made. `delta`
is the PATH prepend and the env a set of installs contributes. `fetch`
lands every host's artifact into a store at another root, a mirror by
construction.

Nothing here prints; every step reaches the caller through the
progress callback. Reach for [livery.toolroom.store.Store][].
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tarfile
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from livery.strongroom import (
    Digest,
    IntegrityError,
    MissingObject,
    Source,
    Subject,
    ViewRecord,
    fetch_url,
)
from livery.strongroom import Store as ObjectStore
from livery.toolroom.store._home import TOOLS, Home
from livery.toolroom.store._spec import (
    DOWNLOAD_KINDS,
    HOSTS,
    PACKAGE_VAR,
    Definition,
    Spec,
    host_key,
)


def _by() -> Subject:
    """Who moves the `tools/` refs: the person running the engine."""
    import getpass

    try:
        return Subject("person", getpass.getuser())
    except OSError:  # pragma: no cover - a runner without a user name
        return Subject("person", "toolroom")


BY = _by()

LINKS = ".links.json"
"""The manifest a bin directory keeps of the links the engine made there."""

_BUILD = ".build-"
_ARCHIVES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")


class StoreError(Exception):
    """A tool the store cannot supply; the message names the tool and the reason."""


@dataclass(frozen=True)
class Event:
    """One step of the engine's work, as it starts or resolves.

    Attributes:
        name: The tool.
        version: The version.
        action: A short verb: `probe`, `fetch`, `install`, `link`.
        detail: Free text a renderer may show: a URL, a path, a reason.
    """

    name: str
    version: str
    action: str
    detail: str = ""


Progress = Callable[[Event], None]
"""Receives every event; must not raise."""


def silent(_event: Event) -> None:
    """The default progress: drop the event."""


@dataclass(frozen=True)
class Ensured:
    """A tool version the store supplies.

    Attributes:
        name: The tool.
        version: The version.
        installed: True when this call installed it, False when it was
            present already.
        tool_dir: The view a shell runs from.
        definition: The host's definition the install followed.
        tree: The tree `tools/<name>@<version>` names.
    """

    name: str
    version: str
    installed: bool
    tool_dir: Path
    definition: Definition
    tree: Digest

    @property
    def paths(self) -> tuple[Path, ...]:
        """The directories the definition puts on PATH, under the tool directory."""
        return tuple(
            self.tool_dir if entry == "." else self.tool_dir / entry
            for entry in self.definition.paths
        )

    @property
    def env(self) -> dict[str, str]:
        """The definition's env with `PACKAGE_VAR` replaced by the tool directory."""
        return {
            key: str(Path(value.replace(PACKAGE_VAR, str(self.tool_dir))))
            if PACKAGE_VAR in value
            else value
            for key, value in self.definition.env.items()
        }


@dataclass(frozen=True)
class Delta:
    """What a set of installs adds to an environment.

    Attributes:
        paths: Directories to put in front of PATH, first first.
        env: Variables to set, a later install's value winning.
    """

    paths: tuple[Path, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Fetched:
    """One artifact `fetch` landed or found in the mirror.

    Attributes:
        name: The tool.
        version: The version.
        host: The host key.
        digest: The artifact's digest.
        landed: True when the bytes came in on this call.
    """

    name: str
    version: str
    host: str
    digest: Digest
    landed: bool


def _default_host() -> str:
    import platform

    return host_key(platform.system(), platform.machine())


def _download(url: str) -> bytes:
    """The origin's bytes; the seam the tests replace."""
    with fetch_url(url, connect_timeout=10.0, transfer_timeout=600.0) as response:
        return response.read()


download: Callable[[str], bytes] = _download
"""Fetches an origin URL. A variable, so a test hands the engine bytes
without a network."""


def _symlink(target: Path, link: Path) -> None:
    os.symlink(target, link)


symlink: Callable[[Path, Path], None] = _symlink
"""Makes a symlink. A variable, so a test forces the launcher fallback
where the platform would have allowed the link."""


class Store:
    """The tool store: a home's objects, refs and views, driven by specs.

    Args:
        home: The store's directories.
        host: The host key installs follow; the running machine's by
            default.
        sources: The tiers consulted before the origin, in order: a
            folder or an HTTP base in strongroom's layout.
        offline: Never reach an origin; a miss fails closed.
        progress: Receives every event.
    """

    def __init__(
        self,
        home: Home,
        *,
        host: str = "",
        sources: Iterable[Source] = (),
        offline: bool = False,
        progress: Progress = silent,
    ) -> None:
        self.home: Home = home
        self.host: str = host or _default_host()
        if self.host not in HOSTS:
            raise StoreError(f"host {self.host!r} is not one of {', '.join(HOSTS)}")
        self.offline: bool = offline
        self._progress = progress
        self._objects: ObjectStore = home.open_store(sources=sources, offline=offline)

    @property
    def objects(self) -> ObjectStore:
        """The strongroom store underneath."""
        return self._objects

    # --- ensure ------------------------------------------------------------

    def probe(self, spec: Spec, version: str = "") -> Ensured | None:
        """The tool version when it is present here, else None; no source is consulted.

        Present means the ref names a tree and the view directory holds
        every path the view recorded.
        """
        definition = spec.definition_for(self.host, version)
        wanted = version or spec.pinned
        tool_dir = self.home.tool_dir(spec.name, wanted)
        tree = self._objects.ref(TOOLS, f"{spec.name}@{wanted}")
        if tree is None or not self._whole(tool_dir):
            return None
        return Ensured(spec.name, wanted, False, tool_dir, definition, tree)

    def ensure(self, spec: Spec, version: str = "") -> Ensured:
        """Supply the tool at *version*, the pinned version by default.

        Raises:
            SpecError: for a host or version the spec lacks.
            StoreError: for a delegated kind, a miss while offline, a
                mismatch at a tier, an archive that will not extract,
                or a ref already naming another tree.
        """
        if spec.kind not in DOWNLOAD_KINDS:
            raise StoreError(
                f"{spec.name}: kind {spec.kind!r} is delegated to its tool and"
                " not installed through the store yet"
            )
        wanted = version or spec.pinned
        self._progress(Event(spec.name, wanted, "probe"))
        present = self.probe(spec, version)
        if present is not None:
            return present
        definition = spec.definition_for(self.host, version)
        if definition.sha256 is None:  # pragma: no cover - the model refuses this
            raise StoreError(f"{spec.name} {wanted}: no sha256 to land by")
        digest = Digest("sha256", definition.sha256)
        artifact = self._land(spec.name, wanted, definition.url, digest)
        self._progress(Event(spec.name, wanted, "install", str(digest)))
        scratch = self._scratch(spec.name, wanted)
        try:
            self._unpack(spec, definition, artifact, scratch)
            _apply_shims(definition, scratch)
            tree = self._objects.collect(
                scratch,
                sorted(entry.name for entry in scratch.iterdir()),
                executable=(definition.exe,) if definition.exe else (),
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        key = f"{spec.name}@{wanted}"
        current = self._objects.ref(TOOLS, key)
        if current is None:
            self._objects.set_ref(TOOLS, key, tree.digest(), previous=None, by=BY)
        elif current != tree.digest():
            raise StoreError(
                f"{spec.name} {wanted}: tools/{key} already names another tree"
                f" ({current}); the artifact changed under a pinned version, which"
                " a store never accepts"
            )
        tool_dir = self.home.tool_dir(spec.name, wanted)
        stale = self._view_of(tool_dir)
        if stale is not None:
            # A view the store made and something has since damaged:
            # dropped through its record, which removes only what the
            # store created, then filled again.
            self._objects.drop_view(stale.id)
        if tool_dir.exists() and any(tool_dir.iterdir()):
            raise StoreError(
                f"{spec.name} {wanted}: {tool_dir} exists and is not the store's"
                " view; the store never removes what it did not create"
            )
        tool_dir.parent.mkdir(parents=True, exist_ok=True)
        self._objects.view(tree.digest(), tool_dir)
        return Ensured(spec.name, wanted, True, tool_dir, definition, tree.digest())

    def _land(self, name: str, version: str, url: str, digest: Digest) -> Path:
        """The artifact's path here: the tiers first, then the origin unless offline."""
        try:
            return self._objects.fetch(digest)
        except MissingObject as miss:
            if self.offline:
                raise StoreError(
                    f"{name}@{version}: {digest} is in no source and the store is"
                    f" offline; {url} would have satisfied it ({miss})"
                ) from None
        self._progress(Event(name, version, "fetch", url))
        data = download(url)
        try:
            self._objects.land(BytesIO(data), expected=digest)
        except IntegrityError as error:
            raise StoreError(
                f"{name}@{version}: the origin {url} served bytes that are not"
                f" {digest}; refused ({error})"
            ) from None
        return self._objects.path(digest)

    def _scratch(self, name: str, version: str) -> Path:
        self.home.tools.mkdir(parents=True, exist_ok=True)
        scratch = self.home.tools / f"{_BUILD}{name}@{version}-{uuid.uuid4().hex[:8]}"
        scratch.mkdir()
        return scratch

    def _unpack(
        self, spec: Spec, definition: Definition, artifact: Path, into: Path
    ) -> None:
        if spec.kind == "binary":
            if not definition.exe:
                raise StoreError(
                    f"{spec.name}: the binary definition {definition.key} names no exe"
                )
            placed = into / definition.exe
            shutil.copyfile(artifact, placed)
            placed.chmod(
                placed.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            return
        try:
            _extract(artifact, _archive_name(definition.url), into)
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as error:
            raise StoreError(
                f"{spec.name}: the archive at {definition.url} will not extract:"
                f" {error}"
            ) from None
        if definition.root:
            root = into / definition.root
            if not root.is_dir():
                found = ", ".join(sorted(p.name for p in into.iterdir())) or "nothing"
                raise StoreError(
                    f"{spec.name}: the archive has no root {definition.root!r};"
                    f" it holds {found}"
                )
            for member in list(root.iterdir()):
                shutil.move(str(member), into)
            root.rmdir()

    def _view_of(self, tool_dir: Path) -> ViewRecord | None:
        """The record of the view at *tool_dir*, or None when none was made there."""
        wanted = str(tool_dir)
        for record in self._objects.views():
            if record.at == wanted:
                return record
        return None

    def _whole(self, tool_dir: Path) -> bool:
        record = self._view_of(tool_dir)
        if record is None:
            return False
        return all(
            (tool_dir / entry.path).exists()
            for entry in record.entries
            if entry.rung != "parked"
        )

    # --- the bin directory ---------------------------------------------------

    def link(self, ensured: Iterable[Ensured], into: Path) -> tuple[Path, ...]:
        """Fill *into* with one link per executable the installs put on PATH.

        A link is a symlink where the platform allows one and a
        launcher where it refuses; `LINKS` in *into* records what the
        engine made, and a link it did not make is never removed. A
        name two installs both offer goes to the first.

        Returns:
            The links made, in order.
        """
        into.mkdir(parents=True, exist_ok=True)
        manifest = into / LINKS
        previous: list[str] = (
            json.loads(manifest.read_text("utf-8")) if manifest.is_file() else []
        )
        for name in previous:
            stale = into / name
            if stale.is_symlink() or stale.is_file():
                stale.unlink()
        made: list[Path] = []
        names: set[str] = set()
        for item in ensured:
            for directory in item.paths:
                if not directory.is_dir():
                    continue
                for candidate in sorted(directory.iterdir()):
                    if not candidate.is_file() or candidate.name in names:
                        continue
                    if not _is_executable(candidate):
                        continue
                    link = into / candidate.name
                    _make_link(candidate, link)
                    names.add(candidate.name)
                    made.append(link)
                    self._progress(Event(item.name, item.version, "link", str(link)))
        manifest.write_text(json.dumps([p.name for p in made]) + "\n", encoding="utf-8")
        return tuple(made)

    # --- emission ------------------------------------------------------------

    def delta(self, ensured: Iterable[Ensured], bin_dir: Path | None = None) -> Delta:
        """The PATH prepend and env the installs contribute.

        With *bin_dir*, the one directory on PATH is the bin directory
        `link` filled, and the installs contribute their env only.
        """
        paths: list[Path] = [bin_dir] if bin_dir is not None else []
        env: dict[str, str] = {}
        for item in ensured:
            if bin_dir is None:
                for directory in item.paths:
                    if directory not in paths:
                        paths.append(directory)
            env.update(item.env)
        return Delta(tuple(paths), env)

    # --- the mirror ----------------------------------------------------------

    def fetch(
        self, specs: Iterable[Spec], *, hosts: Iterable[str] = HOSTS, into: Path
    ) -> tuple[Fetched, ...]:
        """Land every definition's artifact for *hosts* into a store at *into*.

        The folder is a mirror by construction: a source of this
        engine's layout. Bytes come from this store's sources and, unless
        offline, the origin; a mismatch refuses naming the tool.

        Raises:
            StoreError: for a miss while offline, or a mismatch.
        """
        wanted = tuple(hosts)
        mirror = Home(into).open_store()
        landed: list[Fetched] = []
        for spec in specs:
            if spec.kind not in DOWNLOAD_KINDS:
                continue
            for version in spec.versions.values():
                for host, definition in version.definitions.items():
                    if host not in wanted or definition.sha256 is None:
                        continue
                    digest = Digest("sha256", definition.sha256)
                    if mirror.state(digest) == "present":
                        landed.append(
                            Fetched(spec.name, version.version, host, digest, False)
                        )
                        continue
                    path = self._land(
                        spec.name, version.version, definition.url, digest
                    )
                    with path.open("rb") as handle:
                        mirror.land(handle, expected=digest)
                    landed.append(
                        Fetched(spec.name, version.version, host, digest, True)
                    )
        return tuple(landed)


# --- helpers -----------------------------------------------------------------


def _archive_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].lower()


def _extract(artifact: Path, name: str, into: Path) -> None:
    """Extract a zip or a tar safely; a zip member's mode is restored."""
    if name.endswith(".zip"):
        with zipfile.ZipFile(artifact) as archive:
            archive.extractall(into)
            root = into.resolve()
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0o777
                if not mode or info.is_dir():
                    continue
                target = (into / info.filename).resolve()
                if target.is_relative_to(root) and target.is_file():
                    target.chmod(mode)
        return
    if not name.endswith(_ARCHIVES):
        raise StoreError(f"{name} is not an archive the store extracts")
    with tarfile.open(artifact) as archive:
        archive.extractall(into, filter="data")


def _apply_shims(definition: Definition, into: Path) -> None:
    """Make the definition's shims inside the install: a link name to an executable."""
    for link_name, target_name in definition.shims.items():
        if sys.platform == "win32":
            source = into / f"{target_name}.exe"
            link = into / f"{link_name}.exe"
            if not link.exists():
                shutil.copy2(source, link)
            continue
        link = into / link_name
        if link.is_symlink() or link.exists():
            continue
        symlink(Path(target_name), link)


def _is_executable(path: Path) -> bool:
    if sys.platform == "win32":
        return path.suffix.lower() in (".exe", ".cmd", ".bat")
    return bool(path.stat().st_mode & stat.S_IXUSR)


def _make_link(target: Path, link: Path) -> None:
    """A symlink to *target* at *link*, or a launcher where the platform refuses."""
    try:
        symlink(target, link)
    except OSError:
        _launcher(target, link)


def _launcher(target: Path, link: Path) -> None:
    """A small script at *link* that runs *target* with the arguments it got."""
    if sys.platform == "win32":
        script = link.with_suffix(".cmd")
        script.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
        return
    link.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
    link.chmod(link.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
