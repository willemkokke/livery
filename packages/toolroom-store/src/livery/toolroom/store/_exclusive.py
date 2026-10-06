"""One process at a time through a section: an advisory lock on a file.

The kernel holds the lock for the open file and drops it when the
holder ends, however it ends. So a lock never outlives its holder, and
a waiter needs no stale bound and no timeout: it waits while a live
process works, however long the work takes. POSIX takes ``flock`` on
the whole file; Windows locks the file's first byte with
``msvcrt.locking``. Each is asked without blocking, and asked again
after `POLL` seconds, so the caller can say once that it waits.

The lock is per open file, not per process: two threads that each open
the file exclude each other too.
"""

from __future__ import annotations

import contextlib
import errno
import importlib
import os
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

POLL = 0.05
"""Seconds between two asks while another holder has the lock."""


def _module(name: str) -> Any:
    # Imported by name: the stubs define each platform's module only on
    # that platform, and each function below runs only on its own.
    return importlib.import_module(name)


def take_posix(fd: int, *, fcntl: Any = None) -> bool:
    """Take the lock on *fd* unless another holder has it; True when taken."""
    module = fcntl if fcntl is not None else _module("fcntl")
    try:
        module.flock(fd, module.LOCK_EX | module.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def give_posix(fd: int, *, fcntl: Any = None) -> None:
    """Let go of the lock on *fd*."""
    module = fcntl if fcntl is not None else _module("fcntl")
    module.flock(fd, module.LOCK_UN)


def take_windows(fd: int, *, msvcrt: Any = None) -> bool:
    """Take the lock on *fd*'s first byte unless another holder has it.

    ``msvcrt.locking`` locks from the file's position, so the position
    goes to the start first. The C runtime refuses a byte another
    holder has locked with ``EACCES``; any other error raises.
    """
    module = msvcrt if msvcrt is not None else _module("msvcrt")
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        module.locking(fd, module.LK_NBLCK, 1)
    except OSError as error:
        if error.errno == errno.EACCES:
            return False
        raise
    return True


def give_windows(fd: int, *, msvcrt: Any = None) -> None:
    """Let go of the lock on *fd*'s first byte."""
    module = msvcrt if msvcrt is not None else _module("msvcrt")
    os.lseek(fd, 0, os.SEEK_SET)
    module.locking(fd, module.LK_UNLCK, 1)


_TAKE: Callable[[int], bool] = take_windows if os.name == "nt" else take_posix
_GIVE: Callable[[int], None] = give_windows if os.name == "nt" else give_posix


@contextlib.contextmanager
def exclusive(
    path: Path, *, waiting: Callable[[], None] | None = None
) -> Generator[None]:
    """Hold the lock on *path* while the block runs, waiting while another holds it.

    *path* is created when absent, and stays afterwards: a lock file
    removed while a process waits on it would let a third process take
    a lock on a new file of the same name. *waiting* is called once,
    the first time the lock is found held, so the caller can say what
    it waits for.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        told = False
        while not _TAKE(fd):
            if not told and waiting is not None:
                waiting()
                told = True
            time.sleep(POLL)
        try:
            yield
        finally:
            _GIVE(fd)
    finally:
        os.close(fd)
