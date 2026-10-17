"""What the newborn's store says a run did: the rows the loop's proofs read.

A proof asks the rows the legs wrote, never a job's log: a run's
metrics row (each check leg's scope and the coverage section the gate
job left), the verified series (the tree a run proved and what it
rests on), a base's coverage record (which run measured each unit),
the coverage marks, and a platform leg's durations series (the seconds
per test file a run's gate job recorded). A platform that ran as
shards has one check row, the fold's, keyed by the platform leg with
its shards listed under ``shard``; the forge's rows of the shards
stand beside it with their times alone, and no proof reads them as
the leg. Every reader fails naming the series, the file and the run
when the row it needs is not there, so a proof that fails says what
the legs did not write.

The coverage rows are read by their ref and a few fields, since the
loop lives in the base and the base imports no extension; their schema
is the coverage extension's, and the loop leaves the base with phase
15 of the destination note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from livery import git
from livery.footman import fail
from livery.workshop import _metrics, _shard, _verified
from livery.workshop._ci_context import state_store
from livery.workshop._speed import forge_shard_row

if TYPE_CHECKING:
    from pathlib import Path

#: The coverage extension's record family and marks series, by ref alone.
COVERAGE_RECORD = git.Keyed("coverage", ("base", "leg"))
COVERAGE_MARKS = git.Series("coverage/marks", window=400, ci_only=False)


@dataclass(frozen=True)
class ShardFacts:
    """One shard of a folded check leg, as the fold listed it.

    Attributes:
        shard: The shard, ``1of2``.
        leg: The shard's leg label (``check-ubuntu-latest-3.14-1of2``).
        job: The shard's job name, as the forge lists it.
        files: How many test files the shard ran.
    """

    shard: str
    leg: str
    job: str
    files: int


@dataclass(frozen=True)
class FoldFacts:
    """The fold's record on a platform's check row: what its shards partitioned.

    Attributes:
        count: How many shards the platform ran as.
        digest: The digest of the test-file set the shards partitioned.
        rows: The durations rows the shards partitioned by, newest
            first; empty when no row was there to read.
        shards: Each shard, in order.
    """

    count: int
    digest: str
    rows: tuple[str, ...]
    shards: tuple[ShardFacts, ...]


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
        shard: The shard whose own row this is (``1of2``), when the
            gate job's fold did not fold it into its platform's row;
            empty for a platform's row.
        fold: What the fold recorded on a platform's row; ``None`` for
            a leg that ran as one shard of one.
    """

    job: str
    scope: str
    packages: tuple[str, ...]
    base_tree: str
    leg: str
    tasks: tuple[str, ...]
    shard: str = ""
    fold: FoldFacts | None = None


@dataclass(frozen=True)
class RunFacts:
    """A run's metrics row, read for a proof.

    Attributes:
        run: The run's id.
        jobs: Each job's facts, by the job's name; the forge's rows of
            a platform's shards are not among them.
        coverage: The coverage section the gate job left: the percent
            each unit was judged at, by the unit's path; empty when
            the gate job judged nothing.
        shard_jobs: The names of the forge's rows of a platform's
            shards, kept beside the fold's row with their times alone.
    """

    run: str
    jobs: dict[str, LegFacts]
    coverage: dict[str, float]
    shard_jobs: tuple[str, ...] = ()

    def job(self, prefix: str) -> LegFacts:
        """The one job whose name starts with *prefix*; fails naming the row.

        The answer is the leg's own row: for a platform that ran as
        shards, the fold's, never the forge's row of a shard. A row
        that is one shard's own, or shard rows with no fold's row
        among them, say the gate job's fold did not run, and the
        answer fails naming them.
        """
        found = [facts for name, facts in self.jobs.items() if name.startswith(prefix)]
        shards = [name for name in self.shard_jobs if name.startswith(prefix)]
        if shards and not found:
            fail(
                f"run {self.run}: {_metrics.SERIES.ref}'s row names"
                f" {', '.join(shards)} and no folded row of their platform; the"
                " gate job's fold did not run"
            )
        own = [f"{facts.job} ({facts.shard})" for facts in found if facts.shard]
        if own:
            fail(
                f"run {self.run}: {_metrics.SERIES.ref}'s row holds"
                f" {', '.join(own)}, one shard's own row each, not the platform's;"
                " the gate job's fold did not run"
            )
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
    shard_jobs: list[str] = []
    for job, data in dict(row.data.get("jobs") or {}).items():
        if forge_shard_row(str(job), data):
            shard_jobs.append(str(job))
            continue
        scope = data.get("scope") or {}
        tasks = (*data.get("tasks", {}), *data.get("task_steps", {}))
        leg = str(scope.get("leg") or "")
        own, fold = _shard_facts(leg, data.get("shard"))
        jobs[str(job)] = LegFacts(
            job=str(job),
            scope=str(scope.get("scope", "unknown")),
            packages=tuple(str(p) for p in scope.get("packages") or ()),
            base_tree=str(scope.get("base_tree") or ""),
            leg=leg,
            tasks=tuple(str(t) for t in tasks),
            shard=own,
            fold=fold,
        )
    section = (row.data.get("sections") or {}).get("coverage") or {}
    coverage = {
        str(path): float(value)
        for path, value in dict(section.get("packages") or {}).items()
    }
    return RunFacts(str(run_id), jobs, coverage, tuple(shard_jobs))


def _shard_facts(leg: str, shard: Any) -> tuple[str, FoldFacts | None]:
    """The row's shard: its own, when it is one shard's row, or the fold's record.

    A row whose ``shard`` lists ``shards`` is the fold's; one whose
    ``shard`` is a shard's own record, or whose leg label ends in a
    shard, is one shard's row the fold did not fold.
    """
    if isinstance(shard, dict) and "shards" in shard:
        listed: Any = shard.get("shards")
        entries = tuple(
            ShardFacts(
                str(item.get("shard") or ""),
                str(item.get("leg") or ""),
                str(item.get("job") or ""),
                int(item.get("files") or 0),
            )
            for item in (listed if isinstance(listed, list) else [])
            if isinstance(item, dict)
        )
        return "", FoldFacts(
            int(shard.get("count") or 0),
            str(shard.get("digest") or ""),
            tuple(str(name) for name in shard.get("rows") or ()),
            entries,
        )
    if isinstance(shard, dict):
        return str(shard.get("shard") or ""), None
    try:
        own = _shard.shard_of(leg)
    except ValueError:
        return "", None
    return ("" if own == _shard.ONE else str(own)), None


@dataclass(frozen=True)
class DurationsRow:
    """What a platform leg's durations series holds for one run.

    Attributes:
        ref: The series' ref, ``durations/<platform leg>`` under the
            store's namespace.
        name: The row's file name, the run's.
        files: The seconds per test file the row holds; ``None`` when
            the series holds no row of the run.
    """

    ref: str
    name: str
    files: dict[str, float] | None


def durations_row(root: Path, run_id: int | str, leg: str) -> DurationsRow:
    """The durations row *run_id*'s gate job wrote on *leg*'s platform, fetched fresh.

    The series is the platform leg's ([livery.workshop.platform_leg][]),
    so a shard's label reads its platform's. Fails naming the series
    when it cannot be read; a series without the run's row answers
    ``files`` as ``None``.
    """
    series = _shard.DURATIONS.series(_shard.platform_leg(leg))
    name = _metrics.run_file(str(run_id))
    store = state_store(root)
    with git.remote_snapshot(store, fetch=(series.name,)):
        found = series.rows(store, only=name.__eq__)
    if found.failed:
        fail(f"run {run_id}: {series.ref} could not be read: {found.reason}")
    row = next((item for item in found.rows if item.name == name), None)
    if row is None:
        return DurationsRow(series.ref, name, None)
    files = {
        str(path): float(seconds)
        for path, seconds in dict(row.data.get("files") or {}).items()
        if isinstance(seconds, int | float) and not isinstance(seconds, bool)
    }
    return DurationsRow(series.ref, name, files)


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
    by its moment and closure as well, and a fixed-facts file under
    ``facts/`` are not units of the record. The rows are read raw and
    four fields taken, so the loop carries neither the coverage
    extension's schema number nor its parser. Fails naming the ref when
    it cannot be read; an absent record is empty.
    """
    series = COVERAGE_RECORD.series(base, leg)
    store = state_store(root)

    def current(name: str) -> bool:
        return name.count(".") == 1 and "/" not in name

    with git.remote_snapshot(store, fetch=(series.name,)):
        found = git.read(store, series.ref, only=current)
    if found.failed:
        fail(f"{series.ref} could not be read: {found.reason}")
    units: dict[str, UnitRow] = {}
    for _name, text in sorted((found.files or {}).items()):
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
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
