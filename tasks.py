"""livery's dev loop, temporary until livery.workshop provides it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate; CI
runs the same command. Coverage joins the gate with the first real
code: an empty package has nothing honest to measure.
"""

from __future__ import annotations

from typing import Annotated

from footman import doc, fail, group, parallel, step, task
from toolroom import basedpyright, mypy, pyrefly, pytest, ruff, ruff_format, ty

# The whole repo, as CI lints it. Anything narrower lets a tracked file
# outside src/tests pass the gate and fail the build (footman's lesson).
SRC = (".",)


@task
def lint(fix: Annotated[bool, doc("apply safe fixes in place")] = False):
    """Lint with ruff."""
    ruff.check(*SRC, fix=fix)


@task
def format(check: bool = False):
    """Format with ruff.

    Args:
        check: report instead of rewriting
    """
    ruff_format(*SRC, check=check)


@task
def typecheck():
    """Type-check with all four gating checkers, in parallel.

    basedpyright runs with warnings gating as errors. mypy is strict on
    livery.* and checks every test body as consumer code, once per
    platform (linux from config, darwin and win32 by flag), since mypy
    has no all-platforms mode. ty and pyrefly check every platform at
    once at the scopes pyproject pins. All four gate: a checker livery
    uses is a checker the tree is clean against.
    """

    def based():
        basedpyright(warnings=True)

    # Each mypy run gets its own cache dir: the SQLite cache does not
    # tolerate three concurrent writers on one file.
    def mypy_linux():
        mypy(cache_dir=".mypy_cache/linux")

    def mypy_darwin():
        mypy(platform="darwin", cache_dir=".mypy_cache/darwin")

    def mypy_win32():
        mypy(platform="win32", cache_dir=".mypy_cache/win32")

    def run_ty():
        ty.check()

    def run_pyrefly():
        pyrefly("check")

    parallel(
        step(based, title="basedpyright")(),
        step(mypy_linux)(),
        step(mypy_darwin)(),
        step(mypy_win32)(),
        step(run_ty, title="ty")(),
        step(run_pyrefly, title="pyrefly")(),
    )


@task
def typecomplete():
    """Verify the public API is 100% type-complete (pyright --verifytypes).

    The exit code is the verdict: 0 only when every public symbol has a
    fully known type. A new unannotated export fails the gate here
    before a consumer's checker ever sees it.
    """
    basedpyright(verifytypes="livery.forge", ignoreexternal=True)


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    pytest.opts(in_process=False)(*pytest_args)


@task
def check():
    """Run the gate: format check, lint, both type gates, tests, in parallel."""
    with parallel():
        format(check=True)
        lint()
        typecheck()
        typecomplete()
        test()


forge = group("forge", help="livery.forge development")
dev = forge.group("dev", help="Local forge containers (Gitea and GitLab)")

_NOT_BUILT = (
    "forge.dev is not built yet: the compose file and seed scripts do not exist."
)


@dev.task(name="up")
def dev_up():
    """Start the local Gitea and GitLab containers."""
    fail(_NOT_BUILT)


@dev.task(name="down")
def dev_down():
    """Stop the local forge containers."""
    fail(_NOT_BUILT)
