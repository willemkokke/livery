"""The python extension's answers: about a package, and about the workspace.

About a package, from its manifest and sources: its version, the files
a version is written into, its requirements, the names its code is
referenced by, its public modules and what each source references.
About the workspace, while pytest is listed: the functions that run
the python packages' suites and a package's documentation examples.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.extensions.python import _backend

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from livery.forge import Registry
    from livery.workshop import Package, RequirementWriter, SourceReferences


def current_version(package: Package) -> str:
    """The version the package's ``pyproject.toml`` declares."""
    return _backend.current_version(package)


def version_files(package: Package) -> tuple[Path, ...]:
    """``pyproject.toml``, and every ``__init__.py`` that may carry ``__version__``."""
    return tuple(_backend.stamp_version(package).homes())


def requirements(package: Package) -> Mapping[str, str]:
    """The package's ``[project.dependencies]``, each name to its constraint."""
    return _backend.declared_requirements(package)


def requirement_writers(package: Package) -> tuple[RequirementWriter, ...]:
    """The pyproject's writer of a requirement on a sibling that holds python."""
    del package
    return (_backend.declare_requirement,)


def test_requirements(package: Package) -> Mapping[str, str]:
    """The package's ``test`` extra, each name to its constraint."""
    return _backend.declared_test_requirements(package)


def test_requirement_writers(package: Package) -> tuple[RequirementWriter, ...]:
    """The pyproject's writer of a test requirement on a sibling that holds python."""
    del package
    return (_backend.declare_test_requirement,)


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


def executables(package: Package) -> tuple[str, ...]:
    """The scripts the package's ``pyproject.toml`` names in ``[project.scripts]``."""
    return _backend.scripts(package)


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

    target = resolved(package.root, ECOSYSTEM)
    return SimpleRegistry(target.url, token=target.token)


def suite_runner(root: Path) -> Callable[..., None]:
    """Answer ``python.test.runner`` with the function that runs the suites.

    pytest's test step asks with the workspace root, which the answer
    does not depend on, and calls the answer with the words for pytest,
    the packages whose suites run in one call, the root, the test files
    that stand for each package's suite, the CI point and whether the
    run is measured.
    """
    del root
    return _backend.run_test


def examples_runner(
    root: Path,
) -> Callable[[Package, Path, tuple[str, ...], tuple[str, ...]], None]:
    """Answer ``python.examples.runner`` with the function that runs examples.

    pytest's examples step asks with the workspace root, which the
    answer does not depend on, and calls the answer once per package
    with the package, the root, the example files the run names (empty
    for all of them) and the words for pytest.
    """
    del root
    return _backend.run_examples
