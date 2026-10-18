"""One shard's share of the test files: listed, partitioned, recorded.

A sharded check leg runs the test step on every shard, each over a
share of the test files. `share` lists the test files of every suite
the run reaches (`test_files`), reads the durations and the head of
the leg's platform ([livery.workshop.read_durations][]), packs the
files into the shard's slices with [livery.workshop.partition][], the
first slice starting at the head, takes this
shard's slice, and leaves what it partitioned in the shard's record
([livery.workshop.leave_shard][]) for the gate job's proof. The test
step runs twice on a leg whose coverage entry tops up the suites no
record can supply, so a second run on the same shard adds to the
record instead of replacing it (`SETS_FILE`).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from livery.workshop import (
    WORKSPACE_TESTS,
    checkout_time,
    ci_run,
    files_digest,
    leave_shard,
    partition,
    platform_leg,
    read_durations,
    workspace_suite,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime
    from pathlib import Path

    from livery.workshop import Package, PhaseContext, Shard

#: The step's own record of what this shard partitioned on this leg,
#: under the checkout's ``.workshop/`` directory, which git ignores:
#: the shard, the bound and the rows it read, every file of every set
#: it partitioned, the suites those files belong to, and the files of
#: its own slices. The shard's record
#: for the gate job is written from it, so a second run of the step on
#: the leg adds its set and its slice to the first run's.
SETS_FILE = ".workshop/pytest-shard.json"


def test_files(ctx: PhaseContext, suite: Package) -> tuple[str, ...]:
    """The test files of *suite* this run runs, root-relative with forward slashes.

    The files the run selects for the suite (``ctx.tests``) when it
    names any; else every file pytest collects from the suite's test
    directory, ``test_*.py`` and ``*_test.py`` under it, the workspace's
    own ``tests`` directory included. Sorted. A suite without a test
    directory has none.
    """
    named = ctx.tests.get(suite.path)
    if named:
        return tuple(sorted(named))
    return _collected(suite)


def _collected(suite: Package) -> tuple[str, ...]:
    """Every test file pytest collects from *suite*'s test directory, sorted."""
    unit = suite.path == WORKSPACE_TESTS
    base = suite.directory if unit else suite.directory / "tests"
    prefix = suite.path if unit else f"{suite.path}/tests"
    if not base.is_dir():
        return ()
    return tuple(
        sorted(
            f"{prefix}/{path.relative_to(base).as_posix()}"
            for path in base.rglob("*.py")
            if path.is_file()
            and (path.name.startswith("test_") or path.name.endswith("_test.py"))
        )
    )


def share(
    ctx: PhaseContext, suites: tuple[Package, ...], *, step: str
) -> dict[str, tuple[str, ...]]:
    """This shard's share of *suites*' test files, per suite path, and its record.

    Every test file the workspace holds, each package's and the
    workspace's own, is packed into the shard's count of slices by the
    durations of the leg's platform, read from the rows stamped before
    the checkout's committer time ([livery.workshop.checkout_time][]),
    the first slice starting at the rows' head, the test seconds the
    steps only the first shard runs hold back, and of *suites*' test
    files (`test_files`) the ones in this shard's
    slice are returned, a suite's files sorted; a suite with no file in
    the slice maps to an empty tuple. The packing is over the whole
    workspace, never over the suites of one run, so a file has one
    shard however many runs of the step reach it on a leg: the gate's,
    and the coverage top-up's, which runs other suites and the
    workspace's own tests again. The slice, the digest of the set, the
    suites with a file in the set and the rows read are left in the
    shard's record. A suite with no test file is not named among the
    suites: it runs whole on the first shard, with every step that does
    not read the shard, and the coverage leg expects its parts there.
    Each line printed names *step*.

    With no durations row before the bound, or a store that cannot be
    read, the reason prints, the first shard runs every file and every
    other shard runs none, and the record names no row, so the gate
    job's proof sees the leg ran unsharded. Outside CI no leg names a
    durations series, so every file weighs the same and the files are
    shared out by name.
    """
    listed = {suite.path: test_files(ctx, suite) for suite in suites}
    every = tuple(name for names in listed.values() for name in names)
    named = tuple(path for path, names in listed.items() if names)
    before = checkout_time(ctx.root)
    run = ci_run()
    leg = run.leg if run is not None else ""
    durations: dict[str, float] = {}
    head = 0.0
    rows: tuple[str, ...] = ()
    if not leg:
        print(
            f"  {step}: no leg names a durations series here; every test file"
            " weighs the same"
        )
    else:
        durations, head, rows, why = read_durations(ctx.root, leg, before=before)
        if why:
            print(
                f"  {step}: the durations of {platform_leg(leg)} could not be read"
                f" ({why}); the first shard runs every test file"
            )
        elif not rows:
            print(
                f"  {step}: no durations row of {platform_leg(leg)} before"
                f" {before.isoformat(timespec='seconds')}; the first shard runs"
                " every test file"
            )
    if leg and not rows:
        mine = every if ctx.shard.first else ()
    else:
        slices = partition(
            _workspace_files(ctx, every), durations, ctx.shard.count, head=head
        )
        ours = set(every)
        mine = tuple(name for name in slices[ctx.shard.index - 1] if name in ours)
    _record(
        ctx.root,
        shard=ctx.shard,
        every=every,
        mine=mine,
        suites=named,
        rows=rows,
        before=before,
        attempt=run.attempt if run is not None else "",
    )
    print(
        f"  {step}: shard {ctx.shard} runs {len(mine) or 'none'} of {len(every)}"
        " test file(s)"
    )
    held = set(mine)
    return {
        path: tuple(name for name in names if name in held)
        for path, names in listed.items()
    }


def _workspace_files(ctx: PhaseContext, every: Iterable[str]) -> tuple[str, ...]:
    """Every test file the workspace holds, with *every*, the run's own, among them.

    The packages' test directories and the workspace's own, as pytest
    collects them; a file the run selects outside them is added, so the
    run's files are always packed.
    """
    suites = list(ctx.packages)
    unit = workspace_suite(ctx.root)
    if unit is not None:
        suites.append(unit)
    found = {name for suite in suites for name in _collected(suite)}
    found.update(every)
    return tuple(sorted(found))


def _record(
    root: Path,
    *,
    shard: Shard,
    every: Iterable[str],
    mine: Iterable[str],
    suites: Iterable[str],
    rows: tuple[str, ...],
    before: datetime,
    attempt: str = "",
) -> None:
    """Leave the shard's record, added to the one an earlier run of the step left.

    The gate job proves the shards of a platform from one record each,
    so when `SETS_FILE` holds a run of this shard under the same bound
    and the same rows, this run adds to it: the set partitioned is both
    runs' sets together, the suites are both runs' suites, the files
    are both slices, and the record's digest is of the set together. A
    file of another shard, another bound or other rows, or one that
    does not read, is replaced.
    """
    path = root / SETS_FILE
    key = {"shard": str(shard), "before": before.isoformat(), "rows": list(rows)}
    held = _held(path, key)
    names = sorted(set(held[0]) | set(every))
    files = tuple(dict.fromkeys((*held[1], *mine)))
    partitioned = sorted(set(held[2]) | set(suites))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {**key, "set": names, "files": list(files), "suites": partitioned},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    leave_shard(
        root,
        shard=shard,
        digest=files_digest(names),
        items=files,
        rows=rows,
        before=before,
        suites=partitioned,
        attempt=attempt,
    )


def _held(
    path: Path, key: Mapping[str, object]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The set, the files and the suites `SETS_FILE` holds under *key*, else empty."""
    if not path.is_file():
        return (), (), ()
    try:
        loaded: Any = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return (), (), ()
    if not isinstance(loaded, dict) or any(loaded.get(k) != v for k, v in key.items()):
        return (), (), ()
    held = tuple(_strings(loaded.get(k)) for k in ("set", "files", "suites"))
    names, files, suites = held
    if names is None or files is None or suites is None:
        return (), (), ()
    return names, files, suites


def _strings(value: object) -> tuple[str, ...] | None:
    """*value* as a tuple of strings; None when it is not a list of them."""
    if not isinstance(value, list):
        return None
    items: list[object] = value
    if not all(isinstance(item, str) for item in items):
        return None
    return tuple(str(item) for item in items)
