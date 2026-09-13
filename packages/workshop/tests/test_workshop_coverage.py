"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _coverage_store, _verified
from livery.workshop._backends import _python
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)


def _record(written: list[dict[str, object]]) -> Callable[..., str]:
    """A stand-in for the store's write: it records the row and reports success."""

    def _write(root: Path, **kw: object) -> str:
        written.append(kw)
        return ""

    return _write


def _package(tmp_path: Path, name: str, extra: str = "") -> Package:
    directory = tmp_path / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n{extra}'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=(),
    )


def test_the_floor_policy_comes_from_the_contract_and_refuses_nonsense(
    tmp_path: Path,
) -> None:
    bare = _package(tmp_path, "bare")
    assert _python.coverage_policy(bare) is None
    assert _python.coverage_floor(bare) is None
    floored = _package(tmp_path, "floored", "[qa]\ncoverage-floor = 87\n")
    assert _python.coverage_policy(floored) == _python.FloorPolicy(87.0, False, 0.5)
    assert _python.coverage_floor(floored) == 87.0
    ratchet = _package(
        tmp_path,
        "ratchet",
        '[qa]\ncoverage-floor = "auto-ratchet"\ncoverage-epsilon = 1.5\n',
    )
    assert _python.coverage_policy(ratchet) == _python.FloorPolicy(None, True, 1.5)
    assert _python.coverage_floor(ratchet) is None
    for name, extra, words in (
        ("odd", '[qa]\ncoverage-floor = "high"\n', "auto-ratchet"),
        ("tight", '[qa]\ncoverage-floor = 90\ncoverage-epsilon = "tight"\n', "points"),
        ("negative", "[qa]\ncoverage-floor = 90\ncoverage-epsilon = -1\n", "negative"),
    ):
        with pytest.raises(_FAILURES, match=words):
            _python.coverage_policy(_package(tmp_path, name, extra))


def test_enforcement_grants_the_epsilon_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 90\n")
    measured = {"packages/thing": 89.6}
    monkeypatch.setattr(_python, "measured_coverage", lambda root, packages: measured)
    assert _python.enforce_coverage(tmp_path, (package,)) == measured  # inside
    measured["packages/thing"] = 89.4
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below the committed floor" in str(caught.value)
    wide = _package(
        tmp_path, "wide", "[qa]\ncoverage-floor = 90\ncoverage-epsilon = 2\n"
    )
    measured["packages/wide"] = 88.1
    _python.enforce_coverage(tmp_path, (wide,))  # a declared epsilon widens the floor


def _ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, percent: float
) -> Package:
    directory = tmp_path / "packages" / "thing"
    if directory.is_dir():
        package = Package(
            directory=directory,
            path="packages/thing",
            name="livery-thing",
            type="python",
            depends=(),
        )
    else:
        package = _package(tmp_path, "thing", '[qa]\ncoverage-floor = "auto-ratchet"\n')
    monkeypatch.setattr(
        _python, "measured_coverage", lambda root, packages: {"packages/thing": percent}
    )
    return package


def test_under_auto_ratchet_an_unreadable_store_falls_open_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 50.0)
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: (None, "remote down"))

    def _never(root: Path, **kw: object) -> str:
        raise AssertionError("an unread store is never written")

    monkeypatch.setattr(_coverage_marks, "write_mark", _never)
    _in_ci(monkeypatch, "check-a")
    assert _python.enforce_coverage(tmp_path, (package,)) == {"packages/thing": 50.0}
    out = capsys.readouterr().out
    assert "auto-ratchet: remote down; the gate falls open and records nothing" in out


def test_the_first_run_records_the_mark_only_in_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 80.0)
    monkeypatch.setattr(_coverage_marks, "marks", lambda root: ({}, ""))
    written: list[dict[str, object]] = []
    monkeypatch.setattr(_coverage_marks, "write_mark", _record(written))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    _python.enforce_coverage(tmp_path, (package,))
    out = capsys.readouterr().out
    assert "no mark yet; this run records it" in out
    assert "a CI run records the mark; this run does not" in out
    assert written == []
    _in_ci(monkeypatch, "check-a")
    _python.enforce_coverage(tmp_path, (package,))
    assert "    recorded" in capsys.readouterr().out
    assert written == [
        {
            "package": "packages/thing",
            "value": 80.0,
            "kind": "first",
            "by": "run 7",
        }
    ]


def test_a_clearing_run_ratchets_a_dip_passes_and_a_fall_names_the_accept_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_marks

    mark = _coverage_marks.Mark("packages/thing", 90.0, "first", "run 3", "", "when")
    monkeypatch.setattr(
        _coverage_marks, "marks", lambda root: ({mark.package: mark}, "")
    )
    written: list[dict[str, object]] = []
    monkeypatch.setattr(_coverage_marks, "write_mark", _record(written))
    _in_ci(monkeypatch, "check-a")
    package = _ratchet(tmp_path, monkeypatch, 90.6)
    _python.enforce_coverage(tmp_path, (package,))
    assert "new mark: 90.6%" in capsys.readouterr().out
    assert [kw["kind"] for kw in written] == ["ratchet"]
    _ratchet(tmp_path, monkeypatch, 89.6)
    _python.enforce_coverage(tmp_path, (package,))
    assert len(written) == 1  # a dip inside epsilon passes and moves nothing
    _ratchet(tmp_path, monkeypatch, 89.4)
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below its mark of 90.0%" in str(caught.value)
    assert "coverage.accept packages/thing <value> --reason=" in str(caught.value)


class _Result:
    def __init__(self, code: int) -> None:
        self.code = code
        self.stdout = f"suite output (exit {code})\n" if code else ""
        self.stderr = ""


class _FakePytest:
    """Records each call's arguments and the environment it was bound with."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
        self.codes = codes or {}
        self._env: dict[str, str] | None = None

    def opts(self, **kwargs: object) -> _FakePytest:
        env = kwargs.get("env")
        self._env = dict(env) if isinstance(env, dict) else None
        return self

    def __call__(self, *args: str) -> _Result:
        self.calls.append((args, self._env))
        self._env = None
        return _Result(self.codes.get(args[0] if args else "", 0))


def _suite(tmp_path: Path, name: str) -> Package:
    package = _package(tmp_path, name, "[qa]\ncoverage-floor = 1\n")
    (package.directory / "tests").mkdir()
    return package


def test_inside_ci_the_tests_run_metered_and_the_gate_itself_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(_python, "enforce_coverage", refuse)
    _in_ci(monkeypatch, "check-a")
    _python.run_test(packages=(thing, other), root=tmp_path, scoped=True)
    # One process for every suite, under the leg's own prefix: the
    # plugin names each test's context, and the leg splits the data.
    # The meter is armed in pytest's environment alone, with the
    # config's absolute path, and never in the gate's own.
    assert [args for args, _env in fake.calls] == [
        ("packages/thing/tests", "packages/other/tests")
    ]
    env = fake.calls[0][1]
    assert env is not None
    assert env["COVERAGE_PROCESS_START"] == str(tmp_path / "pyproject.toml")
    assert env["WORKSHOP_LEG"] == "check-a"  # the rest of the environment rides
    assert "COVERAGE_PROCESS_START" not in os.environ
    # The workspace's own tests ride every scoped run once they exist.
    (tmp_path / "tests").mkdir()
    fake.calls.clear()
    _python.run_test(packages=(thing,), root=tmp_path, scoped=True)
    assert [args for args, _env in fake.calls] == [("packages/thing/tests", "tests")]
    assert not any("--cov" in args for args, _env in fake.calls)


def test_the_preview_tolerates_a_run_that_measured_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A selection of tests that reached no source leaves coverage with
    # no data: the preview says so, and a judged read still refuses.
    # The real tool, on a directory without a data file: the toolroom
    # raises on a non-zero exit unless told not to, and a fake that
    # returned a result would hide that.
    del monkeypatch
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 1\n")
    _python.report_coverage(tmp_path, (package,))
    assert "nothing measured by this run" in capsys.readouterr().out
    with pytest.raises(_FAILURES, match="coverage json exited 1"):
        _python.measured_coverage(tmp_path, (package,))


def test_without_a_parent_the_meter_and_the_preview_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 1\n")
    seen: list[tuple[str, ...]] = []
    enforced: list[Path] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())
    monkeypatch.setattr(
        _python, "report_coverage", lambda root, packages: enforced.append(root)
    )
    # A runner's own environment would make this a CI run: scrubbed,
    # so the test reads the same on a runner as on a desk.
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    _python.run_test(packages=(package,), root=tmp_path, scoped=True)
    assert any(arg == "--cov" for arg in seen[0])
    assert "packages/thing/tests" not in seen[0]  # no tests dir exists
    assert enforced == [tmp_path]


def test_a_machines_run_takes_the_runners_shape_and_a_selection_stands_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    (tmp_path / "tests").mkdir()
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)
    monkeypatch.setattr(_python, "report_coverage", lambda root, packages: None)
    for name in ("CI", "GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    selection = {
        "packages/thing": ("packages/thing/tests/test_a.py",),
        "tests": ("tests/test_all.py",),
    }
    _python.run_test(
        packages=(thing, other), root=tmp_path, scoped=True, selection=selection
    )
    args, env = fake.calls[0]
    assert args[:3] == (
        "packages/thing/tests/test_a.py",
        "packages/other/tests",
        "tests/test_all.py",
    )
    assert "--cov" in args
    # The runner's variables are set in pytest's environment alone, so
    # a test that reads them is judged here as on the leg.
    assert env is not None and env["CI"] == "true"
    assert env["GITHUB_ACTIONS"] == "true"
    assert "GITHUB_ACTIONS" not in os.environ
    # The kind's own test call: the selection is the package's alone.
    fake.calls.clear()
    _python.test(thing, tmp_path, selection=("tests/test_b.py",))
    assert fake.calls[0][0][:2] == ("packages/thing/tests/test_b.py", "tests")
    fake.calls.clear()
    _python.test(thing, tmp_path)
    assert fake.calls[0][0][:2] == ("packages/thing/tests", "tests")


# --- the leg: its refusal, then what it stores --------------------------------


def test_the_units_a_leg_ran_come_from_its_marker(tmp_path: Path) -> None:
    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    z = _package(tmp_path, "z")  # no suite of its own
    full = {"scope": "full", "packages": [], "leg": "check-a"}
    narrowed = {
        "scope": "affected",
        "packages": ["packages/y", "packages/z"],
        "leg": "a",
    }
    assert _python.suites_that_ran(full, tmp_path, (x, y, z)) == (x, y)
    assert _python.suites_that_ran(narrowed, tmp_path, (x, y, z)) == (y,)
    for scope in ("verified", "nothing", "unknown"):
        marker = {"scope": scope, "packages": ["packages/x"], "leg": "a"}
        assert _python.suites_that_ran(marker, tmp_path, (x, y, z)) == ()
    (tmp_path / "tests").mkdir()
    ran = _python.suites_that_ran(narrowed, tmp_path, (x, y, z))
    assert [unit.path for unit in ran] == ["packages/y", "tests"]
    assert _python.units_of(tmp_path, (x, y, z))[-1].path == "tests"


def test_a_leg_with_no_metered_data_refuses_naming_the_meter(tmp_path: Path) -> None:
    x = _package(tmp_path, "x")
    with pytest.raises(BaseException, match="COVERAGE_PROCESS_START"):
        _python.combine_leg(tmp_path, (x,))


def test_a_workspace_with_no_packages_puts_its_scope_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import FULL, write_marker

    # A project just born has no packages: its own tests ran unmetered
    # and reached no package source. That is not a dead meter: the
    # tests unit is put with no lines, so the union finds what ran.
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "check-a")
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.head_sha", lambda self: "a" * 40
    )
    write_marker(tmp_path, FULL, leg="check-a")
    put: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        put.append(kw)
        return ""

    monkeypatch.setattr(_coverage_store, "put_run", _capture)
    _python.combine_leg(tmp_path, ())
    assert "no packages to measure" in capsys.readouterr().out
    assert len(put) == 1 and put[0]["scope"] == "full"
    units = put[0]["units"]
    assert isinstance(units, dict) and list(units) == ["tests"]
    unit = units["tests"]
    assert isinstance(unit, _coverage_store.Unit)
    assert unit.files == {} and unit.closure == "k" * 64 and unit.sha == "a" * 40
    # A skipped leg names no unit whatever the packages, so the union
    # carries the tests unit from the record instead of finding it
    # named and unmeasured.
    from livery.workshop._verified import VERIFIED

    write_marker(tmp_path, VERIFIED, leg="check-a")
    _python.combine_leg(tmp_path, ())
    assert put[1]["scope"] == "verified" and put[1]["units"] == {}


def _in_ci(monkeypatch: pytest.MonkeyPatch, leg: str) -> None:
    for name in (
        "GITHUB_EVENT_PATH",
        "GITHUB_SHA",
        "GITHUB_REF",
        "GITHUB_JOB",
        "GITHUB_HEAD_REF",
        "GITHUB_EVENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setenv("WORKSHOP_LEG", leg)
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)


def _arcs(lines: list[int]) -> list[tuple[int, int]]:
    """The arcs of one straight run through *lines*: the entry, each step, the exit."""
    from itertools import pairwise

    return sorted(pairwise([-1, *lines, -1]))


def _merged(*runs: list[int]) -> list[tuple[int, int]]:
    """The arcs of several straight runs, as one row holds them."""
    return sorted(set().union(*(set(_arcs(lines)) for lines in runs)))


def _contextual_part(
    tmp_path: Path, suffix: str, recorded: dict[str, dict[str, list[int]]]
) -> None:
    """A metered part: *recorded* maps a context ("" for none) to lines run per file."""
    from coverage import CoverageData

    data = CoverageData(basename=str(tmp_path / ".coverage"), suffix=suffix)
    for context, files in recorded.items():
        data.set_context(context)
        data.add_arcs({file: set(_arcs(lines)) for file, lines in files.items()})
    data.write()


def test_a_leg_puts_each_suite_it_ran_within_its_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import AFFECTED, FULL, write_marker

    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    # Import time (no context) touched both sources; x's test ran x's lines
    # and, through a shared process, one of y's; y's suite did not run.
    _contextual_part(
        tmp_path,
        "host.1.X",
        {
            "": {x_source: [1], y_source: [1]},
            "packages/x/tests/test_mod.py::test_one|run": {
                x_source: [2, 3],
                y_source: [2],
            },
        },
    )
    _contextual_part(tmp_path, "host.2.X", {"": {x_source: [4]}})
    write_marker(tmp_path, AFFECTED, ("packages/x",), leg="check-a")
    put: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        put.append(kw)
        return ""

    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_coverage_store, "put_run", _capture)
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.head_sha", lambda self: "a" * 40
    )
    _in_ci(monkeypatch, "check-a")
    _python.combine_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    # Only x's suite ran: its own lines plus the import-time lines of its
    # closure, put once with the scope the gate ran.
    assert [sorted(_units(kw)) for kw in put] == [["packages/x"]]
    assert put[0]["leg"] == "check-a" and put[0]["scope"] == "affected"
    assert put[0]["packages"] == ("packages/x",)
    unit = _units(put[0])["packages/x"]
    assert unit.files == {"packages/x/src/livery/x/mod.py": _merged([1], [2, 3], [4])}
    assert unit.closure == "k" * 64 and unit.run == "7" and unit.sha == "a" * 40
    assert (
        "coverage store: packages/x stored for closure kkkkkkkkkkkk on check-a" in out
    )
    assert "coverage store: 1 unit(s) on the run's ref for check-a (affected)" in out
    assert "packages/y" not in out
    assert (tmp_path / ".coverage").is_file()
    assert not (tmp_path / _python.SUITES_DATA).exists()
    # A full leg puts every suite, y's from the import-time lines of
    # its closure alone, and the workspace's own tests as a unit whose
    # closure is everything.
    (tmp_path / "tests").mkdir()
    put.clear()
    _contextual_part(
        tmp_path,
        "host.3.X",
        {
            "": {x_source: [1], y_source: [1]},
            "tests/test_all.py::test_it|run": {y_source: [2, 3]},
        },
    )
    write_marker(tmp_path, FULL, leg="check-a")
    _python.combine_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    units = _units(put[0])
    assert sorted(units) == ["packages/x", "packages/y", "tests"]
    assert units["packages/y"].files == {"packages/y/src/livery/y/mod.py": _arcs([1])}
    assert units["tests"].files == {
        "packages/x/src/livery/x/mod.py": _arcs([1]),
        "packages/y/src/livery/y/mod.py": _merged([1], [2, 3]),
    }
    assert "coverage store: tests stored for closure kkkkkkkkkkkk on check-a" in out


def _units(kw: dict[str, object]) -> dict[str, _coverage_store.Unit]:
    units = kw["units"]
    assert isinstance(units, dict)
    return units


def test_a_leg_that_cannot_put_its_lines_is_red_and_a_skipped_leg_puts_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import FULL, VERIFIED, write_marker

    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    _contextual_part(tmp_path, "host.1.X", {"": {source: [1]}})
    write_marker(tmp_path, FULL, leg="check-a")
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.head_sha", lambda self: "a" * 40
    )
    monkeypatch.setattr(
        _coverage_store, "put_run", lambda root, run, **kw: "push refused: down"
    )
    _in_ci(monkeypatch, "check-a")
    with pytest.raises(
        _FAILURES,
        match=r"could not be put on its per-run ref \(push refused: down\)",
    ):
        _python.combine_leg(tmp_path, (x,))
    # A leg whose gate skipped measured nothing and says so on its ref,
    # so the union reads a skip, never a leg that died.
    for part in tmp_path.glob(".coverage*"):
        part.unlink()
    write_marker(tmp_path, VERIFIED, leg="check-a")
    put: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        put.append(kw)
        return ""

    monkeypatch.setattr(_coverage_store, "put_run", _capture)
    _python.combine_leg(tmp_path, (x,))
    out = capsys.readouterr().out
    assert put == [
        {
            "leg": "check-a",
            "scope": "verified",
            "packages": (),
            "units": {},
            "timing": None,
        }
    ]
    assert "coverage: no data, the gate ran 'verified'; nothing to combine" in out
    assert "coverage store: 0 unit(s) on the run's ref for check-a (verified)" in out
    # Outside CI nothing is put, and the measurement is only named.
    monkeypatch.delenv("GITHUB_ACTIONS")
    put.clear()
    _python.combine_leg(tmp_path, (x,))
    assert put == []
    assert "0 unit(s) measured; outside CI nothing is stored" in capsys.readouterr().out


# --- the union: its refusals, then the reuse ----------------------------------


def _leg(
    key: str,
    scope: str,
    packages: tuple[str, ...] = (),
    units: dict[str, _coverage_store.Unit] | None = None,
    *,
    label: str = "check-a",
    why: str = "",
) -> _coverage_store.Leg:
    """What one leg put on its per-run ref, as the union reads it."""
    return _coverage_store.Leg(key, label, scope, packages, dict(units or {}), why=why)


def _unit(
    path: str, files: dict[str, list[int]], *, run: str = "7", closure: str = "k" * 64
) -> _coverage_store.Unit:
    """A unit whose files hold the arcs of one straight run through the lines given."""
    arcs = {file: _arcs(lines) for file, lines in files.items()}
    return _coverage_store.Unit(path, closure, run, "a" * 40, arcs)


def _source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "packages" / name / "src" / "livery" / name / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    return source


def _union(
    monkeypatch: pytest.MonkeyPatch,
    legs: list[_coverage_store.Leg],
    *,
    held: _coverage_store.Record | None = None,
    records: dict[str, _coverage_store.Record] | None = None,
    listing: str = "",
    proved: str = "",
) -> list[tuple[str, str, list[str], list[str]]]:
    """Stand in for the store: the legs, the records by base, and the writes.

    *held* is main's record, *records* the others by base, *proved* the
    branch the verified row names for a push's tree.
    """
    by_base = dict(records or {})
    if held is not None:
        by_base["main"] = held
    monkeypatch.setattr(_coverage_store, "run_legs", lambda root, run: (legs, listing))
    monkeypatch.setattr(
        _coverage_store,
        "recorded",
        lambda root, *, leg, base="main": by_base.get(base, _coverage_store.Record({})),
    )
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_python, "_moved_on", lambda git, run, branch: ("", ""))
    row = _verified.Verified("t", "5", "a" * 40, "full", ("check-a",), branch=proved)
    monkeypatch.setattr(_verified, "tree_id", lambda git, ref="HEAD": "t")
    monkeypatch.setattr(_verified, "record", lambda root, tree: (row, ""))
    written: list[tuple[str, str, list[str], list[str]]] = []

    def _put(
        root: Path,
        run: object,
        *,
        leg: str,
        fresh: dict[str, _coverage_store.Unit],
        remove: tuple[str, ...] = (),
        base: str = "main",
    ) -> str:
        written.append((base, leg, sorted(fresh), sorted(remove)))
        return ""

    monkeypatch.setattr(_coverage_store, "put_record", _put)
    return written


def test_a_union_outside_ci_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A runner's own environment would make this a CI run: scrubbed,
    # so the test reads the same on a runner as on a desk.
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(_FAILURES, match="outside CI there is no run"):
        _python.combine_union(tmp_path, ())


def test_a_union_with_no_leg_on_the_runs_refs_refuses_naming_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [])
    with pytest.raises(_FAILURES, match="no check leg left its lines on run 7's refs"):
        _python.combine_union(tmp_path, ())
    _union(monkeypatch, [], listing="the remote could not be listed")
    with pytest.raises(
        _FAILURES, match=r"could not be read \(the remote could not be listed\)"
    ):
        _python.combine_union(tmp_path, ())


def test_a_leg_without_its_file_its_label_or_a_known_scope_refuses_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "", why="refs/x carries no coverage.json")])
    with pytest.raises(
        _FAILURES, match=r"leg check-a: refs/x carries no coverage\.json; the leg died"
    ):
        _python.combine_union(tmp_path, ())
    _union(monkeypatch, [_leg("check-a", "verified", label="")])
    with pytest.raises(_FAILURES, match="leg check-a names no label"):
        _python.combine_union(tmp_path, ())
    _union(monkeypatch, [_leg("check-a", "odd")])
    with pytest.raises(_FAILURES, match=r"a scope the union does not read \('odd'\)"):
        _python.combine_union(tmp_path, ())


def test_a_leg_that_ran_a_suite_and_left_its_lines_out_refuses_naming_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = _suite(tmp_path, "x")
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "full")])
    with pytest.raises(
        _FAILURES, match="leg check-a ran 'full' and its ref lacks packages/x, tests"
    ):
        _python.combine_union(tmp_path, (x,))


def test_a_skipped_suite_the_record_cannot_supply_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = _suite(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "verified")])
    with pytest.raises(
        _FAILURES,
        match=r"no record holds packages/x at closure kkkkkkkkkkkk \(main none\)\.",
    ):
        _python.combine_union(tmp_path, (x,))
    held = _coverage_store.Record(
        {"packages/x": _unit("packages/x", {}, closure="j" * 64)}
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    _union(monkeypatch, [_leg("check-a", "nothing")], held=held)
    with pytest.raises(
        _FAILURES,
        match=r"at closure kkkkkkkkkkkk \(feat/x none, main only at jjjjjjjjjjjj\)",
    ):
        _python.combine_union(tmp_path, (x,))
    down = _coverage_store.Record({}, failed=True, reason="remote down")
    _union(monkeypatch, [_leg("check-a", "verified")], held=down)
    with pytest.raises(
        _FAILURES, match=r"main's record on check-a could not be read \(remote down\)"
    ):
        _python.combine_union(tmp_path, (x,))
    assert not (tmp_path / ".coverage").exists()


def test_a_skipped_leg_reuses_every_unit_from_mains_record_and_writes_its_branchs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    (tmp_path / "tests").mkdir()  # the workspace's own tests, a unit too
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    held = _coverage_store.Record(
        {
            "packages/x": _unit("packages/x", {source: [1, 2, 3, 4]}),
            "tests": _unit("tests", {source: [1]}, run="6"),
        }
    )
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "coverage: leg check-a ran 'verified': no suite, no data" in out
    assert "coverage: packages/x on check-a: reused from run 7 (1 files), main's" in out
    assert "coverage: tests on check-a: reused from run 6 (1 files), main's" in out
    assert "the union of 0 leg(s) and 2 reused suite(s)" in out
    # The rows carried from main are copied onto the branch's record,
    # which is its own from here.
    assert written == [("feat/x", "check-a", ["packages/x", "tests"], [])]
    assert "coverage record: feat/x/check-a: 0 fresh, 2 carried, 0 removed" in out
    assert _python.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}
    # A pull request run that names no branch has no record to write.
    monkeypatch.delenv("GITHUB_HEAD_REF")
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert written == []
    assert (
        "coverage record: not written, a pull_request run that names no branch"
        " has no record of its own" in out
    )


def test_a_branchs_own_record_is_asked_before_mains_and_its_rows_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    own = _coverage_store.Record(
        {"packages/x": _unit("packages/x", {source: [1, 2, 3, 4]}, run="9")}
    )
    main = _coverage_store.Record(
        {
            "packages/x": _unit("packages/x", {source: [1, 2]}, run="7"),
            "tests": _unit("tests", {source: [3, 4]}, run="6"),
        }
    )
    written = _union(
        monkeypatch, [_leg("check-a", "verified")], held=main, records={"feat/x": own}
    )
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert (
        "coverage: packages/x on check-a: reused from run 9 (1 files), feat/x's" in out
    )
    assert "coverage: tests on check-a: reused from run 6 (1 files), main's" in out
    # Only the row copied from main is written; the branch's own row stands.
    assert written == [("feat/x", "check-a", ["tests"], [])]
    assert "coverage record: feat/x/check-a: 0 fresh, 2 carried, 0 removed" in out


def test_a_narrowed_leg_reuses_the_suite_it_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    z = _package(tmp_path, "z", "[qa]\ncoverage-floor = 1\n")  # no suite of its own
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    _in_ci(monkeypatch, "gate")
    leg = _leg(
        "check-a",
        "affected",
        ("packages/x",),
        {"packages/x": _unit("packages/x", {x_source: [1, 2, 3, 4]})},
    )
    held = _coverage_store.Record(
        {"packages/y": _unit("packages/y", {y_source: [1, 2, 3, 4]}, run="5")}
    )
    _union(monkeypatch, [leg], held=held)
    assert _python.combine_union(tmp_path, (z, y, x)) == (z, y, x)
    out = capsys.readouterr().out
    assert "coverage: packages/y on check-a: reused from run 5 (1 files)" in out
    assert "unjudged" not in out
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    assert _python.measured_coverage(tmp_path, (x, y)) == {
        "packages/x": 100.0,
        "packages/y": 100.0,
    }


def test_mains_run_copies_the_merged_branchs_record_and_drops_the_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._state import Skipped

    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    leg = _leg(
        "check-a",
        "measured",
        ("packages/x",),
        {
            "packages/x": _unit("packages/x", {x_source: [1, 2, 3, 4]}),
            "tests": _unit("tests", {y_source: [1]}),
        },
    )
    # y comes from the merged branch's record, named by the verified row;
    # main's own row of y is at another closure and is replaced.
    branch = _coverage_store.Record(
        {"packages/y": _unit("packages/y", {y_source: [1, 2, 3, 4]}, run="5")}
    )
    # Main's own row of x is of an older shape: skipped, and replaced by
    # the fresh row of the same name rather than counted as removed.
    held = _coverage_store.Record(
        {
            "packages/y": _unit("packages/y", {}, run="3", closure="j" * 64),
            "packages/gone": _unit("packages/gone", {}, run="3"),
            "tests": _unit("tests", {y_source: [2]}, run="5"),
        },
        skipped=(
            Skipped("junk.json", "does not parse"),
            Skipped("packages-x.json", "schema 1, this reader speaks 2"),
        ),
    )
    written = _union(
        monkeypatch, [leg], held=held, records={"feat/x": branch}, proved="feat/x"
    )
    assert _python.combine_union(tmp_path, (x, y)) == (x, y)
    out = capsys.readouterr().out
    assert "coverage record: main takes feat/x's record for the tree it proved" in out
    assert "coverage: main/check-a: junk.json: does not parse; skipped" in out
    assert (
        "coverage: packages/y on check-a: reused from run 5 (1 files), feat/x's" in out
    )
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    assert written == [
        (
            "main",
            "check-a",
            ["packages/x", "packages/y", "tests"],
            ["junk.json", "packages-gone.json", "packages-x.json"],
        )
    ]
    assert "coverage record: main/check-a: 2 fresh, 1 carried, 2 removed" in out
    # A record that cannot be written prints why and never reddens the
    # union: the next run reruns what it cannot reuse.
    monkeypatch.setattr(
        _coverage_store, "put_record", lambda root, run, **kw: "push refused: down"
    )
    assert _python.combine_union(tmp_path, (x, y)) == (x, y)
    assert (
        "coverage record: main/check-a not written (push refused: down)"
        in capsys.readouterr().out
    )


def test_a_run_whose_branch_moved_on_judges_but_never_writes_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    leg = _leg(
        "check-a",
        "full",
        units={"packages/x": _unit("packages/x", {source: [1, 2, 3, 4]})},
    )
    written = _union(monkeypatch, [leg])
    asked: list[str] = []

    def _elsewhere(git: object, run: object, branch: str) -> tuple[str, str]:
        asked.append(branch)
        return "b" * 40, "a" * 40

    monkeypatch.setattr(_python, "_moved_on", _elsewhere)
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "the union of 1 leg(s) and 0 reused suite(s)" in out
    assert asked == ["feat/x"]
    assert written == []
    assert (
        "coverage record: feat/x's head moved on to bbbbbbbbbbbb since this run's"
        " aaaaaaaaaaaa; not written, the newer run writes" in out
    )


def test_a_push_of_an_unnamed_tree_carries_from_main_alone_and_writes_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    held = _coverage_store.Record(
        {
            "packages/x": _unit("packages/x", {source: [1, 2, 3, 4]}),
            "tests": _unit("tests", {source: [1]}, run="6"),
        }
    )
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "the union of 0 leg(s) and 2 reused suite(s)" in out
    assert "main takes" not in out
    # Every row came from main itself: nothing to write back.
    assert written == []
    assert "coverage record: main/check-a: unchanged, 2 carried" in out


def test_the_union_of_two_legs_covers_what_each_left_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _suite(tmp_path, "x")
    (tmp_path / "packages" / "x" / "workshop.toml").write_text(
        'type = "python"\nname = "livery-x"\n[qa]\ncoverage-floor = 95\n'
    )
    source = str(_source(tmp_path, "x"))
    _in_ci(monkeypatch, "gate")
    legs = [
        _leg(
            "check-a",
            "full",
            units={"packages/x": _unit("packages/x", {source: [1, 2]})},
        ),
        _leg(
            "check-b",
            "affected",
            ("packages/x",),
            {"packages/x": _unit("packages/x", {source: [3, 4]})},
            label="check-b",
        ),
    ]
    _union(monkeypatch, legs)
    assert _python.combine_union(tmp_path, (package,)) == (package,)
    assert (tmp_path / ".coverage").is_file()
    assert "the union of 2 leg(s) and 0 reused suite(s)" in capsys.readouterr().out
    measured = _python.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/x": 100.0}
    _python.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%
