"""Which packages a change to a file outside every package is about.

The affected engine ([livery.workshop._graph.affected_from_paths][])
reads a changed root file as affecting everything, since the root
files configure every gate. Adding or removing a package changes root
files too: the composed `pyproject.toml` names the member, CODEOWNERS
its directory, `uv.lock` its entry. This module attributes such a
change to the packages it is about, so the package's own gate runs and
the others' do not.

A root file's change is explained by the package set when removing
every entry that names an added or removed package, from both
versions, leaves the same text. `uv.lock`'s change is attributed per
member: each workspace member whose resolved closure differs is
affected, and the root project's own change must lie inside the
closures of affected members. Anything else is unexplained, and the
caller runs everything, as before.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from livery.workshop._git_ops import GitOps
from livery.workshop._packages import PACKAGES_DIR, Package

#: The lock every python member resolves through.
LOCK = "uv.lock"


@dataclass(frozen=True)
class Delta:
    """The packages that exist on one side of a change and not the other.

    Attributes:
        added: The packages the change adds, as discovery sees them now.
        removed: The removed packages' paths and distribution names,
            as their contracts said before the change.
    """

    added: tuple[Package, ...]
    removed: tuple[tuple[str, str], ...]

    @property
    def tokens(self) -> tuple[str, ...]:
        """Every path and distribution name that names a package of the delta."""
        names = [
            token for package in self.added for token in (package.path, package.name)
        ]
        names += [token for path, name in self.removed for token in (path, name)]
        return tuple(token for token in names if token)

    def holds(self, path: str) -> bool:
        """Whether *path* lies under a removed package's directory."""
        return any(path.startswith(f"{removed}/") for removed, _ in self.removed)


def package_delta(git: GitOps, before: str, packages: Iterable[Package]) -> Delta:
    """The packages added and removed between *before* and the working tree.

    One listing of *before*'s tree answers which contracts existed
    then; a package's contract is ``packages/<name>/workshop.toml`` or,
    in a group directory, ``packages/<group>/<name>/workshop.toml``.
    """
    listed = git._run("ls-tree", "-r", "--name-only", before, "--", PACKAGES_DIR)
    contracts = {
        line.removesuffix("/workshop.toml")
        for line in listed.splitlines()
        if line.endswith("/workshop.toml") and len(line.split("/")) in (3, 4)
    }
    now = list(packages)
    present = {package.path for package in now}
    added = tuple(package for package in now if package.path not in contracts)
    removed: list[tuple[str, str]] = []
    for path in sorted(contracts - present):
        try:
            contract = tomllib.loads(git.file_at(before, f"{path}/workshop.toml"))
        except tomllib.TOMLDecodeError:
            contract = {}
        removed.append((path, str(contract.get("name") or "")))
    return Delta(added, tuple(removed))


def _boundary(token: str) -> str:
    """*token* as a pattern that does not match inside a longer name."""
    return rf"(?<![\w.-]){re.escape(token)}(?![\w.-])"


def _without(text: str, tokens: tuple[str, ...]) -> str:
    """*text* with every entry naming one of *tokens* removed, whitespace dropped.

    A quoted entry naming a token goes on its own, so a list on one
    line keeps its other entries; a line that still names a token
    after that (a table key, a CODEOWNERS or ignore line) goes whole.
    Whitespace is dropped and commas collapsed, so the list a removal
    leaves reads the same as one that never held the entry.
    """
    for token in tokens:
        text = re.sub(rf'"/?{re.escape(token)}(?:[/\[][^"]*)?"', "", text)
    patterns = [re.compile(_boundary(token)) for token in tokens]
    kept = [
        line
        for line in text.splitlines()
        if not any(pattern.search(line) for pattern in patterns)
    ]
    flat = re.sub(r"\s+", "", "\n".join(kept))
    flat = re.sub(r",+", ",", flat)
    return re.sub(r"\[,", "[", re.sub(r",\]", "]", flat))


def explained(before: str, now: str, delta: Delta) -> bool:
    """Whether a root file's change names only *delta*'s packages."""
    tokens = delta.tokens
    if not tokens:
        return False
    return _without(before, tokens) == _without(now, tokens)


def _lock(text: str) -> dict[str, Any] | None:
    """The parsed lock, or None when there is none or it does not parse."""
    if not text:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


Node = tuple[str, str, str]


def _closures(
    lock: dict[str, Any],
) -> tuple[dict[str, frozenset[Node]], frozenset[Node]]:
    """Each workspace member's resolved closure, by package path, and the root's.

    A node is a locked entry: name, version and source. A member is an
    entry whose source is ``editable`` (its path); the root is the
    ``virtual`` entry. Every dependency section counts: runtime, every
    extra, every development group.
    """
    entries = cast("list[dict[str, Any]]", lock.get("package") or [])
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_name.setdefault(str(entry.get("name", "")), []).append(entry)

    def node(entry: dict[str, Any]) -> Node:
        source = json.dumps(entry.get("source") or {}, sort_keys=True)
        return (str(entry.get("name", "")), str(entry.get("version", "")), source)

    def edges(entry: dict[str, Any]) -> list[str]:
        named = list(cast("list[dict[str, Any]]", entry.get("dependencies") or []))
        for section in ("optional-dependencies", "dev-dependencies"):
            groups = cast("dict[str, list[dict[str, Any]]]", entry.get(section) or {})
            for group in groups.values():
                named.extend(group)
        return [str(item.get("name", "")) for item in named]

    def closure(start: dict[str, Any]) -> frozenset[Node]:
        seen: dict[Node, dict[str, Any]] = {node(start): start}
        stack = [start]
        while stack:
            for name in edges(stack.pop()):
                for entry in by_name.get(name, []):
                    key = node(entry)
                    if key not in seen:
                        seen[key] = entry
                        stack.append(entry)
        return frozenset(seen)

    members: dict[str, frozenset[Node]] = {}
    root: frozenset[Node] = frozenset()
    for entry in entries:
        source = cast("dict[str, Any]", entry.get("source") or {})
        if "editable" in source:
            members[str(source["editable"])] = closure(entry)
        elif "virtual" in source:
            root = closure(entry)
    return members, root


def lock_affected(before: str, now: str, delta: Delta) -> set[str] | None:
    """The package paths whose resolution the lock's change moves; None for everything.

    Everything when either side does not parse, when anything beside
    the member list and the entries changed (the resolution's
    settings), or when the root project's closure changed outside the
    closures of the affected members: a tool only the root resolves
    configures every gate.
    """
    old, new = _lock(before), _lock(now)
    if old is None or new is None:
        return None
    settings = {key for key in (*old, *new) if key not in ("package", "manifest")}
    if any(old.get(key) != new.get(key) for key in settings):
        return None
    tokens = delta.tokens
    old_manifest = json.dumps(old.get("manifest") or {}, sort_keys=True)
    new_manifest = json.dumps(new.get("manifest") or {}, sort_keys=True)
    if old_manifest != new_manifest and (
        not tokens or _without(old_manifest, tokens) != _without(new_manifest, tokens)
    ):
        return None
    old_members, old_root = _closures(old)
    new_members, new_root = _closures(new)
    affected = {
        path
        for path in new_members
        if new_members[path] != old_members.get(path, frozenset())
    }
    covered: set[Node] = set()
    for path in affected | {path for path, _ in delta.removed}:
        covered |= new_members.get(path, frozenset())
        covered |= old_members.get(path, frozenset())
    moved = (old_root ^ new_root) - covered
    if moved:
        return None
    return affected
