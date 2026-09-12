"""The local store: objects landed by digest, refs moved by compare-and-swap.

A store is a directory with a root manifest, an `objects/` tree, a
`refs/` tree and a local index. Objects are immutable and idempotent
to land; refs are the only mutable thing, and every move writes a
record beside the ref under a per-ref lock. The layout is the one in
`spec/layout.md`; the index under `index/` is this implementation's
own and not a format.

Reach for [livery.strongroom.Store][]: `create` or `open`, then `land`
and `path` for objects, `ref` and `set_ref` for names.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import IO, Literal

import livery.strongroom._lifecycle as lifecycle
import livery.strongroom._rungs as rungs
from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import Algorithm, Digest, registered
from livery.strongroom._errors import (
    ErasedObject,
    IntegrityError,
    LockTimeout,
    ManifestError,
    MissingObject,
    NotFastForward,
    RefConflict,
    RefTampered,
    UnknownNamespace,
    WriteOnceRefused,
)
from livery.strongroom._fields import Subject, expect_int, expect_object, expect_str
from livery.strongroom._lifecycle import (
    PENDING,
    Pending,
    SweepReport,
    drop_ref,
    erase,
    pin,
    publish_begin,
    publish_commit,
    reachable_from,
    retire,
    sweep,
    unpin,
)
from livery.strongroom._records import RefRecord, Tombstone
from livery.strongroom._sources import (
    FolderSource,
    HttpSource,
    OriginHint,
    Progress,
    Source,
    Unreachable,
    fetch_url,
)
from livery.strongroom._tree import Tree, check_name
from livery.strongroom._version import Version
from livery.strongroom._views import (
    DropReport,
    ShedReport,
    ViewRecord,
    collect,
    drop_view,
    live_roots,
    prefetch,
    shed,
    view,
    view_record,
    views,
)

MANIFEST_NAME = "strongroom.json"
"""The root manifest's file name."""

LAYOUT_VERSION = 1
"""The layout this implementation speaks."""

MutationClass = Literal["write-once", "monotone", "volatile"]
"""How a namespace's refs may move; see `spec/namespaces.md`."""

ObjectState = Literal["present", "absent", "erased"]
"""The three states an object name can be in."""

MUTATION_CLASSES: tuple[MutationClass, ...] = ("write-once", "monotone", "volatile")

Clock = Callable[[], str]
"""Returns the current instant as an RFC 3339 UTC string."""

_RECORD = ".record"
_LOCK = ".lock"
_TOMBSTONE = ".tombstone"
_PART = ".part"
_RESERVED_SUFFIXES = (_RECORD, _LOCK, _TOMBSTONE, _PART)
_CHUNK = 1 << 20
_LOCK_POLL = 0.02


def now() -> str:
    """The current instant, RFC 3339 in UTC to the second."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def silent(message: str) -> None:
    """The default progress sink: report nothing."""


def _replace(source: Path, destination: Path) -> None:
    # The one seam the Windows case needs: a replace over a destination
    # a reader holds open fails there, and landing treats it as success
    # when the destination verifies. Tests fake this function.
    os.replace(source, destination)


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_unknown(pid: int) -> bool:
    # Windows' os.kill terminates the process for any signal it does
    # not special-case, so a liveness probe is never sent there; a lock
    # is stale by age alone.
    return True


_PID_ALIVE: Callable[[int], bool] = {"posix": _pid_alive_posix}.get(
    os.name, _pid_alive_unknown
)


@dataclass(frozen=True)
class Manifest:
    """The root manifest: which layout and which algorithm this store speaks.

    Attributes:
        layout: the layout version.
        algorithm: the one algorithm of this address space.
    """

    layout: int
    algorithm: str

    def encode(self) -> bytes:
        """The manifest's canonical JSON bytes."""
        return canonical({"layout": self.layout, "algorithm": self.algorithm})

    @classmethod
    def decode(cls, data: bytes) -> Manifest:
        """Decode a manifest, refusing bytes that are not one.

        Args:
            data: the manifest file's bytes.

        Returns:
            The manifest.

        Raises:
            ManifestError: when the bytes are not a manifest.
        """
        try:
            value: Value = json.loads(data)
            fields = expect_object(value, ("layout", "algorithm"), where="manifest")
            return cls(
                expect_int(fields["layout"], where="manifest.layout"),
                expect_str(fields["algorithm"], where="manifest.algorithm"),
            )
        except ValueError as error:
            raise ManifestError(f"{MANIFEST_NAME} is not a manifest: {error}") from None


@dataclass(frozen=True)
class Namespace:
    """A ref namespace and its mutation class.

    Attributes:
        name: the namespace, lowercase letters, digits and dashes.
        mutation: how its refs may move.
    """

    name: str
    mutation: MutationClass

    def __post_init__(self) -> None:
        if not self.name or self.name.strip("abcdefghijklmnopqrstuvwxyz0123456789-"):
            raise ValueError(
                f"namespace {self.name!r} is not lowercase letters, digits and dashes"
            )
        if self.mutation not in MUTATION_CLASSES:
            known = ", ".join(MUTATION_CLASSES)
            raise ValueError(
                f"namespace {self.name!r} mutation {self.mutation!r} is not"
                f" one of {known}"
            )


OWNED: tuple[Namespace, ...] = (
    Namespace("pins", "volatile"),
    Namespace("pending", "volatile"),
)
"""The two namespaces the store declares itself; see `spec/namespaces.md`."""


@dataclass(frozen=True)
class Landed:
    """What a landing produced.

    Attributes:
        digest: the object's name.
        size: its byte count.
        written: whether the bytes were written now; false when the
            object already existed, which is success without a write.
    """

    digest: Digest
    size: int
    written: bool


@dataclass(frozen=True)
class ScrubReport:
    """What a scrub found.

    Attributes:
        verified: every object whose bytes matched its name.
        corrupt: every object that did not; each is removed with its
            mark, so the next fill lands it again.
    """

    verified: tuple[Digest, ...] = field(default_factory=tuple)
    corrupt: tuple[Digest, ...] = field(default_factory=tuple)


class Store:
    """A local store: `objects/` by digest, `refs/` by name.

    Open one with [livery.strongroom.Store.create][] or
    [livery.strongroom.Store.open][]. Every namespace a caller will
    write is declared at open with its mutation class; `pins/` and
    `pending/` are declared by the store.

    Attributes:
        root: the store's directory.
        algorithm: the address space's algorithm.
        namespaces: every declared namespace by name.
        sources: the sources consulted in order for an object the
            local directory lacks.
        offline: whether origin hints are never consulted.
    """

    root: Path
    algorithm: Algorithm
    namespaces: dict[str, Namespace]
    sources: tuple[Source, ...]
    offline: bool

    def __init__(
        self,
        root: Path,
        algorithm: Algorithm,
        namespaces: Iterable[Namespace],
        *,
        clock: Clock,
        lock_stale: float,
        lock_timeout: float,
        sources: Iterable[Source],
        offline: bool,
        progress: Progress,
    ) -> None:
        self.root = root
        self.algorithm = algorithm
        self.sources = tuple(sources)
        self.offline = offline
        self._progress = progress
        for source in self.sources:
            if isinstance(source, FolderSource):
                self._check_folder(source)
        self.namespaces = {owned.name: owned for owned in OWNED}
        for namespace in namespaces:
            declared = self.namespaces.get(namespace.name)
            if declared is not None and declared != namespace:
                raise ValueError(
                    f"namespace {namespace.name!r} is already declared"
                    f" {declared.mutation}, not {namespace.mutation}"
                )
            self.namespaces[namespace.name] = namespace
        self._clock = clock
        self._lock_stale = lock_stale
        self._lock_timeout = lock_timeout
        self._checked_http: set[str] = set()

    # Creation and opening.

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        algorithm: str = "sha256",
        namespaces: Iterable[Namespace] = (),
        clock: Clock = now,
        lock_stale: float = 600.0,
        lock_timeout: float = 30.0,
        sources: Iterable[Source] = (),
        offline: bool = False,
        progress: Progress = silent,
    ) -> Store:
        """Create a store at *root* and open it.

        Args:
            root: the directory; created when missing.
            algorithm: a registry entry's name.
            namespaces: the caller's namespaces, each with its class.
            clock: the instant source for records.
            lock_stale: seconds after which a lock whose holder cannot
                be proven alive is broken.
            lock_timeout: seconds to wait for a live lock.
            sources: where to look for an object the store lacks, in
                order. A folder source is checked now; an HTTP source
                on first use.
            offline: never consult an origin hint.
            progress: where a fetch reports a skipped or refused
                source; silent by default.

        Returns:
            The open store.

        Raises:
            ManifestError: when *root* already holds a manifest.
            FormatError: when the algorithm is not registered.
        """
        entry = registered(algorithm)
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / MANIFEST_NAME
        if manifest.exists():
            raise ManifestError(f"{root} is already a store: {MANIFEST_NAME} exists")
        _write_atomically(manifest, Manifest(LAYOUT_VERSION, entry.name).encode())
        for child in ("objects", "refs", "index"):
            (root / child).mkdir(exist_ok=True)
        return cls.open(
            root,
            namespaces=namespaces,
            clock=clock,
            lock_stale=lock_stale,
            lock_timeout=lock_timeout,
            sources=sources,
            offline=offline,
            progress=progress,
        )

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        namespaces: Iterable[Namespace] = (),
        clock: Clock = now,
        lock_stale: float = 600.0,
        lock_timeout: float = 30.0,
        sources: Iterable[Source] = (),
        offline: bool = False,
        progress: Progress = silent,
    ) -> Store:
        """Open the store at *root*.

        Args:
            root: the store's directory.
            namespaces: the caller's namespaces, each with its class.
            clock: the instant source for records.
            lock_stale: seconds after which a lock whose holder cannot
                be proven alive is broken.
            lock_timeout: seconds to wait for a live lock.
            sources: where to look for an object the store lacks, in
                order. A folder source is checked now; an HTTP source
                on first use.
            offline: never consult an origin hint.
            progress: where a fetch reports a skipped or refused
                source; silent by default.

        Returns:
            The store.

        Raises:
            ManifestError: when the manifest is missing or malformed,
                or names a layout or an algorithm this implementation
                does not speak; or when a folder source's manifest is
                missing or of another store.
            ValueError: when a namespace is declared twice with
                different classes.
        """
        path = root / MANIFEST_NAME
        try:
            manifest = Manifest.decode(path.read_bytes())
        except FileNotFoundError:
            raise ManifestError(f"{root} is not a store: no {MANIFEST_NAME}") from None
        if manifest.layout != LAYOUT_VERSION:
            raise ManifestError(
                f"{root} is layout {manifest.layout}; this implementation"
                f" speaks layout {LAYOUT_VERSION}"
            )
        try:
            entry = registered(manifest.algorithm)
        except FormatError as error:
            raise ManifestError(f"{root}: {error}") from None
        return cls(
            root,
            entry,
            namespaces,
            clock=clock,
            lock_stale=lock_stale,
            lock_timeout=lock_timeout,
            sources=sources,
            offline=offline,
            progress=progress,
        )

    # Objects.

    def object_path(self, digest: Digest) -> Path:
        """The path an object of *digest* lives at, present or not.

        Raises:
            IntegrityError: when the digest is of another algorithm
                than this store's; one address space, one algorithm.
        """
        if digest.algorithm != self.algorithm.name:
            raise IntegrityError(
                f"{digest} is a {digest.algorithm} name; this store's"
                f" address space is {self.algorithm.name}"
            )
        algorithm, fanout, rest = digest.path_parts
        return self.root / "objects" / algorithm / fanout / rest

    def state(self, digest: Digest) -> ObjectState:
        """Whether *digest* is present, absent, or erased here."""
        path = self.object_path(digest)
        if path.with_name(path.name + _TOMBSTONE).exists():
            return "erased"
        if path.exists():
            return "present"
        return "absent"

    def land(
        self, source: IO[bytes] | bytes, *, expected: Digest | None = None
    ) -> Landed:
        """Land bytes as an object, verifying them on the way in.

        The bytes stream through the digest into a `.part` scratch file
        and are moved into place atomically. Landing is idempotent: an
        object that already exists is success without a write. Two
        writers of the same digest land identical bytes, so no lock is
        taken.

        Args:
            source: the bytes, or a binary stream read to its end.
            expected: the digest the bytes must have, when known. A
                mismatch removes the scratch and raises.

        Returns:
            The landing.

        Raises:
            IntegrityError: when *expected* is given and the bytes do
                not match it.
            ErasedObject: when the object was erased here; its
                tombstone stands until a sweep decides otherwise.
        """
        stream = BytesIO(source) if isinstance(source, bytes) else source
        if expected is not None:
            destination = self.object_path(expected)
            scratch = _scratch_beside(destination)
        else:
            scratch = _scratch_beside(
                self.root / "objects" / self.algorithm.name / "incoming"
            )
        scratch.parent.mkdir(parents=True, exist_ok=True)
        hasher = self.algorithm.constructor(b"")
        size = 0
        with scratch.open("wb") as handle:
            while chunk := stream.read(_CHUNK):
                hasher.update(chunk)
                size += len(chunk)
                handle.write(chunk)
        digest = Digest(self.algorithm.name, hasher.hexdigest())
        if expected is not None and digest != expected:
            scratch.unlink()
            raise IntegrityError(
                f"landing expected {expected} and the bytes are {digest};"
                " nothing was kept"
            )
        destination = self.object_path(digest)
        state = self.state(digest)
        if state == "erased":
            scratch.unlink()
            raise self._erased(digest)
        if state == "present":
            scratch.unlink()
            return Landed(digest, size, written=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _replace(scratch, destination)
        except PermissionError:
            # A reader holds the destination open (Windows). The bytes
            # are the same by name, so the landing succeeded if the
            # destination verifies.
            scratch.unlink()
            if not destination.exists() or self._hash_file(destination) != digest:
                raise
            self._write_mark(digest, size)
            return Landed(digest, size, written=False)
        self._write_mark(digest, size)
        return Landed(digest, size, written=True)

    def put(self, data: bytes) -> Digest:
        """Land *data* and return its name."""
        return self.land(data).digest

    def path(self, digest: Digest) -> Path:
        """A read-only path to the object, verified on first access.

        Size is checked against the verified mark on every access; an
        object without a mark (landed by copy, not by this store) is
        hashed in full and marked.

        Raises:
            MissingObject: when the object is absent.
            ErasedObject: when the object was erased.
            IntegrityError: when the bytes do not match the name; the
                object and its mark are removed so a fill lands it
                again.
        """
        state = self.state(digest)
        path = self.object_path(digest)
        if state == "erased":
            raise self._erased(digest)
        if state == "absent":
            raise MissingObject(f"{digest} is not in the store at {self.root}")
        size = self.verified_size(digest)
        if size is None:
            self.verify(digest)
        else:
            actual = path.stat().st_size
            if actual != size:
                self.evict(digest)
                raise IntegrityError(
                    f"{digest} is {actual} bytes on disk and was verified at"
                    f" {size}; the object is removed"
                )
        return path

    def read(self, digest: Digest) -> bytes:
        """The object's bytes, through [livery.strongroom.Store.path][]."""
        return self.path(digest).read_bytes()

    def verify(self, digest: Digest) -> int:
        """Hash the object in full and mark it verified.

        Returns:
            The object's size.

        Raises:
            MissingObject: when the object is absent.
            IntegrityError: when the bytes do not match; the object and
                its mark are removed.
        """
        path = self.object_path(digest)
        if not path.exists():
            raise MissingObject(f"{digest} is not in the store at {self.root}")
        actual = self._hash_file(path)
        if actual != digest:
            self.evict(digest)
            raise IntegrityError(
                f"{digest} holds bytes named {actual}; the object is removed"
            )
        size = path.stat().st_size
        self._write_mark(digest, size)
        return size

    def verified_size(self, digest: Digest) -> int | None:
        """The size this store verified the object at, or None if never."""
        try:
            return int(self._mark(digest).read_text("ascii"))
        except FileNotFoundError:
            return None

    def objects(self) -> Iterator[Digest]:
        """Every object present here, in no particular order."""
        objects = self.root / "objects" / self.algorithm.name
        for fanout in sorted(objects.glob("??")):
            for path in sorted(fanout.iterdir()):
                if path.name.endswith(_RESERVED_SUFFIXES):
                    continue
                yield Digest(self.algorithm.name, fanout.name + path.name)

    def scrub(self) -> ScrubReport:
        """Hash every object and remove the ones that do not match."""
        verified: list[Digest] = []
        corrupt: list[Digest] = []
        for digest in list(self.objects()):
            try:
                self.verify(digest)
            except IntegrityError:
                corrupt.append(digest)
            else:
                verified.append(digest)
        return ScrubReport(tuple(verified), tuple(corrupt))

    def _mark(self, digest: Digest) -> Path:
        algorithm, fanout, rest = digest.path_parts
        return self.root / "index" / "verified" / algorithm / fanout / rest

    def _write_mark(self, digest: Digest, size: int) -> None:
        mark = self._mark(digest)
        mark.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(mark, str(size).encode("ascii"))

    def _hash_file(self, path: Path) -> Digest:
        hasher = self.algorithm.constructor(b"")
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                hasher.update(chunk)
        return Digest(self.algorithm.name, hasher.hexdigest())

    # Sources.

    def fetch(self, digest: Digest) -> Path:
        """A path to the object, consulting the sources when it is not here.

        Sources are consulted in order. A hit is verified on arrival:
        a copy-policy folder, an HTTP tier and an origin hint land the
        bytes here; a reference-policy folder answers with a path into
        the folder and lands nothing. An unreachable source is skipped
        and reported; a source serving wrong bytes is refused and
        reported, and the next one consulted. An origin hint is
        consulted only for its own digest and never when offline.

        Returns:
            A read-only path: this store's object, or the folder's
            under a reference policy.

        Raises:
            MissingObject: when no source has it. The message names
                the digest, and when an origin hint was passed over
                because the store is offline, the URL that would have
                satisfied it.
            ErasedObject: when the object was erased here.
            ManifestError: when an HTTP source's manifest is of
                another store.
        """
        state = self.state(digest)
        if state == "erased":
            raise self._erased(digest)
        if state == "present":
            return self.path(digest)
        passed_over: OriginHint | None = None
        for source in self.sources:
            if isinstance(source, OriginHint):
                if source.digest != digest:
                    continue
                if self.offline:
                    passed_over = source
                    continue
            try:
                found = self._fetch_from(source, digest)
            except Unreachable as error:
                self._progress(f"skipped {source.describe()}: {error}")
                continue
            except IntegrityError as error:
                self._progress(f"refused {source.describe()}: {error}")
                continue
            if found is not None:
                return found
        offline = (
            ""
            if passed_over is None
            else (
                f"; the store is offline and {passed_over.url} would have satisfied it"
            )
        )
        raise MissingObject(
            f"{digest} is not in the store at {self.root} nor at any of its"
            f" {len(self.sources)} source(s){offline}"
        )

    def fill(self, target: Path, digests: Iterable[Digest]) -> list[Landed]:
        """Land *digests* into the folder tier at *target*.

        The folder becomes a store of this store's algorithm when it is
        not one already, and afterwards opens as a
        [livery.strongroom.FolderSource][] that serves every filled
        digest. Each object is fetched through this store's sources.

        Args:
            target: the folder; created when missing.
            digests: the objects to land there.

        Returns:
            One landing per digest, in order.

        Raises:
            ManifestError: when *target* is a store of another layout
                or algorithm.
            MissingObject: when a digest is not obtainable.
        """
        if (target / MANIFEST_NAME).exists():
            mirror = Store.open(target)
        else:
            mirror = Store.create(target, algorithm=self.algorithm.name)
        if mirror.algorithm != self.algorithm:
            raise ManifestError(
                f"{target} is a {mirror.algorithm.name} store; this store is"
                f" {self.algorithm.name}"
            )
        landed: list[Landed] = []
        for digest in digests:
            with self.fetch(digest).open("rb") as handle:
                landed.append(mirror.land(handle, expected=digest))
        return landed

    def _fetch_from(self, source: Source, digest: Digest) -> Path | None:
        if isinstance(source, FolderSource):
            return self._fetch_from_folder(source, digest)
        if isinstance(source, HttpSource):
            return self._fetch_from_http(source, digest)
        return self._fetch_from_origin(source, digest)

    def _fetch_from_folder(self, source: FolderSource, digest: Digest) -> Path | None:
        algorithm, fanout, rest = digest.path_parts
        remote = source.path / "objects" / algorithm / fanout / rest
        if not remote.exists():
            return None
        if source.fill == "copy":
            with remote.open("rb") as handle:
                self.land(handle, expected=digest)
            return self.path(digest)
        mark = self._reference_mark(digest)
        size = remote.stat().st_size
        try:
            if int(mark.read_text("ascii")) == size:
                return remote
        except FileNotFoundError:
            pass
        actual = self._hash_file(remote)
        if actual != digest:
            raise IntegrityError(f"{remote} holds bytes named {actual}, not {digest}")
        mark.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(mark, str(size).encode("ascii"))
        return remote

    def _fetch_from_http(self, source: HttpSource, digest: Digest) -> Path | None:
        if source.base_url not in self._checked_http:
            with fetch_url(
                source.url(MANIFEST_NAME),
                connect_timeout=source.connect_timeout,
                transfer_timeout=source.transfer_timeout,
            ) as response:
                self._check_manifest(
                    Manifest.decode(response.read()), source.describe()
                )
            self._checked_http.add(source.base_url)
        algorithm, fanout, rest = digest.path_parts
        with fetch_url(
            source.url(f"objects/{algorithm}/{fanout}/{rest}"),
            connect_timeout=source.connect_timeout,
            transfer_timeout=source.transfer_timeout,
        ) as response:
            self.land(response, expected=digest)
        return self.path(digest)

    def _fetch_from_origin(self, source: OriginHint, digest: Digest) -> Path:
        with fetch_url(
            source.url,
            connect_timeout=source.connect_timeout,
            transfer_timeout=source.transfer_timeout,
        ) as response:
            self.land(response, expected=digest)
        return self.path(digest)

    def _check_folder(self, source: FolderSource) -> None:
        try:
            manifest = Manifest.decode((source.path / MANIFEST_NAME).read_bytes())
        except FileNotFoundError:
            raise ManifestError(
                f"{source.describe()} is not a store: no {MANIFEST_NAME}"
            ) from None
        self._check_manifest(manifest, source.describe())

    def _check_manifest(self, manifest: Manifest, what: str) -> None:
        if manifest != Manifest(LAYOUT_VERSION, self.algorithm.name):
            raise ManifestError(
                f"{what} is layout {manifest.layout}, {manifest.algorithm};"
                f" this store is layout {LAYOUT_VERSION}, {self.algorithm.name}"
            )

    def _reference_mark(self, digest: Digest) -> Path:
        algorithm, fanout, rest = digest.path_parts
        return self.root / "index" / "referenced" / algorithm / fanout / rest

    # Tombstones.

    def tombstone(self, digest: Digest) -> Tombstone | None:
        """The tombstone under the object's path, or None when there is none.

        Raises:
            IntegrityError: when the file under the tombstone's name is
                not a tombstone.
        """
        path = self.object_path(digest)
        try:
            data = path.with_name(path.name + _TOMBSTONE).read_bytes()
        except FileNotFoundError:
            return None
        try:
            return Tombstone.decode(data)
        except FormatError as error:
            raise IntegrityError(f"tombstone of {digest}: {error}") from None

    def write_tombstone(self, stone: Tombstone) -> None:
        """Write *stone* under its object's path, in this tier."""
        path = self.object_path(stone.digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path.with_name(path.name + _TOMBSTONE), stone.encode())

    def evict(self, digest: Digest) -> None:
        """Remove the object's bytes and its marks; the name is untouched."""
        rungs.remove(self.object_path(digest))
        self._mark(digest).unlink(missing_ok=True)
        self._reference_mark(digest).unlink(missing_ok=True)

    def clock(self) -> str:
        """The current instant, from the clock the store was opened with."""
        return self._clock()

    def _erased(self, digest: Digest) -> ErasedObject:
        # A tombstone that vanished between the state check and this
        # read is a torn state; the refusal stands without its reason.
        stone = self.tombstone(digest)
        detail = (
            "a tombstone stands"
            if stone is None
            else f"erased at {stone.at} by {stone.authority.value}: {stone.reason}"
        )
        return ErasedObject(f"{digest}: {detail}")

    # The lifecycle. The bodies live in `_lifecycle`.

    def publish_begin(
        self, target: Digest, *, by: Subject, lease: float = 3600.0
    ) -> Pending:
        """Begin a publish: root *target* under `pending/<id>` first.

        The pending ref is a root while it exists, so a sweep during the
        publish keeps every object the target names. Nothing removes a
        pending ref on a clock; a crashed publish is retired
        deliberately with [livery.strongroom.Store.retire][].

        Args:
            target: the tree or version to publish; present here.
            by: who publishes.
            lease: seconds the publish expects to take; recorded in the
                pending ref's record for whoever retires it.

        Returns:
            The pending publish.

        Raises:
            MissingObject: when *target* is not present here.
        """
        return publish_begin(self, target, by=by, lease=lease)

    def publish_commit(
        self,
        pending_id: str,
        namespace: str,
        path: str,
        *,
        previous: Digest | None,
        by: Subject,
        receipt: Digest | None = None,
        meta: dict[str, Value] | None = None,
    ) -> RefRecord:
        """Commit a publish: move the real ref, then drop the pending one.

        The move obeys the namespace's mutation class exactly as
        [livery.strongroom.Store.set_ref][] does.

        Raises:
            NoSuchPending: when `pending/<pending_id>` does not exist.
            RefConflict: when the real ref does not name *previous*, or
                its class refuses the move.
        """
        return publish_commit(
            self,
            pending_id,
            namespace,
            path,
            previous=previous,
            by=by,
            receipt=receipt,
            meta=meta,
        )

    def retire(self, pending_id: str) -> Digest:
        """Drop a pending ref deliberately, for a publish that will not commit.

        Returns:
            What the pending ref named.

        Raises:
            NoSuchPending: when it does not exist.
        """
        return retire(self, pending_id)

    def pendings(self) -> list[str]:
        """Every pending publish's id, sorted."""
        return self.refs(PENDING)

    def drop_ref(self, namespace: str, path: str, *, previous: Digest) -> None:
        """Remove a volatile ref and its record by compare-and-swap.

        Raises:
            RefProtected: when the namespace is write-once or monotone.
            RefConflict: when the ref does not name *previous*.
        """
        drop_ref(self, namespace, path, previous=previous)

    def pin(self, name: str, digest: Digest, *, by: Subject) -> RefRecord:
        """Root *digest* under `pins/<name>`, replacing an earlier pin of that name."""
        return pin(self, name, digest, by=by)

    def unpin(self, name: str) -> Digest:
        """Drop `pins/<name>` under the maintenance lease, never beside a sweep.

        Returns:
            What the pin named.

        Raises:
            RefConflict: when the pin does not exist.
            LockTimeout: when a sweep holds the lease past the timeout.
        """
        return unpin(self, name)

    def erase(
        self, digest: Digest, *, by: Subject, reason: str, receipt: Digest | None = None
    ) -> Tombstone:
        """Erase the object's bytes and keep the fact as a tombstone.

        The name stays valid in every tree that carries it; a path to
        the object raises naming this tombstone's reason; landing the
        bytes again is refused while the tombstone stands. An absent
        object may be erased too, which refuses its future landing.
        """
        return erase(self, digest, by=by, reason=reason, receipt=receipt)

    def reachable(self, digest: Digest) -> Iterator[Digest]:
        """Every object reachable from *digest*, itself included.

        Structured formats are read by shape, never by namespace: a
        version yields its tree, parents, receipt and attachments, a
        tree its entries, anything else nothing. An absent or erased
        digest is yielded and not walked.
        """
        return reachable_from(self, digest)

    def roots(self) -> list[Digest]:
        """Every root: what every ref on disk names, and every live view's tree.

        Namespaces are read from disk, not from the declaration at
        open, so a consumer's refs root its objects whoever opened the
        store. A ref file that is not a digest roots nothing. A view
        whose directory is gone is retired here rather than rooted.
        """
        found: list[Digest] = live_roots(self)
        base = self.root / "refs"
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.endswith(_RESERVED_SUFFIXES):
                continue
            try:
                found.append(Digest.parse(path.read_text("ascii").strip()))
            except (FormatError, UnicodeDecodeError):
                continue
        return found

    def sweep(self, *, scratch_age: float = 86400.0) -> SweepReport:
        """Remove every object no root reaches, and old scratch.

        Runs under the maintenance lease. Pending refs are read again
        immediately before deleting, so a publish that began during the
        sweep roots its objects. Tombstones are never removed.

        Args:
            scratch_age: seconds after which an orphaned `.part` file
                is removed; the one age rule.

        Returns:
            The report.

        Raises:
            LockTimeout: when another maintenance holds the lease past
                the timeout.
        """
        return sweep(self, scratch_age=scratch_age, hook=lifecycle.after_mark)

    @contextlib.contextmanager
    def _maintenance(self) -> Generator[None]:
        with self._locked(self.root / "index" / "maintenance"):
            yield

    # The materialiser. The bodies live in `_views`.

    def view(
        self,
        tree: Digest,
        at: Path,
        *,
        writable: bool = False,
        allow_hardlink: bool = False,
    ) -> ViewRecord:
        """Fill *at* from the tree by the cheapest safe rung per entry.

        The ladder is clone, hardlink, link, copy; a rung that refuses
        once is not tried again for this view. A hardlink is refused
        into a writable view unless *allow_hardlink* says the view's
        consumers never write in place; a link is refused from a
        writable view. An executable entry never shares an inode or a
        target's mode. A symlink entry becomes a real symlink where the
        platform allows, the within-view target's content as a marked
        copy where it does not, and a parked refusal when the target
        escapes the view. Blobs are fetched through the sources.

        Args:
            tree: the tree to present; fetched if absent here.
            at: the directory to fill; missing or empty.
            writable: whether the caller will write in the view.
            allow_hardlink: allow hardlinks into a writable view.

        Returns:
            The record, listing every path and its rung. The view is a
            root while its directory exists.

        Raises:
            FileExistsError: when *at* exists and is not an empty
                directory.
            MissingObject: when a blob is obtainable from no source,
                naming the entry's path.
            ErasedObject: when a blob was erased, naming the entry's
                path and the tombstone's reason.
            FormatError: when a path in the tree is over the budget.
        """
        return view(self, tree, at, writable=writable, allow_hardlink=allow_hardlink)

    def views(self) -> list[ViewRecord]:
        """Every recorded view, live or not, by id."""
        return views(self)

    def view_record(self, view_id: str) -> ViewRecord | None:
        """The record of one view, or None."""
        return view_record(self, view_id)

    def drop_view(self, view_id: str) -> DropReport:
        """Remove what the view's record lists, and nothing else.

        A path inside the view that the record does not list is left
        and named; so is a directory that then stays non-empty, the
        view's root included. A view whose directory is already gone
        is retired with nothing removed. The record goes either way.

        Raises:
            FileNotFoundError: when no such view is recorded.
        """
        return drop_view(self, view_id)

    def collect(self, at: Path, declared: Iterable[str]) -> Tree:
        """Read the declared paths under *at* back into a tree, landing every object.

        A declared directory is collected whole; an undeclared path is
        not read. Names must be portable and paths within the budget.

        Returns:
            The root tree, landed here with every subtree.

        Raises:
            FileNotFoundError: when a declared path is absent.
            FormatError: when a name or a symlink target breaks a rule,
                or a path is over the budget.
        """
        return collect(self, at, declared)

    def prefetch(self, digest: Digest) -> list[Digest]:
        """Fetch *digest* and everything it reaches, for offline use.

        Returns:
            Every object fetched, the root first.

        Raises:
            MissingObject: naming the first digest no source answered.
        """
        return prefetch(self, digest)

    def shed(self, source: Source) -> ShedReport:
        """Evict every local copy *source* holds, except what a live view needs.

        Holding is checked by existence in a folder, a HEAD on an HTTP
        source, or the digest an origin hint names; never by trust.
        The next fetch verifies on arrival, so a lying source costs a
        named miss and never a wrong byte. Objects a live view reaches
        through a hardlink or a link are kept.
        """
        return shed(self, source)

    def head(self, source: HttpSource, relative: str) -> bool:
        """Whether the HTTP source answers a HEAD for *relative*."""
        try:
            with fetch_url(
                source.url(relative),
                connect_timeout=source.connect_timeout,
                transfer_timeout=source.transfer_timeout,
                method="HEAD",
            ):
                return True
        except Unreachable:
            return False

    # Refs.

    def ref_path(self, namespace: str, path: str) -> Path:
        """The file a ref lives at, present or not.

        Args:
            namespace: a declared namespace.
            path: the ref's path within it, forward-slashed; each
                component is a portable name and none ends in a
                reserved suffix.

        Raises:
            UnknownNamespace: when the namespace was not declared.
            FormatError: when a path component breaks a rule.
        """
        self._namespace(namespace)
        parts = path.split("/")
        for part in parts:
            check_name(part)
            if part.endswith(_RESERVED_SUFFIXES):
                raise FormatError(
                    f"ref path component {part!r} ends in a reserved suffix"
                )
        return self.root.joinpath("refs", namespace, *parts)

    def ref(self, namespace: str, path: str) -> Digest | None:
        """What the ref names, or None when it does not exist.

        Raises:
            RefTampered: when the ref and its record disagree, or the
                record is missing or malformed.
        """
        found = self._read_ref(namespace, path)
        return None if found is None else found[0]

    def _read_ref(self, namespace: str, path: str) -> tuple[Digest, RefRecord] | None:
        target = self.ref_path(namespace, path)
        try:
            text = target.read_text("ascii")
        except FileNotFoundError:
            return None
        record = self.record(namespace, path)
        try:
            digest = Digest.parse(text.strip())
        except FormatError as error:
            raise RefTampered(
                f"ref {namespace}/{path} is not a digest: {error}"
            ) from None
        if record is None or record.digest != digest:
            found = "no record" if record is None else f"record names {record.digest}"
            raise RefTampered(
                f"ref {namespace}/{path} names {digest} but {found}; an"
                " out-of-band edit or a torn update"
            )
        return digest, record

    def _namespace(self, namespace: str) -> Namespace:
        declared = self.namespaces.get(namespace)
        if declared is None:
            known = ", ".join(sorted(self.namespaces))
            raise UnknownNamespace(
                f"namespace {namespace!r} was not declared at open (declared: {known})"
            )
        return declared

    def record(self, namespace: str, path: str) -> RefRecord | None:
        """The record beside the ref, or None when there is none.

        Raises:
            RefTampered: when the record's bytes are not a record.
        """
        target = self.ref_path(namespace, path)
        try:
            data = target.with_name(target.name + _RECORD).read_bytes()
        except FileNotFoundError:
            return None
        try:
            return RefRecord.decode(data)
        except FormatError as error:
            raise RefTampered(f"record of {namespace}/{path}: {error}") from None

    def refs(self, namespace: str) -> list[str]:
        """Every ref path in the namespace, sorted."""
        self._namespace(namespace)
        base = self.root / "refs" / namespace
        found: list[str] = []
        for path in base.rglob("*"):
            if path.is_file() and not path.name.endswith(_RESERVED_SUFFIXES):
                found.append(path.relative_to(base).as_posix())
        return sorted(found)

    def set_ref(
        self,
        namespace: str,
        path: str,
        digest: Digest,
        *,
        previous: Digest | None,
        by: Subject,
        receipt: Digest | None = None,
        meta: dict[str, Value] | None = None,
    ) -> RefRecord:
        """Move a ref by compare-and-swap, writing its record.

        The move happens under the ref's lock. The namespace's
        mutation class is enforced: write-once refuses a second digest
        and treats the same digest as done; monotone requires the new
        target to be a version present here whose parents include the
        current target; volatile needs only the compare-and-swap.

        Args:
            namespace: a declared namespace.
            path: the ref's path.
            digest: what the ref names afterwards.
            previous: what the caller believes it names now; None to
                create.
            by: who moves it.
            receipt: the receipt of the moving call, if any.
            meta: the consumer's own object for the record.

        Returns:
            The record written, or the existing record when a
            write-once ref already names *digest*.

        Raises:
            RefConflict: when the ref does not name *previous*.
            WriteOnceRefused: on a write-once ref that names another digest.
            NotFastForward: on a monotone ref whose new target does not
                descend from the current one, or is not a version here.
            LockTimeout: when a live lock outlasts the timeout.
            RefTampered: when the ref and its record disagree.
        """
        target = self.ref_path(namespace, path)
        mutation = self._namespace(namespace).mutation
        with self._locked(target):
            found = self._read_ref(namespace, path)
            current = None if found is None else found[0]
            if current != previous:
                raise RefConflict(
                    f"ref {namespace}/{path} names {current}, not {previous};"
                    " re-read it and retry"
                )
            if mutation == "write-once" and found is not None:
                if found[0] == digest:
                    return found[1]
                raise WriteOnceRefused(
                    f"ref {namespace}/{path} is write-once and names {found[0]};"
                    f" {digest} is refused"
                )
            if mutation == "monotone" and current is not None:
                self._check_fast_forward(namespace, path, digest, current)
            record = RefRecord(
                digest, previous, by, self._clock(), receipt, dict(meta or {})
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomically(target.with_name(target.name + _RECORD), record.encode())
            _write_atomically(target, f"{digest}\n".encode("ascii"))
            return record

    def _check_fast_forward(
        self, namespace: str, path: str, digest: Digest, current: Digest
    ) -> None:
        try:
            version = Version.decode(self.read(digest))
        except (MissingObject, ErasedObject, FormatError) as error:
            raise NotFastForward(
                f"ref {namespace}/{path} is monotone and {digest} is not a"
                f" version present here: {error}"
            ) from None
        if current not in version.parents:
            raise NotFastForward(
                f"ref {namespace}/{path} names {current}, which is not a"
                f" parent of {digest}; rebase the version and retry"
            )

    @contextlib.contextmanager
    def _locked(self, target: Path) -> Generator[None]:
        lock = target.with_name(target.name + _LOCK)
        lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._lock_timeout
        while True:
            try:
                with lock.open("x") as handle:
                    handle.write(json.dumps({"pid": os.getpid(), "at": time.time()}))
                break
            except FileExistsError:
                if self._lock_is_stale(lock):
                    lock.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"{lock} is held by a live process past"
                        f" {self._lock_timeout}s; retry later"
                    ) from None
                time.sleep(_LOCK_POLL)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _lock_is_stale(self, lock: Path) -> bool:
        # Break only on provable staleness: a holder that is dead, or a
        # lock older than the stale bound. A torn lock file is a holder
        # that died mid-write. The compare-and-swap still decides the
        # winner afterwards, so a wrong break costs a retry, never a
        # torn record.
        try:
            content = json.loads(lock.read_text("ascii"))
            pid = int(content["pid"])
            at = float(content["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return True
        if not _PID_ALIVE(pid):
            return True
        return time.time() - at > self._lock_stale


def _scratch_beside(path: Path) -> Path:
    # Two processes landing the same digest each need their own scratch,
    # or their writes interleave in one file; the process id and a
    # per-process counter make the name unique, and the `.part` suffix
    # keeps it the one thing an age sweep may remove.
    _SCRATCH_COUNT[0] += 1
    return path.with_name(f"{path.name}.{os.getpid()}-{_SCRATCH_COUNT[0]}{_PART}")


_SCRATCH_COUNT = [0]


def _write_atomically(path: Path, data: bytes) -> None:
    scratch = _scratch_beside(path)
    scratch.write_bytes(data)
    os.replace(scratch, path)
