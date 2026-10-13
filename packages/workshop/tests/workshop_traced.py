"""One callable run as a task under ``--profile``, and the trace it wrote.

`traced` writes a tasks file whose one task calls the callable a test
hands it, runs that task in-process with the profile plugin mounted,
and reads the trace back. The tasks file imports this module to reach
the callable, so a test passes a closure over its own paths and
values, and every module attribute the test replaced is the one the
task sees.

`environment_kept` puts the process environment back after an
in-process run: a profiled run writes its drop box and its clock
anchor into the environment for its children, and nothing removes
them when the run ends inside a process that goes on.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
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


@contextlib.contextmanager
def environment_kept() -> Iterator[None]:
    """Give the process environment back as it was, whatever the block wrote."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        for name in [name for name in os.environ if name not in saved]:
            del os.environ[name]
        os.environ.update(saved)


def traced(directory: Path, act: Callable[[], object]) -> tuple[str, list[str]]:
    """Run *act* as a task under ``--profile`` in *directory*.

    The task must succeed: a failed run fails the test with its output.
    The process environment is the same afterwards.

    Returns:
        The trace file's text, and the name of every event in it.
    """
    tasks = directory / "tasks.py"
    tasks.write_text(TASKS, encoding="utf-8")
    trace = directory / "fm-profile.json"
    ACTS.append(act)
    try:
        with environment_kept():
            result = Runner().invoke(
                [f"--profile={trace}", "act"], tasks=tasks, cwd=directory
            )
    finally:
        ACTS.pop()
    assert result.ok, result.stdout + result.stderr
    text = trace.read_text(encoding="utf-8")
    events: list[dict[str, object]] = json.loads(text)["traceEvents"]
    return text, [str(event.get("name", "")) for event in events]
