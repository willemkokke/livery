"""The check leg's half of coverage: what each suite reached, left for the leg's write.

A check leg's tests run metered, one data file per process. At the
leg's end the coverage extension's entry, ``coverage.leg``, first runs
the suites no record can supply at their current closure, when the
gate ran narrowed or skipped
([livery.extensions.coverage._leg.top_up][]); then it splits the
leg's data per unit, a package's suite or the workspace's own tests,
each unit's arcs within its closure, and leaves them with the scope
the leg measured for the leg's one write
([livery.extensions.coverage._leg.combine_leg][]), which puts them on
the leg's per-run ref beside its timing row. The data then combines
into the leg's ``.coverage``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import livery.toolroom.tools as tools
from livery import git
from livery.extensions.coverage._lines import measures_lines, pairs, read_parts
from livery.footman import fail
from livery.workshop._packages import WORKSPACE_TESTS, workspace_suite

if TYPE_CHECKING:
    from livery.workshop._ci_context import RunContext
    from livery.workshop._packages import Package

#: coverage.py's reading, the measurer of every python suite and the
#: workspace's own tests.
ARCS_MEASURER = "arcs"


#: The scope a leg's file names when the gate proved the tree or
#: judged nothing, and the leg then ran the named suites for their
#: lines alone: no record held them at their current closure.
MEASURED = "measured"


def suites_of(packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The packages with a test directory: the units the record keys."""
    return tuple(
        package for package in packages if (package.directory / "tests").is_dir()
    )


def units_of(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The recorded units: every package's suite, then the workspace's own tests."""
    unit = workspace_suite(root)
    return suites_of(packages) + ((unit,) if unit is not None else ())


def suite_arcs_by_context(
    data: Path, packages: tuple[Package, ...], package: Package, *, root: Path
) -> dict[str, list[tuple[int, int]]]:
    """The arcs *package*'s suite recorded in the leg's data, for its closure's files.

    Two sources make the suite's arcs: the contexts of its own tests
    (the node ids under ``<package>/tests/``, as the workshop's pytest
    plugin names them), and the arcs recorded under no context at all,
    which run at import time before any test and belong to whichever
    suites' closures hold their files. Files outside the closure are
    not the suite's to store. The names are workspace-relative,
    whichever way the meter stored them. An arc is a pair of line
    numbers, so the row carries statements and branches alike.
    """
    import re

    from coverage import CoverageData

    from livery.extensions.coverage._record import in_closure

    measured = CoverageData(basename=str(data))
    measured.read()
    files: dict[str, list[tuple[int, int]]] = {}
    own = (
        rf"^{re.escape(package.path)}/"
        if package.path == WORKSPACE_TESTS
        else rf"^{re.escape(package.path)}/tests/"
    )
    for query in ([own], [r"^$"]):
        measured.set_query_contexts(query)
        for filename in measured.measured_files():
            name = workspace_name(filename, root)
            if name is None or not in_closure(packages, package, name):
                continue
            arcs = measured.arcs(filename) or []
            if arcs:
                found = {(a, b) for a, b in arcs}
                files[name] = sorted(set(files.get(name, [])) | found)
    measured.set_query_contexts(None)
    return files


def suites_that_ran(
    marker: dict[str, Any], root: Path, packages: tuple[Package, ...]
) -> tuple[Package, ...]:
    """The units the leg ran, by its scope marker.

    Every suite after a full gate, the named packages' suites after a
    narrowed or a measured one, none after a skip; the workspace's
    own tests whenever any suite ran, since every leg that runs a
    suite runs them. The marker decides, never the data's contexts: a
    suite whose tests reach no line that collection had not already
    reached leaves no context of its own under a first-hit tracer,
    and still ran.
    """
    from livery.workshop._verified import AFFECTED, FULL

    scope = marker["scope"]
    if scope == FULL:
        ran = suites_of(packages)
    elif scope in (AFFECTED, MEASURED):
        named = set(marker["packages"])
        ran = tuple(package for package in suites_of(packages) if package.path in named)
    else:
        return ()
    unit = workspace_suite(root)
    return ran + ((unit,) if unit is not None else ())


def workspace_name(filename: str, root: Path) -> str | None:
    """*filename* relative to *root*, with slashes; None outside *root*."""
    name = filename.replace("\\", "/")
    if Path(name).is_absolute():
        try:
            return Path(name).relative_to(root).as_posix()
        except ValueError:
            return None
    return name


#: The leg's own combined data, read for the split and never uploaded:
#: named outside the ``.coverage.*`` glob the leg's combine consumes.
SUITES_DATA = "coverage-suites.db"


def _leave_leg(root: Path, marker: dict[str, Any], units: dict[str, Any]) -> None:
    """Leave the leg's scope and *units* for its one write; red when it cannot.

    Outside CI nothing is left and the line says so. Inside CI a leg
    that cannot leave its lines is red: the union would otherwise lack
    a suite, and a smaller union never passes. The leg's last entry,
    ``ci.leg``, puts the file on its per-run ref beside its timing row
    ([livery.workshop._metrics.put_leg][]).
    """
    from livery.extensions.coverage._record import leave_run
    from livery.workshop._ci_context import run_context

    run = run_context()
    leg = marker["leg"]
    if run is None:
        print(
            f"  coverage store: {len(units)} unit(s) measured; outside CI nothing"
            " is stored"
        )
        return
    why = leave_run(
        root,
        run,
        leg=leg,
        scope=marker["scope"],
        packages=tuple(marker["packages"]),
        units=units,
    )
    if why:
        fail(
            f"the leg's lines could not be left for its per-run ref ({why}): a"
            " leg that measured a suite and cannot store its lines is red, never"
            " a smaller union"
        )
    print(
        f"  coverage store: {len(units)} unit(s) for the run's ref of"
        f" {leg or 'an unlabelled leg'} ({marker['scope']})"
    )


def store_suites(
    root: Path, packages: tuple[Package, ...], *, marker: dict[str, Any]
) -> None:
    """Split the leg's data per unit and leave every unit for the leg's per-run ref.

    The leg's parts combine into `SUITES_DATA` first (kept apart from
    the leg's own combine); each unit the marker says ran is split out
    and its lines within its closure ride the per-run ref under the
    unit's closure identity, with the scope the leg measured. Only a
    CI run leaves them; a leg that cannot is red.
    """
    from coverage import CoverageData

    from livery.extensions.coverage._record import LINES, Unit, closure_id
    from livery.workshop._ci_context import run_context

    leg = marker["leg"]
    parts = sorted(root.glob(".coverage.*"))
    lines_parts = read_parts(root)
    if not parts and not lines_parts:
        return
    if parts:
        combined = CoverageData(basename=str(root / SUITES_DATA))
        for part in parts:
            piece = CoverageData(basename=str(part))
            piece.read()
            combined.update(piece)
        combined.write()
    run = run_context()
    measured: dict[str, Unit] = {}
    for package in suites_that_ran(marker, root, packages):
        measurer = LINES if measures_lines(package) else ARCS_MEASURER
        if measurer == LINES:
            lines = lines_parts.get(package.path)
            if lines is None:
                print(
                    f"  coverage store: {package.path} ran without a measurement;"
                    " nothing to store for it"
                )
                continue
            files = pairs(lines)
        elif parts:
            files = suite_arcs_by_context(
                root / SUITES_DATA, packages, package, root=root
            )
        else:
            continue
        if run is None:
            print(
                f"  coverage store: {package.path} measured ({len(files)} files);"
                " outside CI nothing is stored"
            )
            continue
        key = closure_id(root, packages, package)
        measured[package.path] = Unit(
            package.path, key, run.run_id, git.head_sha(root), files, measurer=measurer
        )
    (root / SUITES_DATA).unlink(missing_ok=True)
    if run is None:
        return
    _leave_leg(root, marker, measured)
    for unit in measured.values():
        print(
            f"  coverage store: {unit.path} stored for closure {unit.closure[:12]}"
            f" on {leg} ({len(unit.files)} files)"
        )


def combine_leg(
    root: Path,
    packages: tuple[Package, ...],
    *,
    marker: dict[str, Any] | None = None,
) -> None:
    """Combine the leg's data into ``.coverage``, each suite's lines left first.

    Every suite the leg ran is combined apart and left for the leg's
    per-run ref under the leg's label (the marker's), so the gate
    job's union reads it there; then every data file combines into
    the leg's ``.coverage``. *marker* is the scope the leg measured,
    the gate's own marker when absent. Refuses when a leg that ran its
    gate left no data, naming the variable that arms the meter; a leg
    whose gate skipped (a tree already proved, or nothing affected)
    legitimately measured nothing, says so, and leaves its scope
    alone, so the union knows the leg skipped rather than died. A
    workspace with no packages (a project just born) runs its own
    tests unmetered, and they reach no package source; that leg leaves
    its units with no lines, so the union finds what the leg ran.
    """
    from livery.extensions.coverage._union import _unmetered
    from livery.workshop._verified import NOTHING, VERIFIED, read_marker

    parts = sorted(root.glob(".coverage.*"))
    marker = read_marker(root) if marker is None else marker
    scope = marker["scope"]
    if not parts and not read_parts(root) and not (root / ".coverage").is_file():
        if scope in (VERIFIED, NOTHING):
            # A skipped leg measured nothing and names no unit, so the
            # union carries every unit from the records.
            print(f"  coverage: no data, the gate ran {scope!r}; nothing to combine")
            _leave_leg(root, marker, {})
            return
        if not packages:
            # The workspace's own tests ran, unmetered, and reached no
            # package source: each unit the leg ran is left with no
            # lines, so the union finds what ran and judges nothing.
            from livery.extensions.coverage._record import Unit, closure_id
            from livery.workshop._ci_context import run_context

            print(
                "  coverage: the workspace has no packages to measure; its own"
                " tests ran unmetered"
            )
            run = run_context()
            empty: dict[str, Unit] = {}
            if run is not None:
                for unit in units_of(root, ()):
                    empty[unit.path] = Unit(
                        unit.path,
                        closure_id(root, (), unit),
                        run.run_id,
                        git.head_sha(root),
                        {},
                    )
            _leave_leg(root, marker, empty)
            return
        fail(
            "this leg left no coverage data: nothing was metered. Inside CI"
            " the test runner arms COVERAGE_PROCESS_START in pytest's"
            " environment, so the tests and every process they start are"
            " metered; a leg that ran its gate and left no data ran no"
            " metered pytest, and there is nothing to union."
        )
    store_suites(root, packages, marker=marker)
    if parts:
        result = tools.coverage.opts(
            cwd=root, env=_unmetered(), nofail=True, recorded=False
        )("combine")
        if result.code != 0:
            fail(
                f"coverage combine exited {result.code}:\n"
                f"{result.stdout}{result.stderr}"
            )
    print(f"  coverage: {len(parts)} data file(s) combined into .coverage")


def unrecorded(
    root: Path,
    run: RunContext,
    packages: tuple[Package, ...],
    units: tuple[Package, ...],
    *,
    bases: tuple[str, ...],
) -> tuple[Package, ...]:
    """Of *units*, those no record in *bases* can supply on this leg; each says why.

    One read per record. A unit a record holds at its current closure
    identity is supplied, the branch's record asked before main's; a
    unit every record lacks or holds at another closure, an unreadable
    record, a leg without a label, or a closure git cannot identify
    runs the suite fresh and says why, so the union never lacks a
    suite and the records need no backfill.
    """
    from livery.extensions.coverage._record import closure_id, recorded

    if not units:
        return ()
    if not run.leg:
        for unit in units:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (this leg"
                " has no label)"
            )
        return units
    held = {base: recorded(root, leg=run.leg, base=base) for base in bases}
    out: list[Package] = []
    for unit in units:
        try:
            key = closure_id(root, packages, unit)
        except git.GitError as error:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (its closure"
                f" has no identity: {error})"
            )
            out.append(unit)
            continue
        states: list[str] = []
        for base in bases:
            record = held[base]
            if record.failed:
                states.append(f"{base}'s record could not be read ({record.reason})")
                continue
            row = record.units.get(unit.path)
            if row is not None and row.closure == key:
                break
            states.append(
                f"{base}'s record holds it at closure {row.closure[:12]}, not"
                f" {key[:12]}"
                if row is not None
                else f"{base}'s record holds no measurement of it"
            )
        else:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (on this leg"
                f" {', '.join(states)})"
            )
            out.append(unit)
    return tuple(out)


def with_unstored_suites(
    root: Path,
    run: RunContext,
    packages: tuple[Package, ...],
    subset: tuple[Package, ...],
) -> tuple[Package, ...]:
    """*subset* plus every suite no record can supply for this leg.

    A leg skips a suite only when its branch's record or main's holds
    the suite's lines at its current closure on this leg; the rest run
    fresh, and the line says why.
    """
    from livery.extensions.coverage._record import record_bases

    kept = {package.path for package in subset}
    skipped = tuple(
        package for package in suites_of(packages) if package.path not in kept
    )
    bases = record_bases(run.head_ref)
    extra = {
        unit.path for unit in unrecorded(root, run, packages, skipped, bases=bases)
    }
    if not extra:
        return subset
    # The workspace's own tests ride the subset as a unit of their own,
    # outside the packages, and stay in it.
    unit = tuple(package for package in subset if package.path == WORKSPACE_TESTS)
    return tuple(package for package in packages if package.path in kept | extra) + unit


def top_up(
    root: Path, packages: tuple[Package, ...], marker: dict[str, Any]
) -> dict[str, Any]:
    """Run the suites no record can supply on this leg; the scope the leg measured.

    Inside CI alone, after the gate: a full gate ran every suite, and a
    machine's run has no record to read. A narrowed gate
    (``affected``) ran the suites its marker names, and every other
    suite is asked of the branch's record and main's
    ([livery.extensions.coverage._leg.with_unstored_suites][]); a gate
    that judged nothing (``nothing``) asks the same of every suite; a
    gate that skipped a proved tree (``verified``) asks it of every
    unit a listed test step runs, the workspace's own tests among
    them, the record of the branch the verified row names first. The
    units no record holds at their current closure run through the
    test phase's steps ([livery.workshop.run_phase_over][]), measured,
    and the scope names them: a narrowed leg's packages gain them, and
    a skipped leg's scope becomes ``measured``, naming them. A leg with
    nothing to run keeps its scope.
    """
    from livery.workshop._ci_context import run_context, state_store
    from livery.workshop._verified import AFFECTED, NOTHING, VERIFIED

    run = run_context()
    scope = marker["scope"]
    if run is None or scope not in (AFFECTED, NOTHING, VERIFIED):
        return marker
    from livery.extensions.coverage._record import record_bases
    from livery.git import remote_snapshot
    from livery.workshop._steps import tested

    named = set(marker["packages"])
    # The verified row and every record a unit may come from: one
    # listing and one fetch.
    with remote_snapshot(state_store(root), fetch=("verified", "coverage/")):
        if scope == VERIFIED:
            units = unrecorded(
                root,
                run,
                packages,
                tested(units_of(root, packages)),
                bases=record_bases(_proving_branch(root)),
            )
            widened = units
        else:
            subset = tuple(package for package in packages if package.path in named)
            unit = workspace_suite(root)
            if unit is not None and unit.path in named:
                subset += (unit,)
            widened = with_unstored_suites(root, run, packages, subset)
            units = tuple(package for package in widened if package.path not in named)
    if not units:
        return marker
    from livery.workshop import run_phase_over

    names = ", ".join(unit.path for unit in units)
    proved = "; the gate is proved" if scope == VERIFIED else ""
    print(f"  measuring: {names} run for their lines alone{proved}")
    run_phase_over("test", units)
    if scope == AFFECTED:
        return {**marker, "packages": [package.path for package in widened]}
    return {**marker, "scope": MEASURED, "packages": [unit.path for unit in units]}


def _proving_branch(root: Path) -> str:
    """The branch the verified row names for this checkout's tree; empty when none.

    An unreadable row, or a tree git cannot name, says why, and main's
    record alone is read.
    """
    from livery.workshop._verified import record, tree_id

    try:
        row, why = record(root, tree_id(root))
    except git.GitError as error:
        print(f"  coverage store: the tree has no id ({error}); main's record alone")
        return ""
    if why:
        print(
            f"  coverage store: the verified row is unread ({why}); main's record alone"
        )
        return ""
    return row.branch if row is not None else ""
