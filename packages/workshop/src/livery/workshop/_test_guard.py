"""The workshop's test guard: what an extension mount writes goes back after every test.

Registered under footman's ``footman.test_guards`` entry-point group, so
footman's pytest plugin calls [guard][livery.workshop._test_guard.guard]
once per test process, after it has pointed the runner's directories
away, and enters what it returns around every test. Nothing here
imports pytest: a session in a venv with the workshop loads this module
whatever runs the tests.

What an extension mount writes is put back after every test: the steps
the listed extensions declare, the CI jobs they contribute and join,
the task tree a run keeps, and the mount's record of what it found.
Each of the three modules that hold them saves and restores its own
state ([livery.workshop._steps.snapshot][],
[livery.workshop._points.snapshot][] and
[livery.workshop._extensions.snapshot][]). These registries are the
workshop's own, so footman's registry guard does not hold them.
Without the guard, a step one test mounted would judge the changes of
every later test in the worker. Registries the test loaded itself go
back as a process starts with them, and a session that loads none of
them imports nothing for the guard.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager

#: The workshop modules that hold what an extension mount writes.
MOUNT_MODULES = (
    "livery.workshop._extensions",
    "livery.workshop._points",
    "livery.workshop._steps",
)


def guard(**facts: object) -> Callable[[], AbstractContextManager[None]]:
    """Drop the check leg's label, and hand footman the mount guard.

    The job runner names the leg, its shard included, to every child it
    spawns, and a test that drives the gate in-process would otherwise
    run as that shard and leave every step without the flag to the first
    one. A test that wants a leg sets the variable itself.

    Args:
        **facts: What footman knows about the session: ``data_dir``,
            ``cache_dir`` and ``config_dir``, the runner's directories
            after it pointed them away. The guard reads none of them.

    Returns:
        [mount_state_stays][livery.workshop._test_guard.mount_state_stays],
        which footman enters around every test.
    """
    from livery.workshop._ci_context import LEG_VARIABLE

    del facts
    os.environ.pop(LEG_VARIABLE, None)
    return mount_state_stays


def _mount_loaded() -> bool:
    """Whether this process has loaded any module a mount writes into."""
    return any(name in sys.modules for name in MOUNT_MODULES)


def _mount_reset() -> None:
    """Put the mount's registries back as a process starts with them."""
    from livery.workshop import _extensions, _points, _steps

    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))


@contextlib.contextmanager
def mount_state_stays() -> Generator[None, None, None]:
    """Put back what an extension mount wrote during the test.

    The registries go back as they were before the test. When the test
    loaded them itself, they go back as a process starts with them. A
    session that loads none of them imports nothing here.
    """
    if not _mount_loaded():
        yield
        if _mount_loaded():
            _mount_reset()
        return
    from livery.workshop import _extensions, _points, _steps

    steps, jobs, record = _steps.snapshot(), _points.snapshot(), _extensions.snapshot()
    yield
    _steps.restore(steps)
    _points.restore(jobs)
    _extensions.restore(record)
