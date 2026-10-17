"""The check leg's half of coverage: each suite's parts, left for the leg's write.

A check leg's tests run measured. At the leg's end the coverage
extension's entry, ``coverage.leg``, first runs the suites no record
can supply at their current closure, when the gate ran narrowed or
skipped (`top_up`). Then it gathers the parts of every suite the leg
ran (`gather_leg`): the parts a native suite's measurer left in the
workspace, and the parts the listed reductions make of the data the
run left, once for each suite, each holding the files of the suite's
closure. It leaves them in the line format's stored form, with the
fixed facts they name and the scope the leg measured, for the leg's
one write, which puts them on the leg's per-run ref beside its timing
row, and it prints each suite's size as a record stores it, what ran
and its fixed facts, raw and gzipped, with the share of what ran that
names the tests that reached each line.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from livery import git
from livery.extensions.coverage._format import (
    Part,
    PartError,
    decode,
    facts_text,
    stored,
)
from livery.footman import fail
from livery.workshop import WORKSPACE_TESTS

if TYPE_CHECKING:
    from livery.extensions.coverage._record import Unit
    from livery.workshop import LegScope, Package, RunContext


#: The scope a leg's file names when the gate proved the tree or
#: judged nothing, and the leg then ran the named suites for their
#: parts alone: no record held them at their current closure.
MEASURED = "measured"


def suites_of(packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The packages with a test directory: the units the record keys."""
    return tuple(
        package for package in packages if (package.directory / "tests").is_dir()
    )


def units_of(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The recorded units: every package's suite, then the workspace's own tests.

    The workspace's own tests are a unit while a test step runs them
    (`own_tests`).
    """
    return suites_of(packages) + own_tests(root)


def own_tests(root: Path) -> tuple[Package, ...]:
    """The workspace's own tests, as a unit a test step runs; none otherwise.

    The unit ([livery.workshop.workspace_suite][]) holds the root's
    set, and a test step runs it while that set holds an extension the
    step names ([livery.workshop.tested][]), the rule the test step
    itself asks ([livery.workshop.scoped_suite][]). A root whose set
    holds no such extension runs none of its own tests, so no leg
    leaves a part for them and no record holds them.
    """
    from livery.workshop import tested, workspace_suite

    unit = workspace_suite(root)
    return tested((unit,)) if unit is not None else ()


def suites_that_ran(
    marker: LegScope, root: Path, packages: tuple[Package, ...]
) -> tuple[Package, ...]:
    """The units the leg ran, by its scope marker and its shard's record.

    Every suite after a full gate, the named packages' suites after a
    narrowed or a measured one, none after a skip; the workspace's
    own tests whenever any suite ran and a test step runs them
    (`own_tests`), since every leg that runs a suite runs them then.
    On a shard of a sharded leg, those among them with a test file in
    the shard's share (`on_this_shard`). The marker decides, never the
    data's contexts: a suite whose tests reach no line that collection
    had not already reached leaves no context of its own under a
    first-hit tracer, and still ran.
    """
    scope = marker.scope
    if scope == "full":
        ran = suites_of(packages)
    elif scope in ("affected", MEASURED):
        named = set(marker.packages)
        ran = tuple(package for package in suites_of(packages) if package.path in named)
    else:
        return ()
    return on_this_shard(root, ran + own_tests(root))


def on_this_shard(root: Path, units: tuple[Package, ...]) -> tuple[Package, ...]:
    """*units* less those that did not run on this shard; all on one shard of one.

    A sharded check leg's test step runs each suite's share of its test
    files and records them as the items it ran, with the suites it
    partitioned ([livery.workshop.read_shard][]). A suite the record
    names ran here where a file of it is among the items; one with no
    file among them ran on another shard, whose parts the gate job
    folds in before the union reads. A suite the record does not name,
    a native suite with no python test file, ran whole on the first
    shard with every step that does not read the shard, so the first
    shard expects its parts and every other shard does not. Without a
    record, or on one shard of one, every unit ran whole here.

    Raises:
        Failed: When the record does not read, naming why: a sharded
            leg's parts are judged by the files its shard ran.
    """
    from livery.workshop import read_shard

    record, why = read_shard(root)
    if why:
        fail(
            f"coverage: {why}; a sharded leg's parts are judged by the test files"
            " its shard ran, and this leg's record does not read"
        )
    if record is None or record.shard.count == 1:
        return units
    partitioned = set(record.suites)
    return tuple(
        unit
        for unit in units
        if (
            any(_of_unit(unit, name) for name in record.items)
            if unit.path in partitioned
            else record.shard.first
        )
    )


def _of_unit(unit: Package, name: str) -> bool:
    """Whether the test file *name*, root-relative, is one of *unit*'s own tests."""
    base = unit.path if unit.path == WORKSPACE_TESTS else f"{unit.path}/tests"
    return name.startswith(f"{base}/")


def request(packages: tuple[Package, ...], unit: Package) -> dict[str, Any]:
    """What a reduction is asked for *unit*: its tests' contexts and its closure.

    A package's own tests are the node ids under its ``tests/``
    directory, as the pytest extension's plugin names each test's
    context; the workspace's own tests are every node id under
    ``tests/``, and their closure is every file.
    """
    from livery.extensions.coverage._record import closure

    if unit.path == WORKSPACE_TESTS:
        return {"unit": unit.path, "tests": f"{unit.path}/"}
    return {
        "unit": unit.path,
        "tests": f"{unit.path}/tests/",
        "within": [f"{member.path}/" for member in closure(packages, unit)],
    }


def sizes(unit: Unit) -> tuple[int, int, int, int, int, int]:
    """*unit* as a record stores it: its row's bytes, the tests' share, its facts'.

    Each raw, then gzipped at zlib's default level, the level git
    stores an object at: the row; what the tests that reached each
    line add to it, the row's size less its size without its parts'
    contexts and each file's ``reached``; and the fixed facts, one
    file each as the record keeps them.
    """
    row = json.dumps(list(unit.parts), sort_keys=True).encode("utf-8")
    bare = json.dumps(
        [_without_tests(part) for part in unit.parts], sort_keys=True
    ).encode("utf-8")
    packed = _gzipped(row)
    facts = [
        facts_text(unit.facts[name]).encode("utf-8")
        for name in unit.named
        if name in unit.facts
    ]
    return (
        len(row),
        packed,
        len(row) - len(bare),
        max(0, packed - _gzipped(bare)),
        sum(len(item) for item in facts),
        sum(_gzipped(item) for item in facts),
    )


def _without_tests(part: Mapping[str, Any]) -> dict[str, Any]:
    """*part*, as a row holds it, without the tests that reached each line."""
    files: object = part.get("files")
    held = cast("dict[str, Any]", files) if isinstance(files, dict) else {}
    return {
        **part,
        "contexts": [],
        "files": {
            name: {
                key: value
                for key, value in cast("dict[str, Any]", entry).items()
                if key != "reached"
            }
            if isinstance(entry, dict)
            else entry
            for name, entry in held.items()
        },
    }


def _gzipped(content: bytes) -> int:
    """How many bytes *content* takes gzipped at zlib's default level."""
    return len(gzip.compress(content, compresslevel=6, mtime=0))


def shown_sizes(unit: Unit) -> str:
    """*unit*'s size as a line shows it: what ran, its fixed facts, the tests' share."""
    row, packed, tests, tests_packed, facts, facts_packed = sizes(unit)
    share = (
        f"; the tests that reached each line take {shown_size(tests)} raw,"
        f" {shown_size(tests_packed)} gzipped of what ran"
        if tests
        else ""
    )
    return (
        f"{shown_size(row)} raw, {shown_size(packed)} gzipped, and its fixed facts"
        f" {shown_size(facts)} raw, {shown_size(facts_packed)} gzipped{share}"
    )


def shown_size(count: int) -> str:
    """*count* bytes as a line shows them: ``1.3 MB``, ``412.0 KB``."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} MB"
    return f"{count / 1_000:.1f} KB"


def _leave_leg(root: Path, marker: LegScope, units: dict[str, Unit]) -> None:
    """Leave the leg's scope and *units* for its one write; red when it cannot.

    Outside CI nothing is left and the line says so. Inside CI a leg
    that cannot leave its parts is red: the union would otherwise lack
    a suite, and a smaller union never passes. The file goes to the
    leg's spool ([livery.workshop.leave_leg_file][]), and the leg's
    last entry, ``ci.leg``, puts it on its per-run ref beside its
    timing row. The lines name the size as
    stored, raw and gzipped: the leg's whole file, its fixed facts each
    once, then each unit's row and the fixed facts it names.
    """
    from livery.extensions.coverage._record import leave_run, unit_fields, units_facts
    from livery.workshop import ci_run

    run = ci_run()
    leg = marker.leg
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
        scope=marker.scope,
        packages=marker.packages,
        units=units,
    )
    if why:
        fail(
            f"the leg's parts could not be left for its per-run ref ({why}): a"
            " leg that measured a suite and cannot store its parts is red, never"
            " a smaller union"
        )
    whole = json.dumps(
        {
            "units": [unit_fields(unit) for unit in units.values()],
            "facts": units_facts(units.values()),
        },
        sort_keys=True,
    ).encode("utf-8")
    print(
        f"  coverage store: {len(units)} unit(s) for the run's ref of"
        f" {leg or 'an unlabelled leg'} ({marker.scope}),"
        f" {shown_size(len(whole))} raw, {shown_size(_gzipped(whole))} gzipped"
    )
    for path, unit in units.items():
        print(
            f"  coverage store: {path} stored for closure {unit.closure[:12]} on"
            f" {leg} ({len(unit.files)} files, {shown_sizes(unit)})"
        )


def _left_parts(root: Path) -> list[Part]:
    """The parts the measurers left in the workspace; one off the format is red.

    A part a measurer wrote is the leg's measurement of a suite: one
    that does not read is red, naming its file and the key at fault,
    since leaving it out would store a suite without its lines.
    """
    from livery.extensions.coverage._parts import part_files, read_part

    found: list[Part] = []
    for path in part_files(root):
        try:
            found.append(read_part(path, root))
        except PartError as error:
            fail(f"coverage: {error}")
    return found


def _reduced_parts(
    root: Path, packages: tuple[Package, ...], units: Sequence[Package]
) -> list[Part]:
    """The parts the listed reductions make of the leg's data, for *units*.

    Each reduction is asked once, for every unit at once, and answers
    one part per unit, in the order asked; a reduction that refuses,
    that answers another number of parts or another unit's part, or
    whose part is off the format, is red by name.
    """
    from livery.extensions.coverage._parts import MEASURERS, reduced
    from livery.workshop import answer

    if not units:
        return []
    asked = [request(packages, unit) for unit in units]
    found: list[Part] = []
    for name, reduction in sorted(answer(root, MEASURERS).items()):
        source = f"the {name} reduction of this leg's data"
        raw = reduced(partial(reduction.leg, root, asked), source)
        if len(raw) != len(asked):
            fail(
                f"coverage: {source} returned {len(raw)} part(s) for {len(asked)}"
                " suite(s); it returns one part per suite"
            )
        for unit, item in zip(units, raw, strict=True):
            try:
                part = decode(item, source=f"the {name} reduction of {unit.path}")
            except PartError as error:
                fail(f"coverage: {error}")
            if part.unit != unit.path:
                fail(
                    f"coverage: {source} answered a part of {part.unit} for"
                    f" {unit.path}; it answers one part per suite, in the order"
                    " asked"
                )
            found.append(part)
    return found


def _within(part: Part, packages: tuple[Package, ...], unit: Package) -> Part:
    """*part* with the files of *unit*'s closure alone."""
    from livery.extensions.coverage._record import in_closure

    files = {
        name: entry
        for name, entry in part.files.items()
        if in_closure(packages, unit, name)
    }
    return Part(part.unit, part.measurer, files, part.contexts)


def _stored_unit(
    parts: Sequence[Part], path: str, closure: str, run: str, sha: str
) -> Unit:
    """The unit at *path* holding *parts* in the stored form, with their facts."""
    from livery.extensions.coverage._record import Unit

    held: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    for part in parts:
        kept, named = stored(part)
        held.append(kept)
        facts.update(named)
    return Unit(path, closure, run, sha, tuple(held), facts)


def gather_leg(
    root: Path,
    packages: tuple[Package, ...],
    *,
    marker: LegScope | None = None,
) -> None:
    """Gather the parts of every suite the leg ran and leave them for its write.

    The parts are the ones a native suite's measurer left in the
    workspace and the ones the listed reductions make of the data the
    run left, asked for every other suite the leg ran; each suite's
    parts hold the files of its closure, and they ride the leg's
    per-run ref under the suite's closure identity, with the scope the
    leg measured, its size printed raw and gzipped. *marker* is the
    scope the leg measured, the gate's own marker when absent. A leg
    whose gate skipped (a tree already proved, or nothing affected)
    ran no suite, says so, and leaves its scope alone, so the union
    knows the leg skipped rather than died. A workspace with no
    packages (a project just born) runs its own tests unmetered, and
    they reach no package source; that leg leaves its units with no
    parts, so the union finds what the leg ran. A suite that ran and
    has no part is named and left out, and the union names it again;
    a leg whose suites ran and has no part of any is red, since nothing
    was measured. Only a CI run leaves the parts.
    """
    from livery.extensions.coverage._parts import native
    from livery.extensions.coverage._record import Unit, closure_id
    from livery.workshop import ci_run, leg_scope

    marker = leg_scope(root) if marker is None else marker
    scope = marker.scope
    ran = suites_that_ran(marker, root, packages)
    run = ci_run()
    if not ran:
        print(f"  coverage: the gate ran {scope!r}; no suite ran, nothing to gather")
        _leave_leg(root, marker, {})
        return
    if not packages:
        # The workspace's own tests ran, unmetered, and reached no
        # package source: each unit the leg ran is left with no parts,
        # so the union finds what ran and judges nothing.
        print(
            "  coverage: the workspace has no packages to measure; its own"
            " tests ran unmetered"
        )
        empty: dict[str, Unit] = {}
        if run is not None:
            for unit in ran:
                empty[unit.path] = Unit(
                    unit.path,
                    closure_id(root, (), unit),
                    run.run_id,
                    git.head_sha(root),
                )
        _leave_leg(root, marker, empty)
        return
    by_unit: dict[str, list[Part]] = {}
    left = _left_parts(root)
    reduced = _reduced_parts(root, packages, [unit for unit in ran if not native(unit)])
    for part in (*left, *reduced):
        by_unit.setdefault(part.unit, []).append(part)
    if not any(by_unit.get(unit.path) for unit in ran):
        fail(
            f"this leg ran {scope!r} and no measurer left a part of any suite it"
            " ran: nothing was measured. A measurer leaves its parts while"
            " coverage is listed and it measures for coverage, so a leg that ran"
            " its suites and left none ran no measured suite, and there is"
            " nothing to union."
        )
    measured: dict[str, Unit] = {}
    for unit in ran:
        parts = by_unit.get(unit.path)
        if not parts:
            print(
                f"  coverage store: {unit.path} ran without a measurement; nothing"
                " to store for it"
            )
            continue
        held = [_within(part, packages, unit) for part in parts]
        if run is None:
            found = _stored_unit(held, unit.path, "", "", "")
            print(
                f"  coverage store: {unit.path} measured ({len(found.files)} files,"
                f" {shown_sizes(found)}); outside CI nothing is stored"
            )
            continue
        key = closure_id(root, packages, unit)
        measured[unit.path] = _stored_unit(
            held, unit.path, key, run.run_id, git.head_sha(root)
        )
    if run is not None:
        _leave_leg(root, marker, measured)


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
    the suite's parts at its current closure on this leg; the rest run
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


def top_up(root: Path, packages: tuple[Package, ...], marker: LegScope) -> LegScope:
    """Run the suites no record can supply on this leg; the scope the leg measured.

    Inside CI alone, after the gate: a full gate ran every suite, and a
    machine's run has no record to read. A narrowed gate
    (``affected``) ran the suites its marker names, and every other
    suite is asked of the branch's record and main's
    (`with_unstored_suites`); a gate
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
    from livery.workshop import ci_run, state_store

    run = ci_run()
    scope = marker.scope
    if run is None or scope not in ("affected", "nothing", "verified"):
        return marker
    from livery.extensions.coverage._record import record_bases, record_prefixes
    from livery.git import remote_snapshot
    from livery.workshop import tested

    named = set(marker.packages)
    # The verified row and the records this leg reads, the head
    # branch's and main's: one listing and one fetch. A verified leg
    # reads the record of the branch the verified row names, known
    # only once that row is read; when that is another branch, its
    # first read brings the record, every leg of it, in one more fetch
    # (`remote_snapshot` brings a ref's whole key group), so it is not
    # missing from the prefixes here.
    with remote_snapshot(
        state_store(root), fetch=("verified", *record_prefixes(run.head_ref))
    ):
        if scope == "verified":
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
            subset += tuple(unit for unit in own_tests(root) if unit.path in named)
            widened = with_unstored_suites(root, run, packages, subset)
            units = tuple(package for package in widened if package.path not in named)
    if not units:
        return marker
    from livery.workshop import run_phase_over

    names = ", ".join(unit.path for unit in units)
    proved = "; the gate is proved" if scope == "verified" else ""
    print(f"  measuring: {names} run for their lines alone{proved}")
    run_phase_over("test", units)
    if scope == "affected":
        return replace(marker, packages=tuple(package.path for package in widened))
    return replace(marker, scope=MEASURED, packages=tuple(unit.path for unit in units))


def _proving_branch(root: Path) -> str:
    """The branch the verified row names for this checkout's tree; empty when none.

    An unreadable row, or a tree git cannot name, says why, and main's
    record alone is read.
    """
    from livery.workshop import verified_branch

    proving = verified_branch(root)
    if proving.reason:
        print(
            f"  coverage store: the verified row is unread ({proving.reason});"
            " main's record alone"
        )
    return proving.branch
