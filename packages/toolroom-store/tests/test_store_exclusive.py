"""The supply lock: a dead holder leaves it free, a live one makes the next wait."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from livery.toolroom.store import _exclusive

#: A process that takes the lock on argv[1], says so, and holds it
#: until its stdin closes or it is killed.
HOLD = """
import sys
from pathlib import Path
from livery.toolroom.store._exclusive import exclusive
with exclusive(Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.readline()
"""


def _until(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


def _taken(lock: Path) -> bool:
    """Whether this process can take *lock* now; it lets go at once."""
    fd = os.open(lock, os.O_RDWR)
    try:
        if not _exclusive._TAKE(fd):  # pyright: ignore[reportPrivateUsage]
            return False
        _exclusive._GIVE(fd)  # pyright: ignore[reportPrivateUsage]
        return True
    finally:
        os.close(fd)


# The fallback first: a holder that dies takes its lock with it.


def test_a_holder_that_dies_leaves_the_lock_free(tmp_path: Path) -> None:
    lock = tmp_path / "locks" / "tool@1.0.0.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        assert not _taken(lock)
        child.kill()
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)
    # The kernel lets go with the holder, at once on POSIX and soon on
    # Windows, which releases a dead process's locks when it can.
    _until(lambda: _taken(lock))
    # The file stays: a waiter on it must never lose it to a new one.
    assert lock.exists()


def test_a_live_holder_makes_the_next_wait_and_say_so_once(tmp_path: Path) -> None:
    lock = tmp_path / "tool@1.0.0.lock"
    said: list[str] = []
    order: list[str] = []

    def second() -> None:
        with _exclusive.exclusive(lock, waiting=lambda: said.append("waiting")):
            order.append("second")

    with _exclusive.exclusive(lock):
        waiter = threading.Thread(target=second)
        waiter.start()
        _until(lambda: said == ["waiting"])
        # Several asks later, it has still said so once.
        time.sleep(4 * _exclusive.POLL)
        order.append("first")
    waiter.join(10)
    assert said == ["waiting"]
    assert order == ["first", "second"]
    assert _taken(lock)


def test_a_free_lock_is_taken_without_a_word(tmp_path: Path) -> None:
    lock = tmp_path / "deep" / "tool@1.0.0.lock"
    said: list[str] = []
    with _exclusive.exclusive(lock, waiting=lambda: said.append("waiting")):
        assert not _taken(lock)
    assert said == []
    assert _taken(lock)


# Each platform's calls, through a stand-in for its module, wherever the
# tests run.


class _Fcntl:
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self, *, held: bool) -> None:
        self.held = held
        self.calls: list[int] = []

    def flock(self, fd: int, operation: int) -> None:
        del fd
        self.calls.append(operation)
        if self.held and operation & self.LOCK_NB:
            raise BlockingIOError(errno.EWOULDBLOCK, "held")


class _Msvcrt:
    LK_UNLCK = 0
    LK_NBLCK = 2

    def __init__(self, *, refusal: int = 0) -> None:
        self.refusal = refusal
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((os.lseek(fd, 0, os.SEEK_CUR), mode, nbytes))
        if self.refusal:
            raise OSError(self.refusal, os.strerror(self.refusal))


@pytest.fixture
def fd(tmp_path: Path) -> Iterator[int]:
    """An open file whose position is past its start, closed after the test."""
    descriptor = os.open(tmp_path / "x.lock", os.O_RDWR | os.O_CREAT)
    os.write(descriptor, b"0123456789")
    yield descriptor
    os.close(descriptor)


def test_posix_takes_flock_without_blocking_and_reads_a_holder_as_a_refusal(
    fd: int,
) -> None:
    held = _Fcntl(held=True)
    assert _exclusive.take_posix(fd, fcntl=held) is False
    free = _Fcntl(held=False)
    assert _exclusive.take_posix(fd, fcntl=free) is True
    _exclusive.give_posix(fd, fcntl=free)
    assert free.calls == [_Fcntl.LOCK_EX | _Fcntl.LOCK_NB, _Fcntl.LOCK_UN]


def test_windows_locks_the_first_byte_and_raises_what_is_no_refusal(
    fd: int,
) -> None:
    held = _Msvcrt(refusal=errno.EACCES)
    assert _exclusive.take_windows(fd, msvcrt=held) is False
    broken = _Msvcrt(refusal=errno.EBADF)
    with pytest.raises(OSError, match=os.strerror(errno.EBADF)):
        _exclusive.take_windows(fd, msvcrt=broken)
    free = _Msvcrt()
    os.lseek(fd, 5, os.SEEK_SET)
    assert _exclusive.take_windows(fd, msvcrt=free) is True
    os.lseek(fd, 5, os.SEEK_SET)
    _exclusive.give_windows(fd, msvcrt=free)
    # Both at the first byte, wherever the file's position was.
    assert free.calls == [(0, _Msvcrt.LK_NBLCK, 1), (0, _Msvcrt.LK_UNLCK, 1)]


def test_this_platform_s_calls_are_chosen_at_import() -> None:
    windows = os.name == "nt"
    take = _exclusive._TAKE  # pyright: ignore[reportPrivateUsage]
    give = _exclusive._GIVE  # pyright: ignore[reportPrivateUsage]
    assert take is (_exclusive.take_windows if windows else _exclusive.take_posix)
    assert give is (_exclusive.give_windows if windows else _exclusive.give_posix)
