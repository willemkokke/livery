"""A type checker and a test runner for the tests that ask what judges a set.

The base registers no python type checker or test runner: listed
extensions do (the type checkers and pytest, in this repository). A
package's set is judged by the steps that name an extension it holds,
so `python_steps` registers a type checker and a test runner with
mypy's and pytest's shapes under the extension ``fake`` and puts the
registry back after the test; a test module imports
`python_steps_fixture` and names ``python_steps`` as a parameter.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    PACKAGES,
    PATHS,
    Claim,
    StepRecord,
    register_step,
    restore,
    snapshot,
)

#: The suffixes the pair reads.
SUFFIXES = (".py", ".pyi")


def _run(ctx: PhaseContext) -> None:
    del ctx


def records() -> tuple[StepRecord, ...]:
    """A type checker and a test runner over the python packages."""
    return (
        StepRecord(
            "fake",
            "typecheck",
            _run,
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
            _run,
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
        ),
    )


@pytest.fixture(name="python_steps")
def python_steps_fixture() -> Iterator[tuple[StepRecord, ...]]:
    """Register the pair for the test; the registry is put back after it."""
    state = snapshot()
    registered = records()
    for record in registered:
        register_step(record)
    try:
        yield registered
    finally:
        restore(state)
