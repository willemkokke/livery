"""The python extension's answers about a package, from its manifest and sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.extensions.python import _backend

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from livery.workshop._packages import Package


def current_version(package: Package) -> str:
    """The version the package's ``pyproject.toml`` declares."""
    return _backend.current_version(package)


def version_files(package: Package) -> tuple[Path, ...]:
    """``pyproject.toml``, and every ``__init__.py`` that may carry ``__version__``."""
    return tuple(_backend.stamp_version(package).homes())


def requirements(package: Package) -> Mapping[str, str]:
    """The package's ``[project.dependencies]``, each name to its constraint."""
    return _backend.declared_requirements(package)


def module_roots(package: Package) -> tuple[str, ...]:
    """The import prefixes the package's code is referenced by."""
    return tuple(_backend.module_roots(package))


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare the package's public API."""
    return tuple(_backend.public_modules(package))
