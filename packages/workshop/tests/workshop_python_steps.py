"""A python formatter, linter, type checker and test runner for the gate's tests.

The base registers no python formatter, linter, type checker or test
runner: listed extensions do (ruff, the type checkers and pytest, in
this repository). The gate's tests need such steps to walk, scope,
order and claim, so `python_steps` registers four with ruff's, mypy's
and pytest's shapes under the extension ``fake``, after the base's
steps as a mount would, and puts the registry back after the test; a
test module imports `python_steps_fixture` and names ``python_steps``
as a parameter. Each body calls this module's `run_format`, `run_lint`,
`run_typecheck` or `run_test`, which a test replaces to watch them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.workshop._coverage_store import workspace_suite
from livery.workshop._invoke import run_batched
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    PACKAGES,
    PATHS,
    Claim,
    Option,
    StepRecord,
    register_step,
    restore,
    scoped_packages,
    scoped_paths,
    snapshot,
)

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


def run_test(*, packages: tuple[Package, ...], scoped: bool, point: str) -> None:
    """The test runner's call; a test replaces it to watch the calls."""
    del packages, scoped, point


def _format_run(ctx: PhaseContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.fake"),
        lambda batch: run_format(check=True, paths=batch),
    )


def _format_fix(ctx: PhaseContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.fake"),
        lambda batch: run_format(check=False, safe_fix=ctx.safe, paths=batch),
    )


def _lint_run(ctx: PhaseContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.fake"),
        lambda batch: run_lint(fix=False, paths=batch),
    )


def _lint_fix(ctx: PhaseContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.fake"),
        lambda batch: run_lint(fix=not ctx.safe, safe_fix=ctx.safe, paths=batch),
    )


def _typecheck_run(ctx: PhaseContext) -> None:
    run_batched(
        scoped_paths(ctx, "typecheck.fake"),
        lambda batch: run_typecheck(paths=batch),
    )


def _test_run(ctx: PhaseContext) -> None:
    # The workspace's own tests ride every run, as pytest's do.
    unit = workspace_suite(ctx.root)
    suites = scoped_packages(ctx, "test.fake") + ((unit,) if unit else ())
    if suites:
        run_test(packages=suites, scoped=ctx.scoped, point=ctx.point)


def records() -> tuple[StepRecord, ...]:
    """A formatter and a linter as ruff claims and judges, a type checker, tests."""
    extensions = ("python", "conan")
    example = ("D", "E", "I", "UP", "B", "SIM", "C4", "RUF", "F401", "F811", "F841")
    return (
        StepRecord(
            "fake",
            "format",
            _format_run,
            narrowing=PATHS,
            fix=_format_fix,
            extensions=extensions,
            tools=("fake",),
            extension="fake",
            claims=tuple(
                Claim(category, suffixes=SUFFIXES)
                for category in ("source", "test", "test-support", "configuration")
            ),
        ),
        StepRecord(
            "fake",
            "lint",
            _lint_run,
            narrowing=PATHS,
            fix=_lint_fix,
            extensions=extensions,
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
        StepRecord(
            "fake",
            "typecheck",
            _typecheck_run,
            narrowing=PATHS,
            extensions=("python",),
            tools=("fake",),
            extension="fake",
            claims=tuple(
                Claim(category, suffixes=SUFFIXES)
                for category in ("source", "test", "test-support")
            ),
        ),
        StepRecord(
            "fake",
            "test",
            _test_run,
            flags=("point",),
            narrowing=PACKAGES,
            extensions=("python",),
            tools=("fake",),
            extension="fake",
            claims=(
                Claim("test"),
                Claim("test-support"),
                Claim("source", suffixes=SUFFIXES),
            ),
            options=(Option("parallel", "bool", True, "run the suite across cores"),),
        ),
    )


@pytest.fixture(name="python_steps")
def python_steps_fixture() -> Iterator[tuple[StepRecord, ...]]:
    """Register the four for the test; the registry is put back after it."""
    state = snapshot()
    pair = records()
    for record in pair:
        register_step(record)
    try:
        yield pair
    finally:
        restore(state)
