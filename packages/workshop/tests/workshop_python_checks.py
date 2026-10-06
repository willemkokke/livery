"""A python formatter, linter and type checker for the gate's tests.

The base registers no python formatter, linter or type checker: listed
extensions do (ruff and the type checkers, in this repository). The
gate's tests need such checks to walk, scope, order and claim, so
`python_checks` registers three with ruff's and mypy's shapes under the
extension ``fake``, after the base's checks as a mount would, and puts
the registry back after the test; a test module imports
`python_checks_fixture` and names ``python_checks`` as a parameter. Each
body calls this module's `run_format`, `run_lint` or `run_typecheck`,
which a test replaces to watch them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.workshop._checks import (
    PATHS,
    CheckRecord,
    Claim,
    GateContext,
    register_check,
    restore,
    scoped_paths,
    snapshot,
)
from livery.workshop._invoke import run_batched

#: The suffixes the pair reads.
SUFFIXES = (".py", ".pyi")


def run_format(*, check: bool, safe_fix: bool = False, paths: tuple[str, ...]) -> None:
    """The formatter's call; a test replaces it to watch the calls."""
    del check, safe_fix, paths


def run_lint(*, fix: bool, safe_fix: bool = False, paths: tuple[str, ...]) -> None:
    """The linter's call; a test replaces it to watch the calls."""
    del fix, safe_fix, paths


def run_typecheck(*, paths: tuple[str, ...]) -> None:
    """The type checker's call; a test replaces it to watch the calls."""
    del paths


def _format_run(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.fake"),
        lambda batch: run_format(check=True, paths=batch),
    )


def _format_fix(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.fake"),
        lambda batch: run_format(check=False, safe_fix=ctx.safe, paths=batch),
    )


def _lint_run(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.fake"),
        lambda batch: run_lint(fix=False, paths=batch),
    )


def _lint_fix(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.fake"),
        lambda batch: run_lint(fix=not ctx.safe, safe_fix=ctx.safe, paths=batch),
    )


def _typecheck_run(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "typecheck.fake"),
        lambda batch: run_typecheck(paths=batch),
    )


def records() -> tuple[CheckRecord, ...]:
    """A formatter and a linter with ruff's claims and kinds, then a type checker."""
    kinds = ("python", "cpp-conan")
    example = ("D", "E", "I", "UP", "B", "SIM", "C4", "RUF", "F401", "F811", "F841")
    return (
        CheckRecord(
            "fake",
            "format",
            _format_run,
            narrowing=PATHS,
            fix=_format_fix,
            kinds=kinds,
            tools=("fake",),
            extension="fake",
            claims=tuple(
                Claim(category, suffixes=SUFFIXES)
                for category in ("source", "test", "test-support", "configuration")
            ),
        ),
        CheckRecord(
            "fake",
            "lint",
            _lint_run,
            narrowing=PATHS,
            fix=_lint_fix,
            kinds=kinds,
            tools=("fake",),
            extension="fake",
            claims=(
                Claim("source", suffixes=SUFFIXES),
                Claim("test", ignore=("D1",), suffixes=SUFFIXES),
                Claim("example", ignore=example, suffixes=SUFFIXES),
                Claim("test-support", ignore=("D1",), suffixes=SUFFIXES),
                Claim("configuration", suffixes=SUFFIXES),
            ),
        ),
        CheckRecord(
            "fake",
            "typecheck",
            _typecheck_run,
            narrowing=PATHS,
            kinds=("python",),
            tools=("fake",),
            extension="fake",
            claims=tuple(
                Claim(category, suffixes=SUFFIXES)
                for category in ("source", "test", "test-support")
            ),
        ),
    )


@pytest.fixture(name="python_checks")
def python_checks_fixture() -> Iterator[tuple[CheckRecord, ...]]:
    """Register the three for the test; the registry is put back after it."""
    state = snapshot()
    pair = records()
    for record in pair:
        register_check(record)
    try:
        yield pair
    finally:
        restore(state)
