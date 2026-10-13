"""The coverage record: main's and each branch's parts per leg, and the parts in flight.

Coverage stays global under the affected mode. A record holds, per
check leg, one row per unit (a package's test suite, or the
workspace's own tests): the unit's parts in the line format, one per
measurer that measured the suite, each holding the files of the
suite's closure it reached, and the identity of the closure it was
measured at. Main has a record, and so does every branch with a pull
request run, under the same family with the branch as the base part.
A check leg skips a suite when its branch's record or main's holds the
unit at the suite's current closure identity, and runs it otherwise. A
leg that ran a suite leaves the suite's parts, with the scope its gate
ran, for the leg's one write, which puts them on its per-run ref beside
its timing half; the run's gate job unions the per-run refs with the
rows it carries from the records, judges the floors on that union, and
writes the union back: a pull request's run onto its branch's record,
main's run onto main's, each replaced in place, the units that no
longer exist removed. A row a write replaces at another closure, or
removes, stays for a day under its closure's name and the moment it
was kept: a run whose leg skipped a suite on it, while another run
moved the record on, still unions it. At the merge main's run finds its
tree on the verified record, which names the branch, and copies the
branch's rows into main's without measuring; a squash of a stale
branch has another tree, and main pays the full gate. A unit neither
record holds is measured on the spot, never red.

The unit is the suite that ran, never the package covered: a suite
executes lines across packages (a dependant's tests run its
dependencies), so a unit's parts hold the files of the suite's closure
alone, the files whose identity the key names. Reach for `recorded` to
read a record on a leg, `leave_run` to leave a leg's parts for its
write, `run_legs` to read them in the gate job, and `put_record` to
write the record. A read of a record parses its current rows alone and
names its kept rows, reading one only when the union asks for it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from operator import attrgetter
from pathlib import Path
from typing import Any

from livery import git
from livery.extensions.coverage._format import FORMAT
from livery.workshop._ci_context import RunContext, state_store
from livery.workshop._packages import WORKSPACE_TESTS, Package

#: The rows' shape: 3 holds a unit's parts in the line format, so a
#: package is judged on what each of its measurers judges. A row of
#: another schema is skipped and named, so a row of arcs written before
#: is a miss and the unit is measured afresh.
SCHEMA = 3

#: The record every leg reads last and main's run alone writes: the
#: measurement of main's tree. A branch's record has the branch's
#: name as its base instead, written by that branch's own runs.
MAIN = "main"

#: The file a check leg leaves for its one write, which puts it on the
#: leg's per-run ref beside its timing half: the scope its gate ran and
#: the parts of every suite it ran.
RUN_FILE = "coverage.json"

#: The root files whose identity every suite's key carries: a pin
#: change is a dependency change for every suite, whichever package
#: the closure walks.
ROOT_PINS = ("pyproject.toml", "uv.lock")


def branches(root: Path) -> list[str] | None:
    """The branches origin holds, by name; ``None`` if origin cannot be listed."""
    heads = git.list_refs(state_store(root), "refs/heads/")
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
    bases = {MAIN, *(git.slug(branch) for branch in heads)}
    return {(base, git.slug(leg)) for base in bases for leg in legs}


#: The family: one series per base and check leg, ``coverage/main/<leg>``
#: for main and ``coverage/<branch>/<leg>`` for a branch, one row per
#: unit, replaced in place by every write, the rows a write replaced
#: or removed kept beside them for a day (`KEPT_FOR`); a series of a
#: branch gone from origin, or of a leg the matrix no longer produces,
#: is the janitor's to drop. A gone branch's record lingers a day first:
#: main's run for the squash that merged it carries every suite its
#: legs skipped from that record, and the merge deletes the branch
#: minutes before that run's union reads, while another main run's
#: janitor may be sweeping in between.
RECORD = git.Keyed(
    "coverage",
    ("base", "leg"),
    schema=SCHEMA,
    current=current_keys,
    linger=timedelta(days=1),
)


#: How long a record keeps a row a write replaced at another closure, or
#: removed. A leg skips a suite on the row it read, and the run's union
#: reads the record minutes later, after main's run for another merge
#: may have moved the row on or dropped its unit; a kept row is found
#: at the closure the leg skipped on. A run that outlives the window
#: refuses in its union, and a run of the full gate measures again.
KEPT_FOR = timedelta(days=1)


#: How a kept row's name spells the moment its write kept it, in UTC.
KEPT_STAMP = "%Y%m%dT%H%M%SZ"


def kept_name(path: str, closure: str, when: datetime) -> str:
    """The record's file for a kept row: the unit at *path*, measured at *closure*.

    The name carries the moment *when* the write kept it, so a reader
    tells a kept row's age from its name and reads no kept row for it.
    """
    return f"{git.slug(path)}.{closure}.{when.astimezone(UTC):{KEPT_STAMP}}.json"


def _current_row(name: str) -> bool:
    """Whether the file *name* is a current row: a unit's slug alone.

    A slug holds no dot, so a current row's name holds one and a kept
    row's, which adds its closure and its moment, holds three.
    """
    return name.count(".") == 1


@dataclass(frozen=True)
class Unit:
    """One unit's measurement: the parts its suite's measurers made within its closure.

    Attributes:
        path: The unit's path (``packages/forge``, or ``tests``).
        closure: The closure identity the measurement is keyed by.
        run: The run that measured it.
        sha: The commit that run checked out.
        parts: The unit's parts in the line format, as JSON holds
            them: one for each measurer that measured the suite, each
            holding the files of the suite's closure it reached. A
            suite that ran unmeasured has none.
    """

    path: str
    closure: str
    run: str
    sha: str
    parts: tuple[dict[str, Any], ...] = ()

    @property
    def files(self) -> tuple[str, ...]:
        """The files the unit's parts hold, by their path from the root, sorted."""
        found: set[str] = set()
        for part in self.parts:
            named = part.get("files")
            if isinstance(named, dict):
                found.update(str(name) for name in named)
        return tuple(sorted(found))


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
class Kept:
    """A row a write replaced at another closure, or removed, kept for a day.

    Attributes:
        name: The row's file on the ref: its unit's slug, the closure it
            was measured at, and the moment the write kept it
            (`kept_name`).
        unit: The measurement, once read. A read of a record names its
            kept rows and reads none; the union reads the one it asks
            for (`Record.at`).
    """

    name: str
    unit: Unit | None = None

    @property
    def closure(self) -> str:
        """The closure the kept row was measured at, from its name; empty for none."""
        pieces = self.name.removesuffix(".json").split(".")
        return pieces[1] if len(pieces) > 1 else ""

    @property
    def when(self) -> datetime | None:
        """When the write kept the row, from its name; ``None`` when it names none."""
        pieces = self.name.removesuffix(".json").split(".")
        if len(pieces) != 3:
            return None
        try:
            return datetime.strptime(pieces[2], KEPT_STAMP).replace(tzinfo=UTC)
        except ValueError:
            return None


@dataclass(frozen=True)
class Record:
    """A base's record on one leg, as read.

    Attributes:
        units: The recorded units by path: the current rows.
        skipped: The rows that are not units, named; printing one
            gives the line.
        failed: Whether the record could not be read at all.
        reason: Why it could not, in the store's wording.
        kept: The rows writes replaced or removed, by file name, kept
            for `KEPT_FOR` from the write that kept each.
        read_kept: Reads a kept row by its name, for `at`; ``None``
            when the record was not read from the store.
    """

    units: dict[str, Unit]
    skipped: tuple[git.Skipped, ...] = ()
    failed: bool = False
    reason: str = ""
    kept: tuple[Kept, ...] = ()
    read_kept: Callable[[str], Unit | None] | None = field(
        default=None, compare=False, repr=False
    )

    def at(self, path: str, closure: str) -> Unit | None:
        """The measurement of *path* at *closure*: the current row's, else a kept one's.

        The union asks here for what a leg skipped: the leg read the
        current row, and a write since may have kept it instead. The
        kept rows of *path* at *closure* are read newest first, and the
        first that is the unit's answers.
        """
        row = self.units.get(path)
        if row is not None and row.closure == closure:
            return row
        prefix = f"{git.slug(path)}.{closure}."
        for kept in sorted(self.kept, key=attrgetter("name"), reverse=True):
            if not kept.name.startswith(prefix):
                continue
            unit = kept.unit
            if unit is None and self.read_kept is not None:
                unit = self.read_kept(kept.name)
            if unit is not None and unit.path == path and unit.closure == closure:
                return unit
        return None

    def expired(self, now: datetime) -> list[str]:
        """The names of the kept rows past their day at *now*.

        A row is kept for `KEPT_FOR` from the write that kept it, the
        moment its name carries. A row whose name carries none counts
        as expired: its age cannot be told.
        """
        names: list[str] = []
        for kept in self.kept:
            when = kept.when
            if when is None or now - when > KEPT_FOR:
                names.append(kept.name)
        return names

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


def record_bases(branch: str) -> tuple[str, ...]:
    """The records a leg reads, in order: the branch's own if any, then main's."""
    return (branch, MAIN) if branch and branch != MAIN else (MAIN,)


def series(root: Path) -> tuple[git.Series | git.Keyed, ...]:
    """Answer ``store.series``: the coverage record and the coverage marks.

    The janitor bounds them and ``fm store.ls`` lists them while the
    workspace lists the coverage extension. The answer is the same in
    every workspace, so *root* is not read.
    """
    from livery.extensions.coverage._marks import SERIES

    del root
    return (RECORD, SERIES)


def row_name(path: str) -> str:
    """The record's file for the unit at *path*."""
    return f"{git.slug(path)}.json"


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


def closure_id(root: Path, packages: tuple[Package, ...], package: Package) -> str:
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
    if package.path == WORKSPACE_TESTS:
        required = [member.path for member in sorted(packages, key=attrgetter("path"))]
        required.append(WORKSPACE_TESTS)
    else:
        required = [member.path for member in closure(packages, package)]
    # One process whatever the closure holds: the ids of every required
    # directory and every pin come back together, and a pin the tree
    # lacks is simply absent.
    ids = git.object_ids(root, "HEAD", [*required, *ROOT_PINS])
    parts: list[str] = []
    for path in required:
        if path not in ids:
            raise git.GitError(f"HEAD holds no {path}")
        parts.append(f"{path}={ids[path]}")
    parts.extend(f"{pin}={ids[pin]}" for pin in ROOT_PINS if pin in ids)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def in_closure(packages: tuple[Package, ...], package: Package, filename: str) -> bool:
    """Whether *filename* (workspace-relative) belongs to *package*'s closure.

    Every file belongs to the workspace tests' closure.
    """
    if package.path == WORKSPACE_TESTS:
        return True
    roots = tuple(member.path + "/" for member in closure(packages, package))
    return filename.replace("\\", "/").startswith(roots)


def unit_fields(unit: Unit) -> dict[str, Any]:
    """*unit* as a row and a leg's file hold it."""
    return {
        "unit": unit.path,
        "closure": unit.closure,
        "run": unit.run,
        "sha": unit.sha,
        "parts": list(unit.parts),
    }


def _parse_unit(raw: Any, *, path: str = "") -> tuple[Unit | None, str]:
    """A unit from its fields; or ``None`` and why they are not a unit's.

    A part is read whole only where it is used; here each one must be a
    table of this workshop's format, so a part of another version makes
    the row a miss.
    """
    if not isinstance(raw, dict):
        return None, "carries no unit"
    parts = raw.get("parts")
    unit_path = str(raw.get("unit", "") or path)
    if not isinstance(parts, list) or not unit_path:
        return None, "carries no unit"
    for part in parts:
        if not isinstance(part, dict):
            return None, "carries no unit"
        version = part.get("format")
        if version != FORMAT:
            return None, (
                f"holds a part of format {version!r}, and this workshop reads"
                f" format {FORMAT}"
            )
    return (
        Unit(
            path=unit_path,
            closure=str(raw.get("closure", "")),
            run=str(raw.get("run", "")),
            sha=str(raw.get("sha", "")),
            parts=tuple(parts),
        ),
        "",
    )


def leave_run(
    root: Path,
    run: RunContext,
    *,
    leg: str,
    scope: str,
    packages: tuple[str, ...],
    units: Mapping[str, Unit],
) -> str:
    """Leave *leg*'s scope and measured *units* for its one write; ``""`` or why not.

    The one file carries the scope the leg's gate ran, the packages a
    narrowed gate named, and every unit's parts, so the gate job reads
    a leg's whole fact in one read and the parts go with the ref when
    the run's metrics are collected. It lands in the leg's spool
    ([livery.workshop._metrics.LEG_SPOOL][]), and the leg's last
    entry, ``ci.leg``, puts it on the leg's per-run ref beside the
    timing row ([livery.workshop._metrics.put_leg][]), so a leg writes
    its ref once.
    """
    from livery.workshop._metrics import leave_leg_file

    if not leg:
        return "refusing: the leg has no label, so its parts have no key"
    row = {
        "leg": leg,
        "scope": scope,
        "packages": list(packages),
        "run": run.run_id,
        "forge": run.forge,
        "units": {path: unit_fields(unit) for path, unit in sorted(units.items())},
    }
    leave_leg_file(root, RUN_FILE, row)
    return ""


def run_legs(root: Path, run: RunContext) -> tuple[list[Leg], str]:
    """Every check leg's coverage file on *run*'s per-run refs; the legs, or the reason.

    The reason is set when the refs could not be listed. A ref that
    carries no readable coverage file is a leg whose ``why`` names
    it: a leg that died before its parts, or a file that is not a
    row; the caller decides, and a missing put is red there.
    """
    from livery.workshop._metrics import RUNS

    keys = RUNS.listed(state_store(root), run.run_id)
    if keys is None:
        return [], f"{RUNS.prefix}{run.run_id}/*: the remote could not be listed"
    legs: list[Leg] = []
    for key in keys:
        series = RUNS.at(*key)
        found, why = series.row(state_store(root), RUN_FILE)
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
                unit, _why = _parse_unit(raw, path=str(path))
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
    unit is skipped and named, and the rest stand. The current rows
    alone are read: a row named by its unit, closure and moment
    (`kept_name`) is a kept one, named in the record and read only when
    the union asks for it (`Record.at`). A leg without a label, or an
    empty base, has no record to read.
    """
    if not leg:
        return Record({}, failed=True, reason="this leg has no label")
    if not base:
        return Record({}, failed=True, reason="no branch to read a record for")
    series = RECORD.series(base, leg)
    found = series.rows(state_store(root), only=_current_row)
    if found.failed:
        return Record({}, failed=True, reason=found.reason)
    units: dict[str, Unit] = {}
    skipped = list(found.skipped)
    for row in found.rows:
        unit, why = _parse_unit(row.data)
        if unit is None:
            skipped.append(git.Skipped(row.name, why))
            continue
        units[unit.path] = unit

    def read_kept(name: str) -> Unit | None:
        row, _why = series.row(state_store(root), name)
        return None if row is None else _parse_unit(row.data)[0]

    kept = tuple(Kept(name) for name in found.unread)
    return Record(units, tuple(skipped), kept=kept, read_kept=read_kept)


def put_record(
    root: Path,
    run: RunContext,
    *,
    leg: str,
    fresh: Mapping[str, Unit],
    remove: Iterable[str] = (),
    base: str = MAIN,
    held: Record | None = None,
    now: datetime | None = None,
) -> str:
    """Write the *fresh* units onto *base*'s record on *leg*; ``""`` or the reason.

    Main's record is written by main's run alone, the run of a push;
    a branch's record by that branch's own pull request run alone.
    Either replaces its record in place: a fresh row replaces the
    unit's, a row named in *remove* goes, every other row stands as
    the carried measurement it is. Any other run is refused by name,
    so a pull request never writes what main's next run will skip on,
    and no branch writes another's record.

    A current row that a fresh one replaces at another closure, or
    that *remove* names, stays as a kept row under its closure's name
    and the moment *now* (`kept_name`), and every kept row older than
    `KEPT_FOR` at *now* goes. *held* is the record as the caller read
    it; it is read here when absent, and a record that cannot be read
    refuses the write, since what it would keep is unknown.
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
    current = held if held is not None else recorded(root, leg=leg, base=base)
    if current.failed:
        return (
            f"refusing: {base}'s record on {leg} could not be read"
            f" ({current.reason}), so the rows it keeps are unknown"
        )
    moment = now or datetime.now(UTC)
    removed = list(remove)
    rows = {row_name(path): unit_fields(unit) for path, unit in sorted(fresh.items())}
    for path, unit in sorted(current.units.items()):
        replaced = path in fresh and fresh[path].closure != unit.closure
        dropped = path not in fresh and row_name(path) in removed
        if replaced or dropped:
            rows[kept_name(path, unit.closure, moment)] = unit_fields(unit)
    removed += current.expired(moment)
    return RECORD.series(base, leg).put(
        state_store(root),
        rows,
        message=f"coverage: {base}'s record on {leg} by run {run.run_id}",
        remove=removed,
    )
