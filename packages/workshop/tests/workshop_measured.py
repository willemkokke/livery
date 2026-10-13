"""A module whose branches each run one way, measured by coverage.py for real.

The coverage pins share it: the workshop's figure for it must equal
coverage.py's own total, and its pages must show what coverage.py's
pages show. Its lines, by number:

- line 6, `if value:` in `Tally.note`, is always true: partial, and
  its jump to the function's exit never ran;
- line 12, a `for` over an empty list, never starts: partial;
- line 14, an `if` whose condition is never true: partial;
- lines 19 and 20, a `# pragma: no cover` block: excluded;
- line 26, an `if` always true whose untaken jump `# pragma: no branch`
  waives: run, the waived jump counted covered;
- lines 27 to 29, one statement over three lines.

Each of these holds alike on Python 3.11 to 3.14, and an edit to the
fixture must keep it so: the nightly runs the pins on 3.11 as well.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FIXTURE = """\
class Tally:
    def __init__(self):
        self.seen = []

    def note(self, value):
        if value:
            self.seen.append(value)


def total(values):
    result = 0
    for value in values:
        result += value
    if result > 100:
        result = 100
    return result


def unused():  # pragma: no cover
    return "never called"


tally = Tally()
tally.note(1)
size = total([])
if size == 0:  # pragma: no branch
    label = "-".join(
        ["no", "values"]
    )
"""


def measure(module: Path, root: Path) -> None:
    """Write the fixture at *module* and run it under coverage.py's branch meter.

    The meter measures *module*'s directory, and its data lands in
    *root*'s ``.coverage``. The run is a fresh interpreter whose
    environment has none of the outer gate's meter variables:
    pytest-cov and coverage's subprocess patch would point an
    inheriting python at the live run's data file, and this recording
    must land in *root*.
    """
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(FIXTURE)
    unmetered = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source",
            str(module.parent),
            str(module),
        ],
        cwd=root,
        capture_output=True,
        check=True,
        env=unmetered,
        timeout=60,
    )
