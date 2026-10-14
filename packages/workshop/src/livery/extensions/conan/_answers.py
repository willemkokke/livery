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


#: The ecosystem a conan package is released to, as the ``distributions``
#: query names it.
ECOSYSTEM = "conan"


def distributions(package: Package) -> tuple[tuple[str, str], ...]:
    """The conan package the recipe releases, as its ecosystem and its name.

    A recipe that declares both ``name`` and ``version`` names a
    reference and releases the package; a consumer's recipe,
    requirements alone, releases nothing, so it answers empty.
    """
    if not _package.names_a_reference(package):
        return ()
    return ((ECOSYSTEM, package.name),)


def registry(package: Package) -> _package.ConanRegistry | None:
    """The conan target the recipe's package goes to, as a probe of its versions.

    The target is the one the registry ladder resolves for conan in the
    package's workspace ([livery.workshop.registry][]): a conan remote,
    a folder, or the forge's releases. A consumer's recipe releases
    nothing, so it answers None.
    """
    if not _package.names_a_reference(package):
        return None
    from livery.workshop import registry as resolved
    from livery.workshop._packages import root_of

    root = root_of(package)
    return _package.ConanRegistry(resolved(root, ECOSYSTEM), root=root)
