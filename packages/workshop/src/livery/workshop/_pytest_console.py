"""Name a child that ended on a console control event, and who shared the console.

Registered through livery-workshop's ``pytest11`` entry point, so every
pytest this venv starts carries it. A process on Windows that receives
a console control event, Ctrl+C or Ctrl+Break or the console closing,
exits with ``STATUS_CONTROL_C_EXIT`` (``0xC000013A``, 3221225786 as
Python reads it). A test whose git child ends that way fails with the
bare number, and one sighting in a run of thousands of tests leaves
nothing to bisect. When a failed test's exception carries that status,
this plugin adds a section to the failure report: the command, the
process and its worker, the run, every control event this process's
own handler saw in the session with the time it arrived, and every
process attached to the console at the moment of the report, by id
and image name. On another platform the section says so and carries
what it can.

The handler this plugin installs records events and passes them on: it
returns false to the console, so the interpreter's own handling is
untouched.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import pytest

STATUS_CONTROL_C_EXIT = 0xC000013A
"""The NTSTATUS a process exits with after a console control event."""

_SIGNED = STATUS_CONTROL_C_EXIT - 2**32

_EVENTS: list[tuple[float, int]] = []
"""The control events this process's handler saw: wall-clock time and type."""

_HANDLER: Any = None
"""The installed handler, kept so the console keeps a live callback."""

_EVENT_NAMES = {
    0: "CTRL_C_EVENT",
    1: "CTRL_BREAK_EVENT",
    2: "CTRL_CLOSE_EVENT",
    5: "CTRL_LOGOFF_EVENT",
    6: "CTRL_SHUTDOWN_EVENT",
}


def control_exit(code: object) -> bool:
    """Whether *code* is ``STATUS_CONTROL_C_EXIT``, as Python or the shell spells it."""
    return code in (STATUS_CONTROL_C_EXIT, _SIGNED)


def exit_codes(error: BaseException | None) -> list[int]:
    """Every exit code a failure carries: a called process's, down its cause chain."""
    codes: list[int] = []
    seen = 0
    current = error
    while current is not None and seen < 10:
        code = getattr(current, "returncode", None)
        if isinstance(code, int) and not isinstance(code, bool):
            codes.append(code)
        text = str(current)
        if str(STATUS_CONTROL_C_EXIT) in text and STATUS_CONTROL_C_EXIT not in codes:
            codes.append(STATUS_CONTROL_C_EXIT)
        current = current.__cause__ or current.__context__
        seen += 1
    return codes


def pytest_configure(config: pytest.Config) -> None:
    """Install the recording handler on Windows; elsewhere there is no console."""
    del config
    if sys.platform != "win32":
        return
    _install_handler()


def _install_handler() -> None:
    global _HANDLER
    if sys.platform != "win32" or _HANDLER is not None:
        return
    import ctypes

    kind = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def seen(event: int) -> bool:
        _EVENTS.append((time.time(), event))
        return False  # recorded, never consumed

    handler = kind(seen)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    _HANDLER = handler


def console_processes() -> list[tuple[int, str]] | None:
    """The processes attached to this console, by id and image; None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    room = 64
    while True:
        ids = (ctypes.c_uint * room)()
        count = kernel32.GetConsoleProcessList(ids, room)
        if count == 0:
            return []
        if count <= room:
            break
        room = count
    found: list[tuple[int, str]] = []
    for pid in list(ids)[:count]:
        found.append((int(pid), _image_name(int(pid))))
    return found


def _image_name(pid: int) -> str:
    """The executable behind *pid*, or a word for why it cannot be read."""
    if sys.platform != "win32":
        return "unreadable"
    import ctypes

    kernel32 = ctypes.windll.kernel32
    query_limited = 0x1000
    handle = kernel32.OpenProcess(query_limited, False, pid)
    if not handle:
        return "unreadable"
    try:
        size = ctypes.c_uint(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return "unnamed"
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def diagnosis(error: BaseException | None) -> str:
    """The section's text for a failure that carries the control-exit status."""
    lines = [
        "a child ended with STATUS_CONTROL_C_EXIT (0xC000013A, 3221225786): a"
        " console control event reached it; the exit is not the child's own",
    ]
    command = getattr(error, "cmd", None)
    if command is not None:
        lines.append(f"command: {command!r}")
    lines.append(f"cwd: {os.getcwd()}")
    lines.append(
        f"this process: pid {os.getpid()}, parent {os.getppid()}, worker"
        f" {os.environ.get('PYTEST_XDIST_WORKER', 'none')}"
    )
    run = {
        key: os.environ[key]
        for key in ("GITHUB_RUN_ID", "GITHUB_JOB", "RUNNER_NAME", "RUNNER_OS")
        if os.environ.get(key)
    }
    lines.append(f"run: {run or 'not a CI run'}")
    if _EVENTS:
        lines.append("control events this process saw:")
        lines.extend(
            f"  {time.strftime('%H:%M:%S', time.gmtime(when))}Z"
            f" {_EVENT_NAMES.get(kind, str(kind))}"
            for when, kind in _EVENTS
        )
    else:
        lines.append("control events this process saw: none")
    attached = console_processes()
    if attached is None:
        lines.append("console processes: not a Windows console")
    elif not attached:
        lines.append("console processes: none, this process has no console")
    else:
        lines.append("console processes:")
        lines.extend(f"  {pid} {name}" for pid, name in attached)
    lines.append(f"reported at: {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}Z")
    return "\n".join(lines)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    """Add the section to a failed report whose exception carries the status."""
    del item
    outcome = yield
    report = outcome.get_result()
    if not report.failed or call.excinfo is None:
        return
    error = call.excinfo.value
    if not any(control_exit(code) for code in exit_codes(error)):
        return
    report.sections.append(("console control event", diagnosis(error)))
