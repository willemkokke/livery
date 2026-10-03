"""Link wheel-shipped content into a checkout, the way the fragment engine does it.

The fragment engine delivers skills and hooks as links into the listed
extensions' shipped content ([livery.workshop._fragment_engine][]) and
calls these for the mechanism. Links are the zero-drift way (an
extension upgrade moves every checkout at once, nothing to re-copy) but
they are an optimisation, not the contract: where a link cannot be made,
the same content is copied and refreshed on every sync.

Link mechanism, in order of preference: a relative ``os.symlink`` (a
relative target survives the repository moving; in the monorepo the
target sits inside the repository, so containment holds by
construction); a Windows directory junction, which needs no privilege
where symlinks do; a copy, whose digest the engine records so the next
sync tells a stale copy of ours from a local edit.

**Local content always wins.** A real entry whose content differs from
the shipped one is a deliberate override: it is kept and named, and the
engine's self-scoped ``.gitignore`` leaves it out, so the override
commits like any repository file.

**Editing through a link writes into the wheel.** In the monorepo that
is correct: the target is the source tree. In an instance it edits
site-packages, machine-wide and lost on the next upgrade; the escape is
to copy the entry to a real directory of the same name, which then
takes precedence.
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path


def _is_link(path: Path) -> bool:
    """Whether *path* is a symlink or a Windows reparse point (junction)."""
    if path.is_symlink():
        return True
    try:
        tag = getattr(path.lstat(), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag)


def _points_at(link: Path, target: Path) -> bool:
    """Whether *link* already resolves to *target* (False when dangling)."""
    try:
        return os.path.samestat(link.stat(), target.stat())
    except OSError:
        return False


def _relative_target(link: Path, target: Path) -> str:
    """*target* relative to *link*'s directory, or absolute if impossible.

    A relative target is what makes the link survive the repository
    moving; a cross-volume layout falls back to the absolute path.
    """
    try:
        return os.path.relpath(target, link.parent)
    except ValueError:  # different drives on Windows
        return str(target)


def _make_link(link: Path, target: Path) -> str:
    """Materialise *link* to *target*: "linked", "junction", or "copied"."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(
            _relative_target(link, target), link, target_is_directory=target.is_dir()
        )
    except (OSError, NotImplementedError):
        pass
    else:
        return "linked"
    if sys.platform == "win32" and target.is_dir():
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError, AttributeError):
            pass
        else:
            return "junction"
    if target.is_dir():
        shutil.copytree(target, link)
    else:
        shutil.copy2(target, link)
    return "copied"


def _remove(path: Path) -> None:
    """Remove a link, a plain file, or a directory we own."""
    if _is_link(path):
        try:
            path.unlink()
        except OSError:
            os.rmdir(path)  # a junction reads as a directory to some APIs
        return
    if not path.is_dir():
        path.unlink()
        return

    def _force(func: Callable[[str], object], target: str, _exc: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=_force)


def _same_content(local: Path, shipped: Path) -> bool:
    """Whether a real entry matches the shipped copy byte for byte.

    The one question that separates a stale committed copy (safe to
    replace with a link) from a deliberate local override (keep, and
    say so).
    """
    if not local.is_dir() or not shipped.is_dir():
        return (
            local.is_file()
            and shipped.is_file()
            and filecmp.cmp(local, shipped, shallow=False)
        )
    comparison = filecmp.dircmp(str(local), str(shipped))
    # common_funny holds names present on both sides with different
    # types; those appear in neither common_files nor common_dirs, so
    # omitting the check would call the trees identical and delete the
    # local one.
    if (
        comparison.left_only
        or comparison.right_only
        or comparison.funny_files
        or comparison.common_funny
    ):
        return False
    mismatch, errors = filecmp.cmpfiles(
        str(local), str(shipped), comparison.common_files, shallow=False
    )[1:]
    if mismatch or errors:
        return False
    return all(
        _same_content(local / sub, shipped / sub) for sub in comparison.common_dirs
    )


def _digest(target: Path) -> str:
    """A stable digest of a file's bytes or a directory's whole content."""
    digest = hashlib.sha256()
    if target.is_file():
        digest.update(target.read_bytes())
        return digest.hexdigest()
    for child in sorted(target.rglob("*")):
        if child.is_file():
            digest.update(str(child.relative_to(target)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def write_lf(path: Path, text: str) -> None:
    r"""Write *text* with LF endings on every platform.

    ``Path.write_text`` translates ``\n`` to the platform separator;
    generated files are written on one machine and linted on another,
    so their bytes cannot depend on which one wrote them.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def _entry(
    link: Path,
    target: Path,
    overrides: list[str],
    reclaimed: list[str],
    *,
    copied_digest: str | None,
) -> tuple[bool, str]:
    """Reconcile one materialised entry. Never raises.

    Returns ``(ours, mode)``: whether the entry is ours afterwards (a
    link or a copy we own) and which mechanism this call used (empty
    when nothing changed). ``ours=False`` means a local override is in
    place, which keeps its name out of the managed ``.gitignore`` so
    the developer can commit it.
    """
    if not link.exists() and not _is_link(link):
        return True, _make_link(link, target)
    if _is_link(link):
        if _points_at(link, target):
            return True, ""
        _remove(link)  # wrong or dangling target: re-point it
        return True, _make_link(link, target)
    if copied_digest is not None:
        if _same_content(link, target):
            return True, ""  # our copy, current: nothing to do or say
        if copied_digest and _digest(link) != copied_digest:
            # Changed since we copied it: a local edit, not staleness.
            # The override is kept and named, exactly as on a link
            # platform where a replaced link means the same thing.
            overrides.append(link.name)
            return False, ""
        # Stale (or legacy-owned without a digest): ours to refresh.
        _remove(link)
        return True, _make_link(link, target)
    if link.is_dir() and not any(link.iterdir()):
        link.rmdir()
        return True, _make_link(link, target)
    if _same_content(link, target):
        _remove(link)  # a committed copy identical to ours: reclaim it
        reclaimed.append(link.name)
        return True, _make_link(link, target)
    overrides.append(link.name)
    return False, ""
