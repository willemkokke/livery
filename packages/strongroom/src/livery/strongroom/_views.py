"""The materialiser: the only route from a digest to a path.

`view` fills a directory from a tree, choosing the cheapest safe rung
per entry and recording which one it used; `collect` reads declared
outputs back into a tree; `drop_view` removes only what the record
lists. A view is a root while it lives. `prefetch` warms everything
one digest reaches, and `shed` evicts local copies a named source
holds, except what a live view depends on.

The functions here are the bodies of the [livery.strongroom.Store][]
methods of the same names; reach for the methods.
"""

from __future__ import annotations

import json
import os
import posixpath
import secrets
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import livery.strongroom._rungs as rungs
from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import Digest
from livery.strongroom._errors import ErasedObject, IntegrityError, MissingObject
from livery.strongroom._fields import (
    expect_bool,
    expect_list,
    expect_object,
    expect_str,
)
from livery.strongroom._lifecycle import children
from livery.strongroom._rungs import MadeRung, RungUnavailable
from livery.strongroom._sources import FolderSource, HttpSource, Source
from livery.strongroom._tree import Entry, Link, Tree, check_target

if TYPE_CHECKING:
    from livery.strongroom._store import Store

PATH_BUDGET = 1024
"""The most UTF-8 bytes a path inside a view may take, separators included."""

EntryRung = Literal["clone", "hardlink", "link", "copy", "symlink", "parked"]
"""How one view entry was made: a ladder rung, a real symlink, or not at all."""

ENTRY_RUNGS: tuple[EntryRung, ...] = (
    "clone",
    "hardlink",
    "link",
    "copy",
    "symlink",
    "parked",
)


def new_view_id() -> str:
    """A fresh view id; the seam tests fix for a known name."""
    return secrets.token_hex(8)


@dataclass(frozen=True)
class ViewEntry:
    """One path a view created, and how.

    Attributes:
        path: relative to the view's root, forward-slashed.
        rung: how it was made; `parked` means it was refused and the
            note says why.
        digest: the blob it presents, or None for a symlink entry.
        note: why a symlink became a copy or was parked; empty otherwise.
    """

    path: str
    rung: EntryRung
    digest: Digest | None = None
    note: str = ""

    def to_json(self) -> dict[str, Value]:
        """The entry as a JSON object."""
        return {
            "path": self.path,
            "rung": self.rung,
            "digest": None if self.digest is None else str(self.digest),
            "note": self.note,
        }

    @classmethod
    def from_json(cls, value: Value) -> ViewEntry:
        """Decode an entry, refusing a rung outside the ladder."""
        fields = expect_object(value, ("path", "rung", "digest", "note"), where="entry")
        digest = fields["digest"]
        return cls(
            expect_str(fields["path"], where="entry.path"),
            _entry_rung(expect_str(fields["rung"], where="entry.rung")),
            None
            if digest is None
            else Digest.parse(expect_str(digest, where="entry.digest")),
            expect_str(fields["note"], where="entry.note"),
        )


def _entry_rung(text: str) -> EntryRung:
    for rung in ENTRY_RUNGS:
        if text == rung:
            return rung
    raise FormatError(f"entry.rung {text!r} is not a rung")


@dataclass(frozen=True)
class ViewRecord:
    """A view: local state in the index, and a root while its directory exists.

    Attributes:
        id: the record's name under `index/views/`.
        tree: the tree the view presents.
        at: the view's root directory.
        writable: whether the caller said it would write.
        created: when, an instant.
        entries: every path created, in creation order.
        directories: every directory created, shallowest first.
    """

    id: str
    tree: Digest
    at: str
    writable: bool
    created: str
    entries: tuple[ViewEntry, ...] = field(default_factory=tuple)
    directories: tuple[str, ...] = field(default_factory=tuple)

    def encode(self) -> bytes:
        """The record's canonical JSON bytes."""
        return canonical(
            {
                "id": self.id,
                "tree": str(self.tree),
                "at": self.at,
                "writable": self.writable,
                "created": self.created,
                "entries": [entry.to_json() for entry in self.entries],
                "directories": list(self.directories),
            }
        )

    @classmethod
    def decode(cls, data: bytes) -> ViewRecord:
        """Decode a record.

        Raises:
            FormatError: when the bytes are not a record.
        """
        try:
            value: Value = json.loads(data)
        except ValueError as error:
            raise FormatError(f"view record bytes are not JSON: {error}") from None
        fields = expect_object(
            value,
            ("id", "tree", "at", "writable", "created", "entries", "directories"),
            where="view",
        )
        return cls(
            expect_str(fields["id"], where="view.id"),
            Digest.parse(expect_str(fields["tree"], where="view.tree")),
            expect_str(fields["at"], where="view.at"),
            expect_bool(fields["writable"], where="view.writable"),
            expect_str(fields["created"], where="view.created"),
            tuple(
                ViewEntry.from_json(item)
                for item in expect_list(fields["entries"], where="view.entries")
            ),
            tuple(
                expect_str(item, where="view.directories[]")
                for item in expect_list(fields["directories"], where="view.directories")
            ),
        )

    @property
    def root(self) -> Path:
        """The view's root as a path."""
        return Path(self.at)

    def depends_on(self) -> set[Digest]:
        """The objects this view reads through the store's own files."""
        return {
            entry.digest
            for entry in self.entries
            if entry.rung in ("hardlink", "link") and entry.digest is not None
        }


@dataclass(frozen=True)
class DropReport:
    """What dropping a view removed and what it left.

    Attributes:
        removed: every path removed, files first, then directories
            deepest first, then the root as `.`.
        left: every path found that the record did not list, with the
            reason; never touched.
    """

    removed: tuple[str, ...] = field(default_factory=tuple)
    left: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShedReport:
    """What a shed did.

    Attributes:
        source: the source relied on, described.
        shed: every local copy evicted because the source holds it.
        kept: every object kept because a live view depends on it.
    """

    source: str
    shed: tuple[Digest, ...] = field(default_factory=tuple)
    kept: tuple[Digest, ...] = field(default_factory=tuple)


# Planning a view.


@dataclass(frozen=True)
class _Plan:
    blobs: list[tuple[str, Entry]] = field(default_factory=list)
    links: list[tuple[str, Link]] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)


def _plan(store: Store, tree: Tree, prefix: str, into: _Plan) -> None:
    for item in tree.entries:
        path = f"{prefix}{item.name}"
        if len(path.encode("utf-8")) > PATH_BUDGET:
            raise FormatError(f"path {path!r} is longer than {PATH_BUDGET} UTF-8 bytes")
        if isinstance(item, Link):
            into.links.append((path, item))
        elif item.kind == "tree":
            into.directories.append(path)
            subtree = Tree.decode(store.fetch(item.digest).read_bytes())
            _plan(store, subtree, f"{path}/", into)
        else:
            into.blobs.append((path, item))


class _Ladder:
    """The rungs still open to one view; a rung that refuses once is closed."""

    def __init__(self, *, writable: bool, allow_hardlink: bool) -> None:
        self.open: list[MadeRung] = ["clone"]
        # A hardlink into a writable tree is the DVC hazard: a write in
        # place corrupts the store's object. Allowed only when the
        # caller says its consumers never write in place.
        if not writable or allow_hardlink:
            self.open.append("hardlink")
        # A link's target must be a read-only tier; from a writable
        # view a program could write through it.
        if not writable:
            self.open.append("link")

    def make(self, source: Path, destination: Path, *, executable: bool) -> MadeRung:
        for rung in list(self.open):
            # A hardlink and a link share the target's mode, so an
            # executable entry cannot use them without changing the
            # object's own mode.
            if executable and rung in ("hardlink", "link"):
                continue
            try:
                _MAKE[rung](source, destination)
            except RungUnavailable:
                self.open.remove(rung)
                continue
            return rung
        rungs.copy(source, destination)
        return "copy"


def _clone(source: Path, destination: Path) -> None:
    rungs.CLONE(source, destination)


def _hardlink(source: Path, destination: Path) -> None:
    rungs.hardlink(source, destination)


def _link(source: Path, destination: Path) -> None:
    rungs.symlink(os.path.relpath(source, destination.parent), destination)


_MAKE = {"clone": _clone, "hardlink": _hardlink, "link": _link}


def view(
    store: Store, tree_digest: Digest, at: Path, *, writable: bool, allow_hardlink: bool
) -> ViewRecord:
    """Fill *at* from the tree, cheapest safe rung per entry, and record it."""
    if at.exists() and (not at.is_dir() or any(at.iterdir())):
        raise FileExistsError(f"{at} exists and is not an empty directory")
    tree = Tree.decode(store.fetch(tree_digest).read_bytes())
    plan = _Plan()
    _plan(store, tree, "", plan)
    at.mkdir(parents=True, exist_ok=True)
    for directory in plan.directories:
        (at / directory).mkdir()
    ladder = _Ladder(writable=writable, allow_hardlink=allow_hardlink)
    entries: list[ViewEntry] = []
    for path, entry in plan.blobs:
        try:
            source = store.fetch(entry.digest)
        except (ErasedObject, MissingObject) as error:
            raise type(error)(f"view entry {path!r}: {error}") from None
        destination = at / path
        rung = ladder.make(source, destination, executable=entry.executable)
        _set_mode(destination, rung, executable=entry.executable, writable=writable)
        entries.append(ViewEntry(path, rung, entry.digest))
    for path, link in plan.links:
        entries.append(_make_link(at, path, link))
    record = ViewRecord(
        new_view_id(),
        tree_digest,
        str(at),
        writable,
        store.clock(),
        tuple(entries),
        tuple(plan.directories),
    )
    _write_record(store, record)
    return record


def _set_mode(path: Path, rung: MadeRung, *, executable: bool, writable: bool) -> None:
    # A clone and a copy are private files and take the entry's mode; a
    # hardlink shares the object's inode and is marked read-only, which
    # an object should be anyway; a link has no mode of its own.
    if rung == "link":
        return
    mode = 0o444 | (0o111 if executable else 0)
    if writable and rung != "hardlink":
        mode |= 0o200
    os.chmod(path, mode)


def _make_link(at: Path, path: str, link: Link) -> ViewEntry:
    destination = at / path
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), link.target))
    escapes = resolved == ".." or resolved.startswith("../")
    inside = None if escapes else at / resolved
    try:
        rungs.symlink(
            link.target, destination, directory=inside is not None and inside.is_dir()
        )
    except RungUnavailable as error:
        if inside is None:
            return ViewEntry(path, "parked", note=f"target escapes the view: {error}")
        if not inside.exists():
            return ViewEntry(
                path, "parked", note=f"target is absent in the view: {error}"
            )
        # The within-view target's content, as a marked copy.
        if inside.is_dir():
            shutil.copytree(inside, destination)
        else:
            shutil.copyfile(inside, destination)
        return ViewEntry(path, "copy", note=f"symlink materialised as a copy: {error}")
    return ViewEntry(path, "symlink")


# Records.


def _records_dir(store: Store) -> Path:
    return store.root / "index" / "views"


def _write_record(store: Store, record: ViewRecord) -> None:
    directory = _records_dir(store)
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / f"{record.id}.json.{os.getpid()}.part"
    scratch.write_bytes(record.encode())
    os.replace(scratch, directory / f"{record.id}.json")


def views(store: Store) -> list[ViewRecord]:
    """Every recorded view, by id."""
    found: list[ViewRecord] = []
    for path in sorted(_records_dir(store).glob("*.json")):
        found.append(_decode_record(path))
    return found


def view_record(store: Store, view_id: str) -> ViewRecord | None:
    """The record of *view_id*, or None."""
    path = _records_dir(store) / f"{view_id}.json"
    if not path.exists():
        return None
    return _decode_record(path)


def _decode_record(path: Path) -> ViewRecord:
    try:
        return ViewRecord.decode(path.read_bytes())
    except FormatError as error:
        raise IntegrityError(f"view record {path.name}: {error}") from None


def retire_record(store: Store, view_id: str) -> None:
    """Forget a view: its record goes, its paths are not touched."""
    (_records_dir(store) / f"{view_id}.json").unlink(missing_ok=True)


def drop_view(store: Store, view_id: str) -> DropReport:
    """Remove what the record lists and nothing else; then forget the view."""
    record = view_record(store, view_id)
    if record is None:
        raise FileNotFoundError(f"view {view_id} is not recorded at {store.root}")
    root = record.root
    if not root.exists():
        retire_record(store, view_id)
        return DropReport((), (f"{root}: the view's directory is already gone",))
    removed: list[str] = []
    left: list[str] = []
    for entry in record.entries:
        path = root / entry.path
        if entry.rung == "parked" or not _lexists(path):
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(entry.path)
    for directory in [*reversed(record.directories), "."]:
        path = root / directory
        strays = sorted(child.name for child in path.iterdir())
        if strays:
            left.append(f"{directory}: not created by the view: {', '.join(strays)}")
            continue
        path.rmdir()
        removed.append(directory)
    retire_record(store, view_id)
    return DropReport(tuple(removed), tuple(left))


def _lexists(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def live_roots(store: Store) -> list[Digest]:
    """Every live view's tree; a view whose directory is gone is retired."""
    roots: list[Digest] = []
    for record in views(store):
        if record.root.exists():
            roots.append(record.tree)
        else:
            retire_record(store, record.id)
    return roots


# Collecting.


@dataclass
class _Node:
    files: dict[str, Entry | Link] = field(default_factory=dict)
    dirs: dict[str, _Node] = field(default_factory=dict)


def collect(store: Store, at: Path, declared: Iterable[str]) -> Tree:
    """Read the declared paths under *at* back into a tree, landing every object."""
    root = _Node()
    for relative in declared:
        parts = relative.split("/")
        path = at.joinpath(*parts)
        if not _lexists(path):
            raise FileNotFoundError(
                f"declared output {relative!r} is absent under {at}"
            )
        node = root
        for part in parts[:-1]:
            node = node.dirs.setdefault(part, _Node())
        collected = _collect_path(store, at, path, relative)
        if isinstance(collected, _Node):
            node.dirs[parts[-1]] = collected
        else:
            node.files[parts[-1]] = collected
    return _land_tree(store, root)


def _collect_path(
    store: Store, at: Path, path: Path, relative: str
) -> Entry | Link | _Node:
    if len(relative.encode("utf-8")) > PATH_BUDGET:
        raise FormatError(f"path {relative!r} is longer than {PATH_BUDGET} UTF-8 bytes")
    if path.is_symlink() and _points_inside(at, path):
        # A link inside the view is content: a symlink entry. A link
        # out of the view is the materialiser's own presentation of
        # bytes owned elsewhere, the link rung, and is read through.
        return Link(path.name, check_target(os.readlink(path)))
    if path.is_dir():
        node = _Node()
        for child in sorted(path.iterdir()):
            collected = _collect_path(store, at, child, f"{relative}/{child.name}")
            if isinstance(collected, _Node):
                node.dirs[child.name] = collected
            else:
                node.files[child.name] = collected
        return node
    with path.open("rb") as handle:
        landed = store.land(handle)
    executable = bool(path.stat().st_mode & stat.S_IXUSR)
    return Entry(path.name, "blob", landed.digest, landed.size, executable)


def _points_inside(at: Path, link: Path) -> bool:
    # The link's own target, one step and no further: a link to a
    # within-view file that is itself the link rung's symlink into the
    # store is still content, because its target is inside the view.
    raw = os.readlink(link)
    target = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(link)), raw))
    root = os.path.abspath(at)
    return target == root or target.startswith(root + os.sep)


def _land_tree(store: Store, node: _Node) -> Tree:
    entries: list[Entry | Link] = list(node.files.values())
    for name, child in node.dirs.items():
        data = _land_tree(store, child).encode()
        entries.append(Entry(name, "tree", store.put(data), len(data)))
    tree = Tree.of(entries)
    store.put(tree.encode())
    return tree


# Warming and shedding.


def prefetch(store: Store, digest: Digest) -> list[Digest]:
    """Fetch *digest* and everything it reaches; the objects fetched, in order."""
    fetched: list[Digest] = []
    seen: set[Digest] = set()
    stack = [digest]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        store.fetch(current)
        fetched.append(current)
        stack.extend(children(store.read(current)))
    return fetched


def shed(store: Store, source: Source) -> ShedReport:
    """Evict every local copy *source* holds, except what a live view depends on."""
    needed: set[Digest] = set()
    for record in views(store):
        if record.root.exists():
            needed |= record.depends_on()
    shed_out: list[Digest] = []
    kept: list[Digest] = []
    for digest in list(store.objects()):
        if digest in needed:
            kept.append(digest)
        elif source_holds(store, source, digest):
            store.evict(digest)
            shed_out.append(digest)
    return ShedReport(source.describe(), tuple(shed_out), tuple(kept))


def source_holds(store: Store, source: Source, digest: Digest) -> bool:
    """Whether *source* has the object, by existence and never by trust."""
    algorithm, fanout, rest = digest.path_parts
    if isinstance(source, FolderSource):
        return (source.path / "objects" / algorithm / fanout / rest).exists()
    if isinstance(source, HttpSource):
        return store.head(source, f"objects/{algorithm}/{fanout}/{rest}")
    return source.digest == digest
