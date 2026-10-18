"""Sum each package's test time in a pytest run, for the runner to print.

Registered through livery-workshop's ``pytest11`` entry point, so every
pytest this venv starts carries it. It acts only when the test runner
names a file in [livery.workshop.SPEED_FILE_VARIABLE][],
``WORKSHOP_SPEED_FILE``: every phase of every test adds its duration
to the sum of its package, the one [livery.workshop.package_of][]
names, and the session's end writes the sums as JSON to that file,
from the controlling process alone under xdist. A pytest started
without the variable, the nested runs the workshop's own tests spawn
included, sums nothing and writes nothing.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from livery.workshop import SPEED_FILE_VARIABLE, package_of

_sums: dict[str, dict[str, float]] = defaultdict(lambda: {"seconds": 0.0, "tests": 0})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Add the phase's duration to its package's sum."""
    if not os.environ.get(SPEED_FILE_VARIABLE):
        return
    package = package_of(report.nodeid)
    if not package:
        return
    _sums[package]["seconds"] += report.duration
    if report.when == "call":
        _sums[package]["tests"] += 1


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    """Write the sums to the named file, from the controlling process."""
    target = os.environ.get(SPEED_FILE_VARIABLE)
    if not target or hasattr(session.config, "workerinput"):
        return
    Path(target).write_text(
        json.dumps(
            {
                package: {
                    "seconds": round(data["seconds"], 1),
                    "tests": int(data["tests"]),
                }
                for package, data in sorted(_sums.items())
            }
        ),
        encoding="utf-8",
    )
