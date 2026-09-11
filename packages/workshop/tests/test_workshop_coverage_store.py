"""The coverage record: the refusals and misses first, then the reads and writes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop import _coverage_store, _state
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._packages import Edge, Package

LEG = "check-linux-3.14"
RUN = _state.RunContext("gitea", "1013", "push", "refs/heads/main", leg=LEG)
PULL = _state.RunContext(
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
        f'type = "python"\nname = "livery-{name}"\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=tuple(
            Edge(path=f"packages/{dep}", kind="build", floor="0") for dep in depends
        ),
    )


@pytest.fixture
def work(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "pyproject.toml").write_text("[project]\nname = 'w'\n")
    (work / "uv.lock").write_text("version = 1\n")
    _package(work, "base")
    _package(work, "top", ("base",))
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


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


def _unit(
    path: str,
    *,
    closure: str = "a" * 64,
    run: str = "1013",
    files: dict[str, list[int]] | None = None,
) -> _coverage_store.Unit:
    return _coverage_store.Unit(path, closure, run, "b" * 40, files or {})


# --- the refusals and misses first --------------------------------------------


def test_a_leg_without_a_label_neither_puts_nor_reads(work: Path) -> None:
    why = _coverage_store.put_run(
        work, RUN, leg="", scope="full", packages=(), units={}
    )
    assert why == "refusing: the leg has no label, so its lines have no key"
    held = _coverage_store.recorded(work, leg="")
    assert held.failed and held.reason == "this leg has no label"
    why = _coverage_store.put_record(work, RUN, leg="", fresh={})
    assert why == "refusing: the leg has no label, so the record has no key"


def test_the_puts_refuse_outside_ci(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS")
    why = _coverage_store.put_run(
        work, RUN, leg=LEG, scope="full", packages=(), units={}
    )
    assert "only a CI run writes" in why
    assert "only a CI run writes" in _coverage_store.put_record(
        work, RUN, leg=LEG, fresh={}
    )


def test_only_mains_run_writes_mains_record_and_only_a_branchs_own_run_its_own(
    work: Path,
) -> None:
    fresh = {"packages/base": _unit("packages/base")}
    why = _coverage_store.put_record(work, PULL, leg=LEG, fresh=fresh)
    assert why == "refusing: a pull_request run reads main's record and never writes it"
    local = _state.RunContext("gitea", "", "", "", leg=LEG)
    assert "a local run reads main's record" in _coverage_store.put_record(
        work, local, leg=LEG, fresh=fresh
    )
    assert _coverage_store.recorded(work, leg=LEG) == _coverage_store.Record({})
    # A branch's record: its own pull request run writes it, no other run.
    why = _coverage_store.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/y")
    assert why == (
        "refusing: feat/y's record is written by that branch's own pull request"
        " run, not a pull_request run of feat/x"
    )
    why = _coverage_store.put_record(work, RUN, leg=LEG, fresh=fresh, base="feat/x")
    assert why.endswith("not a push run of no branch")
    assert _coverage_store.put_record(work, PULL, leg=LEG, fresh=fresh, base="") == (
        "refusing: no branch to write a record for"
    )
    assert (
        _coverage_store.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/x")
        == ""
    )
    assert _coverage_store.recorded(work, leg=LEG, base="feat/x").units == fresh
    assert _coverage_store.recorded(work, leg=LEG) == _coverage_store.Record({})
    empty = _coverage_store.recorded(work, leg=LEG, base="")
    assert empty.failed and empty.reason == "no branch to read a record for"
    assert _coverage_store.record_ref(LEG, "feat/x") == (
        _state.NAMESPACE + "coverage/feat-x/" + _state.slug(LEG)
    )


def test_an_absent_record_is_no_units_and_an_unreachable_store_names_its_reason(
    work: Path, tmp_path: Path
) -> None:
    assert _coverage_store.recorded(work, leg=LEG) == _coverage_store.Record({})
    assert _coverage_store.run_legs(work, RUN) == ([], "")
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    held = _coverage_store.recorded(work, leg=LEG)
    assert held.failed and "could not be read" in held.reason and held.units == {}
    legs, why = _coverage_store.run_legs(work, RUN)
    assert legs == [] and why.endswith("the remote could not be listed")


def test_a_row_that_is_not_a_unit_is_skipped_named_and_stale(work: Path) -> None:
    ref = _coverage_store.record_ref(LEG)
    rows = {
        "odd.json": json.dumps({"schema": 99}),
        "bare.json": json.dumps({"schema": 1, "unit": "packages/x"}),
        "junk.json": "not json",
    }
    assert _state.put(work, ref, rows, message="foreign") == ""
    held = _coverage_store.recorded(work, leg=LEG)
    assert held.units == {} and not held.failed
    assert sorted(str(item) for item in held.skipped) == [
        "bare.json: carries no unit; skipped",
        "junk.json: does not parse; skipped",
        "odd.json: schema 99, this reader speaks 1; skipped",
    ]
    assert sorted(held.stale(())) == ["bare.json", "junk.json", "odd.json"]


def test_a_per_run_ref_without_the_file_or_with_a_foreign_one_is_named(
    work: Path,
) -> None:
    from livery.workshop._metrics import ROW_FILE, RUNS

    half = RUNS.series(RUN.run_id, LEG)
    assert half.put(work, {ROW_FILE: {"job": "check"}}, message="half") == ""
    legs, why = _coverage_store.run_legs(work, RUN)
    assert why == "" and len(legs) == 1
    assert legs[0].key == _state.slug(LEG) and legs[0].units == {}
    assert legs[0].why == f"{half.ref} carries no {_coverage_store.RUN_FILE}"
    foreign = {_coverage_store.RUN_FILE: json.dumps({"schema": 99})}
    assert _state.put(work, half.ref, foreign, message="foreign") == ""
    legs, _why = _coverage_store.run_legs(work, RUN)
    assert legs[0].why.startswith(half.ref) and "this reader speaks" in legs[0].why


def test_the_closure_id_refuses_a_directory_head_lacks(work: Path) -> None:
    base, _top = _packages(work)
    ghost = Package(
        directory=work / "packages" / "ghost",
        path="packages/ghost",
        name="livery-ghost",
        type="python",
        depends=(),
    )
    with pytest.raises(GitError):
        _coverage_store.closure_id(GitOps(work), (base, ghost), ghost)


# --- the identity -------------------------------------------------------------


def test_the_closure_id_follows_the_closure_and_the_root_pins(work: Path) -> None:
    base, top = _packages(work)
    git = GitOps(work)
    packages = (base, top)
    before = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert _coverage_store.closure(packages, top) == (base, top)
    assert _coverage_store.closure(packages, base) == (base,)
    # A change inside the dependency moves the dependant's identity too.
    (work / "packages" / "base" / "src" / "base" / "mod.py").write_text("a = 2\n")
    _git(work, "commit", "-qam", "base changes")
    after = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert after["base"] != before["base"] and after["top"] != before["top"]
    # A change inside the dependant leaves the dependency's identity alone.
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 3\n")
    _git(work, "commit", "-qam", "top changes")
    again = {
        "base": _coverage_store.closure_id(git, packages, base),
        "top": _coverage_store.closure_id(git, packages, top),
    }
    assert again["base"] == after["base"] and again["top"] != after["top"]
    # A pin change is a dependency change for every suite.
    (work / "uv.lock").write_text("version = 2\n")
    _git(work, "commit", "-qam", "lock moves")
    pinned = _coverage_store.closure_id(git, packages, base)
    assert pinned != again["base"]
    # Files outside the closure are not the suite's to store.
    assert _coverage_store.in_closure(packages, top, "packages/base/src/base/mod.py")
    assert not _coverage_store.in_closure(packages, base, "packages/top/src/top/mod.py")
    assert _coverage_store.in_closure(packages, top, "packages\\top\\src\\top\\mod.py")


# --- the reads and the writes -------------------------------------------------


def test_a_legs_lines_ride_its_per_run_ref_and_go_with_it(work: Path) -> None:
    from livery.workshop import _metrics

    unit = _unit("packages/base", files={"packages/base/src/base/mod.py": [3, 1, 2]})
    why = _coverage_store.put_run(
        work,
        RUN,
        leg=LEG,
        scope="affected",
        packages=("packages/base",),
        units={"packages/base": unit},
    )
    assert why == ""
    ref = _metrics.run_ref(RUN, LEG)
    held = _state.read(work, ref).files
    assert held is not None and set(held) == {_coverage_store.RUN_FILE}
    legs, why = _coverage_store.run_legs(work, RUN)
    assert why == "" and len(legs) == 1
    leg = legs[0]
    assert (leg.key, leg.label, leg.scope, leg.packages, leg.why) == (
        _state.slug(LEG),
        LEG,
        "affected",
        ("packages/base",),
        "",
    )
    assert leg.units == {
        "packages/base": _coverage_store.Unit(
            "packages/base",
            "a" * 64,
            "1013",
            "b" * 40,
            {"packages/base/src/base/mod.py": [1, 2, 3]},
        )
    }
    # Another run's refs are another run's; the lines go with the ref.
    other = _state.RunContext("gitea", "9", "push", "refs/heads/main", leg=LEG)
    assert _coverage_store.run_legs(work, other) == ([], "")
    assert _state.drop(work, ref) == ""
    assert _coverage_store.run_legs(work, RUN) == ([], "")


def test_the_record_is_replaced_in_place_fresh_over_carried_stale_removed(
    work: Path,
) -> None:
    series = _coverage_store.RECORD.series(_coverage_store.MAIN, LEG)
    assert series.ref == _coverage_store.record_ref(LEG)
    assert series.ref == _state.NAMESPACE + "coverage/main/" + _state.slug(LEG)
    base = _unit("packages/base", run="1", files={"packages/base/src/b.py": [1]})
    top = _unit("packages/top", closure="c" * 64, run="1")
    ghost = _unit("packages/ghost", run="1")
    fresh = {"packages/base": base, "packages/top": top, "packages/ghost": ghost}
    assert _coverage_store.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    held = _coverage_store.recorded(work, leg=LEG)
    assert held.units == fresh and held.skipped == ()
    first = {row.name: row.when for row in series.rows(work).rows}
    # The next merge: base fresh again, top carried untouched, ghost gone.
    again = _unit("packages/base", closure="d" * 64, run="2")
    later = _state.RunContext("gitea", "2", "push", "refs/heads/main", leg=LEG)
    stale = held.stale(["packages/base", "packages/top"])
    assert stale == [_coverage_store.row_name("packages/ghost")]
    why = _coverage_store.put_record(
        work, later, leg=LEG, fresh={"packages/base": again}, remove=stale
    )
    assert why == ""
    held = _coverage_store.recorded(work, leg=LEG)
    assert held.units == {"packages/base": again, "packages/top": top}
    rows = {row.name: row.when for row in series.rows(work).rows}
    assert rows[_coverage_store.row_name("packages/top")] == first["packages-top.json"]
    assert rows["packages-base.json"] > first["packages-base.json"]
    # Another leg's record is another ref.
    assert _coverage_store.recorded(work, leg="check-macos-3.13") == (
        _coverage_store.Record({})
    )


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
    assert _coverage_store.current_keys(work) is None
    _contract(work, '"ubuntu-latest", "macos-latest"')
    assert _coverage_store.current_keys(work) == {
        ("main", "check-ubuntu-latest-3-14"),
        ("main", "check-macos-latest-3-14"),
    }
    # A branch on origin is a base while it lives, spelled ref-safe.
    _git(work, "push", "-q", "origin", "main:refs/heads/feat/x")
    assert _coverage_store.branches(work) == ["feat/x", "main"]
    assert _coverage_store.current_keys(work) == {
        ("main", "check-ubuntu-latest-3-14"),
        ("main", "check-macos-latest-3-14"),
        ("feat-x", "check-ubuntu-latest-3-14"),
        ("feat-x", "check-macos-latest-3-14"),
    }
    # A remote that cannot be listed tells no keys, and nothing is dropped.
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert _coverage_store.branches(work) is None
    assert _coverage_store.current_keys(work) is None


def test_the_janitor_drops_a_closure_keyed_ref_and_keeps_the_record_and_the_marks(
    work: Path,
) -> None:
    # A ref of the shape the family no longer produces, one leg and
    # unit keyed by closure, is an orphan under the current keys; the
    # marks series beside the family is not the family's at all.
    old = _state.NAMESPACE + "coverage/check-linux-3-14/livery-base"
    marks = _state.NAMESPACE + "coverage/marks"
    assert _state.put(work, old, {"x": "{}"}, message="old") == ""
    assert _state.put(work, marks, {"m": "{}"}, message="marks") == ""
    _contract(work, '"linux"')
    fresh = {"packages/base": _unit("packages/base")}
    assert _coverage_store.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    # A branch's record lives while the branch does, and goes with it.
    _git(work, "push", "-q", "origin", "main:refs/heads/feat/x")
    assert (
        _coverage_store.put_record(work, PULL, leg=LEG, fresh=fresh, base="feat/x")
        == ""
    )
    branch_ref = _coverage_store.record_ref(LEG, "feat/x")
    lines = _state.sweep(work, (_coverage_store.RECORD,), remote=True)
    assert f"  {old}: no current base and leg produces it; dropped" in lines
    assert f"  {_coverage_store.record_ref(LEG)}: 1 row(s), within its bounds" in lines
    assert f"  {branch_ref}: 1 row(s), within its bounds" in lines
    assert _state.read(work, old).files is None
    assert _state.read(work, marks).files == {"m": "{}"}
    assert _coverage_store.recorded(work, leg=LEG).units == fresh
    _git(work, "push", "-q", "origin", ":refs/heads/feat/x")
    lines = _state.sweep(work, (_coverage_store.RECORD,), remote=True)
    assert f"  {branch_ref}: no current base and leg produces it; dropped" in lines
    assert _state.read(work, branch_ref).files is None
    assert _coverage_store.recorded(work, leg=LEG).units == fresh


def test_the_stored_union_pulls_every_recorded_unit_and_names_the_misses(
    work: Path, tmp_path: Path
) -> None:
    from livery.workshop._backends._python import stored_union

    fresh = {
        "packages/base": _unit("packages/base", files={"packages/base/src/x.py": [1]}),
        "packages/top": _unit("packages/top", files={"packages/top/src/x.py": [1, 2]}),
    }
    assert _coverage_store.put_record(work, RUN, leg=LEG, fresh=fresh) == ""
    into = tmp_path / "pages"
    files, misses = stored_union(work, [LEG, "check-other"], into)
    assert sorted(path.name for path in files) == [
        "reuse-packages-base.coverage",
        "reuse-packages-top.coverage",
    ]
    assert all(path.parent == into / LEG for path in files)
    assert misses == ["main/check-other: nothing recorded"]
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    _files, misses = stored_union(work, [LEG], into)
    assert len(misses) == 1 and misses[0].startswith(f"main/{LEG} (")


# --- the workspace's own tests, a unit keyed by the whole tree -----------------


def test_the_workspace_tests_are_a_unit_keyed_by_the_tree(work: Path) -> None:
    from livery.workshop._packages import Package

    base, top = _packages(work)
    assert _coverage_store.workspace_suite(work / "nowhere") is None
    (work / "tests").mkdir()
    unit = _coverage_store.workspace_suite(work)
    assert unit is not None and unit.path == "tests" and unit.name == "workspace-tests"
    git = GitOps(work)
    (work / "tests" / "test_all.py").write_text("def test_it():\n    pass\n")
    _git(work, "add", "tests")
    _git(work, "commit", "-qm", "workspace tests")
    before = _coverage_store.closure_id(git, (base, top), unit)
    # Prose leaves the key alone; a package or a pin moves it.
    (work / "notes").mkdir()
    (work / "notes" / "plan.md").write_text("# plan\n")
    _git(work, "add", "notes")
    _git(work, "commit", "-qm", "a note")
    assert _coverage_store.closure_id(git, (base, top), unit) == before
    (work / "packages" / "top" / "src" / "top" / "mod.py").write_text("a = 9\n")
    _git(work, "commit", "-qam", "top changes")
    after = _coverage_store.closure_id(git, (base, top), unit)
    assert after != before
    (work / "uv.lock").write_text("version = 3\n")
    _git(work, "commit", "-qam", "lock moves")
    assert _coverage_store.closure_id(git, (base, top), unit) != after
    # Any file is the unit's to store: its tests reach every package.
    assert _coverage_store.in_closure(
        (base, top), unit, "packages/base/src/base/mod.py"
    )
    assert _coverage_store.in_closure((base, top), unit, "tests/test_x.py")
    assert isinstance(unit, Package)
    assert _coverage_store.row_name(unit.path) == "tests.json"
