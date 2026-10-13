"""The gate job's half of coverage: every leg's parts and the records', as one union.

The gate job's judge, ``coverage.union``, reads what each check leg
left on its per-run ref, carries every unit a leg did not measure from
the records, merges every unit's parts by the line format's rules, and
writes the merged files to the workspace's union file, which the floors
then judge (`combine_union`). It writes the run's units back onto the
branch's record or main's.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from livery import git
from livery.extensions.coverage._leg import MEASURED, suites_that_ran, units_of
from livery.footman import fail

if TYPE_CHECKING:
    from livery.extensions.coverage._format import Part
    from livery.extensions.coverage._record import Record, Unit
    from livery.workshop._ci_context import RunContext
    from livery.workshop._packages import Package


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
    """Union the run's legs' parts with the records; the packages it judges.

    Each check leg's per-run ref carries the scope its gate ran and
    the units it measured, each unit's parts in the line format. A full
    leg measured every suite, a narrowed or measured leg the suites its
    marker names; a leg whose gate skipped (a tree already proved, or
    nothing affected) measured nothing. Every unit a leg did not
    measure is carried from a record on that leg, the branch's own
    before main's, at the unit's current closure identity, so the union
    is the same global union a full run produces and every package is
    judged. A unit no record can supply
    refuses by name: a leg skips a suite only when a record holds it,
    so a miss here is a leg that narrowed without the records, or a
    record moved on since, never a smaller union. Every unit's parts
    merge by the line format's rules: one file's readings merge at the
    content the checkout holds and by one measurer, and every reading
    left out is named. The merged files go to the union file, which the
    floors read; no union file outlives a run that did not write one.

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

    Refuses by name when no leg left its parts, when a leg's ref
    carries no readable file, when a leg names no scope the union
    reads, when a leg that ran a suite left its parts out, when a unit
    is neither the run's nor any record's, and when a part is off the
    line format, naming where it came from and the key.

    Returns:
        The judged packages, in *packages* order; empty when the
        workspace has no unit to union, and then no union file is
        written.
    """
    from livery.extensions.coverage._parts import UNION
    from livery.extensions.coverage._record import (
        RUN_FILE,
        Leg,
        closure_id,
        put_record,
        row_name,
        run_legs,
    )
    from livery.workshop._ci_context import run_context
    from livery.workshop._verified import AFFECTED, FULL, NOTHING, VERIFIED

    # The floors read the union file this run writes, and no other.
    (root / UNION).unlink(missing_ok=True)
    run = run_context()
    if run is None:
        fail(
            "the union reads the run's per-run refs, and outside CI there is no"
            " run: the legs' parts are unioned and judged inside CI alone"
        )
    legs, why = run_legs(root, run)
    if why:
        fail(f"the run's per-run refs could not be read ({why}); nothing to union")
    if not legs:
        fail(
            f"no check leg left its parts on run {run.run_id}'s refs: every leg"
            f" puts its scope and its parts there ({RUN_FILE}) at its end, and"
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
    units = units_of(root, packages)
    keys = {unit.path: closure_id(root, packages, unit) for unit in units}
    bases, target = _record_bases(root, run)
    # Every unit the union merges, with where its parts came from.
    merged: list[tuple[Unit, str]] = []
    reused = 0
    ran = 0
    writes: list[tuple[str, dict[str, Unit], dict[str, tuple[str, Unit]], Record]] = []
    for leg in sorted(legs, key=lambda item: item.key):
        if leg.why:
            fail(
                f"leg {leg.key}: {leg.why}; the leg died before its parts, or"
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
                    " parts, and a missing put is a red leg, never a smaller union"
                )
            ran += 1
            merged.extend(
                (unit, f"leg {leg.label}'s {path}")
                for path, unit in sorted(leg.units.items())
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
            merged.append((row, f"{base}'s record on {leg.label}, {suite.path}"))
            reused += 1
            print(
                f"  coverage: {suite.path} on {leg.label}: reused from run"
                f" {row.run} ({len(row.files)} files), {base}'s record{kept}"
            )
        if target:
            own = _read_record(root, held, target, leg.label)
            writes.append((leg.label, dict(leg.units), carried, own))
    if not merged:
        print("  coverage: no unit to union; nothing judged")
        return ()
    _write_merged(root, merged)
    print(f"  coverage: the union of {ran} leg(s) and {reused} reused suite(s)")
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


def _write_merged(root: Path, merged: list[tuple[Unit, str]]) -> None:
    """Merge every unit's parts and write the union file; each left-out reading named.

    A file's readings merge at the content the checkout holds, so the
    checkout's digest of every file the parts name is read first. Each
    part is read from its stored form with the fixed facts its unit
    holds.

    Raises:
        Failed: when a part is off the line format, or names fixed
            facts its unit does not hold, naming the unit it came from
            and the key at fault.
    """
    from livery.extensions.coverage._format import (
        PartError,
        digest,
        from_stored,
        merge,
    )
    from livery.extensions.coverage._parts import UNION, write_union

    parts: list[Part] = []
    for unit, source in merged:
        for index, raw in enumerate(unit.parts):
            try:
                parts.append(
                    from_stored(raw, unit.facts, source=f"{source}, part {index}")
                )
            except PartError as error:
                fail(f"coverage: {error}")
    names = sorted({name for part in parts for name in part.files})
    current = {
        name: digest((root / name).read_bytes())
        for name in names
        if (root / name).is_file()
    }
    combined = merge(parts, current)
    for line in combined.left_out:
        print(f"  coverage: {line}")
    write_union(root, combined, [unit.path for unit, _source in merged])
    print(
        f"  coverage: {len(combined.files)} file(s) unioned from {len(parts)}"
        f" part(s), written to {UNION}"
    )


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
