"""Name each test's coverage context in a metered pytest run.

Registered through livery-workshop's ``pytest11`` entry point, so every
pytest this venv starts carries it. It acts only when coverage's
process startup is measuring the process (a CI check leg arms
``COVERAGE_PROCESS_START``): each phase of a test records under a
context made of the test's node id and the phase, the shape pytest-cov
uses, so a leg can split one pooled run's data by the suite each test
belongs to. Outside a metered run every hook returns at once, and a
meter that is not collecting is left alone.
"""

from __future__ import annotations

from typing import Any

import pytest


def _meter() -> Any | None:
    """The coverage object process startup began in this process, or None."""
    try:
        import coverage
    except ImportError:
        return None
    return getattr(coverage.process_startup, "coverage", None)


def _switch(context: str) -> None:
    meter = _meter()
    if meter is None:
        return
    from coverage.exceptions import CoverageException

    try:
        meter.switch_context(context)
    except CoverageException:
        return


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Record the test's setup under its own context."""
    _switch(f"{item.nodeid}|setup")


def pytest_runtest_call(item: pytest.Item) -> None:
    """Record the test's body under its own context."""
    _switch(f"{item.nodeid}|run")


def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Record the test's teardown under its own context."""
    _switch(f"{item.nodeid}|teardown")


def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    """Return to the empty context once the test is over."""
    _switch("")
