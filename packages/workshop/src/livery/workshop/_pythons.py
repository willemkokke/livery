"""Python version facts, derived from the workspace, never asked.

The floor comes from the root ``pyproject.toml``'s ``requires-python``
bound; the newest supported minor is the workshop's own declaration.
The CI matrix is the pair, so a new Python reaches every instance
through a wheel bump, never an instance edit.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

#: The oldest Python a workspace being born supports; the floor once
#: its ``pyproject.toml`` exists and declares one.
FLOOR_DEFAULT = "3.11"

#: The newest minor the workshop tests against. Raising it widens
#: every instance's CI matrix on its next wheel bump.
NEWEST_SUPPORTED = "3.14"


def python_floor(root: Path) -> str:
    """The workspace's Python floor.

    The lower bound of the root ``pyproject.toml``'s
    ``requires-python``;
    livery.workshop._pythons.FLOOR_DEFAULT when the file or the
    bound is absent, which is what a workspace being born looks
    like.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return FLOOR_DEFAULT
    data = tomllib.loads(pyproject.read_text("utf-8"))
    spec = str(data.get("project", {}).get("requires-python", ""))
    match = re.search(r">=\s*(\d+\.\d+)", spec)
    return match.group(1) if match else FLOOR_DEFAULT


def _minor(version: str) -> tuple[int, int]:
    major, _, minor = version.partition(".")
    return int(major), int(minor)


def python_matrix(root: Path) -> list[str]:
    """The CI matrix: the floor, and the newest supported minor above it."""
    floor = python_floor(root)
    if _minor(floor) >= _minor(NEWEST_SUPPORTED):
        return [floor]
    return [floor, NEWEST_SUPPORTED]
