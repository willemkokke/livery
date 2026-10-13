"""A native measurer's parts: refusals first, then the readings, the row, the floors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.extensions.cmake import _build as _cmake
from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage import _record as coverage_record
from livery.extensions.coverage._parts import PARTS, part_files, read_part
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
        " format 2; this workshop reads format 1; the figure reads without it"
    ) in capsys.readouterr().out
    bad.unlink()
    assert part_files(tmp_path) == []


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


def test_cmake_s_part_is_one_coverage_reads_counting_each_line_s_runs(
    tmp_path: Path,
) -> None:
    # The part in the line format is the contract between the cmake
    # extension, which writes it, and coverage, which reads it.
    package = _native(tmp_path)
    (package.directory / "src" / "x.cpp").write_bytes(
        b"int f() {\r\n  return 1;\r\n}\r\n"
    )
    measured = {
        "packages/n/src/x.cpp": {0: 4, 1: 1, 2: 0},
        "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
        "packages/other/src/y.cpp": {1: 0},
    }
    assert _cmake.within(measured, package) == {
        "packages/n/src/x.cpp": {0: 4, 1: 1, 2: 0},
        "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
    }
    path = _cmake.write_part(
        tmp_path, "packages/n", _cmake.within(measured, package), "llvm"
    )
    assert path == tmp_path / PARTS / "llvm-cov" / "packages-n.json"
    part = read_part(path, tmp_path)
    assert part.unit == "packages/n"
    assert part.measurer == fmt.Measurer("llvm-cov", counts=fmt.COUNTS)
    source = part.files["packages/n/src/x.cpp"]
    # Line 0 is no line; the digest is the text's, its CRLF read as LF.
    assert source.statements == {1, 2} and dict(source.counts) == {1: 1}
    assert source.digest == fmt.digest(b"int f() {\n  return 1;\n}\n")
    test_file = part.files["packages/n/tests/test_x.cpp"]
    assert test_file.statements == {1, 2, 3} and test_file.counts == {}
    assert test_file.digest == fmt.digest(b"int main() { return 0; }\n")
    # A file the checkout lacks is named with no content.
    _cmake.write_part(
        tmp_path, "packages/n", {"packages/n/src/gone.cpp": {1: 1}}, "gcov"
    )
    gone = read_part(tmp_path / PARTS / "gcov" / "packages-n.json", tmp_path)
    assert gone.files["packages/n/src/gone.cpp"].digest == ""
    assert [path.parent.name for path in part_files(tmp_path)] == ["gcov", "llvm-cov"]
    # A measured run lets the package's parts of an earlier run go, any
    # measurer's, and no other package's.
    _cmake.write_part(tmp_path, "packages/other", {}, "msvc")
    _cmake.remove_parts(tmp_path, "packages/n")
    assert part_files(tmp_path) == [
        tmp_path / PARTS / "dotnet-coverage" / "packages-other.json"
    ]


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
        "format": 1,
        "unit": "packages/n",
        "measurer": {"name": "gcov", "counts": "counts", "branches": "none"},
        "files": {"packages/n/src/x.cpp": {"digest": "", "statements": "1-2"}},
    }
    unit = coverage_record.Unit("packages/n", "k" * 64, "7", "a" * 40, (reading,))
    fields = coverage_record.unit_fields(unit)
    assert fields["parts"] == [reading]
    assert coverage_record._parse_unit(fields) == (unit, "")
    assert unit.files == ("packages/n/src/x.cpp",)
    later = {**fields, "parts": [{**reading, "format": 2}]}
    assert coverage_record._parse_unit(later) == (
        None,
        "holds a part of format 2, and this workshop reads format 1",
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
    _cmake.write_part(
        tmp_path, "packages/n", {"packages/n/src/x.cpp": {1: 1, 2: 0}}, "gcov"
    )
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
