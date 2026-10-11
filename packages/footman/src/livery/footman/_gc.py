"""Cache garbage collection — the detached child a run spawns at most daily.

The cache is derived state top to bottom (completion manifests rebuild on
any execution-path run; timing history regrows), which is what makes
collecting it casually safe: the worst possible outcome of any deletion is
a rebuild. Four rules, in order:

1. **The directory is gone.** Manifests bake in the ``cwd`` they describe;
   if that path no longer exists, the pair (manifest + timing history) is
   leftovers from a deleted project — collected at any age. Only a file
   footman recognises as its own manifest is read this way; a task author is
   invited to keep state in ``cache_dir()``, and one of those files with a
   ``cwd`` key of its own is nobody's leftovers.
2. **The pair is idle.** Untouched for `IDLE_DAYS`, nobody even TAB-completes
   there any more (background refreshes keep a visited manifest's mtime
   fresh) — collected. Manifests from before the ``cwd`` key, and anything
   in the directory footman did not write, rely on this rule alone.
3. **The fetch room lives by reference, and only its clocks by age.**
   ``fetch/`` holds one small manifest per URL naming a content-addressed
   data file. The manifest is the clock: a cache serve touches it
   (`_fetch._touch`), so an idle one means nothing asked in `IDLE_DAYS` and
   it goes. A live manifest pins the data file it names at any age; a data
   file no manifest names is unreachable — fetch resolves only through
   manifests — so it is garbage immediately, however young. ``.part``
   files (downloads in flight, referenced by nothing yet) keep the one
   remaining clock: a killed process's leftover ages out on `PART_DAYS`,
   while a live download keeps its own mtime fresh by writing. The old
   ``<key>.bin`` + ``.meta.json`` pairs age exactly as they always did.
4. **A profile box belongs to one run.** ``profiles/<box>/`` is where a
   profiled run's children drop trace fragments, and that run's own writer
   removes it at the end. One still there after `BOX_DAYS` outlived the
   process that opened it, and an exec or a kill leaves no exit handler to
   notice that.
5. **A scratch directory belongs to one process.** ``scratch/<label>-<pid>-
   <random>/`` is what `footman.scratch_dir()` hands a task for one
   process's scratch, and that process removes it when its work ends. The
   owner's pid is in the name: one whose owner is gone was left by a kill
   or an exec, and goes whole at any age. A name carrying no pid is not
   footman's to judge and stays.

The invoking directory's own pair is never touched, and every failure is
silent — a concurrently-reading completion child on Windows may hold a file
open, and a collector must never be louder than what it collects.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

IDLE_DAYS = 90
PART_DAYS = 1
BOX_DAYS = 1
STAMP = "gc.stamp"
#: The cache folder `footman.scratch_dir()` fills, one directory per call.
SCRATCH = "scratch"


def collect(cache_dir: Path, skip_stem: str = "") -> int:
    """Apply the rules to *cache_dir*; returns the number of files removed.

    *skip_stem* is the invoking directory's manifest stem (its hash) — that
    pair is in active use and never touched.
    """
    now = time.time()
    removed = 0

    def unlink(path: Path) -> None:
        nonlocal removed
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass

    for manifest_path in cache_dir.glob("*.json"):
        name = manifest_path.name
        if name.endswith(".times.json"):
            continue  # handled with its manifest, or as an orphan below
        stem = name[: -len(".json")]
        if stem == skip_stem:
            continue
        times_path = cache_dir / f"{stem}.times.json"
        try:
            data = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            data = None
        cwd = _baked_cwd(data)
        if cwd and not Path(cwd).exists():
            doomed = True  # rule 1: leftovers of a deleted project
        else:
            doomed = _idle(now, manifest_path, times_path)  # rule 2
        if doomed:
            unlink(manifest_path)
            unlink(times_path)

    # Timing files whose manifest twin is already gone: age them alone.
    for times_path in cache_dir.glob("*.times.json"):
        stem = times_path.name[: -len(".times.json")]
        if stem == skip_stem or (cache_dir / f"{stem}.json").exists():
            continue
        if _idle(now, times_path):
            unlink(times_path)

    # Rule 3, the fetch room. The manifest is the clock and the reference:
    # an idle manifest goes (nobody asked in IDLE_DAYS — a serve touches
    # it), and a live one pins the data file it names. Completed data needs
    # no clock of its own — a data file no manifest names is unreachable
    # (fetch resolves only through manifests), so it is garbage the moment
    # it is orphaned, however young. Only a download in flight keeps a
    # clock, because nothing references it *yet*.
    fetch_room = cache_dir / "fetch"
    referenced: set[str] = set()
    for manifest in fetch_room.glob("*.json"):
        if manifest.name.endswith(".meta.json"):
            continue  # the old layout's sidecar, aged with its pair below
        try:
            data = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError):
            data = None
        data_name = data.get("data") if isinstance(data, dict) else None
        if _idle(now, manifest):
            unlink(manifest)
        elif isinstance(data_name, str):
            referenced.add(data_name)
    for body in fetch_room.glob("*.bin"):
        if "-" in body.stem:
            # New layout: content-addressed, reachable only via a manifest.
            if body.name not in referenced:
                unlink(body)
            continue
        # Old layout (`<key>.bin` + sidecar): the age rule it always had.
        sidecar = fetch_room / f"{body.stem}.meta.json"
        if _idle(now, body, sidecar):
            unlink(body)
            unlink(sidecar)
    for sidecar in fetch_room.glob("*.meta.json"):
        stem = sidecar.name[: -len(".meta.json")]
        if (fetch_room / f"{stem}.bin").exists():
            continue
        if _idle(now, sidecar):
            unlink(sidecar)
    # A download in flight keeps its own mtime fresh; one that stopped
    # writing a day ago belongs to a process that is not coming back.
    for part in fetch_room.glob("*.part"):
        if _idle(now, part, days=PART_DAYS):
            unlink(part)

    # Rule 4, the profile boxes. One directory per profiled run, where its
    # children drop trace fragments; the writer consumes it at the end of
    # the run. A run that was killed, or replaced by an exec that then
    # failed, leaves one behind, and no exit handler runs to take it away.
    # The directory is the clock: a child dropping a fragment in keeps it
    # fresh, so a day of silence means the run it belonged to is gone.
    for box in (cache_dir / "profiles").glob("*"):
        if not box.is_dir() or not _idle(now, box, days=BOX_DAYS):
            continue
        for fragment in box.glob("*"):
            unlink(fragment)
        with contextlib.suppress(OSError):
            box.rmdir()

    # Rule 5, the scratch directories. Each is named for its owner's pid,
    # and the owner removes it when its work ends; one whose owner is
    # gone was left by a kill or an exec, and no handler is coming for
    # it. The whole tree counts as one removal.
    for scratch in (cache_dir / SCRATCH).glob("*"):
        pid = owner(scratch.name)
        if pid is None or not scratch.is_dir() or alive(pid):
            continue
        shutil.rmtree(scratch, ignore_errors=True)
        if not scratch.exists():
            removed += 1

    return removed


def owner(name: str) -> int | None:
    """The pid in a scratch directory's name, ``<label>-<pid>-<random>``, or None.

    The label may carry hyphens of its own; the pid and the random part
    never do, so the name splits from its end. A name of any other shape
    names nobody, and the collector leaves it alone.
    """
    parts = name.rsplit("-", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    return int(parts[1])


def alive(pid: int) -> bool:
    """Whether a process with *pid* exists.

    The one probe for every pid a task or a plugin writes down: a daemon's
    pid file, a scratch directory's owner, a lock's holder. A pid of zero
    or less is nobody's. On POSIX a signal of zero asks without sending,
    and a process that refuses the asking exists. On Windows the kernel is
    asked for a handle, since ``os.kill`` there ends the process for any
    signal it does not special-case, and a handle whose exit code is set
    is a process that ended and is not yet reaped.

    ```python
    if not footman.alive(int(pid_file.read_text())):
        pid_file.unlink()
    ```
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_STILL_ACTIVE = 259


def _kernel32() -> Any:
    # Windows only: elsewhere ctypes has no WinDLL and this raises. Looked
    # up by name, because the stubs define it on Windows alone and the type
    # checkers read this file everywhere.
    import ctypes

    return getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009


def _last_error() -> int:
    import ctypes

    return int(getattr(ctypes, "get_last_error")())  # noqa: B009


def _alive_windows(
    pid: int, *, kernel32: Any = None, last_error: Callable[[], int] = _last_error
) -> bool:
    # A handle refused for access is a process that exists and is not
    # ours to query; any other refusal is no such process. A handle whose
    # exit code is not still-active is a process that exited and is not
    # yet reaped. The kernel and the error reader are parameters so every
    # platform's tests reach the four answers.
    import ctypes

    dll = kernel32 if kernel32 is not None else _kernel32()
    handle = dll.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not dll.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        dll.CloseHandle(handle)


def _baked_cwd(data: object) -> str:
    """The directory *data* describes, or `""` if it is not one of ours.

    Rule 1 reads meaning out of a file and no age protects what it condemns,
    so it may only speak for a file footman wrote. A task is invited to keep
    its own state in `cache_dir()`; one of those that happens to carry a `cwd`
    key of its own is judged by age alone, like an unparseable file. `tree`
    and `hash` have been in every manifest since the first schema.
    """
    if not isinstance(data, dict) or "tree" not in data or "hash" not in data:
        return ""
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) else ""


def _idle(now: float, *paths: Path, days: float = IDLE_DAYS) -> bool:
    """Whether the newest of *paths* is older than the idle window."""
    newest = 0.0
    for path in paths:
        with contextlib.suppress(OSError):
            newest = max(newest, path.stat().st_mtime)
    return newest > 0 and (now - newest) > days * 86400


def main() -> None:
    """Entry for the detached child: argv is (cache_dir, skip_stem).

    After its own collect the child runs every registered sweeper
    (`livery.footman._tasks.janitor`), as silently as
    itself: a sweeper's failure is the sweeper's, never the child's.
    """
    if len(sys.argv) < 2:
        return
    skip = sys.argv[2] if len(sys.argv) > 2 else ""
    collect(Path(sys.argv[1]), skip)
    with contextlib.suppress(Exception):
        from livery.footman._tasks.janitor import run_sweepers

        run_sweepers(dry_run=False)
