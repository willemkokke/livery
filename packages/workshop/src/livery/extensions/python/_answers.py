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

    from livery.workshop._packages import Package, SourceReferences
    from livery.workshop._queries import RequirementWriter


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


def module_roots(package: Package) -> tuple[str, ...]:
    """The import prefixes the package's code is referenced by."""
    return tuple(_backend.module_roots(package))


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare the package's public API."""
    return tuple(_backend.public_modules(package))


def references(package: Package) -> tuple[SourceReferences, ...]:
    """What each python source under ``src`` and ``tests`` references, one per file."""
    return _backend.references(package)


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
