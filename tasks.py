"""livery's dev loop, temporary until livery.workshop provides it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate; CI
runs the same command. Coverage joins the gate with the first real
code: an empty package has nothing honest to measure.
"""

from __future__ import annotations

from typing import Annotated

from footman import doc, fail, group, parallel, task
from toolroom import basedpyright, pytest, ruff, ruff_format

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
    """Type-check with basedpyright (warnings gate as errors)."""
    basedpyright(warnings=True)


@task
def test(*pytest_args: str):
    """Run the test suite.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    pytest.opts(in_process=False)(*pytest_args)


@task
def check():
    """The gate: format check, lint, typecheck, and tests, in parallel."""
    with parallel():
        format(check=True)
        lint()
        typecheck()
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
