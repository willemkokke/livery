"""The materialiser's rungs: how one object becomes one path.

Cheapest first: clone (copy-on-write), hardlink, link, copy. Each rung
is one function with the same shape, and the platform-specific ones
sit behind seams so a test forces every fallback without the
filesystem's cooperation. The reference rung is
[livery.strongroom.Store.path][]: a path into the tier itself, with
nothing created.

Which rung a view reaches on a platform is not conformance material;
the rules that decide eligibility are, and they live in `_views`.

`remove` and `remove_tree` are the rungs' inverse: they take a path a
rung made back off the disk whatever mode the rung left on it.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

Rung = Literal["reference", "clone", "hardlink", "link", "copy"]
"""The ladder, cheapest first."""

MadeRung = Literal["clone", "hardlink", "link", "copy"]
"""The rungs that create a path inside a view: every rung but reference."""

RUNGS: tuple[Rung, ...] = ("reference", "clone", "hardlink", "link", "copy")

# Linux: FICLONE is _IOW(0x94, 9, int), which is 0x40049409 on every
# architecture Python supports.
_FICLONE = 0x40049409


class RungUnavailable(OSError):
    """The rung cannot produce this path here; try the next one."""


def clone_darwin(source: Path, destination: Path) -> None:
    """APFS copy-on-write through libc's clonefile."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    result = libc.clonefile(
        os.fsencode(source), os.fsencode(destination), ctypes.c_int(0)
    )
    if result != 0:
        errno = ctypes.get_errno()
        raise RungUnavailable(errno, f"clonefile: {os.strerror(errno)}")


def clone_linux(source: Path, destination: Path) -> None:
    """Copy-on-write through the FICLONE ioctl: btrfs, XFS, bcachefs."""
    import fcntl

    # Looked up by name: the stubs define ioctl only off Windows, and
    # this function is never called there.
    ioctl: Callable[[int, int, int], int] = getattr(fcntl, "ioctl")  # noqa: B009
    with source.open("rb") as src, destination.open("wb") as dst:
        try:
            ioctl(dst.fileno(), _FICLONE, src.fileno())
        except OSError as error:
            dst.close()
            destination.unlink(missing_ok=True)
            raise RungUnavailable(error.errno, f"FICLONE: {error.strerror}") from None


def clone_unavailable(source: Path, destination: Path) -> None:
    """No copy-on-write here: ReFS block cloning is not wired, nothing else has it."""
    raise RungUnavailable(0, f"no clone rung on {sys.platform}")


CLONE: Callable[[Path, Path], None] = {
    "darwin": clone_darwin,
    "linux": clone_linux,
}.get(sys.platform, clone_unavailable)
"""This platform's clone, chosen once at import."""


def hardlink(source: Path, destination: Path) -> None:
    """A second name for the object's inode; the mode is shared."""
    try:
        os.link(source, destination)
    except OSError as error:
        raise RungUnavailable(error.errno, f"link: {error.strerror}") from None


def _symlink(target: str | Path, destination: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(target, destination, target_is_directory=directory)
    except OSError as error:
        raise RungUnavailable(error.errno, f"symlink: {error.strerror}") from None


Symlink = Callable[..., None]
"""The shape of the symlink seam: target, destination, and `directory` by keyword."""

symlink: Symlink = _symlink
"""A symlink to the target, relative when given so; a junction's place on Windows.

A variable, not a function, so the conformance harness can stand a
refusing platform in its place and put it back.
"""


def copy(source: Path, destination: Path) -> None:
    """The last rung: bytes copied, always available."""
    shutil.copyfile(source, destination)


def _unlink(path: Path) -> None:
    # The Windows seam: unlinking a file whose read-only attribute is
    # set is refused there, and a view marks every clone, copy, and
    # hardlink read-only. Tests fake this function to force the retry.
    os.unlink(path)


def _rmtree(path: Path) -> None:
    # The same seam for a copied directory: one read-only file inside
    # refuses the whole removal on Windows.
    shutil.rmtree(path)


def _writable(path: Path) -> None:
    # Adding the owner's write bit clears Windows' read-only attribute
    # and changes nothing else; a bare S_IWRITE would strip a POSIX
    # directory's read and search bits and make it untraversable. A
    # symlink is never marked and may dangle, so it is not followed.
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        return
    os.chmod(path, mode | stat.S_IWRITE)


def remove(path: Path) -> None:
    """Remove a file or link a rung made, whatever its mode; a missing path is done.

    A refusal on a read-only file clears the read-only attribute and
    unlinks again. A hardlink shares that attribute with the store's
    object, which is left writable afterwards, the state the store
    keeps its objects in.
    """
    try:
        _unlink(path)
    except FileNotFoundError:
        return
    except PermissionError:
        _writable(path)
        _unlink(path)


def remove_tree(path: Path) -> None:
    """Remove a directory a rung copied, whatever the modes inside it.

    A refusal makes every directory and file under *path* writable
    and removes again.
    """
    try:
        _rmtree(path)
    except PermissionError:
        for parent, directories, files in os.walk(path):
            for name in [*directories, *files]:
                _writable(Path(parent) / name)
        _writable(path)
        _rmtree(path)
