"""A python test runner for the coverage tests that ask which suites a leg runs.

The base registers no test runner: a listed extension does (pytest, in
this repository). A leg measures the units whose suites a registered
step of the test phase runs, so `python_steps` registers one with
pytest's shape under the extension ``fake`` and puts the registry back
after the test; a test module imports `python_steps_fixture` and names
``python_steps`` as a parameter.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    PACKAGES,
    Claim,
    StepRecord,
    register_step,
    restore,
    snapshot,
)


def _test_run(ctx: PhaseContext) -> None:
    del ctx


def records() -> tuple[StepRecord, ...]:
    """A test runner over the python packages, as pytest's step is shaped."""
    return (
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
                Claim("source", suffixes=(".py", ".pyi")),
            ),
        ),
    )


@pytest.fixture(name="python_steps")
def python_steps_fixture() -> Iterator[tuple[StepRecord, ...]]:
    """Register the runner for the test; the registry is put back after it."""
    state = snapshot()
    registered = records()
    for record in registered:
        register_step(record)
    try:
        yield registered
    finally:
        restore(state)
