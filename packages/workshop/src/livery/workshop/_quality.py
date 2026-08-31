"""The quality verbs: the gate and its parts, dispatched by contract.

Each verb discovers the packages by their ``livery.toml``, refuses
any type without a backend, and hands the work to the type's backend
module. ``check`` is the whole local gate; CI runs the same command.
"""

from __future__ import annotations

from typing import Annotated

from footman import doc, parallel, task

from livery.workshop._backends import _python, require_backends
from livery.workshop._layers import workspace_root
from livery.workshop._packages import Package, discover_packages


def _packages() -> tuple[Package, ...]:
    """The workspace's packages, backends verified before anything runs."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no livery.toml above the working directory")
    packages = discover_packages(root)
    require_backends(packages)
    return packages


@task
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False) -> None:
    """Lint every package with its type's linter."""
    _packages()
    _python.run_lint(fix=fix)


@task
def format(check: bool = False) -> None:
    """Format every package with its type's formatter.

    Args:
        check: report instead of rewriting
    """
    _packages()
    _python.run_format(check=check)


@task
def typecheck() -> None:
    """Type-check every package with its type's gating checkers."""
    _packages()
    _python.run_typecheck()


@task
def typecomplete() -> None:
    """Verify every package's public API is 100% type-complete."""
    _python.run_typecomplete(_packages())


@task
def test(*pytest_args: str) -> None:
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    _packages()
    _python.run_test(*pytest_args)


@task
def check() -> None:
    """Run the gate: format check, lint, both type gates, tests, in parallel."""
    with parallel():
        format(check=True)
        lint()
        typecheck()
        typecomplete()
        test()


@task
def clean() -> None:
    """Remove build artifacts and tool caches."""
    import shutil

    root = workspace_root()
    if root is None:
        return
    for name in ("dist", ".pytest_cache", ".ruff_cache", ".mypy_cache"):
        shutil.rmtree(root / name, ignore_errors=True)
