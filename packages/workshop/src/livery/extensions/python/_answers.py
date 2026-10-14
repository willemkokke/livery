"""The python extension's answers about a package, from its manifest and sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.extensions.python import _backend

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from livery.forge import Registry
    from livery.workshop._packages import Package, SourceReferences


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


def references(package: Package) -> tuple[SourceReferences, ...]:
    """What each python source under ``src`` and ``tests`` references, one per file."""
    return _backend.references(package)


#: The ecosystem a python package is released to, as the ``distributions``
#: query names it, and the registry kind its target resolves by.
ECOSYSTEM = "python"


def distributions(package: Package) -> tuple[tuple[str, str], ...]:
    """The distribution the package releases, as its ecosystem and its name."""
    return ((ECOSYSTEM, package.name),)


def registry(package: Package) -> Registry:
    """The simple index the package is released to, as a probe of its versions.

    The index is the python target the registry ladder resolves for the
    package's workspace ([livery.workshop.registry][]), and the probe
    reads it with the target's credential: an authenticated index, a
    forge's own registry or a private owner's, answers only with it, and
    an anonymous probe would wait for a version the index already
    serves.
    """
    from livery.forge import SimpleRegistry
    from livery.workshop import registry as resolved
    from livery.workshop._packages import root_of

    target = resolved(root_of(package), ECOSYSTEM)
    return SimpleRegistry(target.url, token=target.token)
