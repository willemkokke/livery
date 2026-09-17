"""The engine: a pinned tool installed from verified tiers, linked, emitted, fetched.

`ensure` lands a version's artifact by the record's digest through the store's
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

import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
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
    Unreachable,
    ViewRecord,
    fetch_url,
)
from livery.strongroom import Store as ObjectStore
from livery.toolroom.store._home import TOOLS, Home
from livery.toolroom.store._record import (
    DOWNLOAD_KINDS,
    HOSTS,
    PACKAGE_VAR,
    Deployment,
    Record,
    host_key,
    resolve,
)
from livery.toolroom.tools import version_tuple


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
        tool_dir: The view a shell runs from; a delegated kind's own
            directory under the home, or a system tool's directory.
        deployment: The host's deployment the install followed; for a
            delegated kind, the entry points its installer wrote under
            `bin`, and nothing else.
        tree: The tree `tools/<name>@<version>` names; `None` for a
            delegated kind, whose bytes never went through the store.
    """

    name: str
    version: str
    installed: bool
    tool_dir: Path
    deployment: Deployment
    tree: Digest | None

    @property
    def paths(self) -> tuple[Path, ...]:
        """The directories the deployment puts on PATH, under the tool directory."""
        return tuple(
            self.tool_dir if entry == "." else self.tool_dir / entry
            for entry in self.deployment.paths
        )

    @property
    def env(self) -> dict[str, str]:
        """The deployment's env with `PACKAGE_VAR` replaced by the tool directory."""
        return {
            key: str(Path(value.replace(PACKAGE_VAR, str(self.tool_dir))))
            if PACKAGE_VAR in value
            else value
            for key, value in self.deployment.env.items()
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


def _run_installer(argv: list[str], env: dict[str, str]) -> int:
    """Run a delegated kind's installer with *env* over the process's; its exit code."""
    completed = subprocess.run(
        argv, env={**os.environ, **env}, cwd=Path.cwd(), check=False
    )
    return completed.returncode


run_installer: Callable[[list[str], dict[str, str]], int] = _run_installer
"""Runs a delegated kind's installer. A variable, so a test supplies the
install without uv or bun."""


def _read_version(argv: list[str]) -> str:
    """What a tool prints for *argv*, stdout then stderr; empty when it will not run."""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            cwd=Path.cwd(),
            env=dict(os.environ),
        )
    except OSError:
        return ""
    return completed.stdout + completed.stderr


read_version: Callable[[list[str]], str] = _read_version
"""Reads what a system tool prints for its version flag. A variable, so a
test answers for the machine."""


def _version_in(text: str) -> str:
    """The first version-shaped run in *text*, or empty."""
    match = re.search(r"\d+(?:\.\d+)+", text)
    return match[0] if match else ""


def _launchers(bin_dir: Path) -> tuple[str, ...]:
    """The entry points an installer wrote under *bin_dir*, install-relative."""
    if not bin_dir.is_dir():
        return ()
    return tuple(
        f"bin/{path.name}" for path in sorted(bin_dir.iterdir()) if path.is_file()
    )


def _delegated(entry_points: tuple[str, ...]) -> Deployment:
    """The deployment a delegated kind reports: its launchers under `bin`."""
    return Deployment(
        "", "", "", "", entry_points, ("bin",) if entry_points else (), {}, {}, ()
    )


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

    def probe(self, record: Record, version: str) -> Ensured | None:
        """The tool version when it is present here, else None; no source is consulted.

        Present means the ref names a tree and the view directory holds
        every path the view recorded; for a delegated kind, that its
        installer's directory holds what it installed.

        Raises:
            RecordError: for a version the record does not track, or a
                version without this host.
        """
        if record.kind not in DOWNLOAD_KINDS:
            return self._probe_delegated(record.name, record.kind, version)
        deployment = resolve(record, version, self.host)
        return self._probe_download(record.name, version, deployment)

    def ensure(self, record: Record, version: str) -> Ensured:
        """Supply the tool at *version* as its record resolves it on this host.

        Raises:
            RecordError: for a version the record does not track, or a
                version without this host.
            StoreError: as `supply` raises.
        """
        deployment = (
            resolve(record, version, self.host)
            if record.kind in DOWNLOAD_KINDS
            else None
        )
        return self.supply(
            record.name,
            record.kind,
            version,
            deployment,
            package=record.package,
            min_version=record.min_version,
        )

    def supply(
        self,
        name: str,
        kind: str,
        version: str,
        deployment: Deployment | None = None,
        *,
        package: str = "",
        min_version: str = "",
    ) -> Ensured:
        """Supply *name* at *version* from *deployment* or through its installer.

        An `archive` or `binary` lands its artifact by the deployment's
        digest, extracts it, collects it as a tree and views it. A
        `uv-tool` is installed by uv into its own directory under the
        home, *package* naming what uv installs when it differs from the
        tool's name, and its launchers are the entry points. A
        `system-check` is the machine's own tool, found on PATH and
        held to *min_version*. `bun-install` and `uv-python` are not
        supplied through the store yet and refuse naming the kind.

        Raises:
            StoreError: for a kind the store cannot supply, a downloaded
                kind with no deployment, a miss while offline, a
                mismatch at a tier, an archive that will not extract, an
                installer that failed, a system tool missing or below
                its floor, or a ref already naming another tree.
        """
        if kind in DOWNLOAD_KINDS:
            if deployment is None:
                raise StoreError(
                    f"{name}: a {kind} needs its deployment to be supplied"
                )
            return self._supply_download(name, kind, version, deployment)
        if kind == "uv-tool":
            return self._supply_uv_tool(name, version, package or name)
        if kind == "system-check":
            return self._supply_system(name, version, min_version)
        raise StoreError(
            f"{name}: kind {kind!r} is delegated to its tool and not supplied"
            " through the store yet"
        )

    def _probe_download(
        self, name: str, version: str, deployment: Deployment
    ) -> Ensured | None:
        tool_dir = self.home.tool_dir(name, version)
        tree = self._objects.ref(TOOLS, f"{name}@{version}")
        if tree is None or not self._whole(tool_dir):
            return None
        return Ensured(name, version, False, tool_dir, deployment, tree)

    def _supply_download(
        self, name: str, kind: str, version: str, deployment: Deployment
    ) -> Ensured:
        self._progress(Event(name, version, "probe"))
        present = self._probe_download(name, version, deployment)
        if present is not None:
            return present
        digest = Digest("sha256", deployment.sha256)
        artifact = self._land(name, version, deployment.url, digest)
        self._progress(Event(name, version, "install", str(digest)))
        scratch = self._scratch(name, version)
        try:
            self._unpack(name, kind, deployment, artifact, scratch)
            _exclude(deployment, scratch)
            _apply_shims(deployment, scratch)
            _require_entry_points(name, version, self.host, deployment, scratch)
            _annotate_modes(deployment, scratch)
            tree = self._objects.collect(
                scratch,
                sorted(entry.name for entry in scratch.iterdir()),
                executable=deployment.entry_points,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        key = f"{name}@{version}"
        current = self._objects.ref(TOOLS, key)
        if current is None:
            self._objects.set_ref(TOOLS, key, tree.digest(), previous=None, by=BY)
        elif current != tree.digest():
            raise StoreError(
                f"{name} {version}: tools/{key} already names another tree"
                f" ({current}); the artifact changed under a pinned version, which"
                " a store never accepts"
            )
        tool_dir = self.home.tool_dir(name, version)
        stale = self._view_of(tool_dir)
        if stale is not None:
            # A view the store made and something has since damaged:
            # dropped through its record, which removes only what the
            # store created, then filled again.
            self._objects.drop_view(stale.id)
        if tool_dir.exists() and any(tool_dir.iterdir()):
            raise StoreError(
                f"{name} {version}: {tool_dir} exists and is not the store's"
                " view; the store never removes what it did not create"
            )
        tool_dir.parent.mkdir(parents=True, exist_ok=True)
        self._objects.view(tree.digest(), tool_dir)
        return Ensured(name, version, True, tool_dir, deployment, tree.digest())

    # --- the delegated kinds --------------------------------------------------

    def _probe_delegated(self, name: str, kind: str, version: str) -> Ensured | None:
        if kind == "uv-tool":
            tool_dir = self.home.uv / "tools" / f"{name}@{version}"
            launchers = _launchers(tool_dir / "bin")
            if not launchers:
                return None
            return Ensured(name, version, False, tool_dir, _delegated(launchers), None)
        if kind == "system-check":
            found = shutil.which(name)
            if found is None:
                return None
            return Ensured(
                name, version, False, Path(found).parent, _delegated(()), None
            )
        return None

    def _supply_uv_tool(self, name: str, version: str, package: str) -> Ensured:
        """Install *package* at *version* through uv, into the tool's own directory.

        uv keeps one install per tool name in its tool directory, so
        each version gets a tool directory of its own, with its
        launchers in `bin` beside it; the launchers are the entry
        points, which uv writes from the package's own console scripts.
        """
        self._progress(Event(name, version, "probe"))
        present = self._probe_delegated(name, "uv-tool", version)
        if present is not None:
            return present
        tool_dir = self.home.uv / "tools" / f"{name}@{version}"
        bin_dir = tool_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        argv = ["uv", "tool", "install", f"{package}=={version}"]
        if self.offline:
            argv.append("--offline")
        self._progress(Event(name, version, "install", " ".join(argv)))
        code = run_installer(
            argv,
            {
                "UV_TOOL_DIR": str(tool_dir / "tools"),
                "UV_TOOL_BIN_DIR": str(bin_dir),
            },
        )
        launchers = _launchers(bin_dir)
        if code != 0 or not launchers:
            shutil.rmtree(tool_dir, ignore_errors=True)
            raise StoreError(
                f"{name} {version}: `{' '.join(argv)}` exited {code} and left"
                f" {'no launcher' if code == 0 else 'nothing'} in {bin_dir}"
            )
        return Ensured(name, version, True, tool_dir, _delegated(launchers), None)

    def _supply_system(self, name: str, version: str, min_version: str) -> Ensured:
        """The machine's own *name*, found on PATH and at or above *min_version*.

        The floor is the record's alone. The locked *version* is the
        newest reading the stubs render for, not a version anyone
        installs, so a record without a floor accepts whatever the
        machine has, a tool that prints no version included.
        """
        self._progress(Event(name, version, "probe"))
        found = shutil.which(name)
        if found is None:
            raise StoreError(
                f"{name}: not on PATH; a system-check tool is the machine's own and"
                " the store installs nothing for it"
            )
        floor = min_version
        reported = _version_in(read_version([found, "--version"])) if floor else ""
        if floor and version_tuple(reported) < version_tuple(floor):
            raise StoreError(
                f"{name}: {found} reports {reported or 'no version'}, below the"
                f" floor {floor}"
            )
        return Ensured(name, version, False, Path(found).parent, _delegated(()), None)

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
        try:
            data = download(url)
        except (Unreachable, OSError) as error:
            raise StoreError(
                f"{name}@{version}: the origin {url} did not answer ({error}); the"
                " artifact is in no source either"
            ) from None
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
        self, name: str, kind: str, deployment: Deployment, artifact: Path, into: Path
    ) -> None:
        if kind == "binary":
            placed = into / deployment.exe
            shutil.copyfile(artifact, placed)
            placed.chmod(
                placed.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            return
        try:
            _extract(artifact, _archive_name(deployment.url), into)
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as error:
            raise StoreError(
                f"{name}: the archive at {deployment.url} will not extract: {error}"
            ) from None
        if deployment.root:
            root = into / deployment.root
            if not root.is_dir():
                found = ", ".join(sorted(p.name for p in into.iterdir())) or "nothing"
                raise StoreError(
                    f"{name}: the archive has no root {deployment.root!r};"
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
            # The entry points are the annotation, never a directory
            # scan: a bundle's own interpreter stays off the bin
            # directory unless the deployment names it.
            for entry in item.deployment.entry_points:
                candidate = item.tool_dir / entry
                if not candidate.is_file() or candidate.name in names:
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
        self, records: Iterable[Record], *, hosts: Iterable[str] = HOSTS, into: Path
    ) -> tuple[Fetched, ...]:
        """Land every version's artifact for *hosts* into a store at *into*.

        The folder is a mirror by construction: a source of this
        engine's layout. Bytes come from this store's sources and, unless
        offline, the origin; a mismatch refuses naming the tool.

        Raises:
            StoreError: for a miss while offline, or a mismatch.
        """
        wanted = tuple(hosts)
        mirror = Home(into).open_store()
        landed: list[Fetched] = []
        for record in records:
            if record.kind not in DOWNLOAD_KINDS:
                continue
            for delta in record.deltas:
                for host, artifact in delta.artifacts.items():
                    if host not in wanted:
                        continue
                    digest = Digest("sha256", artifact.sha256)
                    if mirror.state(digest) == "present":
                        landed.append(
                            Fetched(record.name, delta.version, host, digest, False)
                        )
                        continue
                    path = self._land(record.name, delta.version, artifact.url, digest)
                    with path.open("rb") as handle:
                        mirror.land(handle, expected=digest)
                    landed.append(
                        Fetched(record.name, delta.version, host, digest, True)
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


def _exclude(deployment: Deployment, into: Path) -> None:
    """Remove the extracted members the deployment's exclusion patterns match.

    A pattern is `fnmatch` style over the install-relative, forward-
    slashed path; a matched directory goes with everything under it.
    Separable from the annotation, and justified by size alone.
    """
    if not deployment.exclude:
        return
    for path in sorted(into.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        relative = path.relative_to(into).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) for pattern in deployment.exclude):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists() or path.is_symlink():
                path.unlink()


def _require_entry_points(
    name: str, version: str, host: str, deployment: Deployment, into: Path
) -> None:
    """Refuse an entry point the extracted tree does not carry, naming it whole."""
    for entry in deployment.entry_points:
        if not (into / entry).is_file():
            raise StoreError(
                f"{name} {version} on {host}: the declared entry point"
                f" {entry!r} is not in the extracted tree; the record's"
                " annotation and the artifact disagree"
            )


def _annotate_modes(deployment: Deployment, into: Path) -> None:
    """Leave the executable bit to the annotation alone.

    `collect` marks every declared entry point executable on every
    platform; every other file loses the bit the extractor happened
    to give it, so one archive lands one tree digest wherever it is
    extracted. On Windows the bit does not exist and nothing moves.
    """
    if sys.platform == "win32":
        return
    declared = {into / entry for entry in deployment.entry_points}
    for path in into.rglob("*"):
        if path.is_symlink() or not path.is_file() or path in declared:
            continue
        mode = path.stat().st_mode
        if mode & 0o111:
            path.chmod(mode & ~0o111)


def _apply_shims(deployment: Deployment, into: Path) -> None:
    """Make the deployment's shims inside the install: a link name to an executable."""
    for link_name, target_name in deployment.shims.items():
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
