"""The Python backend: the quality verbs for ``type = "python"``.

One invocation covers every Python package at once: the checkers read
their scopes from the workspace's own configuration, so the whole
repository is linted exactly as CI lints it, and a tracked file
outside any package still cannot pass the gate and fail the build.
Per-package scoping arrives with the affected engine.
"""

from __future__ import annotations

from toolroom import basedpyright, mypy, pyrefly, pytest, ruff, ruff_format, ty

from livery.workshop._packages import Package

#: The whole repo, as CI lints it.
SRC = (".",)


def run_format(check: bool = False) -> None:
    """Format with ruff; *check* reports instead of rewriting."""
    ruff_format(*SRC, check=check)


def run_lint(fix: bool = False) -> None:
    """Lint with ruff; *fix* applies safe fixes in place."""
    ruff.check(*SRC, fix=fix)


def run_typecheck() -> None:
    """Type-check with all four gating checkers, in parallel.

    basedpyright runs with warnings gating as errors. mypy is strict
    on livery.* and checks every test body as consumer code, once per
    platform (linux from config, darwin and win32 by flag), since
    mypy has no all-platforms mode. ty and pyrefly check every
    platform at once at the scopes pyproject pins. All four gate: a
    checker livery uses is a checker the tree is clean against.
    """
    from footman import parallel, step

    def based() -> None:
        basedpyright(warnings=True)

    # Each mypy run gets its own cache dir: the SQLite cache does not
    # tolerate three concurrent writers on one file.
    def mypy_linux() -> None:
        mypy(cache_dir=".mypy_cache/linux")

    def mypy_darwin() -> None:
        mypy(platform="darwin", cache_dir=".mypy_cache/darwin")

    def mypy_win32() -> None:
        mypy(platform="win32", cache_dir=".mypy_cache/win32")

    def run_ty() -> None:
        ty.check()

    def run_pyrefly() -> None:
        pyrefly("check")

    parallel(
        step(based, title="basedpyright")(),
        step(mypy_linux)(),
        step(mypy_darwin)(),
        step(mypy_win32)(),
        step(run_ty, title="ty")(),
        step(run_pyrefly, title="pyrefly")(),
    )


def run_typecomplete(packages: tuple[Package, ...]) -> None:
    """Verify each package's public API is 100% type-complete.

    The importable module is derived from the distribution name
    (``livery-forge`` is ``livery.forge``); the exit code is the
    verdict, 0 only when every public symbol has a fully known type.
    """
    for package in packages:
        module = package.name.replace("-", ".")
        basedpyright(verifytypes=module, ignoreexternal=True)


def run_test(*pytest_args: str) -> None:
    """Run the test suite; *pytest_args* forwarded verbatim."""
    pytest.opts(in_process=False)(*pytest_args)
