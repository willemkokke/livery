"""A native suite's parts as coverage reads them: refusals first, then the figure.

The cmake extension's own suite covers how its measurers' readings
become parts; here a part is written with coverage's own format.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage import _record as coverage_record
from livery.extensions.coverage._parts import PARTS, part_files, read_part
from livery.footman import Failed
from livery.workshop._packages import Package
from workshop_coverage_meter import leave_part

_FAILURES = (SystemExit, Failed)


def _native(tmp_path: Path, floor: str = "floor = 100") -> Package:
    directory = tmp_path / "packages" / "n"
    (directory / "src").mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "src" / "x.cpp").write_text("int f() { return 1; }\n")
    (directory / "tests" / "test_x.cpp").write_text("int main() { return 0; }\n")
    (directory / "workshop.toml").write_text(
        f'extensions = ["cmake", "conan"]\nname = "acme-n"\n[coverage]\n{floor}\n'
    )
    return Package(
        directory=directory,
        path="packages/n",
        name="acme-n",
        depends=(),
        extensions=("cmake", "conan"),
    )


def test_a_part_that_is_not_one_refuses_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _native(tmp_path)
    bad = tmp_path / PARTS / "gcov" / "packages-n.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("not json")
    with pytest.raises(
        fmt.PartError,
        match=(
            r"^\.workshop/coverage/parts/gcov/packages-n\.json: the part does not"
            r" parse"
        ),
    ):
        read_part(bad, tmp_path)
    with pytest.raises(_FAILURES, match=r"packages-n\.json: the part does not parse"):
        coverage_floors.measured_coverage(tmp_path, (package,))
    # A part of another format version is named and left out of a figure.
    bad.write_text('{"format": 2}')
    with pytest.raises(fmt.OtherVersion, match="a part of format 2"):
        read_part(bad, tmp_path)
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {}
    assert (
        "  coverage: .workshop/coverage/parts/gcov/packages-n.json: a part of"
        " format 2; this workshop reads format 3; the figure reads without it"
    ) in capsys.readouterr().out
    bad.unlink()
    assert part_files(tmp_path) == []


def test_the_figure_counts_source_lines_alone_and_the_union_adds_counts(
    tmp_path: Path,
) -> None:
    package = _native(tmp_path)
    gcov = fmt.Measurer("gcov", counts=fmt.COUNTS)

    def _reading(counts: dict[str, dict[int, int]]) -> fmt.Part:
        return fmt.Part(
            "packages/n",
            gcov,
            {
                name: fmt.Entry(
                    digest="",
                    statements=frozenset(lines),
                    counts={line: runs for line, runs in lines.items() if runs},
                )
                for name, lines in counts.items()
            },
        )

    measured = _reading(
        {
            "packages/n/src/x.cpp": {1: 1, 2: 0},
            "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
            "packages/other/src/y.cpp": {1: 0},
        }
    )

    def figure(*parts: fmt.Part, units: tuple[str, ...] = ("packages/n",)) -> object:
        files = fmt.merge(parts).files
        return coverage_floors.figures(tmp_path, files, units, (package,))

    (tmp_path / "packages" / "other" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "other" / "src" / "y.cpp").write_text("int y;\n")
    assert figure(measured) == {"packages/n": 50.0}
    # A suite measured with no source file reached is 100; one whose
    # suite left no part is not measured.
    assert figure() == {"packages/n": 100.0}
    assert figure(_reading({"packages/n/src/x.cpp": {}})) == {"packages/n": 100.0}
    assert figure(measured, units=()) == {}
    # Two legs, each reaching one line: the union reaches both.
    first = _reading({"packages/n/src/x.cpp": {1: 1, 2: 0}})
    second = _reading(
        {"packages/n/src/x.cpp": {1: 0, 2: 3}, "packages/n/src/w.cpp": {1: 0, 2: 0}}
    )
    (package.directory / "src" / "w.cpp").write_text("int w;\nint v;\n")
    merged = fmt.merge([first, second]).files
    assert dict(merged["packages/n/src/x.cpp"].entry.counts) == {1: 1, 2: 3}
    assert figure(first, second) == {"packages/n": 50.0}
    # A file the checkout no longer has counts nowhere.
    (package.directory / "src" / "w.cpp").unlink()
    assert figure(first, second) == {"packages/n": 100.0}


def test_the_unit_row_carries_its_parts_and_one_of_another_format_is_a_miss() -> None:
    reading = {
        "format": 3,
        "unit": "packages/n",
        "measurer": {"name": "gcov", "counts": "counts", "branches": "none"},
        "files": {"packages/n/src/x.cpp": {"digest": "", "statements": "1-2"}},
    }
    unit = coverage_record.Unit("packages/n", "k" * 64, "7", "a" * 40, (reading,))
    fields = coverage_record.unit_fields(unit)
    assert fields["parts"] == [reading]
    assert coverage_record._parse_unit(fields) == (unit, "")
    assert unit.files == ("packages/n/src/x.cpp",)
    earlier = {**fields, "parts": [{**reading, "format": 2}]}
    assert coverage_record._parse_unit(earlier) == (
        None,
        "holds a part of format 2, and this workshop reads format 3",
    )
    assert coverage_record._parse_unit({**fields, "parts": "no"})[0] is None
    assert coverage_record._parse_unit({**fields, "parts": ["no"]})[0] is None
    assert coverage_record._parse_unit("no") == (None, "carries no unit")


def test_a_native_package_unmeasured_refuses_and_below_its_floor_shares_the_prose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _native(tmp_path)
    # The refusal first: a suite nobody measured never passes as measured.
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {}
    with pytest.raises(_FAILURES, match="packages/n: not measured; its tests ran"):
        coverage_floors.enforce_coverage(tmp_path, (package,))
    coverage_floors.report_coverage(tmp_path, (package,))
    assert "coverage packages/n: not measured here" in capsys.readouterr().out
    # Half the source lines reached, against a floor of 100: the same
    # sentence a python package gets.
    leave_part(tmp_path, "packages/n", {"packages/n/src/x.cpp": {1: 1, 2: 0}})
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {
        "packages/n": 50.0
    }
    with pytest.raises(
        _FAILURES,
        match=r"packages/n: 50\.0% is below the committed floor of 100\.0% by more"
        r" than the 0\.5% epsilon",
    ):
        coverage_floors.enforce_coverage(tmp_path, (package,))
    coverage_floors.report_coverage(tmp_path, (package,))
    assert (
        "coverage packages/n: 50.0% here (floor 100.0% judges"
        in capsys.readouterr().out
    )
    # At its floor it passes, with the numbers on screen.
    package = _native(tmp_path, "floor = 50")
    assert coverage_floors.enforce_coverage(tmp_path, (package,)) == {
        "packages/n": 50.0
    }
    assert (
        "coverage packages/n: 50.0% (floor 50.0%, epsilon 0.5%)"
        in capsys.readouterr().out
    )
