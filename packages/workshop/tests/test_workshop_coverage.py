"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _coverage_store
from livery.workshop._backends import _python
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)


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


def test_the_floor_comes_from_the_contract(tmp_path: Path) -> None:
    bare = _package(tmp_path, "bare")
    assert _python.coverage_floor(bare) is None
    floored = _package(tmp_path, "floored", "[qa]\ncoverage-floor = 87\n")
    assert _python.coverage_floor(floored) == 87.0


def test_enforcement_grants_the_grace_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 90\n")
    measured = {"packages/thing": 89.6}
    monkeypatch.setattr(_python, "measured_coverage", lambda root, packages: measured)
    _python.enforce_coverage(tmp_path, (package,))  # inside the grace
    measured["packages/thing"] = 89.4
    with pytest.raises(_FAILURES) as caught:
        _python.enforce_coverage(tmp_path, (package,))
    assert "below the committed floor" in str(caught.value)


class _Result:
    def __init__(self, code: int) -> None:
        self.code = code


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


def test_a_measuring_parent_runs_each_suite_apart_and_names_the_red_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    thing = _suite(tmp_path, "thing")
    other = _suite(tmp_path, "other")
    empty = _suite(tmp_path, "empty")
    (tmp_path / "tests").mkdir()
    fake = _FakePytest({"packages/other/tests": 1, "packages/empty/tests": 5})
    monkeypatch.setattr(_python, "pytest", fake)

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(_python, "enforce_coverage", refuse)
    monkeypatch.setenv("COVERAGE_PROCESS_START", "pyproject.toml")
    with pytest.raises(_FAILURES, match=r"tests failed in packages/other \(exit 1\)"):
        _python.run_test(packages=(thing, other, empty), root=tmp_path)
    assert [args for args, _env in fake.calls] == [
        ("packages/thing/tests",),
        ("packages/other/tests",),
        ("packages/empty/tests",),
        ("tests",),
    ]
    prefixes = [env.get("COVERAGE_FILE") if env else None for _args, env in fake.calls]
    assert prefixes == [
        ".coverage.suite-livery-thing",
        ".coverage.suite-livery-other",
        ".coverage.suite-livery-empty",
        None,
    ]
    assert not any("--cov" in args for args, _env in fake.calls)
    assert "tests packages/empty: no tests collected" in capsys.readouterr().out
    # A scoped run leaves the workspace's own tests alone.
    fake.calls.clear()
    fake.codes.clear()
    _python.run_test(packages=(thing,), root=tmp_path, scoped=True)
    assert [args for args, _env in fake.calls] == [("packages/thing/tests",)]


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
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    _python.run_test(packages=(package,), root=tmp_path, scoped=True)
    assert any(arg == "--cov" for arg in seen[0])
    assert "packages/thing/tests" not in seen[0]  # no tests dir exists
    assert enforced == [tmp_path]


# --- the leg: its refusal, then what it stores --------------------------------


def test_a_leg_with_no_metered_data_refuses_naming_the_meter(tmp_path: Path) -> None:
    with pytest.raises(BaseException, match="COVERAGE_PROCESS_START"):
        _python.combine_leg(tmp_path, ())


def _in_ci(monkeypatch: pytest.MonkeyPatch, leg: str) -> None:
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setenv("WORKSHOP_LEG", leg)
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)


def _parts(tmp_path: Path, package: Package, lines: dict[str, list[int]]) -> None:
    from coverage import CoverageData

    data = CoverageData(
        basename=str(tmp_path / _python.suite_prefix(package)), suffix="host.1.X"
    )
    data.add_lines(lines)
    data.write()


def test_a_leg_stores_each_suite_it_ran_within_its_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._verified import FULL, write_marker

    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    inside = str(_source(tmp_path, "x"))
    outside = str(_source(tmp_path, "y"))
    _parts(tmp_path, x, {inside: [1, 2], outside: [1]})
    write_marker(tmp_path, FULL, leg="check-a")
    stamped: list[dict[str, object]] = []

    def _capture(root: Path, run: object, **kw: object) -> str:
        stamped.append(kw)
        return ""

    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_coverage_store, "stamp", _capture)
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.head_sha", lambda self: "a" * 40
    )
    # Outside CI the suite is measured, and nothing is stored.
    _python.combine_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    assert "coverage store: packages/x measured (1 files); outside CI" in out
    assert stamped == []
    assert (tmp_path / ".coverage").is_file()
    # In CI the suite's lines within its closure are stamped under the leg.
    _in_ci(monkeypatch, "check-a")
    _parts(tmp_path, x, {inside: [3, 4], outside: [2]})
    _python.combine_leg(tmp_path, (x, y))
    out = capsys.readouterr().out
    assert (
        "coverage store: packages/x stored for closure kkkkkkkkkkkk on check-a" in out
    )
    assert len(stamped) == 1
    assert stamped[0]["leg"] == "check-a"
    assert stamped[0]["closure_key"] == "k" * 64
    # The first call's parts were consumed by the leg's own combine, so
    # only the second call's lines are here, named workspace-relative.
    assert stamped[0]["files"] == {"packages/x/src/livery/x/mod.py": [3, 4]}
    assert "packages/y" not in out.split("stored for closure")[0]
    # A store that refuses is printed, never fatal.
    monkeypatch.setattr(_coverage_store, "stamp", lambda root, run, **kw: "nope")
    _parts(tmp_path, x, {inside: [1]})
    _python.combine_leg(tmp_path, (x, y))
    assert "coverage store: packages/x not stored (nope)" in capsys.readouterr().out


# --- the union: its refusals, then the reuse ----------------------------------


def _leg(
    tmp_path: Path,
    name: str,
    scope: str,
    packages: tuple[str, ...] = (),
    lines: dict[str, list[int]] | None = None,
    leg: str = "check-a",
) -> Path:
    """A collected leg: its marker, and its data when *lines* is given."""
    from coverage import CoverageData

    from livery.workshop._verified import write_marker

    folder = tmp_path / "coverage-data" / name
    folder.mkdir(parents=True)
    write_marker(folder, scope, packages, leg=leg)
    if lines is not None:
        data = CoverageData(basename=str(folder / ".coverage"))
        data.add_lines(lines)
        data.write()
    return folder


def _source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "packages" / name / "src" / "livery" / name / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    return source


def _stored(files: dict[str, list[int]], run: str = "7") -> _coverage_store.Stored:
    return _coverage_store.Stored(
        leg="check-a", package="", closure="k" * 64, run=run, sha="a" * 40, files=files
    )


def test_a_union_with_no_collected_leg_refuses_naming_the_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(BaseException, match="coverage-data"):
        _python.combine_union(tmp_path, ())
    (tmp_path / "coverage-data" / "bare").mkdir(parents=True)
    with pytest.raises(BaseException, match=r"leg bare: no readable fm-gate\.json"):
        _python.combine_union(tmp_path, ())


def test_a_leg_that_ran_its_gate_without_data_refuses_naming_the_leg(
    tmp_path: Path,
) -> None:
    _leg(tmp_path, "leg-a", "full")
    with pytest.raises(BaseException, match="leg leg-a ran its gate 'full'"):
        _python.combine_union(tmp_path, ())
    shutil.rmtree(tmp_path / "coverage-data")
    _leg(tmp_path, "leg-a", "odd", lines={})
    with pytest.raises(BaseException, match=r"leg leg-a: no readable fm-gate\.json"):
        _python.combine_union(tmp_path, ())


def test_a_skipped_suite_the_store_cannot_supply_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = _suite(tmp_path, "x")
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(_coverage_store, "find", lambda root, **kw: (None, ""))
    _leg(tmp_path, "leg-a", "verified", lines={})
    with pytest.raises(
        _FAILURES,
        match=(
            "leg leg-a: no stored suite for packages/x at closure kkkkkkkkkkkk"
            " on check-a"
        ),
    ):
        _python.combine_union(tmp_path, (x,))
    monkeypatch.setattr(
        _coverage_store, "find", lambda root, **kw: (None, "remote down")
    )
    with pytest.raises(_FAILURES, match=r"\(remote down\)"):
        _python.combine_union(tmp_path, (x,))
    shutil.rmtree(tmp_path / "coverage-data")
    _leg(tmp_path, "leg-a", "nothing", leg="")
    with pytest.raises(_FAILURES, match="leg leg-a names no label"):
        _python.combine_union(tmp_path, (x,))
    assert not (tmp_path / ".coverage").exists()


def test_a_skipped_leg_reuses_every_suite_from_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    source = str(_source(tmp_path, "x"))
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    asked: list[tuple[str, str, str]] = []

    def _find(
        root: Path, *, leg: str, package: Package, closure_key: str
    ) -> tuple[_coverage_store.Stored | None, str]:
        asked.append((leg, package.path, closure_key))
        return _stored({source: [1, 2, 3, 4]}), ""

    monkeypatch.setattr(_coverage_store, "find", _find)
    leg = _leg(tmp_path, "leg-a", "verified", lines={})
    assert _python.combine_union(tmp_path, (x,)) == (x,)
    assert asked == [("check-a", "packages/x", "k" * 64)]
    assert leg.is_dir()  # the union's combine consumed its inputs
    out = capsys.readouterr().out
    assert "coverage: packages/x on check-a: reused from run 7 (1 files)" in out
    assert "the union of 0 leg(s) and 1 reused suite(s)" in out
    assert _python.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


def test_a_narrowed_leg_reuses_the_suite_it_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _suite(tmp_path, "x")
    y = _suite(tmp_path, "y")
    z = _package(tmp_path, "z", "[qa]\ncoverage-floor = 1\n")  # no suite of its own
    x_source = str(_source(tmp_path, "x"))
    y_source = str(_source(tmp_path, "y"))
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(
        _coverage_store,
        "find",
        lambda root, **kw: (_stored({y_source: [1, 2, 3, 4]}, run="5"), ""),
    )
    _leg(tmp_path, "leg-a", "affected", ("packages/x",), lines={x_source: [1, 2, 3, 4]})
    assert _python.combine_union(tmp_path, (z, y, x)) == (z, y, x)
    out = capsys.readouterr().out
    assert "coverage: packages/y on check-a: reused from run 5 (1 files)" in out
    assert "unjudged" not in out
    assert "the union of 1 leg(s) and 1 reused suite(s)" in out
    assert _python.measured_coverage(tmp_path, (x, y)) == {
        "packages/x": 100.0,
        "packages/y": 100.0,
    }


def test_the_union_of_two_legs_covers_what_each_left_uncovered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "x", "[qa]\ncoverage-floor = 95\n")
    source = str(_source(tmp_path, "x"))
    _leg(tmp_path, "leg-a", "full", lines={source: [1, 2]})
    _leg(tmp_path, "leg-b", "affected", ("packages/x",), lines={source: [3, 4]})
    _leg(tmp_path, "leg-c", "verified", lines={})
    assert _python.combine_union(tmp_path, (package,)) == (package,)
    assert (tmp_path / ".coverage").is_file()
    assert "the union of 2 leg(s) and 0 reused suite(s)" in capsys.readouterr().out
    measured = _python.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/x": 100.0}
    _python.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%
