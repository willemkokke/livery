"""The coverage floors: parent mode, the grace, and the contract read."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from livery.footman import Failed
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


def test_a_measuring_parent_suspends_the_local_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path, "thing", "[qa]\ncoverage-floor = 1\n")
    seen: list[tuple[str, ...]] = []

    class FakeTool:
        def opts(self, **_kwargs: object) -> FakeTool:
            return self

        def __call__(self, *args: str) -> None:
            seen.append(args)

    monkeypatch.setattr(_python, "pytest", FakeTool())

    def refuse(*_args: object) -> None:
        raise AssertionError("enforcement belongs to the aggregating job")

    monkeypatch.setattr(_python, "enforce_coverage", refuse)
    monkeypatch.setenv("COVERAGE_PROCESS_START", "pyproject.toml")
    _python.run_test(packages=(package,), root=tmp_path)
    assert seen and not any("--cov" in arg for arg in seen[0])


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


def test_a_leg_with_no_metered_data_refuses_naming_the_meter(tmp_path: Path) -> None:
    with pytest.raises(BaseException, match="COVERAGE_PROCESS_START"):
        _python.combine_leg(tmp_path)


def _leg(
    tmp_path: Path,
    name: str,
    scope: str,
    packages: tuple[str, ...] = (),
    lines: dict[str, list[int]] | None = None,
) -> Path:
    """A collected leg: its marker, and its data when *lines* is given."""
    from coverage import CoverageData

    from livery.workshop._verified import write_marker

    folder = tmp_path / "coverage-data" / name
    folder.mkdir(parents=True)
    write_marker(folder, scope, packages)
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


def test_a_skipped_leg_judges_nothing_and_writes_no_union(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "x", "[qa]\ncoverage-floor = 95\n")
    # The metered processes of a skipped leg leave a data file with no
    # measured lines, the shape a verified skip uploads.
    _leg(tmp_path, "leg-a", "verified", lines={})
    _leg(tmp_path, "leg-b", "nothing")
    assert _python.combine_union(tmp_path, (package,)) == ()
    assert not (tmp_path / ".coverage").exists()
    out = capsys.readouterr().out
    assert "leg leg-a ran 'verified': no suite, no data" in out
    assert "unjudged this run, no leg ran their suites: packages/x" in out
    assert "nothing to union" in out


def test_a_narrowed_leg_judges_only_the_packages_it_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    x = _package(tmp_path, "x", "[qa]\ncoverage-floor = 95\n")
    y = _package(tmp_path, "y", "[qa]\ncoverage-floor = 95\n")
    _source(tmp_path, "y")
    lines = {str(_source(tmp_path, "x")): [1, 2, 3, 4]}
    _leg(tmp_path, "leg-a", "affected", ("packages/x",), lines=lines)
    assert _python.combine_union(tmp_path, (y, x)) == (x,)
    out = capsys.readouterr().out
    assert "unjudged this run, no leg ran their suites: packages/y" in out
    assert "the union of 1 leg(s)" in out
    assert _python.measured_coverage(tmp_path, (x,)) == {"packages/x": 100.0}


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
    assert "the union of 2 leg(s)" in capsys.readouterr().out
    measured = _python.measured_coverage(tmp_path, (package,))
    assert measured == {"packages/x": 100.0}
    _python.enforce_coverage(
        tmp_path, (package,)
    )  # each leg alone: 50%; the union: 100%
