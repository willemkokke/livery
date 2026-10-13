"""One callable run as a task under ``--profile``, and the trace it wrote.

`traced` writes a tasks file whose one task calls the callable a test
hands it, runs that task in-process with the profile plugin mounted,
and reads the trace back. The tasks file imports this module to reach
the callable, so a test passes a closure over its own paths and
values, and every module attribute the test replaced is the one the
task sees.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from livery.footman.testing import Runner

#: The callables of the `traced` calls under way, the innermost last.
ACTS: list[Callable[[], object]] = []

TASKS = """\
from livery.footman import task
from livery.footman._compose import plugin

import workshop_traced

plugin("footman.profile")


@task
def act():
    workshop_traced.ACTS[-1]()
"""


def traced(directory: Path, act: Callable[[], object]) -> tuple[str, list[str]]:
    """Run *act* as a task under ``--profile`` in *directory*.

    The task must succeed: a failed run fails the test with its output.

    Returns:
        The trace file's text, and the name of every event in it.
    """
    tasks = directory / "tasks.py"
    tasks.write_text(TASKS, encoding="utf-8")
    trace = directory / "fm-profile.json"
    ACTS.append(act)
    try:
        result = Runner().invoke(
            [f"--profile={trace}", "act"], tasks=tasks, cwd=directory
        )
    finally:
        ACTS.pop()
    assert result.ok, result.stdout + result.stderr
    text = trace.read_text(encoding="utf-8")
    events: list[dict[str, object]] = json.loads(text)["traceEvents"]
    return text, [str(event.get("name", "")) for event in events]
