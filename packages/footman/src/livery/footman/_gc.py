"""Cache garbage collection — the detached child a run spawns at most daily.

The cache is derived state top to bottom (completion manifests rebuild on
any execution-path run; timing history regrows), which is what makes
collecting it casually safe: the worst possible outcome of any deletion is
a rebuild. Two rules, in order:

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

The invoking directory's own pair is never touched, and every failure is
silent — a concurrently-reading completion child on Windows may hold a file
open, and a collector must never be louder than what it collects.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

IDLE_DAYS = 90
PART_DAYS = 1
STAMP = "gc.stamp"


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

    return removed


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
    """Entry for the detached child: argv is (cache_dir, skip_stem)."""
    if len(sys.argv) < 2:
        return
    skip = sys.argv[2] if len(sys.argv) > 2 else ""
    collect(Path(sys.argv[1]), skip)
