"""The update family's file movers.

The pieces ``workflow.update`` drives: raise ``[[depends]]`` floors
to the latest released tags, and read the newest release per package
from the tags. The driver that branches, commits, and submits lives
beside this module.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery.footman.api import fail
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import discover_packages

_RELEASE_TAG_RE = re.compile(r"^(packages/[^/]+)/v(\d+)\.(\d+)\.(\d+)$")


def latest_released(tags: tuple[str, ...]) -> dict[str, str]:
    """The newest released version per package path, from *tags*."""
    latest: dict[str, tuple[int, int, int]] = {}
    for tag in tags:
        match = _RELEASE_TAG_RE.fullmatch(tag)
        if match is None:
            continue
        path = match.group(1)
        version = (int(match.group(2)), int(match.group(3)), int(match.group(4)))
        if version > latest.get(path, (-1, -1, -1)):
            latest[path] = version
    return {path: ".".join(map(str, v)) for path, v in latest.items()}


def bump_floors(root: Path, git: GitOps, *, only: tuple[str, ...] = ()) -> list[str]:
    """Raise floors to the latest released tags; what changed.

    A floor names the oldest version a dependant accepts; this raises
    it to the newest release so instances move together. Both homes
    move in step: the ``[[depends]]`` edge in ``workshop.toml`` and the
    ``>=`` constraint in ``pyproject.toml``. *only* scopes the move
    to floors on the named distributions (``livery-forge``); empty
    moves every floor.
    """
    packages = discover_packages(root)
    dist_names = {p.path: p.name for p in packages}
    released = latest_released(git.tags())
    changed = []
    for package in packages:
        for edge in package.depends:
            if only and dist_names.get(edge.path, "") not in only:
                continue
            newest = released.get(edge.path, "")
            if not edge.floor or not newest or newest == edge.floor:
                continue
            contract = package.directory / "workshop.toml"
            text = contract.read_text("utf-8")
            scoped = _bump_edge_floor(text, edge.path, edge.floor, newest)
            contract.write_text(scoped, encoding="utf-8")
            pyproject = package.directory / "pyproject.toml"
            text = pyproject.read_text("utf-8")
            pyproject.write_text(
                text.replace(f">={edge.floor}", f">={newest}"), encoding="utf-8"
            )
            changed.append(
                f"{package.path}: floor on {edge.path} {edge.floor} -> {newest}"
            )
    return changed


def _bump_edge_floor(text: str, dep_path: str, old: str, new: str) -> str:
    """The contract text with one edge's floor raised, scoped to its block."""
    anchor = text.find(f'path = "{dep_path}"')
    if anchor == -1:
        fail(f"no [[depends]] edge on {dep_path} found to bump")
    tail = text[anchor:]
    bumped, count = re.subn(
        rf'floor = "{re.escape(old)}"', f'floor = "{new}"', tail, count=1
    )
    if count != 1:
        fail(f"the edge on {dep_path} has no floor {old!r} line to bump")
    return text[:anchor] + bumped
