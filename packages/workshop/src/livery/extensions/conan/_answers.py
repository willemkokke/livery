"""The conan extension's answers about a package, from its recipe."""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.extensions.conan import _package

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from livery.workshop._packages import Package


def current_version(package: Package) -> str:
    """The version the recipe's ``version`` attribute declares."""
    return _package.current_version(package)


def version_files(package: Package) -> tuple[Path, ...]:
    """The recipe, ``conanfile.py``, the one file the version lives in."""
    return tuple(_package.stamp_version(package).homes())


def requirements(package: Package) -> Mapping[str, str]:
    """The conan references the recipe requires, each name to its version range."""
    return _package.declared_requirements(package)
