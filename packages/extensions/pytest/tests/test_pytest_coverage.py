"""The reduction of coverage.py's arcs to the line format, and the pages from it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from extensions_pytest_measured import SOURCES, Page, measure, read_page
from livery.extensions.pytest import _coverage

#: The measured modules' paths from the workspace root.
MEASURED_PY = f"{SOURCES}/measured.py"
SHAPES_PY = f"{SOURCES}/shapes.py"

#: Every category a line is drawn in, coverage.py's own.
CATEGORIES = ("exc", "mis", "par", "run", "exc2", "mis2", "par2", "run2")


def _workspace(root: Path, listed: str = '["docs", "pytest", "coverage"]') -> Path:
    """A workspace listing *listed*; its member ``core`` declares an htmlcov report."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")
    core = root / "packages" / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-core"\n\n[docs]\n'
        'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    (core / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    return root


def _leg_data(
    root: Path, suffix: str, recorded: dict[str, dict[str, list[tuple[int, int]]]]
) -> None:
    """One metered process's data file at *root*: its arcs by context, "" for none."""
    from coverage import CoverageData

    data = CoverageData(basename=str(root / ".coverage"), suffix=suffix)
    for context, files in recorded.items():
        data.set_context(context)
        data.add_arcs({name: set(arcs) for name, arcs in files.items()})
    data.write()


#: What a leg asks of the reduction for the member ``core``'s suite.
CORE = {
    "unit": "packages/core",
    "tests": "packages/core/tests/",
    "within": ["packages/core/"],
}


# --- refusals ----------------------------------------------------------------


def test_a_reduction_that_disagrees_with_coverage_py_refuses_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import results

    root = _workspace(tmp_path / "ws")
    measure(root)
    reduced = _coverage.Arcs().left(root)
    assert reduced[0]["files"][MEASURED_PY]["branches"][6] == [26, -1, 0, 1, "waived"]
    # A reading that loses the waiver of `# pragma: no branch` counts the
    # waived jump as never taken, where coverage.py counts it covered.
    original = results.analysis_from_file_reporter

    def forgetful(*args: Any, **kwargs: Any) -> results.Analysis:
        analysis = original(*args, **kwargs)
        analysis.no_branch = set()
        return analysis

    monkeypatch.setattr(results, "analysis_from_file_reporter", forgetful)
    with pytest.raises(
        ValueError,
        match=(
            r"^packages/core/src/acme/core/measured\.py: the part reads 16 of 18"
            r" statements and 4 of 8 branches run, 4 partial \(76\.92%\), and"
            r" coverage\.py \S+ reads 16 of 18 statements and 5 of 8 branches"
            r" run, 3 partial \(80\.77%\); a part whose figure is not"
            r" coverage\.py's is never handed over$"
        ),
    ):
        _coverage.Arcs().left(root)


def test_a_leg_with_no_metered_data_refuses_naming_the_meter(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"^the leg left no coverage\.py data .* arms COVERAGE_PROCESS_START",
    ):
        _coverage.Arcs().leg(tmp_path, [CORE])


def test_a_unit_that_names_no_path_refuses_naming_its_place(tmp_path: Path) -> None:
    _leg_data(tmp_path, "host.1", {"": {}})
    with pytest.raises(ValueError, match=r"^unit 1 names no path"):
        _coverage.Arcs().leg(tmp_path, [CORE, {"tests": "tests/"}])


def test_a_file_coverage_py_cannot_parse_refuses_naming_it(tmp_path: Path) -> None:
    bad = tmp_path / SOURCES / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("def broken(:\n")
    _leg_data(tmp_path, "host.1", {"": {f"{SOURCES}/bad.py": [(-1, 1)]}})
    with pytest.raises(
        ValueError, match=rf"^{SOURCES}/bad\.py: coverage\.py cannot read it"
    ):
        _coverage.Arcs().leg(tmp_path, [CORE])


def test_a_file_the_checkout_lacks_is_named_without_content_and_one_outside_left_out(
    tmp_path: Path,
) -> None:
    from coverage import CoverageData

    root = _workspace(tmp_path / "ws")
    kept = root / SOURCES / "kept.py"
    kept.parent.mkdir(parents=True)
    kept.write_text("a = 1\n")
    data = CoverageData(basename=str(root / ".coverage"))
    # A file named absolute inside the root, one the checkout lost, and
    # one outside the root: a module a test built in its temporary
    # directory, which is no file of the workspace.
    data.add_arcs(
        {
            str(kept): {(-1, 1), (1, -1)},
            f"{SOURCES}/gone.py": {(-1, 1), (1, -1)},
            str(tmp_path / "elsewhere.py"): {(-1, 1), (1, -1)},
        }
    )
    data.write()
    (part,) = _coverage.Arcs().left(root)
    assert sorted(part["files"]) == [f"{SOURCES}/gone.py", f"{SOURCES}/kept.py"]
    assert part["files"][f"{SOURCES}/gone.py"] == {"digest": ""}
    assert part["files"][f"{SOURCES}/kept.py"]["hits"] == "1"
    # No context beside the empty one was recorded: the part names none.
    assert part["unit"] == "." and part["contexts"] == []
    assert "reached" not in part["files"][f"{SOURCES}/kept.py"]


def test_a_run_that_left_no_data_reduces_to_no_part(tmp_path: Path) -> None:
    assert _coverage.Arcs().left(tmp_path) == []


# --- the reduction -----------------------------------------------------------


def test_each_unit_reduces_to_its_own_part_and_each_source_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import python

    sources = tmp_path / SOURCES
    sources.mkdir(parents=True)
    (sources / "__init__.py").write_text("")
    (sources / "mod.py").write_text("a = 1\nif a:\n    b = 2\n")
    built: list[str] = []
    original = python.PythonFileReporter

    def counted(morf: Any, coverage: Any = None) -> Any:
        built.append(str(morf))
        return original(morf, coverage)

    monkeypatch.setattr(python, "PythonFileReporter", counted)
    module = f"{SOURCES}/mod.py"
    init = f"{SOURCES}/__init__.py"
    # The member's own test ran the module; the workspace's own test ran
    # lines that are no statements, and they count none, and an empty
    # module holds no statement at all. A file outside the member is
    # none of its part.
    _leg_data(
        tmp_path,
        "host.1",
        {
            "packages/core/tests/test_mod.py::test_it|run": {
                module: [(-1, 1), (1, 2), (2, 3), (3, -1)],
                "packages/other/x.py": [(-1, 1)],
            },
            "tests/test_all.py::test_it|run": {
                module: [(-1, 99), (99, -1)],
                init: [(-1, 1)],
            },
        },
    )
    one, two = _coverage.Arcs().leg(
        tmp_path, [CORE, {"unit": "tests", "tests": "tests/"}]
    )
    assert [one["unit"], two["unit"]] == ["packages/core", "tests"]
    assert sorted(one["files"]) == [module]
    assert one["files"][module]["hits"] == "1-3"
    # Every branch carries the sentence the source gives it, taken or
    # not: a page shows it while the branch never ran.
    assert one["files"][module]["branches"] == [
        [
            2,
            -1,
            0,
            0,
            "line 2 didn't exit the module because the condition on line 2 was"
            " always true",
        ],
        [
            2,
            3,
            1,
            1,
            "line 2 didn't jump to line 3 because the condition on line 2 was"
            " never true",
        ],
    ]
    assert "hits" not in two["files"][module]
    assert two["files"][init] == {"digest": _coverage._digest(b"")}
    assert sorted(built) == sorted(str(tmp_path / name) for name in (module, init))
    assert one["measurer"]["name"] == "coverage.py"
    assert one["measurer"]["judges"] == ["lines", "branches"]
    # coverage.py reads no columns: its parts hold no ranges.
    assert one["measurer"]["columns"] is False
    assert not [entry for entry in one["files"].values() if "ranges" in entry]
    # Each part names its own suite's tests and no other suite's, for the
    # statements they reached: the workspace's test reached none.
    assert one["contexts"] == ["packages/core/tests/test_mod.py::test_it|run"]
    assert two["contexts"] == []


def test_a_leg_part_names_each_line_s_tests_its_suite_s_own_and_the_code_outside(
    tmp_path: Path,
) -> None:
    from livery.extensions.coverage._format import decode, unpack_set

    sources = tmp_path / SOURCES
    sources.mkdir(parents=True)
    (sources / "mod.py").write_text("a = 1\nif a:\n    b = 2\nc = 3\n")
    module = f"{SOURCES}/mod.py"
    tests = "packages/core/tests/test_mod.py::"
    _leg_data(
        tmp_path,
        "host.1",
        {
            # Import time: the module's first lines, outside every test.
            "": {module: [(-1, 1), (1, 2)]},
            f"{tests}test_b": {module: [(2, 3), (3, 4), (4, -1)]},
            f"{tests}test_a": {module: [(2, 3), (3, 4)]},
            # The workspace's own test reached the module too: none of
            # the member's tests, so none of its part's.
            "tests/test_all.py::test_it": {module: [(2, 4), (4, -1)]},
        },
    )
    (part,) = _coverage.Arcs().leg(tmp_path, [CORE])
    # The contexts that reached the part's lines, sorted.
    assert part["contexts"] == ["", f"{tests}test_a", f"{tests}test_b"]
    reached = part["files"][module]["reached"]
    assert {
        line: unpack_set(reached["sets"][held]) for line, held in reached["lines"]
    } == {1: {0}, 2: {0, 1, 2}, 3: {1, 2}, 4: {1, 2}}
    read = decode(part, source="the leg's part")
    assert {
        line: sorted(read.contexts[index] for index in held)
        for line, held in read.files[module].reached.items()
    } == {
        1: [""],
        2: ["", f"{tests}test_a", f"{tests}test_b"],
        3: [f"{tests}test_a", f"{tests}test_b"],
        4: [f"{tests}test_a", f"{tests}test_b"],
    }
    # A leg whose run named no test names no context.
    (tmp_path / ".coverage.host.1").unlink()
    _leg_data(tmp_path, "host.2", {"": {module: [(-1, 1), (1, 2)]}})
    (bare,) = _coverage.Arcs().leg(tmp_path, [CORE])
    assert bare["contexts"] == [] and "reached" not in bare["files"][module]


def test_both_digests_are_the_blob_git_stores_for_the_text_with_crlf_or_lf() -> None:
    from livery.extensions.coverage._format import digest

    lf = b"if a:\n    b = 2\n"
    crlf = b"if a:\r\n    b = 2\r\n"
    # `git hash-object` gives this id for the LF text, and git stores a
    # CRLF checkout of it, Git for Windows', as the same blob.
    stored = "14acfc4e683f05ebcbae533dc3e273acef7e267c"
    assert _coverage._digest(lf) == _coverage._digest(crlf) == stored
    assert digest(lf) == digest(crlf) == stored


def test_a_crlf_source_and_a_backslashed_name_reduce_as_their_lf_and_slashed_forms(
    tmp_path: Path,
) -> None:
    module = f"{SOURCES}/mod.py"
    reduced: dict[str, dict[str, Any]] = {}
    for ending in ("\n", "\r\n"):
        root = tmp_path / ("crlf" if ending == "\r\n" else "lf")
        (root / SOURCES).mkdir(parents=True)
        (root / module).write_bytes(
            "a = 1\nif a:\n    b = 2\n".replace("\n", ending).encode()
        )
        # A Windows leg's data names the file with backslashes.
        arcs = [(-1, 1), (1, 2), (2, 3), (3, -1)]
        _leg_data(root, "host.1", {"": {module.replace("/", "\\"): arcs}})
        (part,) = _coverage.Arcs().leg(root, [CORE])
        reduced[ending] = part
    assert reduced["\r\n"] == reduced["\n"]
    assert list(reduced["\n"]["files"]) == [module]
    lf_text = b"a = 1\nif a:\n    b = 2\n"
    assert reduced["\n"]["files"][module]["digest"] == _coverage._digest(lf_text)


def test_a_context_set_packs_sorted_deltas_and_a_wide_gap_takes_two_bytes() -> None:
    from livery.extensions.coverage._format import parse_ranges, unpack_set

    assert _coverage._packed({0, 1, 300}) == "AACqAg=="
    assert unpack_set(_coverage._packed({0, 1, 300})) == {0, 1, 300}
    assert _coverage._ranges([5, 1, 2, 3, 9, 10]) == "1-3,5,9-10"
    assert parse_ranges(_coverage._ranges([5, 1, 2, 3, 9, 10])) == {1, 2, 3, 5, 9, 10}


def test_while_coverage_is_listed_pytest_reduces_the_arcs(tmp_path: Path) -> None:
    from livery.workshop import Query, answer

    root = _workspace(tmp_path / "ws")
    found = answer(root, Query[dict[str, Any]]("coverage.measurers"))
    assert list(found) == ["arcs"]
    assert isinstance(found["arcs"], _coverage.Arcs)
    # Coverage unlisted, the [for.coverage] answer is never asked.
    _workspace(root, '["docs", "pytest"]')
    assert answer(root, Query[dict[str, Any]]("coverage.measurers")) == {}


# --- what the pages show ---------------------------------------------------


def _reference(root: Path) -> tuple[dict[str, dict[int, Any]], dict[str, Any]]:
    """coverage.py's own reading of *root*'s data: its lines, and its JSON report.

    Each line as coverage.py's HTML report draws it, by file; the JSON
    report is format 3, with its functions and classes. coverage.py
    names a file with the platform's separator, and both are keyed by
    the name with forward slashes, as a part names it.
    """
    from coverage import Coverage
    from coverage.html import HtmlDataGeneration
    from coverage.report_core import get_analysis_to_report

    cov = Coverage(
        data_file=str(root / ".coverage"), config_file=str(root / ".coveragerc")
    )
    cov.set_option("html:show_contexts", True)
    cov.load()
    generation = HtmlDataGeneration(cov)
    lines: dict[str, dict[int, Any]] = {}
    for reporter, analysis in get_analysis_to_report(cov, None):
        drawn = generation.data_for_file(reporter, analysis)
        lines[_posix(reporter.relative_filename())] = {
            line.number: line for line in drawn.lines
        }
    report = root / "coverage.json"
    cov.json_report(outfile=str(report))
    data: dict[str, Any] = json.loads(report.read_text())
    data["files"] = _keyed_posix(data["files"])
    assert data["meta"]["format"] == 3
    return lines, data


def _numbers(summary: dict[str, Any]) -> tuple[str, ...]:
    """A JSON report's summary as an index row shows it."""
    return (
        f"{summary['percent_statements_covered_display']}%",
        str(summary["num_statements"]),
        str(summary["missing_lines"]),
        str(summary["excluded_lines"]),
        f"{summary['percent_branches_covered_display']}%",
        str(summary["num_branches"]),
        str(summary["num_partial_branches"]),
        f"{summary['percent_covered_display']}%",
    )


def _shown(page: Page, row: dict[str, str]) -> tuple[str, ...]:
    """An index row's numbers, in the order the summary gives them."""
    cells = page.cells[page.rows.index(row)]
    coverage = [text for heading, text in cells if heading == "coverage"]
    return (
        coverage[0],
        row["statements"],
        row["missing"],
        row["excluded"],
        coverage[1],
        row["branches"],
        row["partial"],
        coverage[2],
    )


def test_the_pages_show_what_coverage_py_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.docs import _site as _docs

    root = _workspace(tmp_path / "ws")
    measure(root)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.chdir(root)
    reference, report = _reference(root)
    assert _docs.render_coverage_pages(root) == ["core"]
    tree = root / "packages" / "core" / "htmlcov"
    index = read_page(tree / "index.html")
    rows = {row["File"]: row for row in index.rows if row["File"] != "Total"}
    assert sorted(rows) == sorted(report["files"]) == [MEASURED_PY, SHAPES_PY]
    for name, measured in report["files"].items():
        # Each file's numbers, the three percentages among them.
        assert _shown(index, rows[name]) == _numbers(measured["summary"]), name
        # Each function and class, narrowed as coverage.py narrows them,
        # and the lines outside every one.
        for noun, plural in (("function", "functions"), ("class", "classes")):
            regions = read_page(tree / f"{noun}_index.html")
            shown = {row[noun]: row for row in regions.rows if row.get("File") == name}
            expected = {
                region or f"(no {noun})": data
                for region, data in measured[plural].items()
            }
            assert sorted(shown) == sorted(expected), (name, noun)
            for region, data in expected.items():
                row = shown[region]
                assert _shown(regions, row) == _numbers(data["summary"]), region
                anchor = "" if region.startswith("(no ") else f"t{data['start_line']}"
                assert row["href"].partition("#")[2] == anchor, region
        # Each line: its category, the short and long annotation of each
        # jump that never ran, and the contexts that reached it.
        page = read_page(tree / rows[name]["href"])
        assert sorted(page.lines) == sorted(reference[name]), name
        for number, line in reference[name].items():
            drawn = page.lines[number]
            assert drawn.category == line.category, (name, number)
            assert drawn.jumps == [
                f"{number} ↛ {short}" for short in line.short_annotations
            ], (name, number)
            assert drawn.whys == line.long_annotations, (name, number)
            assert drawn.contexts(page.contexts) == line.contexts, (name, number)
            assert drawn.label == (line.contexts_label if line.contexts else "")
    # The fixture draws every category, every kind of annotation, and
    # both kinds of context label.
    every = [
        line
        for name in rows
        for line in read_page(tree / rows[name]["href"]).lines.values()
    ]
    categories = {line.category for line in every}
    assert categories == {"", *CATEGORIES}
    jumps = " ".join(jump for line in every for jump in line.jumps)
    assert all(text in jumps for text in ("↛ exit", "↛ anywhere", "↛ 13"))
    labels = {line.label for line in every}
    assert {"(empty)", "1 ctx", "2 ctx"} <= labels


def test_pages_drawn_from_stored_parts_equal_the_pages_drawn_at_render_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One tree, one measurement: drawn on a machine from the data at
    # render time, and in the merge point's gate job from the part a leg
    # made of the same data and main's record stored. Only the lines that
    # say where the measurement comes from differ: the record's source on
    # the index, and the suite a stored part names on each file's page.
    # The tests that reached each line are the member's own, so the leg
    # keeps them, and both pages list them.
    from livery.extensions.coverage import _format, _pages
    from livery.extensions.coverage._record import Unit
    from livery.extensions.docs import _site as _docs

    root = _workspace(tmp_path / "ws")
    tests = "packages/core/tests/test_shapes.py::"
    measure(root, contexts=(f"{tests}test_a", f"{tests}test_b"))
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.chdir(root)
    assert _docs.render_coverage_pages(root) == ["core"]
    tree = root / "packages" / "core" / "htmlcov"
    at_render = {path.relative_to(tree): path.read_bytes() for path in tree.rglob("*")}
    (root / ".coverage").rename(root / ".coverage.host.1")
    (made,) = _coverage.Arcs().leg(root, [CORE])
    (root / ".coverage.host.1").unlink()
    # The record keeps the part in its stored form, its facts apart.
    kept, facts = _format.stored(_format.decode(made, source="the leg's part"))
    held = {
        "check-a": {
            "packages/core": Unit(
                "packages/core", "k" * 64, "7", "a" * 40, (kept,), facts
            )
        }
    }
    monkeypatch.setattr(_pages, "_stored", lambda root: (held, []))
    assert _docs.render_coverage_pages(root) == ["core"]
    stored = {path.relative_to(tree): path.read_bytes() for path in tree.rglob("*")}
    assert sorted(stored) == sorted(at_render)
    local = b"Measured on this machine, from the data its last gate run left."
    record = (
        b"Measured on check-a, from main&#x27;s coverage record: 1 unit(s) from"
        b" 1 run(s)."
    )
    suite = b" while the suites of packages/core ran."
    for path in stored:
        drawn = stored[path].replace(record, local).replace(suite, b".")
        assert drawn == at_render[path], path
    # Each line's tests are drawn: shapes.py ran under the member's two,
    # and measured.py outside every test.
    index = read_page(tree / "index.html")
    shapes = read_page(tree / _page_of(index, SHAPES_PY))
    assert {line.label for line in shapes.lines.values()} == {"", "1 ctx", "2 ctx"}
    assert shapes.contexts == [f"{tests}test_a", f"{tests}test_b"]
    measured = read_page(tree / _page_of(index, MEASURED_PY))
    assert {line.label for line in measured.lines.values()} == {"", "(empty)"}


def _page_of(index: Page, name: str) -> str:
    """The page an index links for the file *name*."""
    return next(row["href"] for row in index.rows if row["File"] == name)


# --- this repository's own sources -------------------------------------------


@pytest.mark.only_at("nightly")
def test_the_reduction_counts_this_repository_s_workshop_sources_as_coverage_py_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import Coverage, CoverageData

    from livery.extensions.coverage._format import decode, numbers

    root = Path(__file__).resolve().parents[4]
    sources = root / "packages" / "workshop" / "src"
    # Every module of the workshop's wheel imported under the branch
    # meter, in a fresh interpreter with none of the outer run's meter
    # variables, its data kept apart from the run's own.
    config = tmp_path / "coveragerc"
    # The core the check legs measure with.
    config.write_text("[run]\nbranch = true\nrelative_files = true\ncore = ctrace\n")
    script = tmp_path / "imports.py"
    script.write_text(
        "import importlib, pkgutil\n"
        "import livery.extensions, livery.workshop\n"
        "for root in (livery.workshop, livery.extensions):\n"
        "    for found in pkgutil.walk_packages(root.__path__, root.__name__ + '.'):\n"
        "        try:\n"
        "            importlib.import_module(found.name)\n"
        "        except Exception:\n"
        "            pass\n"
    )
    unmetered = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--rcfile={config}",
            f"--data-file={tmp_path / '.coverage'}",
            f"--source={sources}",
            str(script),
        ],
        cwd=root,
        capture_output=True,
        check=True,
        env=unmetered,
        timeout=600,
    )
    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.read()
    unit = {
        "unit": "packages/workshop",
        "tests": "packages/workshop/tests/",
        "within": ["packages/workshop/"],
    }
    name, files, _reached = _coverage._unit_arcs(data, root, unit, 0)
    assert len(files) > 100
    reduced = CoverageData(no_disk=True)
    reduced.add_arcs(files)
    raw = _coverage._Reducer(root).part(name, reduced, {each: each for each in files})
    part = decode(raw, source="the workshop's sources")
    mine = {each: numbers(entry, part.measurer) for each, entry in part.files.items()}
    monkeypatch.chdir(root)
    cov = Coverage(
        data_file=str(tmp_path / ".coverage"), config_file=str(root / ".coveragerc")
    )
    cov.load()
    cov.json_report(outfile=str(tmp_path / "report.json"))
    report = json.loads((tmp_path / "report.json").read_text())
    theirs = {
        each: (
            summary["num_statements"],
            summary["covered_lines"],
            summary["excluded_lines"],
            summary["num_branches"],
            summary["covered_branches"],
            summary["num_partial_branches"],
        )
        for each, measured in _keyed_posix(report["files"]).items()
        for summary in (measured["summary"],)
        if each in mine
    }
    assert {
        each: (
            counted.statements,
            counted.executed,
            counted.excluded,
            counted.branches,
            counted.taken,
            counted.partial,
        )
        for each, counted in mine.items()
    } == theirs


def _posix(name: str) -> str:
    """A file's name as coverage.py gives it, with forward slashes."""
    return name.replace("\\", "/")


def _keyed_posix(files: dict[str, Any]) -> dict[str, Any]:
    """A JSON report's files keyed by their names with forward slashes."""
    return {_posix(name): held for name, held in files.items()}
