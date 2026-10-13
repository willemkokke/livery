"""The runner labels the release point's wheels job fans out to.

A package that holds nanobind names the runner labels that build its
platform wheels under ``[nanobind] wheel-platforms`` in its own
``workshop.toml``. The wheels job nanobind's declaration adds to the
release point names [livery.extensions.nanobind._ci.wheel_runners][] as
its ``labels`` ([livery.workshop._points.Job][]): one leg per label in
the union over those packages. A workspace whose packages hold no
nanobind names no label, and its release renders no wheels job. The
verb each leg runs is [livery.extensions.nanobind._tasks][]'s; this
module only reads the contracts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._kinds import extension_set
from livery.workshop._packages import Package, discover_packages

#: The extension whose packages build platform wheels.
EXTENSION = "nanobind"

#: The package contract key naming the runner labels that build its wheels.
WHEEL_PLATFORMS_KEY = "wheel-platforms"


def holds_nanobind(package: Package) -> bool:
    """Whether *package* holds nanobind: it lists it, or its kind stands for it."""
    return EXTENSION in extension_set(package)


def declared_wheel_platforms(package: Package) -> list[str]:
    """The runner labels *package* declares under ``[nanobind] wheel-platforms``.

    Empty for a package that holds no nanobind and names no label.

    Raises:
        Failed: naming the package and the key, when a package that
            holds nanobind declares no labels, an empty list or a blank
            label, since the forge cannot schedule a leg on any of
            those; and when a package that holds no nanobind declares
            the key, since no leg builds its wheel. The labels are the
            package's own fact, never guessed from the check runners.
    """
    contract = load_contract(package.directory / "workshop.toml")
    table = contract.get(EXTENSION) or {}
    declared: Any = table.get(WHEEL_PLATFORMS_KEY) if isinstance(table, dict) else None
    where = f"{package.path}: [{EXTENSION}] {WHEEL_PLATFORMS_KEY}"
    if not holds_nanobind(package):
        if declared is not None:
            fail(
                f"{where} is declared on a package that holds no {EXTENSION},"
                " and no wheels job builds its wheel; remove the key"
            )
        return []
    if declared is None:
        fail(
            f"{where} is missing: a package that holds {EXTENSION} names the"
            ' runner labels that build its wheels, like ["ubuntu-latest",'
            ' "macos-latest", "windows-latest"]'
        )
    # The contract's judge holds it to a list of strings.
    if not declared:
        fail(
            f'{where} must be a non-empty list of runner labels, like ["ubuntu-latest"]'
        )
    for label in declared:
        if not label.strip():
            fail(f"{where} entry {label!r} is not a runner label")
    return [str(label) for label in declared]


def wheel_runners(root: Path) -> list[str]:
    """The wheels job's legs: every label the packages declare, once, in order.

    The packages are read in discovery order, and each package's labels
    in its own order. Empty when no package holds nanobind, so the
    release renders no wheels job.
    """
    labels: list[str] = []
    for package in discover_packages(root):
        for label in declared_wheel_platforms(package):
            if label not in labels:
                labels.append(label)
    return labels
