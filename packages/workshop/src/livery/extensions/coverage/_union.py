"""The gate job's half of coverage: every leg's lines and the records, as one union.

The gate job's judge, ``coverage.union``, reads what each check leg
left on its per-run ref, carries every unit a leg did not measure from
the records, combines them into the workspace's ``.coverage`` and the
native packages' lines parts, and writes the union back onto the
branch's record or main's
([livery.extensions.coverage._union.combine_union][]). The floors are
then judged on that one union
([livery.extensions.coverage._floors.enforce_coverage][]).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import livery.toolroom.tools as tools
from livery import git
from livery.extensions.coverage._leg import MEASURED, suites_that_ran, units_of
from livery.footman import fail

if TYPE_CHECKING:
    from livery.extensions.coverage._record import Record
    from livery.workshop._ci_context import RunContext
    from livery.workshop._packages import Package

#: The gate job's scratch directory for the union's inputs: one
#: directory per leg, the leg's fresh units and the units carried from
#: main's record as coverage data files. Ignored by the template's
#: gitignore, written afresh by every union.
COVERAGE_DATA = "coverage-data"


def _unmetered() -> dict[str, str]:
    """The environment for a coverage CLI call on a named data file.

    Ambient ``COVERAGE_*`` and ``COV_CORE_*`` variables, from a shell
    that armed the meter or a pytest-cov parent, would re-point the
    coverage CLI at that process's live data file and meter the CLI
    process itself; scrubbing them keeps the call on the files the
    caller named.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }


def _write_unit(
    folder: Path, name: str, files: dict[str, list[tuple[int, int]]]
) -> Path:
    """One unit's arcs as a coverage data file under *folder*; the path."""
    from coverage import CoverageData

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    data = CoverageData(basename=str(path))
    data.add_arcs(
        {file: {(arc[0], arc[1]) for arc in arcs} for file, arcs in files.items()}
    )
    data.write()
    return path


#: The scope of a leg a pull request did not run: every suite carried.
ABSENT = "absent"


def expected_legs(root: Path, run: RunContext) -> tuple[list[str], list[str]]:
    """The check legs the gate produces, and the ones *run*'s event runs.

    A pull request runs the legs ``[ci] pull-request-runners`` names;
    every other event runs them all. Both empty without a root
    contract, as a test workspace may be.
    """
    from livery.workshop._points import check_legs, pull_request_legs

    if not (root / "workshop.toml").is_file():
        return [], []
    every = check_legs(root)
    due = pull_request_legs(root) if run.event == "pull_request" else every
    return every, due


def combine_union(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """Union the run's legs' lines with the records; the packages it judges.

    Each check leg's per-run ref carries the scope its gate ran and
    the units it measured. A full leg measured every suite, a narrowed
    or measured leg the suites its marker names; a leg whose gate
    skipped (a tree already proved, or nothing affected) measured
    nothing. Every unit a leg did not measure is carried from a record
    on that leg, the branch's own before main's, at the unit's current
    closure identity, so the union is the same global union a full run
    produces and every package is judged. A unit no record can supply
    refuses by name: a leg skips a suite only when a record holds it,
    so a miss here is a leg that narrowed without the records, or a
    record moved on since, never a smaller union.

    The union is written back per leg: a pull request's run onto its
    branch's record, main's run onto main's. The leg's fresh units
    replace their rows, the rows carried from the other record are
    copied in, the target's own carried rows stand, and the rows of
    units that no longer exist go. On main's run the branch is the one
    the verified record names for the tree, so the merge copies the
    branch's record into main's. A record that cannot be written
    prints why: the next run reruns what it cannot reuse, the safe
    direction.

    A leg the contract names and a pull request did not run, one
    outside ``[ci] pull-request-runners``, is carried whole from the
    records, so the narrowed run judges the same global union and a
    line only another platform reaches stays covered by main's
    measurement of it.

    Refuses by name when no leg left its lines, when a leg's ref
    carries no readable file, when a leg names no scope the union
    reads, when a leg that ran a suite left its lines out, and when a
    unit is neither the run's nor any record's.

    Returns:
        The judged packages, in *packages* order; empty when the
        workspace has no unit to union, and then no union file is
        written.
    """
    from livery.extensions.coverage._lines import (
        from_pairs,
        merge,
        remove_parts,
        write_part,
    )
    from livery.extensions.coverage._record import (
        LINES,
        RUN_FILE,
        Leg,
        Unit,
        closure_id,
        put_record,
        row_name,
        run_legs,
    )
    from livery.workshop._ci_context import run_context
    from livery.workshop._verified import AFFECTED, FULL, NOTHING, VERIFIED

    run = run_context()
    if run is None:
        fail(
            "the union reads the run's per-run refs, and outside CI there is no"
            " run: the legs' lines are unioned and judged inside CI alone"
        )
    legs, why = run_legs(root, run)
    if why:
        fail(f"the run's per-run refs could not be read ({why}); nothing to union")
    if not legs:
        fail(
            f"no check leg left its lines on run {run.run_id}'s refs: every leg"
            f" puts its scope and its lines there ({RUN_FILE}) at its end, and"
            " a missing put is a red leg, never a smaller union"
        )
    every, due = expected_legs(root, run)
    present = {leg.label for leg in legs}
    for label in every:
        if label in present or label in due:
            continue
        print(
            f"  coverage: leg {label} did not run on this pull request"
            " ([ci] pull-request-runners); every suite carried from the records"
        )
        legs.append(Leg(label, label, ABSENT, (), {}))
    scratch = root / COVERAGE_DATA
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    units = units_of(root, packages)
    keys = {unit.path: closure_id(root, packages, unit) for unit in units}
    bases, target = _record_bases(root, run)
    collected: list[Path] = []
    reused: list[Path] = []
    # The lines units, merged per package across the legs and the
    # records; written as parts once every leg is read.
    lines_by_package: dict[str, dict[str, dict[int, int]]] = {}
    lines_reused = 0
    ran = 0
    writes: list[tuple[str, dict[str, Unit], dict[str, tuple[str, Unit]], Record]] = []
    for leg in sorted(legs, key=lambda item: item.key):
        if leg.why:
            fail(
                f"leg {leg.key}: {leg.why}; the leg died before its lines, or"
                " its put failed, and a missing put is a red leg, never a"
                " smaller union"
            )
        if not leg.label:
            fail(
                f"leg {leg.key} names no label, so no record can supply what it"
                " skipped; the job runner sets WORKSHOP_LEG for every leg"
            )
        if leg.scope in (VERIFIED, NOTHING):
            print(f"  coverage: leg {leg.label} ran {leg.scope!r}: no suite, no data")
        elif leg.scope == ABSENT:
            pass
        elif leg.scope in (FULL, AFFECTED, MEASURED):
            marker = {"scope": leg.scope, "packages": list(leg.packages), "leg": ""}
            expected = suites_that_ran(marker, root, packages)
            missing = [unit.path for unit in expected if unit.path not in leg.units]
            if missing:
                fail(
                    f"leg {leg.label} ran {leg.scope!r} and its ref lacks"
                    f" {', '.join(missing)}: a leg that ran a suite puts its"
                    " lines, and a missing put is a red leg, never a smaller union"
                )
            ran += 1
            for path, unit in sorted(leg.units.items()):
                if unit.measurer == LINES:
                    lines_by_package[path] = merge(
                        lines_by_package.get(path, {}), from_pairs(unit.files)
                    )
                    continue
                collected.append(
                    _write_unit(
                        scratch / leg.label,
                        f"fresh-{git.slug(path)}.coverage",
                        unit.files,
                    )
                )
        else:
            fail(
                f"leg {leg.label}: its ref names a scope the union does not read"
                f" ({leg.scope!r}); the check legs leave full, affected, nothing,"
                " verified, or measured"
            )
        pending = [unit for unit in units if unit.path not in leg.units]
        held: dict[str, Record] = {}
        carried: dict[str, tuple[str, Unit]] = {}
        for suite in pending:
            key = keys[suite.path]
            states: list[str] = []
            kept = ""
            for base in bases:
                record = _read_record(root, held, base, leg.label)
                row = record.at(suite.path, key)
                current = record.units.get(suite.path)
                if row is not None:
                    carried[suite.path] = (base, row)
                    if row is not current:
                        # The leg read the current row; a write since
                        # moved it on and kept it at this closure.
                        kept = ", a row kept after its record moved on"
                    break
                if leg.scope == ABSENT and current is not None:
                    # The leg did not run on this pull request, so no
                    # measurement of this closure exists on its
                    # platform: the record's last one stands in, and
                    # the merge point measures it afresh.
                    carried[suite.path] = (base, current)
                    kept = (
                        f", at closure {current.closure[:12]} since the leg did not run"
                    )
                    break
                states.append(
                    f"{base} only at {current.closure[:12]}"
                    if current is not None
                    else f"{base} none"
                )
            else:
                if leg.scope == ABSENT:
                    # The leg did not run and no record of any base has
                    # ever measured this suite on its platform: a package
                    # born on this branch. Nothing stands in, the legs that
                    # ran judge it, and the merge point measures it here.
                    print(
                        f"  coverage: leg {leg.label} has no record of"
                        f" {suite.path} yet; the merge point measures it"
                    )
                    continue
                fail(
                    f"leg {leg.label}: no record holds {suite.path} at closure"
                    f" {key[:12]} ({', '.join(states)}). A leg skips a suite"
                    " only when a record holds it at the suite's closure, and"
                    " a record keeps a row it moved on from for a day, so this"
                    " leg narrowed without the records, or the run outlived"
                    " that day; a run of the full gate measures it again."
                )
            base, row = carried[suite.path]
            if row.measurer == LINES:
                lines_by_package[suite.path] = merge(
                    lines_by_package.get(suite.path, {}), from_pairs(row.files)
                )
                lines_reused += 1
            else:
                reused.append(
                    _write_unit(
                        scratch / leg.label,
                        f"reuse-{git.slug(suite.path)}.coverage",
                        row.files,
                    )
                )
            print(
                f"  coverage: {suite.path} on {leg.label}: reused from run"
                f" {row.run} ({len(row.files)} files), {base}'s record{kept}"
            )
        if target:
            own = _read_record(root, held, target, leg.label)
            writes.append((leg.label, dict(leg.units), carried, own))
    if not collected and not reused and not lines_by_package:
        print("  coverage: no unit to union; nothing judged")
        return ()
    scrubbed = _unmetered()
    if collected or reused:
        result = tools.coverage.opts(
            cwd=root, env=scrubbed, nofail=True, recorded=False
        )("combine", *(str(path) for path in collected + reused))
        if result.code != 0:
            fail(
                f"coverage combine exited {result.code}:\n"
                f"{result.stdout}{result.stderr}"
            )
        report = tools.coverage.opts(
            cwd=root, env=scrubbed, nofail=True, recorded=False
        )("report", "--sort=cover")
        print(report.stdout.rstrip())
    remove_parts(root)
    for path, lines in sorted(lines_by_package.items()):
        write_part(root, path, lines)
    if lines_by_package:
        print(f"  coverage: {len(lines_by_package)} native package(s) unioned by lines")
    print(
        f"  coverage: the union of {ran} leg(s) and {len(reused) + lines_reused}"
        " reused suite(s)"
    )
    if not target:
        print(
            f"  coverage record: not written, a {run.event or 'local'} run"
            " that names no branch has no record of its own"
        )
        return tuple(packages)
    # Two runs of one branch overlap when a push follows a push: the
    # older run's gate job can finish last, and its rows would replace
    # the newer run's. Only the run of the branch's current head writes.
    moved, head = _moved_on(root, run, target)
    if moved:
        print(
            f"  coverage record: {target}'s head moved on to {moved[:12]} since"
            f" this run's {head[:12]}; not written, the newer run writes"
        )
        return tuple(packages)
    for label, fresh, carried, own in writes:
        stale = own.stale(keys)
        # The rows carried from another record are copied in; the
        # target's own carried rows already stand. A stale row a new
        # row replaces by name (a row of an older shape, say) is
        # replaced, not removed: only the rest go.
        rows = dict(fresh)
        rows.update(
            {path: row for path, (base, row) in carried.items() if base != target}
        )
        removed = [name for name in stale if name not in {row_name(p) for p in rows}]
        if not rows and not stale:
            print(
                f"  coverage record: {target}/{label}: unchanged, {len(carried)}"
                " carried"
            )
            continue
        why = put_record(
            root, run, leg=label, fresh=rows, remove=stale, base=target, held=own
        )
        if why:
            print(
                f"  coverage record: {target}/{label} not written ({why}); the"
                " next run reruns what it cannot reuse"
            )
            continue
        print(
            f"  coverage record: {target}/{label}: {len(fresh)} fresh,"
            f" {len(carried)} carried, {len(removed)} removed"
        )
    return tuple(packages)


def _read_record(root: Path, held: dict[str, Record], base: str, label: str) -> Record:
    """*base*'s record on the leg *label*, read once into *held*; red if unreadable."""
    from livery.extensions.coverage._record import recorded

    if base not in held:
        held[base] = recorded(root, leg=label, base=base)
        if held[base].failed:
            fail(
                f"{base}'s record on {label} could not be read"
                f" ({held[base].reason}); the suites the leg skipped cannot be"
                " supplied, and a smaller union never passes"
            )
        for skipped in held[base].skipped:
            print(f"  coverage: {base}/{label}: {skipped}")
    return held[base]


def _moved_on(root: Path, run: RunContext, branch: str) -> tuple[str, str]:
    """*branch*'s head on origin and this run's, when they differ; empty otherwise.

    This run's head is the pull request's on a pull request and the
    checkout on a push. Empty too when origin cannot be asked: an
    answer the run cannot get never withholds a write on its own.
    """
    try:
        head = run.head_sha if run.event == "pull_request" else git.head_sha(root)
        git.fetch(root)
        current = git.remote_head(root, branch)
    except git.GitError:
        return "", ""
    return (current, head) if current and current != head else ("", "")


def _record_bases(root: Path, run: RunContext) -> tuple[tuple[str, ...], str]:
    """The records the union carries from, in order, and the one it writes.

    A pull request's run reads its branch's record before main's and
    writes its branch's; a run naming no branch reads main's and
    writes nothing. Main's run reads the record of the branch the
    verified row names for its tree, the branch just merged, before
    main's, and writes main's: that is the copy at the merge. A push
    of a tree the record does not name, a stale squash, reads main's
    alone, and its full gate measured everything anyway.
    """
    from livery.extensions.coverage._record import MAIN, record_bases
    from livery.workshop._verified import record, tree_id

    if run.event == "push":
        row, why = record(root, tree_id(root))
        branch = row.branch if row is not None else ""
        if why:
            print(f"  coverage record: the verified row could not be read ({why})")
        elif branch:
            print(
                f"  coverage record: {MAIN} takes {branch}'s record for the tree"
                " it proved"
            )
        return record_bases(branch), MAIN
    if run.event == "pull_request" and run.head_ref:
        return record_bases(run.head_ref), run.head_ref
    return (MAIN,), ""
