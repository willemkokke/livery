"""A native measurer's parts: refusals first, then the readings, the row, the floors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from livery.extensions.cmake import _build as _cmake
from livery.extensions.cmake import _readings
from livery.extensions.cmake._readings import Reading
from livery.extensions.coverage import _floors as coverage_floors
from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage import _record as coverage_record
from livery.extensions.coverage._parts import PARTS, part_files, read_part
from livery.footman import Failed
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)

#: A profile llvm-cov and gcov read of one source: a macro, a block the
#: preprocessor removes, and an ``if``/``else`` on one line whose ``if``
#: arm never runs; llvm-cov's JSON and lcov exports of it, and gcov's
#: JSON with its branches.
MEASURED = Path(__file__).resolve().parent / "fixtures" / "native-measured"

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
    bad.write_text('{"format": 1}')
    with pytest.raises(fmt.OtherVersion, match="a part of format 1"):
        read_part(bad, tmp_path)
    assert coverage_floors.measured_coverage(tmp_path, (package,)) == {}
    assert (
        "  coverage: .workshop/coverage/parts/gcov/packages-n.json: a part of"
        " format 1; this workshop reads format 2; the figure reads without it"
    ) in capsys.readouterr().out
    bad.unlink()
    assert part_files(tmp_path) == []


def test_a_reading_that_is_not_the_tools_format_refuses() -> None:
    with pytest.raises(_FAILURES, match="not JSON"):
        _readings.from_gcov_json("nope")
    with pytest.raises(_FAILURES, match="no files table"):
        _readings.from_gcov_json('{"a": 1}')
    with pytest.raises(_FAILURES, match="not XML"):
        _readings.from_cobertura("<not xml")
    with pytest.raises(_FAILURES, match="llvm-cov printed no JSON export"):
        _readings.from_llvm_json("SF:x.cpp\nDA:1,1\n")
    with pytest.raises(_FAILURES, match="llvm-cov printed no data table"):
        _readings.from_llvm_json('{"type": "llvm.coverage.json.export"}')
    assert _cmake.measurer_for("Intel") == ""
    assert _cmake.measurer_for("") == ""


def _lines(readings: dict[str, Reading]) -> dict[str, dict[int, int]]:
    return {name: reading.lines for name, reading in readings.items()}


def test_each_measurers_reading_reduces_to_lines() -> None:
    assert _lines(_readings.from_gcov_json(GCOV)) == {"../../src/x.cpp": {3: 2, 4: 0}}
    # Two gcov documents on their own lines, one per counter file.
    assert _lines(_readings.from_gcov_json(GCOV + "\n" + GCOV)) == {
        "../../src/x.cpp": {3: 4, 4: 0}
    }
    assert _lines(_readings.from_cobertura(COBERTURA)) == {
        "D:\\a\\w\\packages\\n\\src\\x.cpp": {3: 1, 4: 0}
    }
    assert _cmake.measurer_for("GNU") == "gcov"
    assert _cmake.measurer_for("AppleClang") == "llvm"
    assert _cmake.measurer_for("Clang") == "llvm"
    assert _cmake.measurer_for("MSVC") == "msvc"


def _lcov_lines(text: str) -> dict[int, int]:
    """The ``DA:`` lines of an lcov trace of one file: each line's count."""
    found: dict[int, int] = {}
    for line in text.splitlines():
        if line.startswith("DA:"):
            number, count = line[3:].split(",")[:2]
            found[int(number)] = int(count)
    return found


def test_llvm_line_counts_from_json_equal_its_lcov_export() -> None:
    # One profile, both exports: the JSON's segments give each line the
    # count llvm-cov's own lcov export prints, the macro's line, the
    # removed block's lines and the one-line if/else included.
    read = _readings.from_llvm_json((MEASURED / "llvm.json").read_text())
    lcov = _lcov_lines((MEASURED / "llvm.lcov").read_text())
    assert read["fixture.cpp"].lines == lcov
    assert lcov[3] == 1 and 30 not in lcov and lcov[6] == 1 and lcov[24] == 2


#: The tools a fresh llvm profile needs, in the order a run calls them.
_LLVM_TOOLS = ("clang++", "llvm-profdata", "llvm-cov")


def _clang_tools() -> list[list[str]]:
    """How this host runs clang++, llvm-profdata and llvm-cov; empty when it lacks one.

    On macOS through xcrun, which hands clang the SDK's headers.
    """
    if sys.platform == "darwin" and shutil.which("xcrun"):
        found = [
            subprocess.run(
                ["xcrun", "--find", name], capture_output=True, text=True, check=False
            ).stdout.strip()
            for name in _LLVM_TOOLS
        ]
        return [["xcrun", name] for name in _LLVM_TOOLS] if all(found) else []
    paths = [shutil.which(name) for name in _LLVM_TOOLS]
    return [[path] for path in paths if path] if all(paths) else []


needs_llvm = pytest.mark.skipif(
    not _clang_tools(), reason="this host has no clang++, llvm-profdata and llvm-cov"
)


@needs_llvm
def test_a_fresh_llvm_profile_s_line_counts_equal_its_lcov_export(
    tmp_path: Path,
) -> None:
    # The host's own llvm-cov, on a profile made now: the line view's
    # rule is llvm-cov's, so the reading follows the version installed.
    clang, profdata, cov = _clang_tools()
    source = tmp_path / "fixture.cpp"
    source.write_bytes((MEASURED / "fixture.cpp").read_bytes())
    program = tmp_path / "fixture"
    subprocess.run(
        [
            *clang,
            "-O0",
            "-fprofile-instr-generate",
            "-fcoverage-mapping",
            str(source),
            "-o",
            str(program),
        ],
        check=True,
    )
    subprocess.run(
        [str(program)],
        env={**os.environ, "LLVM_PROFILE_FILE": str(tmp_path / "run.profraw")},
        check=True,
    )
    merged = tmp_path / "run.profdata"
    subprocess.run(
        [
            *profdata,
            "merge",
            "-sparse",
            str(tmp_path / "run.profraw"),
            "-o",
            str(merged),
        ],
        check=True,
    )

    def exported(form: str) -> str:
        return subprocess.run(
            [
                *cov,
                "export",
                f"-format={form}",
                f"-instr-profile={merged}",
                str(program),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    read = _readings.from_llvm_json(exported("text"))
    (name,) = [name for name in read if name.endswith("fixture.cpp")]
    assert read[name].lines == _lcov_lines(exported("lcov"))
    assert read[name].ranges[(6, 21, 6, 37)] == 0


def test_llvm_s_reading_holds_its_ranges_branches_functions_and_removed_lines(
    tmp_path: Path,
) -> None:
    read = _readings.from_llvm_json((MEASURED / "llvm.json").read_text())["fixture.cpp"]
    # The if/else of line 6: its condition ran once, false; the `if` arm
    # never ran and the `else` arm did.
    assert read.branches[(6, 0)] == (0, "") and read.branches[(6, 1)] == (1, "")
    assert read.ranges[(6, 7, 6, 19)] == 1
    assert read.ranges[(6, 21, 6, 37)] == 0 and read.ranges[(6, 43, 6, 60)] == 1
    # The macro counts where it is called, never on its own line.
    assert read.ranges[(19, 51, 19, 56)] == 1
    assert not [extent for extent in read.ranges if extent[0] == 3]
    # The block the preprocessor removed inside main is excluded; the one
    # outside every function has no region to say so.
    assert read.excluded == {29, 30, 31, 32}
    assert read.functions[("_ZL5clampii", 5)] == (7, 1)
    assert read.functions[("_ZL12never_calledi", 9)] == (11, 0)

    def demangled(names: list[str]) -> list[str]:
        known = {
            "_ZL5clampii": "clamp(int, int)",
            "_ZL12never_calledi": "never_called(int)",
            "_Z7doubledIiET_S0_": "int doubled<int>(int)",
        }
        return [known.get(name, name) for name in names]

    readings = {"packages/n/src/fixture.cpp": read}
    _readings.demangle(readings, demangled)
    assert read.functions[("clamp(int, int)", 5)] == (7, 1)
    assert ("int doubled<int>(int)", 19) in read.functions and (
        "main",
        21,
    ) in read.functions
    # A demangler that answers another number of names changes none.
    _readings.demangle(readings, lambda names: names[:1])
    assert ("clamp(int, int)", 5) in read.functions
    source = tmp_path / "packages" / "n" / "src" / "fixture.cpp"
    source.parent.mkdir(parents=True)
    source.write_bytes((MEASURED / "fixture.cpp").read_bytes())
    part = read_part(
        _readings.write_part(tmp_path, "packages/n", readings, "llvm"), tmp_path
    )
    assert part.measurer == fmt.Measurer(
        "llvm-cov",
        counts=fmt.COUNTS,
        branches=fmt.INDEXES,
        regions=("function",),
        columns=True,
    )
    entry = part.files["packages/n/src/fixture.cpp"]
    assert entry.excluded == {29, 30, 31, 32}
    assert fmt.Range(6, 21, 6, 37, 0) in entry.ranges
    assert entry.partial == {6, 27, 34}
    assert fmt.unrun_columns(entry)[6] == ((21, 37),)
    fmt.check_ranges(entry, source.read_text(), source="fixture.cpp")
    assert [region.name for region in entry.regions] == [
        "clamp(int, int)",
        "never_called(int)",
        "int doubled<int>(int)",
        "main",
    ]


def test_gcov_s_reading_holds_its_branches_by_index_and_its_functions() -> None:
    read = _readings.from_gcov_json((MEASURED / "gcov.json").read_text())["fixture.cpp"]
    assert read.lines[6] == 1 and read.lines[28] == 0 and 3 not in read.lines
    assert read.branches[(6, 0)] == (0, "") and read.branches[(6, 1)] == (1, "")
    assert read.branches[(24, 0)] == (1, "") and read.branches[(24, 1)] == (1, "")
    assert read.functions[("clamp(int, int)", 5)] == (7, 1)
    assert read.functions[("never_called(int)", 9)] == (11, 0)
    assert read.ranges == {} and read.excluded == set()
    # An exception's path is tagged so.
    thrown = json.dumps(
        {
            "files": [
                {
                    "file": "x.cpp",
                    "lines": [
                        {
                            "line_number": 2,
                            "count": 1,
                            "branches": [
                                {"count": 1, "throw": False},
                                {"count": 0, "throw": True},
                            ],
                        }
                    ],
                }
            ]
        }
    )
    assert _readings.from_gcov_json(thrown)["x.cpp"].branches == {
        (2, 0): (1, ""),
        (2, 1): (0, "throw"),
    }


def _gcc_tools() -> tuple[str, str]:
    """g++ and the gcov beside it where this host has both; empty if not."""
    for suffix in ("", "-15", "-14", "-13"):
        compiler, gcov = shutil.which(f"g++{suffix}"), shutil.which(f"gcov{suffix}")
        if compiler and gcov:
            version = subprocess.run(
                [compiler, "--version"], capture_output=True, text=True, check=False
            ).stdout
            if "clang" not in version.lower():
                return compiler, gcov
    return "", ""


needs_gcc = pytest.mark.skipif(
    not all(_gcc_tools()), reason="this host has no g++ with its gcov"
)


@needs_gcc
def test_a_fresh_gcov_run_reads_branches_and_functions(tmp_path: Path) -> None:
    compiler, gcov = _gcc_tools()
    source = tmp_path / "fixture.cpp"
    source.write_bytes((MEASURED / "fixture.cpp").read_bytes())
    subprocess.run(
        [compiler, "--coverage", "-O0", str(source), "-o", str(tmp_path / "fixture")],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run([str(tmp_path / "fixture")], cwd=tmp_path, check=True)
    (data,) = tmp_path.glob("*.gcda")
    printed = subprocess.run(
        [gcov, "--branch-probabilities", "--json-format", "--stdout", str(data)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    read = _readings.from_gcov_json(printed)
    (name,) = [name for name in read if name.endswith("fixture.cpp")]
    assert read[name].branches[(6, 0)][0] == 0 and read[name].branches[(6, 1)][0] == 1
    assert ("clamp(int, int)", 5) in read[name].functions


def test_a_cobertura_report_reads_how_many_of_a_line_s_branches_ran(
    tmp_path: Path,
) -> None:
    report = (
        '<?xml version="1.0"?><coverage><sources><source>/w</source></sources>'
        '<packages><package name="p"><classes>'
        '<class name="x.cpp" filename="packages/n/src/x.cpp">'
        '<methods><method name="clamp" signature="(int, int)">'
        '<lines><line number="5" hits="1"/>'
        '<line number="6" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
        "</lines></method></methods>"
        '<lines><line number="5" hits="1"/>'
        '<line number="6" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
        '<line number="9" hits="0"/></lines>'
        "</class></classes></package></packages></coverage>"
    )
    read = _readings.from_cobertura(report)["/w/packages/n/src/x.cpp"]
    # A line named under its method and again under its class counts once.
    assert read.lines == {5: 1, 6: 1, 9: 0}
    assert read.branch_counts == {6: (2, 1)}
    # A Cobertura report counts no calls, so the method's are left out.
    assert read.functions == {("clamp(int, int)", 5): (6, None)}
    root = tmp_path / "w"
    (root / "packages" / "n" / "src").mkdir(parents=True)
    part = read_part(
        _readings.write_part(
            root, "packages/n", {"packages/n/src/x.cpp": read}, "msvc"
        ),
        root,
    )
    (method,) = part.files["packages/n/src/x.cpp"].regions
    assert (method.name, method.start, method.calls) == ("clamp(int, int)", 5, None)
    assert part.files["packages/n/src/x.cpp"].branch_counts == ((6, 2, 1),)


def test_relativise_keys_by_the_repository_path_and_drops_strangers(
    tmp_path: Path,
) -> None:
    inside = tmp_path / "packages" / "n" / "src" / "x.cpp"
    read = {
        str(inside): Reading(lines={1: 1}),
        "packages\\n\\src\\z.cpp": Reading(lines={2: 0}),
        "packages/n/src/x.cpp": Reading(lines={1: 2}),
        str(tmp_path.parent / "elsewhere.cpp"): Reading(lines={3: 1}),
    }
    # Two names of one file add up.
    assert _lines(_readings.relativise(read, tmp_path)) == {
        "packages/n/src/x.cpp": {1: 3},
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
        "packages/n/src/x.cpp": Reading(lines={0: 4, 1: 1, 2: 0}),
        "packages/n/tests/test_x.cpp": Reading(lines={1: 0, 2: 0, 3: 0}),
        "packages/other/src/y.cpp": Reading(lines={1: 0}),
    }
    assert _lines(_readings.within(measured, package)) == {
        "packages/n/src/x.cpp": {0: 4, 1: 1, 2: 0},
        "packages/n/tests/test_x.cpp": {1: 0, 2: 0, 3: 0},
    }
    path = _readings.write_part(
        tmp_path, "packages/n", _readings.within(measured, package), "llvm"
    )
    assert path == tmp_path / PARTS / "llvm-cov" / "packages-n.json"
    part = read_part(path, tmp_path)
    assert part.unit == "packages/n"
    assert part.measurer == fmt.Measurer(
        "llvm-cov",
        counts=fmt.COUNTS,
        branches=fmt.INDEXES,
        regions=("function",),
        columns=True,
    )
    source = part.files["packages/n/src/x.cpp"]
    # Line 0 is no line; the digest is the text's, its CRLF read as LF.
    assert source.statements == {1, 2} and dict(source.counts) == {1: 1}
    assert source.digest == fmt.digest(b"int f() {\n  return 1;\n}\n")
    test_file = part.files["packages/n/tests/test_x.cpp"]
    assert test_file.statements == {1, 2, 3} and test_file.counts == {}
    assert test_file.digest == fmt.digest(b"int main() { return 0; }\n")
    # A file the checkout lacks is named with no content.
    _readings.write_part(
        tmp_path,
        "packages/n",
        {"packages/n/src/gone.cpp": Reading(lines={1: 1})},
        "gcov",
    )
    gone = read_part(tmp_path / PARTS / "gcov" / "packages-n.json", tmp_path)
    assert gone.files["packages/n/src/gone.cpp"].digest == ""
    assert [path.parent.name for path in part_files(tmp_path)] == ["gcov", "llvm-cov"]
    # A measured run lets the package's parts of an earlier run go, any
    # measurer's, and no other package's.
    _readings.write_part(tmp_path, "packages/other", {}, "msvc")
    _readings.remove_parts(tmp_path, "packages/n")
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
        "format": 2,
        "unit": "packages/n",
        "measurer": {"name": "gcov", "counts": "counts", "branches": "none"},
        "files": {"packages/n/src/x.cpp": {"digest": "", "statements": "1-2"}},
    }
    unit = coverage_record.Unit("packages/n", "k" * 64, "7", "a" * 40, (reading,))
    fields = coverage_record.unit_fields(unit)
    assert fields["parts"] == [reading]
    assert coverage_record._parse_unit(fields) == (unit, "")
    assert unit.files == ("packages/n/src/x.cpp",)
    earlier = {**fields, "parts": [{**reading, "format": 1}]}
    assert coverage_record._parse_unit(earlier) == (
        None,
        "holds a part of format 1, and this workshop reads format 2",
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
    _readings.write_part(
        tmp_path,
        "packages/n",
        {"packages/n/src/x.cpp": Reading(lines={1: 1, 2: 0})},
        "gcov",
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
