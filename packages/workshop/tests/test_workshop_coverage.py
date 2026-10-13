"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

import pytest

from livery import git
from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _leg as coverage_leg
from livery.extensions.coverage import _record as coverage_record
from livery.extensions.coverage import _union as coverage_union
from livery.extensions.coverage._format import Merged
from livery.extensions.python import _backend as _python
from livery.footman import Failed
from livery.workshop import _verified
from livery.workshop._packages import Package
from workshop_coverage_meter import asked, install_meter, meter, part, ran
from workshop_measured import measure

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
        f'kind = "python"\nname = "livery-{name}"\n{extra}'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        kind="python",
        depends=(),
    )


def test_a_floor_beside_required_false_refuses(tmp_path: Path) -> None:
    for name, floor, shown in (
        ("numbered", "90", "90"),
        ("ratcheted", '"auto-ratchet"', "'auto-ratchet'"),
    ):
        package = _package(
            tmp_path, name, f"[coverage]\nfloor = {floor}\nrequired = false\n"
        )
        with pytest.raises(_FAILURES) as caught:
            coverage_floors.coverage_policy(package)
        assert (
            f"packages/{name}: [coverage] floor is {shown} beside required ="
            " false; a package nothing judges keeps no floor, so remove one of"
            " the two"
        ) in str(caught.value)


def test_a_package_with_coverage_not_required_is_never_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage import _marks as coverage_marks

    package = _package(tmp_path, "thing", "[coverage]\nrequired = false\n")
    (package.directory / "tests").mkdir()
    native_directory = tmp_path / "packages" / "native"
    (native_directory / "tests").mkdir(parents=True)
    (native_directory / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-native"\n[coverage]\nrequired = false\n'
    )
    native = Package(
        directory=native_directory,
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )
    unjudged = coverage_floors.FloorPolicy(None, False, 0.5, required=False)
    assert coverage_floors.coverage_policy(package) == unjudged
    assert coverage_floors.coverage_floor(package) is None

    def _never(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("a package nothing judges reads and writes no mark")

    monkeypatch.setattr(coverage_marks, "marks", _never)
    monkeypatch.setattr(coverage_marks, "write_mark", _never)
    measured = {"packages/thing": 12.5}
    monkeypatch.setattr(
        coverage_floors, "measured_coverage", lambda root, packages, **_kw: measured
    )
    _in_ci(monkeypatch, "gate")
    # Far below any floor, and a native suite never measured: no
    # verdict, no mark, and each line says the package is not judged.
    assert coverage_floors.enforce_coverage(tmp_path, (package, native)) == measured
    out = capsys.readouterr().out
    assert (
        "  coverage packages/thing: 12.5% (not judged: its [coverage] table sets"
        " required = false)\n"
    ) in out
    assert (
        "  coverage packages/native: not measured (not judged: its [coverage]"
        " table sets required = false)\n"
    ) in out
    coverage_floors.report_coverage(tmp_path, (package,))
    assert (
        "  coverage packages/thing: 12.5% here (not judged: its [coverage] table"
        " sets required = false)\n"
    ) in capsys.readouterr().out


def test_a_package_with_tests_and_no_floor_is_judged_at_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fallback first: a package without tests and without a floor
    # has nothing that measures it, so nothing judges it.
    assert coverage_floors.coverage_policy(_package(tmp_path, "bare")) is None
    package = _package(tmp_path, "thing")
    (package.directory / "tests").mkdir()
    assert coverage_floors.coverage_policy(package) == coverage_floors.FloorPolicy(
        100.0, False, 0.5, default=True
    )
    assert coverage_floors.coverage_floor(package) == 100.0
    measured = {"packages/thing": 99.6}
    monkeypatch.setattr(
        coverage_floors, "measured_coverage", lambda root, packages, **_kw: measured
    )
    coverage_floors.report_coverage(tmp_path, (package,))
    assert (
        "  coverage packages/thing: 99.6% here (floor 100.0% by default judges"
        " the CI union)\n"
    ) in capsys.readouterr().out
    assert coverage_floors.enforce_coverage(tmp_path, (package,)) == measured  # inside
    assert (
        "  coverage packages/thing: 99.6% (floor 100.0% by default, epsilon 0.5%)\n"
    ) in capsys.readouterr().out
    measured["packages/thing"] = 99.4
    with pytest.raises(_FAILURES) as caught:
        coverage_floors.enforce_coverage(tmp_path, (package,))
    assert (
        "packages/thing: 99.4% is below the default floor of 100.0% by more than"
        " the 0.5% epsilon; [coverage] floor in its workshop.toml names another"
    ) in str(caught.value)


def test_the_floor_policy_comes_from_the_contract_and_refuses_nonsense(
    tmp_path: Path,
) -> None:
    bare = _package(tmp_path, "bare")
    assert coverage_floors.coverage_policy(bare) is None
    assert coverage_floors.coverage_floor(bare) is None
    floored = _package(tmp_path, "floored", "[coverage]\nfloor = 87\n")
    assert coverage_floors.coverage_policy(floored) == coverage_floors.FloorPolicy(
        87.0, False, 0.5
    )
    assert coverage_floors.coverage_floor(floored) == 87.0
    ratchet = _package(
        tmp_path,
        "ratchet",
        '[coverage]\nfloor = "auto-ratchet"\nepsilon = 1.5\n',
    )
    assert coverage_floors.coverage_policy(ratchet) == coverage_floors.FloorPolicy(
        None, True, 1.5
    )
    assert coverage_floors.coverage_floor(ratchet) is None
    for name, extra, words in (
        ("odd", '[coverage]\nfloor = "high"\n', "auto-ratchet"),
        (
            "tight",
            '[coverage]\nfloor = 90\nepsilon = "tight"\n',
            "it takes a number",
        ),
        ("negative", "[coverage]\nfloor = 90\nepsilon = -1\n", "negative"),
    ):
        with pytest.raises(_FAILURES, match=words):
            coverage_floors.coverage_policy(_package(tmp_path, name, extra))


def test_enforcement_grants_the_epsilon_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[coverage]\nfloor = 90\n")
    measured = {"packages/thing": 89.6}
    monkeypatch.setattr(
        coverage_floors, "measured_coverage", lambda root, packages: measured
    )
    assert coverage_floors.enforce_coverage(tmp_path, (package,)) == measured  # inside
    measured["packages/thing"] = 89.4
    with pytest.raises(_FAILURES) as caught:
        coverage_floors.enforce_coverage(tmp_path, (package,))
    assert "below the committed floor" in str(caught.value)
    wide = _package(tmp_path, "wide", "[coverage]\nfloor = 90\nepsilon = 2\n")
    measured["packages/wide"] = 88.1
    coverage_floors.enforce_coverage(
        tmp_path, (wide,)
    )  # a declared epsilon widens the floor


def _ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, percent: float
) -> Package:
    directory = tmp_path / "packages" / "thing"
    if directory.is_dir():
        package = Package(
            directory=directory,
            path="packages/thing",
            name="livery-thing",
            kind="python",
            depends=(),
        )
    else:
        package = _package(tmp_path, "thing", '[coverage]\nfloor = "auto-ratchet"\n')
    monkeypatch.setattr(
        coverage_floors,
        "measured_coverage",
        lambda root, packages: {"packages/thing": percent},
    )
    return package


def test_under_auto_ratchet_an_unreadable_store_falls_open_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage import _marks as coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 50.0)
    monkeypatch.setattr(coverage_marks, "marks", lambda root: (None, "remote down"))

    def _never(root: Path, **kw: object) -> str:
        raise AssertionError("an unread store is never written")

    monkeypatch.setattr(coverage_marks, "write_mark", _never)
    _in_ci(monkeypatch, "check-a")
    assert coverage_floors.enforce_coverage(tmp_path, (package,)) == {
        "packages/thing": 50.0
    }
    out = capsys.readouterr().out
    assert "auto-ratchet: remote down; the gate falls open and records nothing" in out


def test_the_first_run_records_the_mark_only_in_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage import _marks as coverage_marks

    package = _ratchet(tmp_path, monkeypatch, 80.0)
    monkeypatch.setattr(coverage_marks, "marks", lambda root: ({}, ""))
    written: list[dict[str, object]] = []
    monkeypatch.setattr(coverage_marks, "write_mark", _record(written))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    coverage_floors.enforce_coverage(tmp_path, (package,))
    out = capsys.readouterr().out
    assert "no mark yet; this run records it" in out
    assert "a CI run records the mark; this run does not" in out
    assert written == []
    _in_ci(monkeypatch, "check-a")
    coverage_floors.enforce_coverage(tmp_path, (package,))
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
    from livery.extensions.coverage import _marks as coverage_marks

    mark = coverage_marks.Mark("packages/thing", 90.0, "first", "run 3", "", "when")
    monkeypatch.setattr(
        coverage_marks, "marks", lambda root: ({mark.package: mark}, "")
    )
    written: list[dict[str, object]] = []
    monkeypatch.setattr(coverage_marks, "write_mark", _record(written))
    _in_ci(monkeypatch, "check-a")
    package = _ratchet(tmp_path, monkeypatch, 90.6)
    coverage_floors.enforce_coverage(tmp_path, (package,))
    assert "new mark: 90.6%" in capsys.readouterr().out
    assert [kw["kind"] for kw in written] == ["ratchet"]
    _ratchet(tmp_path, monkeypatch, 89.6)
    coverage_floors.enforce_coverage(tmp_path, (package,))
    assert len(written) == 1  # a dip inside epsilon passes and moves nothing
    _ratchet(tmp_path, monkeypatch, 89.4)
    with pytest.raises(_FAILURES) as caught:
        coverage_floors.enforce_coverage(tmp_path, (package,))
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
    package = _package(tmp_path, name, "[coverage]\nfloor = 1\n")
    (package.directory / "tests").mkdir()
    return package


def test_without_coverage_the_tests_run_unmetered_and_print_no_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thing = _suite(tmp_path, "thing")
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)

    def _never(root: Path, packages: tuple[Package, ...]) -> None:
        raise AssertionError("a run that is not measured prints no preview")

    # Coverage answers the preview while it is listed; a run nothing
    # measures for never asks.
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["coverage"]\n')
    monkeypatch.setattr(coverage_floors, "report_coverage", _never)
    for name in ("CI", "GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    # On a machine the run takes no meter's options.
    _python.run_test(packages=(thing,), root=tmp_path)
    args, _env = fake.calls[-1]
    assert args == ("packages/thing/tests",)
    # Inside CI no meter is armed in pytest's environment.
    _in_ci(monkeypatch, "check-a")
    _python.run_test(packages=(thing,), root=tmp_path)
    args, env = fake.calls[-1]
    assert args == ("packages/thing/tests",)
    assert env is not None and "COVERAGE_PROCESS_START" not in env


def test_inside_ci_the_tests_run_metered_and_the_gate_itself_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(coverage_floors, "enforce_coverage", refuse)
    _in_ci(monkeypatch, "check-a")
    _python.run_test(packages=(thing, other), root=tmp_path, measured=True)
    # One process for every suite, under the leg's own prefix: the
    # plugin names each test's context, and the leg splits the data.
    # The meter is armed in pytest's environment alone, with the
    # config's absolute path, and never in the gate's own.
    assert [args for args, _env in fake.calls] == [
        ("packages/thing/tests", "packages/other/tests")
    ]
    env = fake.calls[0][1]
    assert env is not None
    assert env["COVERAGE_PROCESS_START"] == str(tmp_path / ".coveragerc")
    assert env["WORKSHOP_LEG"] == "check-a"  # the rest of the environment rides
    assert "COVERAGE_PROCESS_START" not in os.environ
    # The workspace's own tests run when their unit is among the
    # packages, and only then.
    from livery.workshop._packages import workspace_suite

    (tmp_path / "tests").mkdir()
    unit = workspace_suite(tmp_path)
    assert unit is not None
    fake.calls.clear()
    _python.run_test(packages=(thing,), root=tmp_path, measured=True)
    _python.run_test(
        packages=(thing, unit), root=tmp_path, point="merge", measured=True
    )
    assert [args for args, _env in fake.calls] == [
        ("packages/thing/tests",),
        ("packages/thing/tests", "tests", "--workshop-point=merge"),
    ]
    assert not any("--cov" in args for args, _env in fake.calls)


def _python_of_the_venv(env: dict[str, str], code: str, cwd: Path) -> str:
    """Run *code* in a fresh interpreter of this venv; what it printed."""
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _unarmed() -> dict[str, str]:
    """This process's environment without the variables that arm a meter."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }


def _coverage_json(root: Path) -> dict[str, Any]:
    """coverage.py's own JSON report of *root*'s data, its files named with slashes."""
    written = root / "coverage.json"
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", str(written)],
        cwd=root,
        capture_output=True,
        check=True,
        env=_unarmed(),
        timeout=60,
    )
    report: dict[str, Any] = json.loads(written.read_text("utf-8"))
    report["files"] = {
        PurePath(name).as_posix(): entry for name, entry in report["files"].items()
    }
    return report


def test_an_unarmed_python_of_the_venv_imports_no_coverage(tmp_path: Path) -> None:
    # Every fm start, test worker and subprocess pays for what the
    # venv's startup hooks import. Coverage's own hook imports coverage
    # only when the runner arms it; a hook that imported it anyway cost
    # most of a bare interpreter start.
    found = _python_of_the_venv(
        _unarmed(), "import sys; print('coverage' in sys.modules)", tmp_path
    )
    assert found == "False"


def test_an_armed_python_of_the_venv_meters_through_coverages_own_hook(
    tmp_path: Path,
) -> None:
    pytest.importorskip("coverage")
    config = tmp_path / "coverage.ini"
    config.write_text(
        f"[run]\nparallel = true\ndata_file = {(tmp_path / '.coverage').as_posix()}\n"
    )
    env = {**_unarmed(), "COVERAGE_PROCESS_START": str(config)}
    _python_of_the_venv(env, "x = 1", tmp_path)
    assert list(tmp_path.glob(".coverage.*"))


def _measured_by_pytest(root: Path) -> None:
    """List pytest and coverage at *root*: pytest's reduction reads coverage.py's data.

    The reduction is the pytest extension's, a distribution of its own:
    a run of this wheel alone has none, and the test skips.
    """
    pytest.importorskip("livery.extensions.pytest")
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["pytest", "coverage"]\n'
    )


def test_the_preview_tolerates_a_run_that_measured_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A selection of tests that reached no source leaves no data: the
    # preview says so, and a judged read refuses.
    package = _package(tmp_path, "thing", "[coverage]\nfloor = 1\n")
    coverage_floors.report_coverage(tmp_path, (package,))
    assert "nothing measured by this run" in capsys.readouterr().out
    with pytest.raises(_FAILURES, match="nothing measured here: no union was written"):
        coverage_floors.measured_coverage(tmp_path, (package,))


def test_the_preview_skips_a_measured_module_the_disk_no_longer_has(
    tmp_path: Path,
) -> None:
    # A test that builds a livery.* module under its tmp_path and imports
    # it leaves the module's lines in the run's data, and a passing test
    # deletes that directory at teardown. The preview reports the
    # packages' own files; a file the checkout lacks is none of
    # theirs, so its absence changes no number and stops no report.
    _measured_by_pytest(tmp_path)
    from coverage import CoverageData

    package = _package(tmp_path, "thing", "[coverage]\nfloor = 1\n")
    source = package.directory / "src" / "livery" / "thing" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("a = 1\nb = 2\n")
    gone = tmp_path / "gone" / "livery" / "acme" / "__init__.py"
    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_lines({str(source): [1], str(gone): [1]})
    data.write()
    measured = coverage_floors.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/thing": 50.0}


def test_a_python_figure_is_coverage_py_s_own_total(tmp_path: Path) -> None:
    _measured_by_pytest(tmp_path)
    package = _package(tmp_path, "thing")
    measure(package.directory / "src" / "measured.py", tmp_path)
    report = _coverage_json(tmp_path)
    figure = coverage_floors.measured_coverage(tmp_path, (package,))["packages/thing"]
    assert figure == report["totals"]["percent_covered"]
    # By hand, from the fixture's lines: 18 statements, the three-line
    # one counted once and the two lines of the `# pragma: no cover`
    # block left out, of which 16 ran, all but the body of the loop
    # that never started and the body of the `if` never true. 8
    # branches, two jumps from each of the three `if` lines and the
    # `for`, of which 4 ran; the untaken jump `# pragma: no branch`
    # waives counts as covered, as coverage.py counts it.
    summary = report["files"]["packages/thing/src/measured.py"]["summary"]
    assert (summary["num_statements"], summary["covered_lines"]) == (18, 16)
    assert (summary["num_branches"], summary["covered_branches"]) == (8, 4 + 1)
    assert figure == 100.0 * (16 + 4 + 1) / (18 + 8)


def test_without_a_parent_the_meter_and_the_preview_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "thing", "[coverage]\nfloor = 1\n")
    seen: list[tuple[str, ...]] = []
    enforced: list[Path] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())
    # The preview is the answer a listed extension gives: coverage's.
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["coverage"]\n')
    monkeypatch.setattr(
        coverage_floors,
        "report_coverage",
        lambda root, packages: enforced.append(root),
    )
    # A runner's own environment would make this a CI run: scrubbed,
    # so the test reads the same on a runner as on a desk.
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    # The fallback first: a package with no tests directory gives pytest
    # nothing to collect, and handed no path it would collect the whole
    # configuration, so no pytest starts.
    _python.run_test(packages=(package,), root=tmp_path, measured=True)
    assert seen == [] and enforced == []
    assert "no test directory in packages/thing" in capsys.readouterr().out
    (package.directory / "tests").mkdir()
    _python.run_test(packages=(package,), root=tmp_path, measured=True)
    assert seen[0][0] == "packages/thing/tests"
    assert any(arg == "--cov" for arg in seen[0])
    assert enforced == [tmp_path]


def test_a_machines_run_prints_no_preview_while_no_extension_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _suite(tmp_path, "thing")
    monkeypatch.setattr(_python, "pytest", _FakePytest())
    for name in ("CI", "GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)

    def _never(root: Path, packages: tuple[Package, ...]) -> None:
        raise AssertionError("no listed extension answers the preview")

    monkeypatch.setattr(coverage_floors, "report_coverage", _never)
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["ruff"]\n')
    _python.run_test(packages=(package,), root=tmp_path, measured=True)


def test_a_machines_run_takes_the_runners_shape_and_a_selection_stands_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._packages import workspace_suite

    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    (tmp_path / "tests").mkdir()
    unit = workspace_suite(tmp_path)
    assert unit is not None
    fake = _FakePytest()
    monkeypatch.setattr(_python, "pytest", fake)
    monkeypatch.setattr(coverage_floors, "report_coverage", lambda root, packages: None)
    for name in ("CI", "GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    selection = {
        "packages/thing": ("packages/thing/tests/test_a.py",),
        "tests": ("tests/test_all.py",),
    }
    _python.run_test(
        packages=(thing, other, unit),
        root=tmp_path,
        selection=selection,
        measured=True,
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
    # The kind's own test call: the package's suite alone, or its
    # selection, with no meter: measuring is the test extension's to ask.
    fake.calls.clear()
    _python.test(thing, tmp_path, selection=("tests/test_b.py",))
    assert fake.calls[0][0] == ("packages/thing/tests/test_b.py",)
    fake.calls.clear()
    _python.test(thing, tmp_path)
    assert fake.calls[0][0] == ("packages/thing/tests",)


# --- the leg: its refusals, then what it leaves -------------------------------


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
    assert coverage_leg.suites_that_ran(full, tmp_path, (x, y, z)) == (x, y)
    assert coverage_leg.suites_that_ran(narrowed, tmp_path, (x, y, z)) == (y,)
    for scope in ("verified", "nothing", "unknown"):
        marker = {"scope": scope, "packages": ["packages/x"], "leg": "a"}
        assert coverage_leg.suites_that_ran(marker, tmp_path, (x, y, z)) == ()
    (tmp_path / "tests").mkdir()
    ran = coverage_leg.suites_that_ran(narrowed, tmp_path, (x, y, z))
    assert [unit.path for unit in ran] == ["packages/y", "tests"]
    assert coverage_leg.units_of(tmp_path, (x, y, z))[-1].path == "tests"


def _capture_leaves(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Stand in for the leg's spool: each file the leg leaves, by its fields."""
    put: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        put.append(kw)
        return ""

    monkeypatch.setattr(coverage_record, "leave_run", _capture)
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(git, "head_sha", lambda root: "a" * 40)
    return put


def test_a_leg_that_ran_its_suites_and_left_no_part_of_any_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._verified import FULL, write_marker

    x = _suite(tmp_path, "x")
    write_marker(tmp_path, FULL, leg="check-a")
    _in_ci(monkeypatch, "check-a")
    with pytest.raises(
        _FAILURES,
        match=r"this leg ran 'full' and no measurer left a part of any suite it ran",
    ):
        coverage_leg.gather_leg(tmp_path, (x,))


def test_a_part_that_does_not_validate_refuses_naming_the_file_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.cmake import _build as _cmake
    from livery.workshop._verified import FULL, write_marker

    n = _native_suite(tmp_path, "n")
    write_marker(tmp_path, FULL, leg="check-a")
    _in_ci(monkeypatch, "check-a")
    put = _capture_leaves(monkeypatch)
    # A part the measurer left in the workspace: its counts name a line
    # that is no statement.
    left = _cmake.write_part(tmp_path, "packages/n", {_NATIVE: {1: 1}}, "gcov")
    broken = json.loads(left.read_text())
    broken["files"][_NATIVE]["counts"] = [[1, 1], [9, 2]]
    left.write_text(json.dumps(broken))
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: \.workshop/coverage/parts/gcov/packages-n\.json:"
            r' files\["packages/n/src/x\.cpp"\]\.counts: names lines that are no'
            r" statements: 9"
        ),
    ):
        coverage_leg.gather_leg(tmp_path, (n,))
    left.write_text("{not json")
    with pytest.raises(_FAILURES, match=r"packages-n\.json: the part does not parse"):
        coverage_leg.gather_leg(tmp_path, (n,))
    left.unlink()
    # A reduction's part off the format refuses the same way, naming the
    # reduction and the key; one that refuses is named with its words.
    x = _suite(tmp_path, "x")
    install_meter(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.meter", "coverage"]\n'
    )
    source = _source(tmp_path, "x")
    name = source.relative_to(tmp_path).as_posix()
    off = part(tmp_path, "packages/x", {name: [1]})
    off["files"][name]["hits"] = "1-9"
    meter(tmp_path, units={"packages/x": off})
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: the arcs reduction of packages/x:"
            r' files\["packages/x/src/livery/x/mod\.py"\]\.hits: names lines that'
            r" are no statements: 5-9"
        ),
    ):
        coverage_leg.gather_leg(tmp_path, (x,))
    meter(tmp_path, refuse="the leg left no data")
    with pytest.raises(
        _FAILURES,
        match=(
            "coverage: the arcs reduction of this leg's data refuses: the leg left"
            " no data"
        ),
    ):
        coverage_leg.gather_leg(tmp_path, (x,))
    meter(tmp_path, units={"packages/x": part(tmp_path, "packages/x", {})}, cut=0)
    with pytest.raises(_FAILURES, match=r"returned 0 part\(s\) for 1 suite\(s\)"):
        coverage_leg.gather_leg(tmp_path, (x,))
    meter(tmp_path, units={"packages/x": part(tmp_path, "packages/y", {})})
    with pytest.raises(
        _FAILURES,
        match=(
            "coverage: the arcs reduction of this leg's data answered a part of"
            " packages/y for packages/x"
        ),
    ):
        coverage_leg.gather_leg(tmp_path, (x,))
    assert put == []


def test_a_workspace_with_no_packages_puts_its_scope_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import FULL, write_marker

    # A project just born has no packages: its own tests ran unmetered
    # and reached no package source. That is not a dead meter: the
    # tests unit is put with no parts, so the union finds what ran.
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "check-a")
    put = _capture_leaves(monkeypatch)
    write_marker(tmp_path, FULL, leg="check-a")
    coverage_leg.gather_leg(tmp_path, ())
    assert "no packages to measure" in capsys.readouterr().out
    assert len(put) == 1 and put[0]["scope"] == "full"
    units = put[0]["units"]
    assert isinstance(units, dict) and list(units) == ["tests"]
    unit = units["tests"]
    assert isinstance(unit, coverage_record.Unit)
    assert unit.parts == () and unit.closure == "k" * 64 and unit.sha == "a" * 40
    # A skipped leg names no unit whatever the packages, so the union
    # carries the tests unit from the record instead of finding it
    # named and unmeasured.
    from livery.workshop._verified import VERIFIED

    write_marker(tmp_path, VERIFIED, leg="check-a")
    coverage_leg.gather_leg(tmp_path, ())
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

    _measured_by_pytest(tmp_path)
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
    put = _capture_leaves(monkeypatch)
    _in_ci(monkeypatch, "check-a")
    coverage_leg.gather_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    # Only x's suite ran: its own lines plus the import-time lines of its
    # closure, put once with the scope the gate ran.
    assert [sorted(_units(kw)) for kw in put] == [["packages/x"]]
    assert put[0]["leg"] == "check-a" and put[0]["scope"] == "affected"
    assert put[0]["packages"] == ("packages/x",)
    unit = _units(put[0])["packages/x"]
    assert ran(unit.parts) == {"packages/x/src/livery/x/mod.py": {1, 2, 3, 4}}
    assert unit.closure == "k" * 64 and unit.run == "7" and unit.sha == "a" * 40
    assert (
        "coverage store: packages/x stored for closure kkkkkkkkkkkk on check-a"
        " (1 files, " in out
    )
    assert "coverage store: 1 unit(s) for the run's ref of check-a (affected)" in out
    assert "packages/y" not in out
    # The leg combines no data file of its own: what the meter left stays.
    assert sorted(path.name for path in tmp_path.glob(".coverage*")) == [
        ".coverage.host.1.X",
        ".coverage.host.2.X",
    ]
    # A full leg puts every suite, y's from the import-time lines of
    # its closure alone, and the workspace's own tests as a unit whose
    # closure is everything.
    for left in tmp_path.glob(".coverage.*"):
        left.unlink()
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
    coverage_leg.gather_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    units = _units(put[0])
    assert sorted(units) == ["packages/x", "packages/y", "tests"]
    assert ran(units["packages/y"].parts) == {"packages/y/src/livery/y/mod.py": {1}}
    assert ran(units["tests"].parts) == {
        "packages/x/src/livery/x/mod.py": {1},
        "packages/y/src/livery/y/mod.py": {1, 2, 3},
    }
    assert "coverage store: tests stored for closure kkkkkkkkkkkk on check-a" in out


def _units(kw: dict[str, object]) -> dict[str, coverage_record.Unit]:
    units = kw["units"]
    assert isinstance(units, dict)
    return units


def test_a_leg_asks_the_reductions_for_each_suite_s_tests_and_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._packages import Edge
    from livery.workshop._verified import FULL, write_marker

    base = _suite(tmp_path, "base")
    top = _suite(tmp_path, "top")
    top = Package(
        directory=top.directory,
        path=top.path,
        name=top.name,
        kind="python",
        depends=(Edge(path="packages/base", kind="build", floor="0"),),
    )
    (tmp_path / "tests").mkdir()
    install_meter(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.meter", "coverage"]\n'
    )
    base_source = _source(tmp_path, "base").relative_to(tmp_path).as_posix()
    top_source = _source(tmp_path, "top").relative_to(tmp_path).as_posix()
    # The meter hands back a file outside top's closure: the leg keeps the
    # files of each suite's closure alone.
    meter(
        tmp_path,
        units={
            "packages/base": part(tmp_path, "packages/base", {base_source: [1]}),
            "packages/top": part(
                tmp_path, "packages/top", {top_source: [1, 2], base_source: [3]}
            ),
            "tests": part(tmp_path, "tests", {base_source: [4], top_source: [4]}),
        },
    )
    write_marker(tmp_path, FULL, leg="check-a")
    put = _capture_leaves(monkeypatch)
    _in_ci(monkeypatch, "check-a")
    coverage_leg.gather_leg(tmp_path, (top, base))
    assert asked(tmp_path) == [
        {
            "unit": "packages/top",
            "tests": "packages/top/tests/",
            "within": ["packages/base/", "packages/top/"],
        },
        {
            "unit": "packages/base",
            "tests": "packages/base/tests/",
            "within": ["packages/base/"],
        },
        {"unit": "tests", "tests": "tests/"},
    ]
    units = _units(put[0])
    assert ran(units["packages/top"].parts) == {top_source: {1, 2}, base_source: {3}}
    assert ran(units["packages/base"].parts) == {base_source: {1}}
    assert ran(units["tests"].parts) == {base_source: {4}, top_source: {4}}
    # A file outside a suite's closure is not the suite's to store.
    stray = tmp_path / "packages" / "base" / "src" / "livery" / "base" / "stray.py"
    stray.write_text("a = 1\n")
    held = part(tmp_path, "packages/base", {base_source: [1]})
    meter(
        tmp_path,
        units={
            "packages/base": held,
            "packages/top": part(tmp_path, "packages/top", {top_source: [1]}),
            "tests": part(tmp_path, "tests", {}),
        },
    )
    put.clear()
    coverage_leg.gather_leg(tmp_path, (top, base))
    assert ran(_units(put[0])["packages/base"].parts) == {base_source: {1}}


def test_a_leg_that_cannot_leave_its_lines_is_red_and_a_skipped_leg_leaves_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.cmake import _build as _cmake
    from livery.workshop._verified import FULL, VERIFIED, write_marker

    n = _native_suite(tmp_path, "n")
    _cmake.write_part(tmp_path, "packages/n", {_NATIVE: {1: 1}}, "gcov")
    # A marker with no label leaves the parts no key: the leg is red.
    write_marker(tmp_path, FULL, leg="")
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(git, "head_sha", lambda root: "a" * 40)
    _in_ci(monkeypatch, "check-a")
    with pytest.raises(
        _FAILURES,
        match=r"could not be left for its per-run ref \(refusing: the leg has no label",
    ):
        coverage_leg.gather_leg(tmp_path, (n,))
    # A leg whose gate skipped measured nothing and says so in its file,
    # so the union reads a skip, never a leg that died.
    write_marker(tmp_path, VERIFIED, leg="check-a")
    put = _capture_leaves(monkeypatch)
    coverage_leg.gather_leg(tmp_path, (n,))
    out = capsys.readouterr().out
    assert put == [
        {
            "leg": "check-a",
            "scope": "verified",
            "packages": (),
            "units": {},
        }
    ]
    assert "coverage: the gate ran 'verified'; no suite ran, nothing to gather" in out
    assert "coverage store: 0 unit(s) for the run's ref of check-a (verified)" in out
    # Outside CI nothing is left, and the measurement is only named.
    monkeypatch.delenv("GITHUB_ACTIONS")
    put.clear()
    coverage_leg.gather_leg(tmp_path, (n,))
    assert put == []
    assert "0 unit(s) measured; outside CI nothing is stored" in capsys.readouterr().out
    write_marker(tmp_path, FULL, leg="check-a")
    coverage_leg.gather_leg(tmp_path, (n,))
    assert put == []
    assert "coverage store: packages/n measured (1 files, " in capsys.readouterr().out


# --- the union: its refusals, then the reuse ----------------------------------


def _leg(
    key: str,
    scope: str,
    packages: tuple[str, ...] = (),
    units: dict[str, coverage_record.Unit] | None = None,
    *,
    label: str = "check-a",
    why: str = "",
) -> coverage_record.Leg:
    """What one leg put on its per-run ref, as the union reads it."""
    return coverage_record.Leg(key, label, scope, packages, dict(units or {}), why=why)


def _unit(
    root: Path,
    path: str,
    files: dict[str, list[int]],
    *,
    run: str = "7",
    closure: str = "k" * 64,
) -> coverage_record.Unit:
    """A unit whose one part reads coverage.py's way: the lines given ran."""
    return coverage_record.Unit(
        path, closure, run, "a" * 40, (part(root, path, files),)
    )


def _source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "packages" / name / "src" / "livery" / name / "mod.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    return source


def _name(tmp_path: Path, name: str) -> str:
    """The source ``_source`` wrote for *name*, from the root."""
    return _source(tmp_path, name).relative_to(tmp_path).as_posix()


def _union(
    monkeypatch: pytest.MonkeyPatch,
    legs: list[coverage_record.Leg],
    *,
    held: coverage_record.Record | None = None,
    records: dict[str, coverage_record.Record] | None = None,
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
    monkeypatch.setattr(coverage_record, "run_legs", lambda root, run: (legs, listing))
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": by_base.get(base, coverage_record.Record({})),
    )
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(coverage_union, "_moved_on", lambda git, run, branch: ("", ""))
    row = _verified.Verified("t", "5", "a" * 40, "full", ("check-a",), branch=proved)
    monkeypatch.setattr(_verified, "tree_id", lambda git, ref="HEAD": "t")
    monkeypatch.setattr(_verified, "record", lambda root, tree: (row, ""))
    written: list[tuple[str, str, list[str], list[str]]] = []

    def _put(
        root: Path,
        run: object,
        *,
        leg: str,
        fresh: dict[str, coverage_record.Unit],
        remove: tuple[str, ...] = (),
        base: str = "main",
        held: coverage_record.Record | None = None,
    ) -> str:
        written.append((base, leg, sorted(fresh), sorted(remove)))
        return ""

    monkeypatch.setattr(coverage_record, "put_record", _put)
    return written


def _unioned(root: Path) -> dict[str, Merged]:
    """The files the union wrote at *root*."""
    from livery.extensions.coverage._parts import read_union

    found = read_union(root)
    assert found is not None
    return dict(found.files)


def test_a_union_outside_ci_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A runner's own environment would make this a CI run: scrubbed,
    # so the test reads the same on a runner as on a desk.
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(_FAILURES, match="outside CI there is no run"):
        coverage_union.combine_union(tmp_path, ())


def test_a_union_with_no_leg_on_the_runs_refs_refuses_naming_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [])
    with pytest.raises(_FAILURES, match="no check leg left its parts on run 7's refs"):
        coverage_union.combine_union(tmp_path, ())
    _union(monkeypatch, [], listing="the remote could not be listed")
    with pytest.raises(
        _FAILURES, match=r"could not be read \(the remote could not be listed\)"
    ):
        coverage_union.combine_union(tmp_path, ())


def test_a_leg_without_its_file_its_label_or_a_known_scope_refuses_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "", why="refs/x carries no coverage.json")])
    with pytest.raises(
        _FAILURES, match=r"leg check-a: refs/x carries no coverage\.json; the leg died"
    ):
        coverage_union.combine_union(tmp_path, ())
    _union(monkeypatch, [_leg("check-a", "verified", label="")])
    with pytest.raises(_FAILURES, match="leg check-a names no label"):
        coverage_union.combine_union(tmp_path, ())
    _union(monkeypatch, [_leg("check-a", "odd")])
    with pytest.raises(_FAILURES, match=r"a scope the union does not read \('odd'\)"):
        coverage_union.combine_union(tmp_path, ())


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
        coverage_union.combine_union(tmp_path, (x,))


def test_a_skipped_suite_the_record_cannot_supply_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.coverage._parts import UNION

    x = _suite(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "verified")])
    with pytest.raises(
        _FAILURES,
        match=r"no record holds packages/x at closure kkkkkkkkkkkk \(main none\)\.",
    ):
        coverage_union.combine_union(tmp_path, (x,))
    held = coverage_record.Record(
        {"packages/x": _unit(tmp_path, "packages/x", {}, closure="j" * 64)}
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    _union(monkeypatch, [_leg("check-a", "nothing")], held=held)
    with pytest.raises(
        _FAILURES,
        match=r"at closure kkkkkkkkkkkk \(feat/x none, main only at jjjjjjjjjjjj\)",
    ):
        coverage_union.combine_union(tmp_path, (x,))
    down = coverage_record.Record({}, failed=True, reason="remote down")
    _union(monkeypatch, [_leg("check-a", "verified")], held=down)
    with pytest.raises(
        _FAILURES, match=r"main's record on check-a could not be read \(remote down\)"
    ):
        coverage_union.combine_union(tmp_path, (x,))
    assert not (tmp_path / UNION).exists()


def test_a_carried_part_that_does_not_validate_refuses_naming_its_row_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.coverage._parts import UNION

    x = _suite(tmp_path, "x")
    name = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    # A union left by an earlier run on this machine is never read again.
    (tmp_path / UNION).parent.mkdir(parents=True)
    (tmp_path / UNION).write_text("{}")
    broken = _unit(tmp_path, "packages/x", {name: [1]})
    broken.parts[0]["files"][name]["statements"] = "0-2"
    _union(
        monkeypatch,
        [_leg("check-a", "verified")],
        held=coverage_record.Record({"packages/x": broken}),
    )
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: main's record on check-a, packages/x, part 0:"
            r' files\["packages/x/src/livery/x/mod\.py"\]\.statements: .0-2. is not'
            r" a range of lines from 1 up"
        ),
    ):
        coverage_union.combine_union(tmp_path, (x,))
    assert not (tmp_path / UNION).exists()


def test_entries_of_one_file_at_two_digests_merge_the_current_and_name_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage._format import digest

    x = _suite(tmp_path, "x")
    name = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    # One leg measured the file as the checkout holds it; the other
    # measured it at the content it had before, and ran the lines the
    # first did not.
    current = _unit(tmp_path, "packages/x", {name: [1, 2]})
    before = (tmp_path / name).read_bytes()
    (tmp_path / name).write_text("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    older = _unit(tmp_path, "packages/x", {name: [3, 4, 5]})
    (tmp_path / name).write_bytes(before)
    legs = [
        _leg("check-a", "full", units={"packages/x": current}),
        _leg("check-b", "full", units={"packages/x": older}, label="check-b"),
    ]
    _union(monkeypatch, legs)
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    other = digest(b"a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    assert (
        f"  coverage: {name}: left out the reading of packages/x by coverage.py"
        f" 7.16.2 on CPython 3.14 at content {other[:12]}; the union keeps the one"
        f" of packages/x by coverage.py 7.16.2 on CPython 3.14 at content"
        f" {digest(before)[:12]}\n"
    ) in out
    merged = _unioned(tmp_path)[name]
    assert merged.entry.digest == digest(before)
    assert merged.entry.executed == {1, 2}
    # The figure is the current reading's: two of its four lines.
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 50.0}


def test_branches_of_two_measurers_never_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage._format import (
        COUNTS,
        INDEXES,
        Branch,
        Entry,
        Measurer,
        Part,
        digest,
        encode,
    )

    n = _native_suite(tmp_path, "n")
    (tmp_path / _NATIVE).write_text("int f(int a) {\n  return a ? 1 : 2;\n}\n")
    held = digest((tmp_path / _NATIVE).read_bytes())

    def _read(measurer: str, taken: tuple[int, int]) -> coverage_record.Unit:
        entry = Entry(
            digest=held,
            statements=frozenset({1, 2}),
            counts={1: 1, 2: 1},
            branches=(
                Branch(2, None, 0, taken[0]),
                Branch(2, None, 1, taken[1]),
            ),
        )
        reading = Part(
            "packages/n",
            Measurer(measurer, counts=COUNTS, branches=INDEXES),
            {_NATIVE: entry},
        )
        return coverage_record.Unit(
            "packages/n", "k" * 64, "7", "a" * 40, (encode(reading),)
        )

    _in_ci(monkeypatch, "gate")
    # Each compiler numbers the line's branches its own way: branch 0 of
    # one is not branch 0 of the other, so the two readings never merge,
    # and the line keeps the branch one of them never took.
    legs = [
        _leg("check-a", "full", units={"packages/n": _read("gcov", (1, 0))}),
        _leg(
            "check-b",
            "full",
            units={"packages/n": _read("llvm-cov", (0, 1))},
            label="check-b",
        ),
    ]
    _union(monkeypatch, legs)
    assert coverage_union.combine_union(tmp_path, (n,)) == (n,)
    merged = _unioned(tmp_path)[_NATIVE]
    assert merged.measurer.name == "gcov"
    assert [(branch.index, branch.taken) for branch in merged.entry.branches] == [
        (0, 1),
        (1, 0),
    ]
    assert (
        f"  coverage: {_NATIVE}: left out the reading of packages/n by llvm-cov at"
        f" content {held[:12]}; the union keeps the one of packages/n by gcov at"
        f" content {held[:12]}\n"
    ) in capsys.readouterr().out


def test_a_suite_whose_row_moved_on_mid_run_is_unioned_from_the_kept_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    # The leg skipped x and y on main's rows at closure k. Before the
    # union read, main's run for another merge moved x on to closure j
    # and dropped y's unit; both rows stay a day, kept at k.
    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    x_source = _name(tmp_path, "x")
    y_source = _name(tmp_path, "y")
    _in_ci(monkeypatch, "gate")
    then_x = _unit(tmp_path, "packages/x", {x_source: [1, 2, 3, 4]}, run="5")
    then_y = _unit(tmp_path, "packages/y", {y_source: [1, 2, 3, 4]}, run="5")
    stamp = datetime(2026, 10, 1, 2, 5, tzinfo=UTC)
    held = coverage_record.Record(
        {"packages/x": _unit(tmp_path, "packages/x", {}, run="6", closure="j" * 64)},
        kept=(
            coverage_record.Kept(
                coverage_record.kept_name("packages/x", "k" * 64, stamp), then_x
            ),
            coverage_record.Kept(
                coverage_record.kept_name("packages/y", "k" * 64, stamp), then_y
            ),
        ),
    )
    _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert coverage_union.combine_union(tmp_path, (x, y)) == (x, y)
    out = capsys.readouterr().out
    for path in ("packages/x", "packages/y"):
        assert (
            f"coverage: {path} on check-a: reused from run 5 (1 files), main's"
            " record, a row kept after its record moved on" in out
        )
    assert coverage_floors.measured_coverage(tmp_path, (x, y)) == {
        "packages/x": 100.0,
        "packages/y": 100.0,
    }


def test_a_skipped_leg_reuses_every_unit_from_mains_record_and_writes_its_branchs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    (tmp_path / "tests").mkdir()  # the workspace's own tests, a unit too
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    held = coverage_record.Record(
        {
            "packages/x": _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]}),
            "tests": _unit(tmp_path, "tests", {source: [1]}, run="6"),
        }
    )
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "coverage: leg check-a ran 'verified': no suite, no data" in out
    assert "coverage: packages/x on check-a: reused from run 7 (1 files), main's" in out
    assert "coverage: tests on check-a: reused from run 6 (1 files), main's" in out
    assert "the union of 0 leg(s) and 2 reused suite(s)" in out
    # The rows carried from main are copied onto the branch's record,
    # which is its own from here.
    assert written == [("feat/x", "check-a", ["packages/x", "tests"], [])]
    assert "coverage record: feat/x/check-a: 0 fresh, 2 carried, 0 removed" in out
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}
    # A pull request run that names no branch has no record to write.
    monkeypatch.delenv("GITHUB_HEAD_REF")
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert written == []
    assert (
        "coverage record: not written, a pull_request run that names no branch"
        " has no record of its own" in out
    )


def test_a_leg_a_pull_request_does_not_run_is_carried_whole_from_the_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    # The contract names two legs; a pull request runs the first alone.
    monkeypatch.setattr(
        coverage_union,
        "expected_legs",
        lambda root, run: (["check-a", "check-b"], ["check-a"]),
    )
    held = coverage_record.Record(
        {"packages/x": _unit(tmp_path, "packages/x", {source: [3, 4]})}
    )
    fresh = _unit(tmp_path, "packages/x", {source: [1, 2]}, run="7")
    written = _union(
        monkeypatch, [_leg("check-a", "full", units={"packages/x": fresh})], held=held
    )
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert (
        "coverage: leg check-b did not run on this pull request"
        " ([ci] pull-request-runners); every suite carried from the records" in out
    )
    assert "coverage: packages/x on check-b: reused from run 7 (1 files), main's" in out
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    # The two halves make the whole: lines 1 and 2 fresh, 3 and 4 from main.
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}
    assert written == [
        ("feat/x", "check-a", ["packages/x"], []),
        ("feat/x", "check-b", ["packages/x"], []),
    ]


def test_a_leg_a_pull_request_does_not_run_carries_a_changed_suite_at_its_older_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    monkeypatch.setattr(
        coverage_union,
        "expected_legs",
        lambda root, run: (["check-a", "check-b"], ["check-a"]),
    )
    # The change moved x's closure; main measured x on check-b at the old one.
    held = coverage_record.Record(
        {
            "packages/x": _unit(
                tmp_path, "packages/x", {source: [3, 4]}, closure="o" * 64
            )
        }
    )
    fresh = _unit(tmp_path, "packages/x", {source: [1, 2]}, run="7")
    _union(
        monkeypatch, [_leg("check-a", "full", units={"packages/x": fresh})], held=held
    )
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert (
        "coverage: packages/x on check-b: reused from run 7 (1 files), main's record,"
        " at closure oooooooooooo since the leg did not run" in out
    )
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


def test_a_leg_a_pull_request_does_not_run_passes_a_suite_no_record_has_ever_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A package born on this branch: the spared leg never measured it, on
    # any base. Nothing stands in, the leg that ran judges it, and the
    # merge point measures it on every leg.
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    monkeypatch.setattr(
        coverage_union,
        "expected_legs",
        lambda root, run: (["check-a", "check-b"], ["check-a"]),
    )
    fresh = _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]}, run="7")
    _union(
        monkeypatch,
        [_leg("check-a", "full", units={"packages/x": fresh})],
        held=coverage_record.Record({}),
    )
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert (
        "coverage: leg check-b has no record of packages/x yet; the merge point"
        " measures it" in out
    )
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


def test_a_push_carries_no_absent_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setattr(
        coverage_union,
        "expected_legs",
        lambda root, run: (["check-a", "check-b"], ["check-a", "check-b"]),
    )
    fresh = _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]}, run="7")
    _union(monkeypatch, [_leg("check-a", "full", units={"packages/x": fresh})])
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "did not run on this pull request" not in out
    assert "the union of 1 leg(s) and 0 reused suite(s)" in out


def test_a_branchs_own_record_is_asked_before_mains_and_its_rows_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    own = coverage_record.Record(
        {"packages/x": _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]}, run="9")}
    )
    main = coverage_record.Record(
        {
            "packages/x": _unit(tmp_path, "packages/x", {source: [1, 2]}, run="7"),
            "tests": _unit(tmp_path, "tests", {source: [3, 4]}, run="6"),
        }
    )
    written = _union(
        monkeypatch, [_leg("check-a", "verified")], held=main, records={"feat/x": own}
    )
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
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
    z = _package(tmp_path, "z", "[coverage]\nfloor = 1\n")  # no suite of its own
    x_source = _name(tmp_path, "x")
    y_source = _name(tmp_path, "y")
    _in_ci(monkeypatch, "gate")
    leg = _leg(
        "check-a",
        "affected",
        ("packages/x",),
        {"packages/x": _unit(tmp_path, "packages/x", {x_source: [1, 2, 3, 4]})},
    )
    held = coverage_record.Record(
        {"packages/y": _unit(tmp_path, "packages/y", {y_source: [1, 2, 3, 4]}, run="5")}
    )
    _union(monkeypatch, [leg], held=held)
    assert coverage_union.combine_union(tmp_path, (z, y, x)) == (z, y, x)
    out = capsys.readouterr().out
    assert "coverage: packages/y on check-a: reused from run 5 (1 files)" in out
    assert "unjudged" not in out
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    assert coverage_floors.measured_coverage(tmp_path, (x, y)) == {
        "packages/x": 100.0,
        "packages/y": 100.0,
    }


def test_mains_run_copies_the_merged_branchs_record_and_drops_the_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.git import Skipped

    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    x_source = _name(tmp_path, "x")
    y_source = _name(tmp_path, "y")
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    leg = _leg(
        "check-a",
        "measured",
        ("packages/x",),
        {
            "packages/x": _unit(tmp_path, "packages/x", {x_source: [1, 2, 3, 4]}),
            "tests": _unit(tmp_path, "tests", {y_source: [1]}),
        },
    )
    # y comes from the merged branch's record, named by the verified row;
    # main's own row of y is at another closure and is replaced.
    branch = coverage_record.Record(
        {"packages/y": _unit(tmp_path, "packages/y", {y_source: [1, 2, 3, 4]}, run="5")}
    )
    # Main's own row of x is of an older shape: skipped, and replaced by
    # the fresh row of the same name rather than counted as removed.
    held = coverage_record.Record(
        {
            "packages/y": _unit(tmp_path, "packages/y", {}, run="3", closure="j" * 64),
            "packages/gone": _unit(tmp_path, "packages/gone", {}, run="3"),
            "tests": _unit(tmp_path, "tests", {y_source: [2]}, run="5"),
        },
        skipped=(
            Skipped("junk.json", "does not parse"),
            Skipped("packages-x.json", "schema 2, this reader speaks 3"),
        ),
    )
    written = _union(
        monkeypatch, [leg], held=held, records={"feat/x": branch}, proved="feat/x"
    )
    assert coverage_union.combine_union(tmp_path, (x, y)) == (x, y)
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
        coverage_record, "put_record", lambda root, run, **kw: "push refused: down"
    )
    assert coverage_union.combine_union(tmp_path, (x, y)) == (x, y)
    assert (
        "coverage record: main/check-a not written (push refused: down)"
        in capsys.readouterr().out
    )


def test_a_run_whose_branch_moved_on_judges_but_never_writes_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/x")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    leg = _leg(
        "check-a",
        "full",
        units={"packages/x": _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]})},
    )
    written = _union(monkeypatch, [leg])
    asked_branches: list[str] = []

    def _elsewhere(git: object, run: object, branch: str) -> tuple[str, str]:
        asked_branches.append(branch)
        return "b" * 40, "a" * 40

    monkeypatch.setattr(coverage_union, "_moved_on", _elsewhere)
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "the union of 1 leg(s) and 0 reused suite(s)" in out
    assert asked_branches == ["feat/x"]
    assert written == []
    assert (
        "coverage record: feat/x's head moved on to bbbbbbbbbbbb since this run's"
        " aaaaaaaaaaaa; not written, the newer run writes" in out
    )


def test_a_push_of_an_unnamed_tree_carries_from_main_alone_and_writes_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = _name(tmp_path, "x")
    (tmp_path / "tests").mkdir()
    _in_ci(monkeypatch, "gate")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    held = coverage_record.Record(
        {
            "packages/x": _unit(tmp_path, "packages/x", {source: [1, 2, 3, 4]}),
            "tests": _unit(tmp_path, "tests", {source: [1]}, run="6"),
        }
    )
    written = _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    out = capsys.readouterr().out
    assert "the union of 0 leg(s) and 2 reused suite(s)" in out
    assert "main takes" not in out
    # Every row came from main itself: nothing to write back.
    assert written == []
    assert "coverage record: main/check-a: unchanged, 2 carried" in out


def test_the_union_of_two_legs_covers_what_each_left_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage._parts import UNION

    package = _suite(tmp_path, "x")
    (tmp_path / "packages" / "x" / "workshop.toml").write_text(
        'kind = "python"\nname = "livery-x"\n[coverage]\nfloor = 95\n'
    )
    source = _name(tmp_path, "x")
    _in_ci(monkeypatch, "gate")
    legs = [
        _leg(
            "check-a",
            "full",
            units={"packages/x": _unit(tmp_path, "packages/x", {source: [1, 2]})},
        ),
        _leg(
            "check-b",
            "affected",
            ("packages/x",),
            {"packages/x": _unit(tmp_path, "packages/x", {source: [3, 4]})},
            label="check-b",
        ),
    ]
    _union(monkeypatch, legs)
    assert coverage_union.combine_union(tmp_path, (package,)) == (package,)
    assert (tmp_path / UNION).is_file()
    assert "the union of 2 leg(s) and 0 reused suite(s)" in capsys.readouterr().out
    measured = coverage_floors.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/x": 100.0}
    coverage_floors.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%


#: Two branches: the platform decides line 2's, and line 6's condition is
#: true on no platform.
_PLATFORM_SPLIT = """\
import sys
if sys.platform == "win32":
    separator = ";"
else:
    separator = ":"
if not separator:
    separator = ","
joined = separator.join(["a", "b"])
"""


def test_a_branch_one_leg_took_is_covered_in_the_union_and_one_neither_took_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import CoverageData

    from livery.extensions.coverage._leg import request
    from livery.extensions.coverage._parts import MEASURERS
    from livery.workshop import answer

    _measured_by_pytest(tmp_path)
    package = _suite(tmp_path, "x")
    source = tmp_path / "packages" / "x" / "src" / "livery" / "x" / "split.py"
    source.parent.mkdir(parents=True)
    source.write_text(_PLATFORM_SPLIT)
    # One leg took line 2's true side and the other its false side; both
    # took line 6's false side, and neither its true side. Each leg's
    # part is pytest's reduction of that leg's own data.
    windows = {str(source): set(_arcs([1, 2, 3, 6, 8]))}
    linux = {str(source): set(_arcs([1, 2, 5, 6, 8]))}
    reduction = answer(tmp_path, MEASURERS)["arcs"]
    units: dict[str, coverage_record.Unit] = {}
    for label, arcs in (("check-a", windows), ("check-b", linux)):
        data = CoverageData(basename=str(tmp_path / ".coverage"), suffix=label)
        data.add_arcs(arcs)
        data.write()
        (made,) = reduction.leg(tmp_path, [request((package,), package)])
        units[label] = coverage_record.Unit(
            "packages/x", "k" * 64, "7", "a" * 40, (made,)
        )
        (tmp_path / f".coverage.{label}").unlink()
    (tmp_path / "workshop.toml").unlink()
    _in_ci(monkeypatch, "gate")
    legs = [
        _leg("check-a", "full", units={"packages/x": units["check-a"]}),
        _leg(
            "check-b", "full", units={"packages/x": units["check-b"]}, label="check-b"
        ),
    ]
    _union(monkeypatch, legs)
    assert coverage_union.combine_union(tmp_path, (package,)) == (package,)
    name = "packages/x/src/livery/x/split.py"
    merged = _unioned(tmp_path)[name].entry
    # coverage.py's own reading of both legs' data together agrees.
    combined = CoverageData(basename=str(tmp_path / ".coverage"))
    combined.add_arcs({str(source): windows[str(source)] | linux[str(source)]})
    combined.write()
    union = _coverage_json(tmp_path)["files"][name]
    assert union["executed_branches"] == [[2, 3], [2, 5], [6, 8]]
    assert union["missing_branches"] == [[6, 7]]
    assert union["summary"]["num_partial_branches"] == 1
    assert union["missing_lines"] == [7]
    taken = sorted(
        [branch.source, branch.target] for branch in merged.branches if branch.taken
    )
    untaken = sorted(
        [branch.source, branch.target] for branch in merged.branches if not branch.taken
    )
    assert (taken, untaken) == (union["executed_branches"], union["missing_branches"])
    assert sorted(merged.partial) == [6]
    assert sorted(merged.missing) == union["missing_lines"]
    # 6 of the 7 statements ran, all but line 7, and 3 of the 4
    # branches; each leg alone ran 5 statements and 2 branches.
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {
        "packages/x": 100.0 * (6 + 3) / (7 + 4)
    }


def test_a_source_file_no_test_reached_is_left_out_of_the_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import CoverageData

    from livery.workshop._verified import FULL, write_marker

    _measured_by_pytest(tmp_path)
    x = _suite(tmp_path, "x")
    reached = _source(tmp_path, "x")
    unreached = reached.with_name("unreached.py")
    unreached.write_text("e = 5\nf = 6\n")
    # The meter names a file under its source that nothing imported,
    # with no lines; coverage.py's own report counts such a file at 0%.
    # The leg's reduction reads each file's name from the root itself,
    # so no configuration of coverage.py's is needed for it.
    data = CoverageData(basename=str(tmp_path / ".coverage"), suffix="host.1.X")
    data.set_context("packages/x/tests/test_mod.py::test_one|run")
    data.add_arcs({str(reached): set(_arcs([1, 2, 3, 4]))})
    data.touch_files([str(unreached)])
    data.write()
    write_marker(tmp_path, FULL, leg="check-a")
    put = _capture_leaves(monkeypatch)
    _in_ci(monkeypatch, "check-a")
    coverage_leg.gather_leg(tmp_path, (x,))
    units = _units(put[0])
    assert units["packages/x"].files == ("packages/x/src/livery/x/mod.py",)
    # The gate job unions on a machine of its own, from what the leg put.
    for left in tmp_path.glob(".coverage*"):
        left.unlink()
    (tmp_path / "workshop.toml").unlink()
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "full", units=units)])
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


def test_the_preview_and_the_union_leave_out_a_file_the_meter_named_with_no_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import CoverageData

    from livery.workshop._verified import FULL, write_marker

    _measured_by_pytest(tmp_path)
    x = _suite(tmp_path, "x")
    reached = _source(tmp_path, "x")
    unreached = reached.with_name("unreached.py")
    unreached.write_text("e = 5\nf = 6\n")
    # A run whose meter names a source nothing imported, with no lines:
    # coverage.py's own report counts it, four of six statements run.
    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.set_context("packages/x/tests/test_mod.py::test_one|run")
    data.add_arcs({str(reached): set(_arcs([1, 2, 3, 4]))})
    data.touch_files([str(unreached)])
    data.write()
    assert _coverage_json(tmp_path)["totals"]["percent_covered"] == 100.0 * 4 / 6
    # The preview reads a machine's run through the one figure, which
    # leaves the file out...
    preview = coverage_floors.measured_coverage(tmp_path, (x,))
    assert preview == {"packages/x": 100.0}
    # ...as the union of what a leg left of the same run does.
    (tmp_path / ".coverage").rename(tmp_path / ".coverage.host.1.X")
    write_marker(tmp_path, FULL, leg="check-a")
    put = _capture_leaves(monkeypatch)
    _in_ci(monkeypatch, "check-a")
    coverage_leg.gather_leg(tmp_path, (x,))
    (tmp_path / ".coverage.host.1.X").unlink()
    (tmp_path / "workshop.toml").unlink()
    _in_ci(monkeypatch, "gate")
    _union(monkeypatch, [_leg("check-a", "full", units=_units(put[0]))])
    assert coverage_union.combine_union(tmp_path, (x,)) == (x,)
    assert coverage_floors.measured_coverage(tmp_path, (x,)) == preview


#: A native suite's source, from the root.
_NATIVE = "packages/n/src/x.cpp"


def _native_suite(tmp_path: Path, name: str) -> Package:
    """A cpp-conan package with a floor, whose suite its build's measurer measures."""
    directory = tmp_path / "packages" / name
    (directory / "src").mkdir(parents=True)
    (directory / "tests").mkdir()
    (directory / "src" / "x.cpp").write_text("int f() {\n  return 1;\n}\n")
    (directory / "workshop.toml").write_text(
        f'kind = "cpp-conan"\nname = "acme-{name}"\n[coverage]\nfloor = 95\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="cpp-conan",
        depends=(),
    )


def _native_unit(
    root: Path, path: str, counts: dict[int, int], *, run: str = "7"
) -> coverage_record.Unit:
    """A native suite's unit: gcov's counts of the source's lines."""
    from livery.extensions.cmake import _build as _cmake

    written = _cmake.write_part(root, path, {_NATIVE: counts}, "gcov")
    held = json.loads(written.read_text())
    written.unlink()
    return coverage_record.Unit(path, "k" * 64, run, "a" * 40, (held,))


def test_a_leg_puts_a_native_suites_lines_beside_the_python_arcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.cmake import _build as _cmake
    from livery.workshop._verified import FULL, write_marker

    x = _suite(tmp_path, "x")
    n = _native_suite(tmp_path, "n")
    x_source = _name(tmp_path, "x")
    install_meter(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.meter", "coverage"]\n'
    )
    meter(tmp_path, units={"packages/x": part(tmp_path, "packages/x", {x_source: [1]})})
    _cmake.write_part(tmp_path, "packages/n", {_NATIVE: {1: 1, 2: 0}}, "gcov")
    write_marker(tmp_path, FULL, leg="check-a")
    put = _capture_leaves(monkeypatch)
    _in_ci(monkeypatch, "check-a")
    coverage_leg.gather_leg(tmp_path, (x, n))
    # The reduction is asked for the python suite alone: the native
    # suite's measurer left its part itself.
    assert [unit["unit"] for unit in asked(tmp_path)] == ["packages/x"]
    units = _units(put[0])
    assert sorted(units) == ["packages/n", "packages/x"]
    (native,) = units["packages/n"].parts
    assert native["measurer"]["name"] == "gcov"
    assert ran(units["packages/n"].parts) == {_NATIVE: {1}}
    assert native["files"][_NATIVE]["statements"] == "1-2"
    assert units["packages/x"].parts[0]["measurer"]["name"] == "coverage.py"
    out = capsys.readouterr().out
    assert "coverage store: packages/n stored for closure kkkkkkkkkkkk" in out
    # The refusal's cousin: a native suite that ran and left no part is
    # not put, and the union will name it.
    put.clear()
    for left in tmp_path.glob(".workshop/coverage/parts/*/*.json"):
        left.unlink()
    coverage_leg.gather_leg(tmp_path, (x, n))
    assert sorted(_units(put[0])) == ["packages/x"]
    assert "packages/n ran without a measurement" in capsys.readouterr().out


def test_the_union_of_two_legs_lines_covers_what_each_left_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _native_suite(tmp_path, "n")
    _in_ci(monkeypatch, "gate")
    legs = [
        _leg(
            "check-a",
            "full",
            units={"packages/n": _native_unit(tmp_path, "packages/n", {1: 1, 2: 0})},
        ),
        _leg(
            "check-b",
            "affected",
            ("packages/n",),
            {"packages/n": _native_unit(tmp_path, "packages/n", {1: 0, 2: 2})},
            label="check-b",
        ),
    ]
    _union(monkeypatch, legs)
    assert coverage_union.combine_union(tmp_path, (package,)) == (package,)
    # One measurer read both legs: their counts add up.
    assert dict(_unioned(tmp_path)[_NATIVE].entry.counts) == {1: 1, 2: 2}
    out = capsys.readouterr().out
    assert "the union of 2 leg(s) and 0 reused suite(s)" in out
    assert not (tmp_path / ".coverage").exists()  # no coverage.py data at all
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {
        "packages/n": 100.0
    }
    coverage_floors.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%
    # A skipped leg reuses the native suite from the record, its part whole.
    held = coverage_record.Record(
        {"packages/n": _native_unit(tmp_path, "packages/n", {1: 1, 2: 1}, run="5")}
    )
    _union(monkeypatch, [_leg("check-a", "verified")], held=held)
    assert coverage_union.combine_union(tmp_path, (package,)) == (package,)
    assert "packages/n on check-a: reused from run 5" in capsys.readouterr().out
    assert dict(_unioned(tmp_path)[_NATIVE].entry.counts) == {1: 1, 2: 1}
