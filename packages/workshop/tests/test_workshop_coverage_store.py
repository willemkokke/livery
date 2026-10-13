"""The coverage record: the refusals and misses first, then the reads and writes."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from livery import git
from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage import _record as coverage_record
from livery.workshop import _ci_context
from livery.workshop._ci_context import state_store
from livery.workshop._packages import Edge, Package
from workshop_coverage_meter import read_parts, stored_unit
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

LEG = "check-linux-3.14"
RUN = _ci_context.RunContext("gitea", "1013", "push", "refs/heads/main", leg=LEG)
PULL = _ci_context.RunContext(
    "gitea", "1014", "pull_request", "refs/pull/3/merge", leg=LEG, head_ref="feat/x"
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _package(root: Path, name: str, depends: tuple[str, ...] = ()) -> Package:
    directory = root / "packages" / name
    (directory / "src" / name).mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "src" / name / "mod.py").write_text("a = 1\n")
    (directory / "tests" / "test_mod.py").write_text("def test_it():\n    pass\n")
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nname = "livery-{name}"\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        kind="python",
        depends=tuple(
            Edge(path=f"packages/{dep}", kind="build", floor="0") for dep in depends
        ),
    )


def _workspace(repo: Path) -> None:
    """A two-package workspace, `top` over `base`, with a lock beside them."""
    (repo / "pyproject.toml").write_text("[project]\nname = 'w'\n")
    (repo / "uv.lock").write_text("version = 1\n")
    _package(repo, "base")
    _package(repo, "top", ("base",))


def _build(base: Path) -> None:
    pushed(base, fill=_workspace)


@pytest.fixture
def work(seeds: Seeds) -> Path:
    """A clone of a bare origin holding the workspace, pushed to main."""
    return seeds("workspace", _build) / "work"


@pytest.fixture(autouse=True)
def _in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_EVENT_PATH",
        "GITHUB_SHA",
        "GITHUB_REF",
        "GITHUB_JOB",
        "GITHUB_HEAD_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN.run_id)
    monkeypatch.setenv("WORKSHOP_LEG", LEG)


def _packages(work: Path) -> tuple[Package, Package]:
    return _package(work, "base"), _package(work, "top", ("base",))


def _part(path: str, lines: dict[str, str] | None = None) -> dict[str, Any]:
    """A unit's part in the line format: each file's statements, all of them run."""
    return {
        "format": 2,
        "unit": path,
        "measurer": {
            "name": "coverage.py",
            "version": "7.16.2",
            "runtime": "CPython 3.14",
            "counts": "presence",
            "branches": "targets",
            "regions": [],
            "judges": ["lines", "branches"],
            "columns": False,
        },
        "contexts": [],
        "files": {
            name: {"digest": "", "statements": held, "hits": held}
            for name, held in (lines or {}).items()
        },
    }


def _unit(
    path: str,
    *,
    closure: str = "a" * 64,
    run: str = "1013",
    lines: dict[str, str] | None = None,
) -> coverage_record.Unit:
    return stored_unit(
        path, (_part(path, lines),), closure=closure, run=run, sha="b" * 40
    )


def _tested(
    path: str,
    contexts: list[str],
    reached: dict[int, set[int]],
    *,
    closure: str = "a" * 64,
    run: str = "1013",
) -> coverage_record.Unit:
    """A unit whose one file's four lines ran, each reached by the contexts named.

    *reached* gives each line's contexts as indexes into *contexts*, as
    the leg's reduction numbered them.
    """
    sets: list[str] = []
    lines: list[list[int]] = []
    for line, held in sorted(reached.items()):
        packed = fmt.pack_set(held)
        if packed not in sets:
            sets.append(packed)
        lines.append([line, sets.index(packed)])
    part = _part(path, {f"{path}/src/m.py": "1-4"})
    part["contexts"] = contexts
    part["files"][f"{path}/src/m.py"]["reached"] = {"sets": sets, "lines": lines}
    return stored_unit(path, (part,), closure=closure, run=run, sha="b" * 40)


def _tests_of(unit: coverage_record.Unit) -> dict[int, set[str]]:
    """The tests that reached each line of *unit*'s one file, by name."""
    (part,) = read_parts(unit)
    (entry,) = part.files.values()
    return {
        line: {part.contexts[index] for index in held}
        for line, held in entry.reached.items()
    }


def _set_of(unit: coverage_record.Unit, line: int) -> str:
    """The set *unit*'s row keeps for *line* of its one file, as stored."""
    (entry,) = unit.parts[0]["files"].values()
    by_line = dict(map(tuple, entry["reached"]["lines"]))
    return str(entry["reached"]["sets"][by_line[line]])


# --- the refusals and misses first --------------------------------------------


def test_a_fresh_row_whose_context_index_names_no_context_refuses_the_write(
    work: Path,
) -> None:
    # Line 2's set names context 5, and the part numbers two.
    broken = _tested("packages/base", ["", "t1"], {1: {0}, 2: {1}})
    entry = broken.parts[0]["files"]["packages/base/src/m.py"]
    entry["reached"]["sets"][1] = fmt.pack_set({5})
    why = coverage_record.put_record(
        work, RUN, leg=LEG, fresh={"packages/base": broken}
    )
    assert why == (
        "refusing: packages/base's fresh row, part 0:"
        ' files["packages/base/src/m.py"].reached.sets[1]: names context 5, and'
        " the part names 2"
    )
    assert coverage_record.recorded(work, leg=LEG) == coverage_record.Record({})


def test_a_leg_without_a_label_neither_puts_nor_reads(work: Path) -> None:
    why = coverage_record.leave_run(
        work, RUN, leg="", scope="full", packages=(), units={}
    )
    assert why == "refusing: the leg has no label, so its parts have no key"
    held = coverage_record.recorded(work, leg="")
    assert held.failed and held.reason == "this leg has no label"
    why = coverage_record.put_record(work, RUN, leg="", fresh={})
    assert why == "refusing: the leg has no label, so the record has no key"


def test_a_legs_timing_row_rides_the_one_write_of_its_per_run_ref(
    work: Path, tmp_path: Path
) -> None:
    from livery.workshop._metrics import ROW_FILE, RUNS, put_leg
    from workshop_spawns import counting_spawns

    why = coverage_record.leave_run(
        work, RUN, leg=LEG, scope="full", packages=(), units={}
    )
    assert why == ""
    trace = tmp_path / "fm-profile-check.json"
    task = {"ph": "X", "cat": "task", "name": "check", "pid": 1, "tid": 1}
    trace.write_text(json.dumps({"traceEvents": [{**task, "ts": 0, "dur": 12000}]}))
    with counting_spawns() as spawned:
        _lines, why = put_leg(work, RUN, job="check (a, b)", label=LEG, trace=trace)
    assert why == ""
    assert spawned["git push"] == 1
    series = RUNS.series(RUN.run_id, LEG)
    row, why = series.row(state_store(work), ROW_FILE)
    assert why == "" and row is not None and row.data["total_ms"] == 12.0
    lines, why = series.row(state_store(work), coverage_record.RUN_FILE)
    assert why == "" and lines is not None and lines.data["scope"] == "full"


def test_the_puts_refuse_outside_ci(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._metrics import put_leg

    monkeypatch.delenv("GITHUB_ACTIONS")
    why = coverage_record.leave_run(
        work, RUN, leg=LEG, scope="full", packages=(), units={}
    )
    assert why == ""
    _lines, why = put_leg(work, RUN, job="check", label=LEG, trace=work / "none.json")
    assert "only a CI run writes" in why
    assert "only a CI run writes" in coverage_record.put_record(
        work, RUN, leg=LEG, fresh={}
    )


def test_only_mains_run_writes_mains_record_and_only_a_branchs_own_run_its_own(
    work: Path,
) -> None:
    fresh = {"packages/base": _unit("packages/base")}
    why = coverage_record.put_record(work, PULL, leg=LEG, fresh=fresh)
    assert why == "refusing: a pull_request run reads main's record and never writes it"
    local = _ci_context.RunContext("gitea", "", "", "", leg=LEG)
    assert "a local run reads main's record" in coverage_record.put_record(
        work, local, leg=LEG, fresh=fresh
    )
    assert coverage_record.recorded(work, leg=LEG) == coverage_record.Record({})
    # A branch's record: its own pull request run writes it, no other run.
    why = coverage_record.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/y")
    assert why == (
        "refusing: feat/y's record is written by that branch's own pull request"
        " run, not a pull_request run of feat/x"
    )
    why = coverage_record.put_record(work, RUN, leg=LEG, fresh=fresh, base="feat/x")
    assert why.endswith("not a push run of no branch")
    assert coverage_record.put_record(work, PULL, leg=LEG, fresh=fresh, base="") == (
        "refusing: no branch to write a record for"
    )
    assert (
        coverage_record.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/x")
        == ""
    )
    assert coverage_record.recorded(work, leg=LEG, base="feat/x").units == fresh
    assert coverage_record.recorded(work, leg=LEG) == coverage_record.Record({})
    empty = coverage_record.recorded(work, leg=LEG, base="")
    assert empty.failed and empty.reason == "no branch to read a record for"
    assert coverage_record.record_ref(LEG, "feat/x") == (
        git.NAMESPACE + "coverage/feat-x/" + git.slug(LEG)
    )


def test_an_absent_record_is_no_units_and_an_unreachable_store_names_its_reason(
    work: Path, tmp_path: Path
) -> None:
    assert coverage_record.recorded(work, leg=LEG) == coverage_record.Record({})
    assert coverage_record.run_legs(work, RUN) == ([], "")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    held = coverage_record.recorded(work, leg=LEG)
    assert held.failed and "could not be read" in held.reason and held.units == {}
    legs, why = coverage_record.run_legs(work, RUN)
    assert legs == [] and why.endswith("the remote could not be listed")


def test_a_row_of_the_old_schema_is_a_miss_and_the_unit_is_measured_afresh(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage._leg import unrecorded

    base, top = _packages(work)
    key = coverage_record.closure_id(work, (base, top), base)
    # Rows of arcs, written before the record held parts: the current
    # row of base at its current closure, and a row kept a day.
    arcs = {"schema": 2, "unit": "packages/base", "closure": key, "run": "5"}
    rows = {
        "packages-base.json": json.dumps(
            {**arcs, "files": {"packages/base/src/base/mod.py": [[-1, 1], [1, -1]]}}
        ),
        f"packages-base.{key}.json": json.dumps({**arcs, "files": {}}),
    }
    ref = coverage_record.record_ref(LEG)
    assert git.put(state_store(work), ref, rows, message="arcs") == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {} and not held.failed
    assert [str(item) for item in held.skipped] == [
        f"packages-base.json: schema 2, this reader speaks {coverage_record.SCHEMA};"
        " skipped"
    ]
    # The kept row of the old shape names no moment, so the next write
    # lets it go, and the union never carries it.
    assert held.expired(datetime.now(UTC)) == [f"packages-base.{key}.json"]
    assert held.at("packages/base", key) is None
    # The leg asks the record and finds no measurement: the suite runs.
    assert unrecorded(work, RUN, (base, top), (base,), bases=("main",)) == (base,)
    assert (
        "coverage store: packages/base runs, nothing to reuse (on this leg main's"
        " record holds no measurement of it)" in capsys.readouterr().out
    )
    # The first write of the new schema replaces the row by name and
    # lets the kept one go.
    fresh = {"packages/base": _unit("packages/base", closure=key)}
    assert (
        coverage_record.put_record(
            work, RUN, leg=LEG, fresh=fresh, remove=held.stale(["packages/base"])
        )
        == ""
    )
    again = coverage_record.recorded(work, leg=LEG)
    assert again.units == fresh and again.skipped == () and again.kept == ()


def test_a_row_written_before_rows_named_the_tests_is_a_miss(work: Path) -> None:
    # A row of parts, its suite's tests not named: written by schema 3.
    row = {
        "schema": 3,
        "unit": "packages/base",
        "closure": "a" * 64,
        "run": "5",
        "sha": "b" * 40,
        "parts": [_part("packages/base")],
    }
    ref = coverage_record.record_ref(LEG)
    assert (
        git.put(
            state_store(work), ref, {"packages-base.json": json.dumps(row)}, message="3"
        )
        == ""
    )
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {} and not held.failed
    assert [str(item) for item in held.skipped] == [
        f"packages-base.json: schema 3, this reader speaks {coverage_record.SCHEMA};"
        " skipped"
    ]


def test_a_row_that_is_not_a_unit_is_skipped_named_and_stale(work: Path) -> None:
    ref = coverage_record.record_ref(LEG)
    rows = {
        "odd.json": json.dumps({"schema": 99}),
        "bare.json": json.dumps(
            {"schema": coverage_record.SCHEMA, "unit": "packages/x"}
        ),
        "junk.json": "not json",
        # A row of lines, written before the record held arcs: another
        # schema, so the unit is a miss and is measured afresh.
        "packages-base.json": json.dumps(
            {"schema": 1, "unit": "packages/base", "files": {"a.py": [1, 2]}}
        ),
        # A row whose part is of a format this workshop does not read.
        "packages-top.json": json.dumps(
            {
                "schema": coverage_record.SCHEMA,
                "unit": "packages/top",
                "parts": [{**_part("packages/top"), "format": 1}],
            }
        ),
    }
    assert git.put(state_store(work), ref, rows, message="foreign") == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {} and not held.failed
    assert sorted(str(item) for item in held.skipped) == [
        "bare.json: carries no unit; skipped",
        "junk.json: does not parse; skipped",
        f"odd.json: schema 99, this reader speaks {coverage_record.SCHEMA}; skipped",
        f"packages-base.json: schema 1, this reader speaks {coverage_record.SCHEMA};"
        " skipped",
        "packages-top.json: holds a part of format 1, and this workshop reads"
        " format 2; skipped",
    ]
    assert sorted(held.stale(())) == [
        "bare.json",
        "junk.json",
        "odd.json",
        "packages-base.json",
        "packages-top.json",
    ]


def test_a_per_run_ref_without_the_file_or_with_a_foreign_one_is_named(
    work: Path,
) -> None:
    from livery.workshop._metrics import ROW_FILE, RUNS

    half = RUNS.series(RUN.run_id, LEG)
    assert (
        half.put(state_store(work), {ROW_FILE: {"job": "check"}}, message="half") == ""
    )
    legs, why = coverage_record.run_legs(work, RUN)
    assert why == "" and len(legs) == 1
    assert legs[0].key == git.slug(LEG) and legs[0].units == {}
    assert legs[0].why == f"{half.ref} carries no {coverage_record.RUN_FILE}"
    foreign = {coverage_record.RUN_FILE: json.dumps({"schema": 99})}
    assert git.put(state_store(work), half.ref, foreign, message="foreign") == ""
    legs, _why = coverage_record.run_legs(work, RUN)
    assert legs[0].why.startswith(half.ref) and "this reader speaks" in legs[0].why


def test_the_closure_id_refuses_a_directory_head_lacks(work: Path) -> None:
    base, _top = _packages(work)
    ghost = Package(
        directory=work / "packages" / "ghost",
        path="packages/ghost",
        name="livery-ghost",
        kind="python",
        depends=(),
    )
    with pytest.raises(git.GitError):
        coverage_record.closure_id(work, (base, ghost), ghost)


def test_the_closure_id_answers_without_a_root_pin(work: Path) -> None:
    # A tree that holds no lock is not an error: the pin contributes
    # nothing and the rest of the identity still stands.
    base, top = _packages(work)
    pinned = coverage_record.closure_id(work, (base, top), base)
    _git(work, "rm", "-q", "uv.lock")
    _git(work, "commit", "-qm", "the lock goes")
    unpinned = coverage_record.closure_id(work, (base, top), base)
    assert unpinned and unpinned != pinned


def test_the_closure_id_reads_the_tree_in_one_process(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cost is the process, not the read: a call per path spent 28 git
    # processes on this workspace's own seven packages, and several times
    # that in wall clock on Windows. One in-process read of the tree
    # answers the closure and the pins together, and nothing here may go
    # back to a process per path.
    from workshop_spawns import counting_spawns

    del monkeypatch
    base, top = _packages(work)
    with counting_spawns() as spawned:
        coverage_record.closure_id(work, (base, top), top)
    assert spawned["git"] == 0, spawned


# --- the identity -------------------------------------------------------------


def test_the_closure_id_follows_the_closure_and_the_root_pins(work: Path) -> None:
    base, top = _packages(work)
    packages = (base, top)
    before = {
        "base": coverage_record.closure_id(work, packages, base),
        "top": coverage_record.closure_id(work, packages, top),
    }
    assert coverage_record.closure(packages, top) == (base, top)
    assert coverage_record.closure(packages, base) == (base,)
    # A change inside the dependency moves the dependant's identity too.
    (work / "packages" / "base" / "src" / "base" / "mod.py").write_text("a = 2\n")
    _git(work, "commit", "-qam", "base changes")
    after = {
        "base": coverage_record.closure_id(work, packages, base),
        "top": coverage_record.closure_id(work, packages, top),
    }
    assert after["base"] != before["base"] and after["top"] != before["top"]
    # A change inside the dependant leaves the dependency's identity alone.
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 3\n")
    _git(work, "commit", "-qam", "top changes")
    again = {
        "base": coverage_record.closure_id(work, packages, base),
        "top": coverage_record.closure_id(work, packages, top),
    }
    assert again["base"] == after["base"] and again["top"] != after["top"]
    # A pin change is a dependency change for every suite.
    (work / "uv.lock").write_text("version = 2\n")
    _git(work, "commit", "-qam", "lock moves")
    pinned = coverage_record.closure_id(work, packages, base)
    assert pinned != again["base"]
    # Files outside the closure are not the suite's to store.
    assert coverage_record.in_closure(packages, top, "packages/base/src/base/mod.py")
    assert not coverage_record.in_closure(packages, base, "packages/top/src/top/mod.py")
    assert coverage_record.in_closure(packages, top, "packages\\top\\src\\top\\mod.py")


# --- the reads and the writes -------------------------------------------------


def test_a_legs_lines_ride_its_per_run_ref_and_go_with_it(work: Path) -> None:
    from livery.workshop import _metrics

    unit = _unit("packages/base", lines={"packages/base/src/base/mod.py": "1-3"})
    why = coverage_record.leave_run(
        work,
        RUN,
        leg=LEG,
        scope="affected",
        packages=("packages/base",),
        units={"packages/base": unit},
    )
    assert why == ""
    # The leg's write puts the file; no trace, so no row beside it.
    _lines, why = _metrics.put_leg(
        work, RUN, job="check (a)", label=LEG, trace=work / "none.json"
    )
    assert why == ""
    ref = _metrics.run_ref(RUN, LEG)
    held = git.read(state_store(work), ref).files
    assert held is not None and set(held) == {coverage_record.RUN_FILE}
    legs, why = coverage_record.run_legs(work, RUN)
    assert why == "" and len(legs) == 1
    leg = legs[0]
    assert (leg.key, leg.label, leg.scope, leg.packages, leg.why) == (
        git.slug(LEG),
        LEG,
        "affected",
        ("packages/base",),
        "",
    )
    assert leg.units == {"packages/base": unit}
    assert unit.files == ("packages/base/src/base/mod.py",)
    # Another run's refs are another run's; the lines go with the ref.
    other = _ci_context.RunContext("gitea", "9", "push", "refs/heads/main", leg=LEG)
    assert coverage_record.run_legs(work, other) == ([], "")
    assert git.drop(state_store(work), ref) == ""
    assert coverage_record.run_legs(work, RUN) == ([], "")


def test_the_record_is_replaced_in_place_fresh_over_carried_stale_removed(
    work: Path,
) -> None:
    series = coverage_record.RECORD.series(coverage_record.MAIN, LEG)
    assert series.ref == coverage_record.record_ref(LEG)
    assert series.ref == git.NAMESPACE + "coverage/main/" + git.slug(LEG)
    base = _unit("packages/base", run="1", lines={"packages/base/src/b.py": "1"})
    top = _unit("packages/top", closure="c" * 64, run="1")
    ghost = _unit("packages/ghost", run="1")
    fresh = {"packages/base": base, "packages/top": top, "packages/ghost": ghost}
    assert coverage_record.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == fresh and held.skipped == ()
    first = {row.name: row.when for row in series.rows(state_store(work)).rows}
    # The next merge: base fresh again, top carried untouched, ghost gone.
    again = _unit("packages/base", closure="d" * 64, run="2")
    later = _ci_context.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    stale = held.stale(["packages/base", "packages/top"])
    assert stale == [coverage_record.row_name("packages/ghost")]
    why = coverage_record.put_record(
        work, later, leg=LEG, fresh={"packages/base": again}, remove=stale
    )
    assert why == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {"packages/base": again, "packages/top": top}
    rows = {row.name: row.when for row in series.rows(state_store(work)).rows}
    assert rows[coverage_record.row_name("packages/top")] == first["packages-top.json"]
    assert rows["packages-base.json"] > first["packages-base.json"]
    # Another leg's record is another ref.
    assert coverage_record.recorded(work, leg="check-macos-3.13") == (
        coverage_record.Record({})
    )


# --- a row a write moves on from stays a day, refusals first -----------------


def test_a_record_that_cannot_be_read_refuses_the_write_naming_why(work: Path) -> None:
    down = coverage_record.Record({}, failed=True, reason="remote down")
    why = coverage_record.put_record(work, RUN, leg=LEG, fresh={}, held=down)
    assert why == (
        f"refusing: main's record on {LEG} could not be read (remote down), so"
        " the rows it keeps are unknown"
    )


def test_a_kept_row_past_the_day_or_without_a_stamp_expires() -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    closure = "c" * 64
    young = coverage_record.kept_name(
        "packages/base", closure, now - timedelta(hours=23)
    )
    old = coverage_record.kept_name("packages/base", closure, now - timedelta(hours=25))
    assert young == f"packages-base.{closure}.20260930T130000Z.json"
    record = coverage_record.Record(
        {},
        kept=(
            coverage_record.Kept(young),
            coverage_record.Kept(old),
            coverage_record.Kept(f"packages-base.{closure}.json"),
            coverage_record.Kept(f"packages-base.{closure}.yesterday.json"),
        ),
    )
    assert record.expired(now) == [
        old,
        f"packages-base.{closure}.json",
        f"packages-base.{closure}.yesterday.json",
    ]
    assert record.kept[0].closure == closure
    assert record.kept[0].when == now - timedelta(hours=23)


def test_a_row_moved_on_or_dropped_stays_a_day_at_its_closure(work: Path) -> None:
    old, new = "c" * 64, "d" * 64
    base = _unit("packages/base", closure=old, run="1")
    top = _unit("packages/top", closure=old, run="1")
    ghost = _unit("packages/ghost", run="1")
    first = {"packages/base": base, "packages/top": top, "packages/ghost": ghost}
    assert coverage_record.put_record(work, RUN, leg=LEG, fresh=first) == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == first and held.kept == ()
    # Another merge's run: base moves on, top is measured again at its
    # own closure, and ghost's unit is gone.
    later = _ci_context.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    moved = _unit("packages/base", closure=new, run="2")
    again = _unit("packages/top", closure=old, run="2")
    stale = held.stale(["packages/base", "packages/top"])
    why = coverage_record.put_record(
        work,
        later,
        leg=LEG,
        fresh={"packages/base": moved, "packages/top": again},
        remove=stale,
        held=held,
    )
    assert why == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {"packages/base": moved, "packages/top": again}
    # A read names the kept rows and reads none of them.
    assert all(kept.unit is None for kept in held.kept)
    assert sorted((kept.name.split(".")[0], kept.closure) for kept in held.kept) == [
        ("packages-base", old),
        ("packages-ghost", "a" * 64),
    ]
    # The union asks at the closure its leg skipped on, and the kept row
    # is read then.
    assert held.at("packages/base", old) == base
    assert held.at("packages/base", new) == moved
    assert held.at("packages/ghost", "a" * 64) == ghost
    assert held.at("packages/base", "e" * 64) is None
    # A day on, the next write lets the kept rows go.
    tomorrow = datetime.now(UTC) + timedelta(days=1, minutes=1)
    why = coverage_record.put_record(
        work, later, leg=LEG, fresh={}, held=held, now=tomorrow
    )
    assert why == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.kept == ()
    assert held.units == {"packages/base": moved, "packages/top": again}


def _facts_files(work: Path) -> list[str]:
    """The fixed facts files main's record on the leg holds, by name."""
    found = git.read(state_store(work), coverage_record.record_ref(LEG))
    return [name for name in found.names if name.startswith(coverage_record.FACTS)]


def test_a_row_naming_fixed_facts_the_record_lacks_is_skipped_naming_its_content(
    work: Path,
) -> None:
    content = "c" * 40
    base = stored_unit(
        "packages/base",
        [
            {
                **_part("packages/base"),
                "files": {
                    "packages/base/src/b.py": {
                        "digest": content,
                        "statements": "1-2",
                        "hits": "1",
                    }
                },
            }
        ],
    )
    assert (
        coverage_record.put_record(work, RUN, leg=LEG, fresh={"packages/base": base})
        == ""
    )
    (facts,) = _facts_files(work)
    name = facts.removeprefix(coverage_record.FACTS).removesuffix(".json")
    ref = coverage_record.record_ref(LEG)
    assert git.put(state_store(work), ref, {}, message="lose", remove=[facts]) == ""
    held = coverage_record.recorded(work, leg=LEG)
    # The row cannot be read without its facts: a miss, its unit measured
    # afresh, and the row named for the next write to drop.
    assert held.units == {} and not held.failed
    assert [str(item) for item in held.skipped] == [
        f"packages-base.json: names the fixed facts {name} of content"
        f" {content[:12]}, and the record holds no {facts}; skipped"
    ]
    assert held.stale(()) == ["packages-base.json"]


def test_the_record_keeps_one_file_s_fixed_facts_once_and_drops_what_no_row_names(
    work: Path,
) -> None:
    shared = {"packages/base/src/b.py": "1-2"}
    base = _unit("packages/base", closure="c" * 64, run="1", lines=shared)
    top = _unit("packages/top", run="1", lines=shared)
    first = {"packages/base": base, "packages/top": top}
    assert coverage_record.put_record(work, RUN, leg=LEG, fresh=first) == ""
    # Two rows name the facts of one content: the record holds them once.
    (kept_facts,) = _facts_files(work)
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == first and held.held_facts == (kept_facts,)
    # base moves on to other content: its old row stays a day as a kept
    # row, and so do the facts it names.
    later = _ci_context.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    moved = _unit(
        "packages/base",
        closure="d" * 64,
        run="2",
        lines={"packages/base/src/b.py": "1-3"},
    )
    why = coverage_record.put_record(
        work, later, leg=LEG, fresh={"packages/base": moved}, held=held
    )
    assert why == ""
    assert len(_facts_files(work)) == 2 and kept_facts in _facts_files(work)
    # top's unit goes: its row is kept a day too, still naming the facts.
    held = coverage_record.recorded(work, leg=LEG)
    why = coverage_record.put_record(
        work,
        later,
        leg=LEG,
        fresh={},
        remove=[coverage_record.row_name("packages/top")],
        held=held,
    )
    assert why == ""
    assert kept_facts in _facts_files(work)
    held = coverage_record.recorded(work, leg=LEG)
    assert held.at("packages/top", "a" * 64) == top
    # A day on, the kept rows go, and the facts no row names go with them.
    tomorrow = datetime.now(UTC) + timedelta(days=1, minutes=1)
    why = coverage_record.put_record(
        work, later, leg=LEG, fresh={}, held=held, now=tomorrow
    )
    assert why == ""
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units == {"packages/base": moved} and held.kept == ()
    assert _facts_files(work) == list(held.held_facts) != [kept_facts]
    # The janitor counts the rows alone.
    lines = git.sweep(state_store(work), (coverage_record.RECORD,), remote=True)
    assert f"  {coverage_record.record_ref(LEG)}: 1 row(s), within its bounds" in lines


def test_each_write_numbers_the_tests_as_the_row_it_replaces(work: Path) -> None:
    # The first run: the code outside every test reached line 1, and the
    # tests t2 and t5 the rest.
    first = _tested(
        "packages/base", ["", "t2", "t5"], {1: {0}, 2: {1}, 3: {1, 2}, 4: {2}}
    )
    assert (
        coverage_record.put_record(work, RUN, leg=LEG, fresh={"packages/base": first})
        == ""
    )
    held = coverage_record.recorded(work, leg=LEG)
    assert held.units["packages/base"].parts[0]["contexts"] == ["", "t2", "t5"]
    # A run later t1 is a new test that sorts before t2, and t5's test is
    # gone; the leg numbers the contexts its run recorded, sorted.
    later = _ci_context.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    second = _tested(
        "packages/base",
        ["", "t1", "t2"],
        {1: {0}, 2: {2}, 3: {1, 2}, 4: {1}},
        closure="c" * 64,
        run="2",
    )
    why = coverage_record.put_record(
        work, later, leg=LEG, fresh={"packages/base": second}, held=held
    )
    assert why == ""
    held = coverage_record.recorded(work, leg=LEG)
    row = held.units["packages/base"]
    # t2 keeps index 1, t5 keeps 2 and no line names it, and t1 takes 3.
    assert row.parts[0]["contexts"] == ["", "t2", "t5", "t1"]
    assert _tests_of(row) == {1: {""}, 2: {"t2"}, 3: {"t1", "t2"}, 4: {"t1"}}
    # The lines t2 alone reached keep their set's bytes, so the two rows
    # differ only where the tests that reached a line did.
    assert _set_of(row, 2) == _set_of(first, 2)
    # The row moved on from stays a day with its own numbering.
    assert held.at("packages/base", "a" * 64) == first
    assert _tests_of(first) == {1: {""}, 2: {"t2"}, 3: {"t2", "t5"}, 4: {"t5"}}


def test_one_run_numbers_a_unit_s_tests_alike_on_every_leg(work: Path) -> None:
    other = "check-macos-3.14"
    # Each leg ran a test the other did not: t3 on this leg alone, t1 on
    # the other.
    linux = {
        "packages/base": _tested(
            "packages/base", ["", "t2", "t3"], {1: {0}, 2: {1}, 3: {2}}
        )
    }
    macos = {
        "packages/base": _tested(
            "packages/base", ["", "t1", "t2"], {1: {0}, 2: {2}, 3: {1}}
        )
    }
    named = coverage_record.run_contexts([linux, macos])
    assert named == {"packages/base": {"coverage.py": {"", "t1", "t2", "t3"}}}
    for leg, rows in ((LEG, linux), (other, macos)):
        why = coverage_record.put_record(
            work, RUN, leg=leg, fresh=rows, run_contexts=named
        )
        assert why == ""
    first = {
        leg: coverage_record.recorded(work, leg=leg).units["packages/base"]
        for leg in (LEG, other)
    }
    # One numbering on both legs, each leg's lines naming its own tests.
    for unit in first.values():
        assert unit.parts[0]["contexts"] == ["", "t1", "t2", "t3"]
    assert _tests_of(first[LEG]) == {1: {""}, 2: {"t2"}, 3: {"t3"}}
    assert _tests_of(first[other]) == {1: {""}, 2: {"t2"}, 3: {"t1"}}
    # The line both legs' t2 reached alone is the same bytes on both.
    assert _set_of(first[LEG], 2) == _set_of(first[other], 2)
    # A run later each leg has a new test of its own: both append both.
    later = _ci_context.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    linux = {
        "packages/base": _tested(
            "packages/base",
            ["", "t0", "t2"],
            {1: {0}, 2: {2}, 3: {1}},
            closure="c" * 64,
            run="2",
        )
    }
    macos = {
        "packages/base": _tested(
            "packages/base",
            ["", "t2", "t9"],
            {1: {0}, 2: {1}, 3: {2}},
            closure="c" * 64,
            run="2",
        )
    }
    named = coverage_record.run_contexts([linux, macos])
    for leg, rows in ((LEG, linux), (other, macos)):
        held = coverage_record.recorded(work, leg=leg)
        why = coverage_record.put_record(
            work, later, leg=leg, fresh=rows, held=held, run_contexts=named
        )
        assert why == ""
    for leg in (LEG, other):
        row = coverage_record.recorded(work, leg=leg).units["packages/base"]
        assert row.parts[0]["contexts"] == ["", "t1", "t2", "t3", "t0", "t9"]


def test_a_branch_s_first_write_numbers_the_tests_as_main_s_row(work: Path) -> None:
    main = _tested("packages/base", ["", "t2", "t5"], {1: {0}, 2: {1}, 3: {2}})
    assert (
        coverage_record.put_record(work, RUN, leg=LEG, fresh={"packages/base": main})
        == ""
    )
    mains = coverage_record.recorded(work, leg=LEG).units
    # The branch's record holds no row of base yet: it numbers as main's.
    fresh = {
        "packages/base": _tested(
            "packages/base", ["", "t1", "t2"], {1: {0}, 2: {2}, 3: {1}}, run="1014"
        ),
        # No row of top anywhere: its own contexts, sorted, number it.
        "packages/top": _tested(
            "packages/top", ["t9", "", "t3"], {1: {1}, 2: {0}, 3: {2}}, run="1014"
        ),
    }
    why = coverage_record.put_record(
        work, PULL, leg=LEG, fresh=fresh, base="feat/x", numbered_like=mains
    )
    assert why == ""
    branch = coverage_record.recorded(work, leg=LEG, base="feat/x").units
    assert branch["packages/base"].parts[0]["contexts"] == ["", "t2", "t5", "t1"]
    assert _tests_of(branch["packages/base"]) == {1: {""}, 2: {"t2"}, 3: {"t1"}}
    assert branch["packages/top"].parts[0]["contexts"] == ["", "t3", "t9"]
    assert _tests_of(branch["packages/top"]) == {1: {""}, 2: {"t9"}, 3: {"t3"}}


def _contract(work: Path, runners: str) -> None:
    (work / "workshop.toml").write_text(
        f'[ci]\nrunners = [{runners}]\npython-versions = ["3.14"]\n'
    )
    for name in ("base", "top"):
        (work / "packages" / name / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0"\n'
        )


def test_the_current_keys_are_main_and_every_branch_with_every_check_leg_or_none(
    work: Path, tmp_path: Path
) -> None:
    # No contract at the root: the keys cannot be told, and the janitor
    # drops nothing.
    assert coverage_record.current_keys(work) is None
    _contract(work, '"ubuntu-latest", "macos-latest"')
    assert coverage_record.current_keys(work) == {
        ("main", "check-ubuntu-latest-3-14"),
        ("main", "check-macos-latest-3-14"),
    }
    # A branch on origin is a base while it lives, spelled ref-safe.
    _git(work, "push", "-q", "origin", "main:refs/heads/feat/x")
    assert coverage_record.branches(work) == ["feat/x", "main"]
    assert coverage_record.current_keys(work) == {
        ("main", "check-ubuntu-latest-3-14"),
        ("main", "check-macos-latest-3-14"),
        ("feat-x", "check-ubuntu-latest-3-14"),
        ("feat-x", "check-macos-latest-3-14"),
    }
    # A remote that cannot be listed tells no keys, and nothing is dropped.
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert coverage_record.branches(work) is None
    assert coverage_record.current_keys(work) is None


def test_the_janitor_drops_a_closure_keyed_ref_and_keeps_the_record_and_the_marks(
    work: Path,
) -> None:
    # A ref of the shape the family no longer produces, one leg and
    # unit keyed by closure, is an orphan under the current keys; the
    # marks series beside the family is not the family's at all.
    old = git.NAMESPACE + "coverage/check-linux-3-14/livery-base"
    marks = git.NAMESPACE + "coverage/marks"
    assert git.put(state_store(work), old, {"x": "{}"}, message="old") == ""
    assert git.put(state_store(work), marks, {"m": "{}"}, message="marks") == ""
    _contract(work, '"linux"')
    fresh = {"packages/base": _unit("packages/base")}
    assert coverage_record.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    # A branch's record lives while the branch does, and goes with it.
    _git(work, "push", "-q", "origin", "main:refs/heads/feat/x")
    assert (
        coverage_record.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/x")
        == ""
    )
    branch_ref = coverage_record.record_ref(LEG, "feat/x")
    # An orphan lingers a day first: main's run for a merged squash reads
    # the merged branch's record after the merge deleted the branch.
    lines = git.sweep(state_store(work), (coverage_record.RECORD,), remote=True)
    assert any(
        line.startswith(f"  {old}: no current base and leg produces it; kept ")
        for line in lines
    )
    assert git.read(state_store(work), old).files == {"x": "{}"}
    later = datetime.now(UTC) + timedelta(days=2)
    lines = git.sweep(
        state_store(work), (coverage_record.RECORD,), remote=True, now=later
    )
    assert f"  {old}: no current base and leg produces it; dropped" in lines
    assert f"  {coverage_record.record_ref(LEG)}: 1 row(s), within its bounds" in lines
    assert f"  {branch_ref}: 1 row(s), within its bounds" in lines
    assert git.read(state_store(work), old).files is None
    assert git.read(state_store(work), marks).files == {"m": "{}"}
    assert coverage_record.recorded(work, leg=LEG).units == fresh
    _git(work, "push", "-q", "origin", ":refs/heads/feat/x")
    lines = git.sweep(state_store(work), (coverage_record.RECORD,), remote=True)
    assert any(
        line.startswith(f"  {branch_ref}: no current base and leg produces it; kept ")
        for line in lines
    )
    assert git.read(state_store(work), branch_ref).files is not None
    lines = git.sweep(
        state_store(work), (coverage_record.RECORD,), remote=True, now=later
    )
    assert f"  {branch_ref}: no current base and leg produces it; dropped" in lines
    assert git.read(state_store(work), branch_ref).files is None
    assert coverage_record.recorded(work, leg=LEG).units == fresh


def test_the_stored_rows_are_every_recorded_unit_and_the_misses_are_named(
    work: Path, tmp_path: Path
) -> None:
    from livery.extensions.coverage._pages import stored_rows

    fresh = {
        "packages/base": _unit("packages/base", lines={"packages/base/src/x.py": "1"}),
        "packages/top": _unit("packages/top", lines={"packages/top/src/x.py": "1-2"}),
    }
    assert coverage_record.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    held, misses = stored_rows(work, [LEG, "check-other"])
    assert held == {LEG: fresh}
    assert misses == ["main/check-other: nothing recorded"]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    held, misses = stored_rows(work, [LEG])
    assert held == {}
    assert len(misses) == 1
    assert misses[0].startswith(f"main/{LEG}: the record could not be read (")
    assert misses[0].endswith("); the pages draw without it")


# --- the workspace's own tests, a unit keyed by the whole tree -----------------


def test_the_workspace_tests_are_a_unit_keyed_by_the_tree(work: Path) -> None:
    from livery.workshop._packages import Package

    base, top = _packages(work)
    from livery.workshop._packages import workspace_suite

    assert workspace_suite(work / "nowhere") is None
    (work / "tests").mkdir()
    unit = workspace_suite(work)
    assert unit is not None and unit.path == "tests" and unit.name == "workspace-tests"
    (work / "tests" / "test_all.py").write_text("def test_it():\n    pass\n")
    _git(work, "add", "tests")
    _git(work, "commit", "-qm", "workspace tests")
    before = coverage_record.closure_id(work, (base, top), unit)
    # Prose leaves the key alone; a package or a pin moves it.
    (work / "notes").mkdir()
    (work / "notes" / "plan.md").write_text("# plan\n")
    _git(work, "add", "notes")
    _git(work, "commit", "-qm", "a note")
    assert coverage_record.closure_id(work, (base, top), unit) == before
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 9\n")
    _git(work, "commit", "-qam", "top changes")
    after = coverage_record.closure_id(work, (base, top), unit)
    assert after != before
    (work / "uv.lock").write_text("version = 3\n")
    _git(work, "commit", "-qam", "lock moves")
    assert coverage_record.closure_id(work, (base, top), unit) != after
    # Any file is the unit's to store: its tests reach every package.
    assert coverage_record.in_closure(
        (base, top), unit, "packages/base/src/base/mod.py"
    )
    assert coverage_record.in_closure((base, top), unit, "tests/test_x.py")
    assert isinstance(unit, Package)
    assert coverage_record.row_name(unit.path) == "tests.json"
