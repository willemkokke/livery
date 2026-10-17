"""What the newborn's store says a run did: the rows the loop's proofs read.

A proof asks the rows the legs wrote, never a job's log: a run's
metrics row (each check leg's scope and the coverage section the gate
job left), the verified series (the tree a run proved and what it
rests on), a base's coverage record (which run measured each unit) and
the coverage marks. Every reader fails naming the series, the file and
the run when the row it needs is not there, so a proof that fails says
what the legs did not write.

The coverage rows are read by their ref and a few fields, since the
loop lives in the base and the base imports no extension; their schema
is the coverage extension's, and the loop leaves the base with phase
15 of the destination note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from livery import git
from livery.footman import fail
from livery.workshop import _metrics, _verified
from livery.workshop._ci_context import state_store

if TYPE_CHECKING:
    from pathlib import Path

#: The coverage extension's record family and marks series, by ref alone.
COVERAGE_RECORD = git.Keyed("coverage", ("base", "leg"))
COVERAGE_MARKS = git.Series("coverage/marks", window=400, ci_only=False)


@dataclass(frozen=True)
class LegFacts:
    """One job's half of a run's metrics row: what its gate ran.

    Attributes:
        job: The job's name, as the forge lists it.
        scope: What the leg's gate ran: ``full``, ``affected``,
            ``nothing``, ``verified``, or ``unknown`` when the leg
            left no marker.
        packages: The suites a narrowed gate ran.
        base_tree: The tree a narrowed gate measured from; empty
            otherwise.
        leg: The leg's label, the key of its coverage record
            (``check-ubuntu-latest-3.14``).
        tasks: The tasks the job ran, by the names its trace recorded:
            the top-level tasks and each task's steps.
    """

    job: str
    scope: str
    packages: tuple[str, ...]
    base_tree: str
    leg: str
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class RunFacts:
    """A run's metrics row, read for a proof.

    Attributes:
        run: The run's id.
        jobs: Each job's facts, by the job's name.
        coverage: The coverage section the gate job left: the percent
            each unit was judged at, by the unit's path; empty when
            the gate job judged nothing.
    """

    run: str
    jobs: dict[str, LegFacts]
    coverage: dict[str, float]

    def job(self, prefix: str) -> LegFacts:
        """The one job whose name starts with *prefix*; fails naming the row."""
        found = [facts for name, facts in self.jobs.items() if name.startswith(prefix)]
        if len(found) != 1:
            fail(
                f"run {self.run}: {_metrics.SERIES.ref}'s row names"
                f" {len(found)} job(s) starting with {prefix!r}, expected one:"
                f" {', '.join(sorted(self.jobs))}"
            )
        return found[0]


def run_facts(root: Path, run_id: int | str) -> RunFacts:
    """The metrics row of *run_id*, fetched fresh from the newborn's origin.

    Fails naming the series and the file when the row is not there:
    the gate job's collect did not write it.
    """
    store = state_store(root)
    name = _metrics.run_file(str(run_id))
    with git.remote_snapshot(store, fetch=(_metrics.SERIES.name,)):
        found = _metrics.SERIES.rows(store, only=lambda file: file == name)
    if found.failed:
        fail(f"run {run_id}: {_metrics.SERIES.ref} could not be read: {found.reason}")
    row = next((item for item in found.rows if item.name == name), None)
    if row is None:
        fail(
            f"run {run_id}: no row {name} on {_metrics.SERIES.ref}; the gate job's"
            " collect did not write the run's row"
        )
    jobs: dict[str, LegFacts] = {}
    for job, data in dict(row.data.get("jobs") or {}).items():
        scope = data.get("scope") or {}
        tasks = (*data.get("tasks", {}), *data.get("task_steps", {}))
        jobs[str(job)] = LegFacts(
            job=str(job),
            scope=str(scope.get("scope", "unknown")),
            packages=tuple(str(p) for p in scope.get("packages") or ()),
            base_tree=str(scope.get("base_tree") or ""),
            leg=str(scope.get("leg") or ""),
            tasks=tuple(str(t) for t in tasks),
        )
    section = (row.data.get("sections") or {}).get("coverage") or {}
    coverage = {
        str(path): float(value)
        for path, value in dict(section.get("packages") or {}).items()
    }
    return RunFacts(str(run_id), jobs, coverage)


def verified_by(root: Path, run_id: int | str) -> _verified.Verified:
    """The verified row *run_id* stamped; fails naming the series when none did."""
    rows, why = _verified.proved(root)
    if why:
        fail(f"run {run_id}: {_verified.SERIES.ref} could not be read: {why}")
    for entry in rows.values():
        if entry.run == str(run_id):
            return entry
    fail(
        f"run {run_id}: no row on {_verified.SERIES.ref} names it; the gate job's"
        " stamp did not record the tree as proved"
    )


def verified_tree(root: Path, tree: str) -> _verified.Verified:
    """The verified row of *tree*; fails naming the series when the record lacks it."""
    entry, why = _verified.record(root, tree)
    if why:
        fail(f"tree {tree[:12]}: {_verified.SERIES.ref} could not be read: {why}")
    if entry is None:
        fail(f"tree {tree[:12]}: no row on {_verified.SERIES.ref}; nothing proved it")
    return entry


@dataclass(frozen=True)
class UnitRow:
    """One unit of a base's coverage record: the run that measured it.

    Attributes:
        unit: The unit's path (``packages/loop-echo``, or ``tests``).
        run: The run that measured it.
        sha: The commit that run checked out.
        closure: The closure identity the measurement is keyed by.
    """

    unit: str
    run: str
    sha: str
    closure: str


def record_units(root: Path, base: str, leg: str) -> dict[str, UnitRow]:
    """*base*'s coverage record on *leg*: the current row of each unit, by path.

    A current row is named by its unit's slug alone; a kept row, named
    by its moment and closure as well, is not a unit of the record.
    Fails naming the ref when it cannot be read; an absent record is
    empty.
    """
    series = COVERAGE_RECORD.series(base, leg)
    store = state_store(root)
    with git.remote_snapshot(store, fetch=(series.ref,)):
        found = series.rows(store, only=lambda name: name.count(".") == 1)
    if found.failed:
        fail(f"{series.ref} could not be read: {found.reason}")
    units: dict[str, UnitRow] = {}
    for row in found.rows:
        data: dict[str, Any] = row.data
        unit = str(data.get("unit") or "")
        if unit:
            units[unit] = UnitRow(
                unit,
                str(data.get("run") or ""),
                str(data.get("sha") or ""),
                str(data.get("closure") or ""),
            )
    return units


@dataclass(frozen=True)
class MarkRow:
    """A package's current coverage mark: the newest row that set it.

    Attributes:
        package: The package path.
        value: The mark, in percent.
        kind: ``first``, ``ratchet`` or ``accept``.
        by: The run id or the person that wrote the row.
    """

    package: str
    value: float
    kind: str
    by: str


def marks(root: Path) -> dict[str, MarkRow]:
    """The current mark per package, the newest row each; fails when unreadable."""
    store = state_store(root)
    with git.remote_snapshot(store, fetch=(COVERAGE_MARKS.name,)):
        found = COVERAGE_MARKS.rows(store)
    if found.failed:
        fail(f"{COVERAGE_MARKS.ref} could not be read: {found.reason}")
    current: dict[str, MarkRow] = {}
    for row in found.rows:
        data: dict[str, Any] = row.data
        package = str(data.get("package") or "")
        if not package or package in current:
            continue
        try:
            value = float(data["value"])
        except (KeyError, TypeError, ValueError):
            continue
        current[package] = MarkRow(
            package, value, str(data.get("kind") or ""), str(data.get("by") or "")
        )
    return current
