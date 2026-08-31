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
from livery.workshop._templates import template_check


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
    """Run the test suite, coverage floors enforced.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    packages = _packages()
    root = workspace_root()
    _python.run_test(*pytest_args, packages=packages, root=root)


def _affected() -> tuple[Package, ...] | None:
    """The affected subset for the gate; None means everything."""
    from livery.workshop._git_ops import GitOps
    from livery.workshop._graph import affected_packages

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no livery.toml above the working directory")
    git = GitOps(root)
    git.fetch()
    return affected_packages(root, git)


@task
def check(
    affected: Annotated[
        bool, doc("scope the gate to the branch's affected packages")
    ] = False,
) -> None:
    """Run the gate: format, lint, types, tests, render gate, in parallel.

    ``--affected`` narrows every verb to the packages this branch's
    changes can influence (their dependents' closure). A change
    outside the packages configures every gate, so the narrowing
    falls back to everything; ty and pyrefly always check their
    configured whole either way.
    """
    subset = _affected() if affected else None
    if affected and subset is not None:
        packages = _packages()
        if not subset:
            print("  nothing affected: the branch changes no files")
            return
        if len(subset) < len(packages):
            names = ", ".join(package.path for package in subset)
            print(f"  affected: {names}")
            _scoped_check(subset)
            return
    with parallel():
        format(check=True)
        lint()
        typecheck()
        typecomplete()
        test()
        template_check()


def _scoped_check(subset: tuple[Package, ...]) -> None:
    """The gate over *subset* only, each verb explicitly scoped.

    The render gate is skipped: its inputs are the root answers and
    the template source, which a package-scoped change cannot touch
    (touching them makes the change root-scoped, and the full gate
    runs instead).
    """
    from footman import step

    root = workspace_root()
    assert root is not None
    paths = _python.package_paths(subset)
    with parallel():
        step(lambda: _python.run_format(check=True, paths=paths), title="format")()
        step(lambda: _python.run_lint(paths=paths), title="lint")()
        step(lambda: _python.run_typecheck(paths=paths), title="typecheck")()
        step(lambda: _python.run_typecomplete(subset), title="typecomplete")()
        step(
            lambda: _python.run_test(packages=subset, root=root, scoped=True),
            title="test",
        )()


@task
def clean() -> None:
    """Remove build artifacts and tool caches."""
    import shutil

    root = workspace_root()
    if root is None:
        return
    for name in ("dist", ".pytest_cache", ".ruff_cache", ".mypy_cache"):
        shutil.rmtree(root / name, ignore_errors=True)
