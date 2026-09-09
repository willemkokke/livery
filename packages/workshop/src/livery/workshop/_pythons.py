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

from livery.footman import fail

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


#: The contract key that overrides the derived matrix, born kebab-case.
PYTHONS_KEY = "python-versions"

_VERSION_RE = re.compile(r"^\d+\.\d+t?$")


def declared_pythons(root: Path) -> list[str] | None:
    """The contract's ``[ci] python-versions``, or ``None`` when undeclared.

    Refuses a declaration that is not a non-empty list of version
    strings (``3.14``, or ``3.14t`` for a free-threaded build), naming
    the key: an empty list would emit a matrix with no leg, and a
    stray value would reach the runner as a python it cannot find.
    """
    contract = root / "workshop.toml"
    if not contract.is_file():
        return None
    ci = tomllib.loads(contract.read_text("utf-8")).get("ci") or {}
    if PYTHONS_KEY not in ci:
        return None
    declared = ci[PYTHONS_KEY]
    if not isinstance(declared, list) or not declared:
        fail(f'[ci] {PYTHONS_KEY} must be a non-empty list of versions, like ["3.14"]')
    for value in declared:
        if not isinstance(value, str) or not _VERSION_RE.match(value):
            fail(
                f"[ci] {PYTHONS_KEY} entry {value!r} is not a python version:"
                ' spell the minor, like "3.14", or "3.14t" for a free-threaded build'
            )
    return [str(value) for value in declared]


def python_matrix(root: Path) -> list[str]:
    """The CI matrix: the contract's declaration, else the floor and the newest minor.

    ``[ci] python-versions`` wins where declared, so a development
    workspace runs one leg and a production contract keeps the full
    pair; without it the pair derives, so a new Python reaches every
    instance through a wheel bump.
    """
    declared = declared_pythons(root)
    if declared is not None:
        return declared
    floor = python_floor(root)
    if _minor(floor) >= _minor(NEWEST_SUPPORTED):
        return [floor]
    return [floor, NEWEST_SUPPORTED]
