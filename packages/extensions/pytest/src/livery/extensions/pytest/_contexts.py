"""Name each test's coverage context in a metered pytest run.

Registered through livery-extensions-pytest's ``pytest11`` entry point,
so every pytest this venv starts carries it. It acts only when
coverage's process startup is measuring the process (inside a CI check
leg the test runner arms ``COVERAGE_PROCESS_START`` in pytest's
environment while coverage is listed, and in the gate's own never):
each test records under one context, its node id, from its setup to
its teardown, so a leg can split one pooled run's data by the suite
each test belongs to. Outside a metered run every hook returns at
once, and a meter that is not collecting is left alone.

Under coverage's sysmon core a line is recorded under the first
context that reaches it and never again, so a suite's contexts hold
the lines its tests reached first. The leg's split keeps the union
complete regardless: a line another suite's test reached first is
stored with that suite, whose closure holds the file.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

#: Coverage's own variable: set in pytest's environment by the test
#: runner inside CI, so pytest and every python it starts are metered
#: from interpreter start, and the meter is the one this plugin names
#: contexts on. A pytest-cov run has its own meter and is left alone.
ARMED = "COVERAGE_PROCESS_START"


def _meter() -> Any | None:
    """The coverage object process startup began in this metered run, or None."""
    if not os.environ.get(ARMED):
        return None
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
    """Record the test under its node id, from its setup to its teardown.

    Switched in the process that runs the test, at its setup: the hooks
    that report on a test also run in the process that schedules the
    tests, whose own lines belong to no test.
    """
    _switch(item.nodeid)


def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    """Return to the empty context once the test is over."""
    _switch("")
