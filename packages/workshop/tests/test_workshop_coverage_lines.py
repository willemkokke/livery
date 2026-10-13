"""Coverage as lines: the refusals first, then the readings, the row, and the floors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.extensions.cmake import _build as _cmake
from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _lines as lines_
from livery.extensions.coverage import _record as coverage_record
from livery.footman import Failed
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)

GCOV = json.dumps(
    {
        "files": [
            {
                "file": "../../src/x.cpp",
                "lines": [
                    {"line_number": 3, "count": 2},
                    {"line_number": 4, "count": 0},
                ],
            }
        ]
    }
)
LCOV = (
    "SF:/w/packages/n/src/x.cpp\nFN:3,f\nDA:3,1\nDA:4,0\nDA:7,0,checksum\n"
    "end_of_record\n"
)
COBERTURA = (
    '<?xml version="1.0"?><coverage><sources><source>D:\\</source></sources>'
    '<packages><package name="p"><classes>'
    '<class name="x.cpp" filename="a\\w\\packages\\n\\src\\x.cpp">'
    '<lines><line number="3" hits="1"/><line number="4" hits="0"/></lines>'
    "</class></classes></package></packages></coverage>"
)


def _native(tmp_path: Path, floor: str = "floor = 100") -> Package:
    directory = tmp_path / "packages" / "n"
    (directory / "src").mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "src" / "x.cpp").write_text("int f() { return 1; }\n")
    (directory / "tests" / "test_x.cpp").write_text("int main() { return 0; }\n")
    (directory / "workshop.toml").write_text(
        f'kind = "cpp-conan"\nname = "acme-n"\n[coverage]\n{floor}\n'
    )
    return Package(
        directory=directory,
        path="packages/n",
        name="acme-n",
        kind="cpp-conan",
        depends=(),
    )


def test_a_part_that_is_not_one_refuses_naming_it(tmp_path: Path) -> None:
    bad = tmp_path / ".coverage-lines.bad.json"
    bad.write_text("{}")
    with pytest.raises(
        _FAILURES, match=r"bad\.json: not a lines part \(no files table\)"
    ):
        lines_.read_parts(tmp_path)
    bad.write_text("not json")
    with pytest.raises(_FAILURES, match=r"bad\.json: not a lines part"):
        lines_.read_parts(tmp_path)
    bad.write_text('{"files": {}}')
    with pytest.raises(_FAILURES, match="names no package"):
        lines_.read_parts(tmp_path)
    bad.unlink()
    assert lines_.read_parts(tmp_path) == {}


def test_a_reading_that_is_not_the_tools_format_refuses() -> None:
    with pytest.raises(_FAILURES, match="not JSON"):
        _cmake.from_gcov_json("nope")
    with pytest.raises(_FAILURES, match="no files table"):
        _cmake.from_gcov_json('{"a": 1}')
    with pytest.raises(_FAILURES, match="not XML"):
        _cmake.from_cobertura("<not xml")
    assert _cmake.measurer_for("Intel") == ""
    assert _cmake.measurer_for("") == ""
    assert _cmake.from_lcov("garbage\nDA:1,1\n") == {}


def test_each_measurers_reading_reduces_to_lines() -> None:
    assert _cmake.from_gcov_json(GCOV) == {"../../src/x.cpp": {3: 2, 4: 0}}
    # Two gcov documents on their own lines, one per counter file.
    assert _cmake.from_gcov_json(GCOV + "\n" + GCOV) == {
        "../../src/x.cpp": {3: 4, 4: 0}
    }
    assert _cmake.from_lcov(LCOV) == {"/w/packages/n/src/x.cpp": {3: 1, 4: 0, 7: 0}}
    assert _cmake.from_cobertura(COBERTURA) == {
        "D:\\a\\w\\packages\\n\\src\\x.cpp": {3: 1, 4: 0}
    }
    assert _cmake.measurer_for("GNU") == "gcov"
    assert _cmake.measurer_for("AppleClang") == "llvm"
    assert _cmake.measurer_for("Clang") == "llvm"
    assert _cmake.measurer_for("MSVC") == "msvc"


def test_relativise_keys_by_the_repository_path_and_drops_strangers(
    tmp_path: Path,
) -> None:
    inside = tmp_path / "packages" / "n" / "src" / "x.cpp"
    read = {
        str(inside): {1: 1},
        "packages\\n\\src\\z.cpp": {2: 0},
        str(tmp_path.parent / "elsewhere.cpp"): {3: 1},
    }
    assert _cmake.relativise(read, tmp_path) == {
        "packages/n/src/x.cpp": {1: 1},
        "packages/n/src/z.cpp": {2: 0},
    }


def test_percent_counts_source_lines_alone_and_the_union_adds_hits(
    tmp_path: Path,
) -> None:
    package = _native(tmp_path)
    measured = {
        "packages/n/src/x.cpp": {1: 1, 2: 0},
        "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
        "packages/other/src/y.cpp": {1: 0},
    }
    assert lines_.percent(measured, package) == 50.0
    assert _cmake.within(measured, package) == {
        "packages/n/src/x.cpp": {1: 1, 2: 0},
        "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
    }
    assert lines_.percent({}, package) is None
    assert lines_.percent({"packages/n/src/x.cpp": {}}, package) == 100.0
    # Two legs, each reaching one line: the union reaches both.
    first = {"packages/n/src/x.cpp": {1: 1, 2: 0}}
    second = {
        "packages/n/src/x.cpp": {1: 0, 2: 3},
        "packages/n/src/w.cpp": {1: 0, 2: 0},
    }
    assert lines_.merge(first, second) == {
        "packages/n/src/x.cpp": {1: 1, 2: 3},
        "packages/n/src/w.cpp": {1: 0, 2: 0},
    }
    assert lines_.percent(lines_.merge(first, second), package) == 50.0
    # A part round-trips through the root, sorted pairs and all.
    lines_.write_part(tmp_path, "packages/n", measured)
    assert lines_.read_parts(tmp_path) == {"packages/n": measured}
    assert lines_.pairs(first) == {"packages/n/src/x.cpp": [(1, 1), (2, 0)]}
    # The cmake extension's test leaves the part byte for byte as the
    # coverage extension writes and reads it: the shape is their contract.
    written = (tmp_path / f"{lines_.PART_PREFIX}packages-n.json").read_bytes()
    assert _cmake.write_part(tmp_path, "packages/n", measured).read_bytes() == written
    lines_.remove_parts(tmp_path)
    assert lines_.read_parts(tmp_path) == {}


def test_the_unit_row_carries_its_measurer_and_an_older_row_reads_as_arcs() -> None:
    unit = coverage_record.Unit(
        "packages/n",
        "k" * 64,
        "7",
        "a" * 40,
        {"packages/n/src/x.cpp": [(1, 1), (2, 0)]},
        measurer=coverage_record.LINES,
    )
    fields = coverage_record._unit_fields(unit)
    assert fields["measurer"] == "lines"
    assert coverage_record._parse_unit(fields) == unit
    older = {k: v for k, v in fields.items() if k != "measurer"}
    parsed = coverage_record._parse_unit(older)
    assert parsed is not None and parsed.measurer == coverage_record.ARCS


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
    lines_.write_part(tmp_path, "packages/n", {"packages/n/src/x.cpp": {1: 1, 2: 0}})
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
