"""The coverage record: main's and each branch's arcs per leg, and the arcs in flight.

Coverage stays global under the affected mode. A record holds, per
check leg, one row per unit (a package's test suite, or the
workspace's own tests): the arcs the suite reached within its
closure, and the identity of the closure it was measured at. Main has
a record, and so does every branch with a pull request run, under the
same family with the branch as the base part. A check leg skips a
suite when its branch's record or main's holds the unit at the suite's
current closure identity, and runs it otherwise. A leg that ran a
suite puts the suite's arcs on its per-run ref, beside its timing
half, with the scope its gate ran; the run's gate job unions the
per-run refs with the rows it carries from the records, judges the
floors on that union, and writes the union back: a pull request's run
onto its branch's record, main's run onto main's, each replaced in
place, the units that no longer exist removed. At the merge main's
run finds its tree on the verified record, which names the branch,
and copies the branch's rows into main's without measuring; a squash
of a stale branch has another tree, and main pays the full gate. A
unit neither record holds is measured on the spot, never red.

The unit is the suite that ran, never the package covered: a suite
executes lines across packages (a dependant's tests run its
dependencies), so the stored lines are filtered to the files of the
suite's closure, the files whose identity the key names. Reach for
[livery.workshop._coverage_store.recorded][] to read main's record on
a leg, [livery.workshop._coverage_store.put_run][] to put a leg's
lines, [livery.workshop._coverage_store.run_legs][] to read them in
the gate job, and [livery.workshop._coverage_store.put_record][] to
write the record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._packages import Package
from livery.workshop._state import (
    Keyed,
    RunContext,
    Series,
    Skipped,
    list_refs,
    slug,
)

#: The rows' shape: 2 holds arcs, pairs of line numbers, so a package
#: is judged on its statements and its branches together. A row of
#: another schema is skipped and named, so a row of lines written
#: before is a miss and the unit is measured afresh.
SCHEMA = 2

#: The record every leg reads last and main's run alone writes: the
#: measurement of main's tree. A branch's record has the branch's
#: name as its base instead, written by that branch's own runs.
MAIN = "main"

#: The file a check leg puts on its per-run ref beside its timing
#: half: the scope its gate ran and the lines of every suite it ran.
RUN_FILE = "coverage.json"

#: The root files whose identity every suite's key carries: a pin
#: change is a dependency change for every suite, whichever package
#: the closure walks.
ROOT_PINS = ("pyproject.toml", "uv.lock")

#: The workspace's own test directory, a stored unit like a package's
#: suite. Its tests reach any package, so its identity is the whole
#: tree, and every leg that runs a suite runs it.
WORKSPACE_TESTS = "tests"


def workspace_suite(root: Path) -> Package | None:
    """The workspace's own tests as a unit, or ``None`` when it has none."""
    directory = root / WORKSPACE_TESTS
    if not directory.is_dir():
        return None
    return Package(
        directory=directory,
        path=WORKSPACE_TESTS,
        name="workspace-tests",
        type="workspace",
        depends=(),
    )


def branches(root: Path) -> list[str] | None:
    """The branches origin holds, by name; ``None`` if origin cannot be listed."""
    heads = list_refs(root, "refs/heads/")
    if heads is None:
        return None
    return sorted(name[len("refs/heads/") :] for name in heads)


def current_keys(root: Path) -> set[tuple[str, ...]] | None:
    """The keys the world produces: main and every branch on origin, per check leg.

    The janitor drops a ref outside this set: a branch's record once
    the branch is gone from origin, and a leg's once the matrix no
    longer produces it. ``None`` when the contract or the remote
    cannot be read, and then nothing is dropped.
    """
    from livery.workshop._points import check_legs

    try:
        legs = check_legs(root)
    except (Exception, SystemExit):
        return None
    heads = branches(root)
    if heads is None:
        return None
    bases = {MAIN, *(slug(branch) for branch in heads)}
    return {(base, slug(leg)) for base in bases for leg in legs}


#: The family: one series per base and check leg, ``coverage/main/<leg>``
#: for main and ``coverage/<branch>/<leg>`` for a branch, one row per
#: unit, replaced in place by every write; a series of a branch gone
#: from origin, or of a leg the matrix no longer produces, is the
#: janitor's to drop.
RECORD = Keyed("coverage", ("base", "leg"), schema=SCHEMA, current=current_keys)


@dataclass(frozen=True)
class Unit:
    """One unit's measurement: the arcs its suite reached within its closure.

    Attributes:
        path: The unit's path (``packages/forge``, or ``tests``).
        closure: The closure identity the measurement is keyed by.
        run: The run that measured it.
        sha: The commit that run checked out.
        files: Measured arcs per file, relative to the workspace root:
            pairs of line numbers as coverage.py records them, a
            negative number an entry or an exit, so a file's
            statements and branches are both in the row.
    """

    path: str
    closure: str
    run: str
    sha: str
    files: dict[str, list[tuple[int, int]]]


@dataclass(frozen=True)
class Leg:
    """What one check leg put on its per-run ref.

    Attributes:
        key: The leg's part of the per-run key, as its ref spells it.
        label: The leg's label (``check-ubuntu-latest-3.14``), the key
            of its record.
        scope: The scope the leg's gate ran, as its marker named it.
        packages: The packages whose suites a narrowed leg ran.
        units: The suites the leg measured, by the unit's path.
        why: Why the ref carries no readable file, when it does not.
    """

    key: str
    label: str
    scope: str
    packages: tuple[str, ...]
    units: dict[str, Unit]
    why: str = ""


@dataclass(frozen=True)
class Record:
    """Main's record on one leg, as read.

    Attributes:
        units: The recorded units by path.
        skipped: The rows that are not units, named; printing one
            gives the line.
        failed: Whether the record could not be read at all.
        reason: Why it could not, in the store's wording.
    """

    units: dict[str, Unit]
    skipped: tuple[Skipped, ...] = ()
    failed: bool = False
    reason: str = ""

    def stale(self, current: Iterable[str]) -> list[str]:
        """The row names to drop: units outside *current*, and rows that are no unit.

        A stale row is a unit that no longer exists, or a row the
        reader could not take as a unit at all.
        """
        keep = set(current)
        names = [row_name(path) for path in self.units if path not in keep]
        return names + [item.name for item in self.skipped]


def record_ref(leg: str, base: str = MAIN) -> str:
    """The ref holding *base*'s record on *leg*: main's, or a branch's."""
    return RECORD.series(base, leg).ref


def row_name(path: str) -> str:
    """The record's file for the unit at *path*."""
    return f"{slug(path)}.json"


def closure(packages: tuple[Package, ...], package: Package) -> tuple[Package, ...]:
    """*package* and every package it depends on, transitively, in path order.

    An edge naming a path no package has is skipped: the workspace
    lint refuses such an edge before any gate runs, and the identity
    here is of the packages that exist.
    """
    by_path = {item.path: item for item in packages}
    seen: dict[str, Package] = {}
    pending = [package]
    while pending:
        current = pending.pop()
        if current.path in seen:
            continue
        seen[current.path] = current
        pending.extend(
            by_path[edge.path] for edge in current.depends if edge.path in by_path
        )
    return tuple(seen[path] for path in sorted(seen))


def closure_id(git: GitOps, packages: tuple[Package, ...], package: Package) -> str:
    """The identity of *package*'s suite inputs at ``HEAD``.

    A digest over the tree ids of the package's closure directories
    and the blob ids of `ROOT_PINS`, so an identical identity means
    identical code and identical dependencies: the argument that lets
    the suite's run be skipped at all. A root pin the tree lacks
    contributes nothing.

    The workspace's own tests reach any package, so their identity is
    every package's tree, their own directory's tree, and the root
    pins: prose leaves it untouched, and a root configuration change
    forces a full run anyway, which measures the unit afresh.

    Raises:
        GitError: When a closure directory is not in ``HEAD``.
    """
    parts: list[str] = []
    if package.path == WORKSPACE_TESTS:
        for member in sorted(packages, key=lambda item: item.path):
            parts.append(f"{member.path}={git.object_id(f'HEAD:{member.path}')}")
        parts.append(f"{WORKSPACE_TESTS}={git.object_id(f'HEAD:{WORKSPACE_TESTS}')}")
        for pin in ROOT_PINS:
            try:
                parts.append(f"{pin}={git.object_id(f'HEAD:{pin}')}")
            except GitError:
                continue
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    for member in closure(packages, package):
        parts.append(f"{member.path}={git.object_id(f'HEAD:{member.path}')}")
    for pin in ROOT_PINS:
        try:
            parts.append(f"{pin}={git.object_id(f'HEAD:{pin}')}")
        except GitError:
            continue
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def in_closure(packages: tuple[Package, ...], package: Package, filename: str) -> bool:
    """Whether *filename* (workspace-relative) belongs to *package*'s closure.

    Every file belongs to the workspace tests' closure.
    """
    if package.path == WORKSPACE_TESTS:
        return True
    roots = tuple(member.path + "/" for member in closure(packages, package))
    return filename.replace("\\", "/").startswith(roots)


def _unit_fields(unit: Unit) -> dict[str, Any]:
    return {
        "unit": unit.path,
        "closure": unit.closure,
        "run": unit.run,
        "sha": unit.sha,
        "files": {
            name: [list(arc) for arc in sorted(arcs)]
            for name, arcs in sorted(unit.files.items())
        },
    }


def _parse_unit(raw: Any, *, path: str = "") -> Unit | None:
    """A unit from its fields, or ``None`` when they are not a unit's."""
    if not isinstance(raw, dict):
        return None
    files = raw.get("files")
    if not isinstance(files, dict):
        return None
    unit_path = str(raw.get("unit", "") or path)
    if not unit_path:
        return None
    return Unit(
        path=unit_path,
        closure=str(raw.get("closure", "")),
        run=str(raw.get("run", "")),
        sha=str(raw.get("sha", "")),
        files={
            str(name): sorted(
                (int(arc[0]), int(arc[1]))
                for arc in arcs
                if isinstance(arc, list | tuple) and len(arc) == 2
            )
            for name, arcs in files.items()
            if isinstance(arcs, list)
        },
    )


def _per_run(run_id: str, leg: str) -> Series:
    """The per-run series *leg* of *run_id* writes, the timing half's."""
    from livery.workshop._metrics import RUNS

    return RUNS.series(run_id, leg)


def put_run(
    root: Path,
    run: RunContext,
    *,
    leg: str,
    scope: str,
    packages: tuple[str, ...],
    units: Mapping[str, Unit],
    timing: Mapping[str, Any] | None = None,
) -> str:
    """Put *leg*'s scope and its measured *units* on its per-run ref; ``""`` or why not.

    Only a CI run writes. The one file carries the scope the leg's
    gate ran, the packages a narrowed gate named, and every unit's
    lines, so the gate job reads a leg's whole fact in one read and
    the lines go with the ref when the run's metrics are collected.
    *timing*, the leg's timing row ([livery.workshop._metrics.leg_row][]),
    rides the same write as the row file, so a leg writes its ref once.
    """
    if not leg:
        return "refusing: the leg has no label, so its lines have no key"
    row = {
        "leg": leg,
        "scope": scope,
        "packages": list(packages),
        "run": run.run_id,
        "forge": run.forge,
        "units": {path: _unit_fields(unit) for path, unit in sorted(units.items())},
    }
    files: dict[str, Mapping[str, Any]] = {RUN_FILE: row}
    if timing is not None:
        from livery.workshop._metrics import ROW_FILE

        files[ROW_FILE] = timing
    return _per_run(run.run_id, leg).put(
        root, files, message=f"coverage: {leg} of run {run.run_id}"
    )


def run_legs(root: Path, run: RunContext) -> tuple[list[Leg], str]:
    """Every check leg's coverage file on *run*'s per-run refs; the legs, or the reason.

    The reason is set when the refs could not be listed. A ref that
    carries no readable coverage file is a leg whose ``why`` names
    it: a leg that died before its lines, or a file that is not a
    row; the caller decides, and a missing put is red there.
    """
    from livery.workshop._metrics import RUNS

    keys = RUNS.listed(root, run.run_id)
    if keys is None:
        return [], f"{RUNS.prefix}{run.run_id}/*: the remote could not be listed"
    legs: list[Leg] = []
    for key in keys:
        series = RUNS.at(*key)
        found, why = series.row(root, RUN_FILE)
        if why:
            legs.append(Leg(key[-1], "", "", (), {}, why=f"{series.ref}: {why}"))
            continue
        if found is None:
            legs.append(
                Leg(
                    key[-1],
                    "",
                    "",
                    (),
                    {},
                    why=f"{series.ref} carries no {RUN_FILE}",
                )
            )
            continue
        raw_units = found.data.get("units")
        units: dict[str, Unit] = {}
        if isinstance(raw_units, dict):
            for path, raw in raw_units.items():
                unit = _parse_unit(raw, path=str(path))
                if unit is not None:
                    units[unit.path] = unit
        legs.append(
            Leg(
                key[-1],
                str(found.data.get("leg", "") or ""),
                str(found.data.get("scope", "") or ""),
                tuple(str(item) for item in found.data.get("packages", []) or []),
                units,
            )
        )
    return legs, ""


def recorded(root: Path, *, leg: str, base: str = MAIN) -> Record:
    """*base*'s record on *leg*, main's by default: every recorded unit, in one read.

    An absent record is no units and no failure; a record the store
    could not read is a failure with its reason; a row that is not a
    unit is skipped and named, and the rest stand. A leg without a
    label, or an empty base, has no record to read.
    """
    if not leg:
        return Record({}, failed=True, reason="this leg has no label")
    if not base:
        return Record({}, failed=True, reason="no branch to read a record for")
    found = RECORD.series(base, leg).rows(root)
    if found.failed:
        return Record({}, failed=True, reason=found.reason)
    units: dict[str, Unit] = {}
    skipped = list(found.skipped)
    for row in found.rows:
        unit = _parse_unit(row.data)
        if unit is None:
            skipped.append(Skipped(row.name, "carries no unit"))
            continue
        units[unit.path] = unit
    return Record(units, tuple(skipped))


def put_record(
    root: Path,
    run: RunContext,
    *,
    leg: str,
    fresh: Mapping[str, Unit],
    remove: Iterable[str] = (),
    base: str = MAIN,
) -> str:
    """Write the *fresh* units onto *base*'s record on *leg*; ``""`` or the reason.

    Main's record is written by main's run alone, the run of a push;
    a branch's record by that branch's own pull request run alone.
    Either replaces its record in place: a fresh row replaces the
    unit's, a row named in *remove* goes, every other row stands as
    the carried measurement it is. Any other run is refused by name,
    so a pull request never writes what main's next run will skip on,
    and no branch writes another's record.
    """
    if not leg:
        return "refusing: the leg has no label, so the record has no key"
    if not base:
        return "refusing: no branch to write a record for"
    if base == MAIN and run.event != "push":
        return (
            f"refusing: a {run.event or 'local'} run reads {MAIN}'s record"
            " and never writes it"
        )
    if base != MAIN and (run.event != "pull_request" or run.head_ref != base):
        return (
            f"refusing: {base}'s record is written by that branch's own pull"
            f" request run, not a {run.event or 'local'} run of"
            f" {run.head_ref or 'no branch'}"
        )
    rows = {row_name(path): _unit_fields(unit) for path, unit in sorted(fresh.items())}
    return RECORD.series(base, leg).put(
        root,
        rows,
        message=f"coverage: {base}'s record on {leg} by run {run.run_id}",
        remove=remove,
    )
