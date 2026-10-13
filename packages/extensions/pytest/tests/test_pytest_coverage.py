"""The reduction of coverage.py's arcs to the line format, and the pages from it."""

from __future__ import annotations

import json
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


def _row(unit: str, files: dict[str, list[tuple[int, int]]]) -> dict[str, Any]:
    return {
        "unit": unit,
        "files": {name: [list(arc) for arc in arcs] for name, arcs in files.items()},
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


def test_a_row_that_holds_no_unit_s_arcs_refuses_naming_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^row 1 is no unit's arcs"):
        _coverage.Arcs().rows(tmp_path, [_row("packages/core", {}), {"files": {}}])


def test_a_file_coverage_py_cannot_parse_refuses_naming_it(tmp_path: Path) -> None:
    bad = tmp_path / SOURCES / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("def broken(:\n")
    with pytest.raises(
        ValueError, match=rf"^{SOURCES}/bad\.py: coverage\.py cannot read it"
    ):
        _coverage.Arcs().rows(
            tmp_path, [_row("packages/core", {f"{SOURCES}/bad.py": [(-1, 1)]})]
        )


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


def test_each_row_reduces_to_its_own_part_and_each_source_is_read_once(
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
    one, two = _coverage.Arcs().rows(
        tmp_path,
        [
            _row("packages/core", {module: [(-1, 1), (1, 2), (2, 3), (3, -1)]}),
            # Lines that are no statements count none, and an empty
            # module holds no statement at all.
            _row("tests", {module: [(-1, 99), (99, -1)], init: [(-1, 1)]}),
        ],
    )
    assert [one["unit"], two["unit"]] == ["packages/core", "tests"]
    assert one["files"][module]["hits"] == "1-3"
    assert one["files"][module]["branches"] == [
        [
            2,
            -1,
            0,
            0,
            "line 2 didn't exit the module because the condition on line 2 was"
            " always true",
        ],
        [2, 3, 1, 1],
    ]
    assert "hits" not in two["files"][module]
    assert two["files"][init] == {"digest": _coverage._digest(b"")}
    assert sorted(built) == sorted(str(tmp_path / name) for name in (module, init))
    assert one["measurer"]["name"] == "coverage.py"
    assert one["measurer"]["judges"] == ["lines", "branches"]


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
        # A Windows leg's row names the file with backslashes.
        arcs = [(-1, 1), (1, 2), (2, 3), (3, -1)]
        (part,) = _coverage.Arcs().rows(
            root, [_row("packages/core", {module.replace("/", "\\"): arcs})]
        )
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


# --- this repository's own suite ------------------------------------------


@pytest.mark.only_at("nightly")
def test_the_reduction_counts_this_repository_s_workshop_suite_as_coverage_py_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import Coverage, CoverageData

    from livery.extensions.coverage._format import decode, numbers
    from livery.extensions.coverage._pages import stored_rows
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import state_store
    from livery.workshop._points import check_legs

    root = Path(__file__).resolve().parents[4]
    leg = check_legs(root)[0]
    with remote_snapshot(state_store(root), fetch=("coverage/main/",)):
        held, misses = stored_rows(root, [leg])
    assert leg in held, misses
    unit = held[leg]["packages/workshop"]
    files = {name: arcs for name, arcs in unit.files.items() if (root / name).is_file()}
    assert len(files) > 200
    (raw,) = _coverage.Arcs().rows(root, [_row(unit.path, files)])
    part = decode(raw, source=f"the workshop suite's row on {leg}")
    mine = {name: numbers(entry, part.measurer) for name, entry in part.files.items()}
    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_arcs({name: set(arcs) for name, arcs in files.items()})
    data.write()
    monkeypatch.chdir(root)
    cov = Coverage(
        data_file=str(tmp_path / ".coverage"), config_file=str(root / ".coveragerc")
    )
    cov.load()
    cov.json_report(outfile=str(tmp_path / "report.json"))
    report = json.loads((tmp_path / "report.json").read_text())
    theirs = {
        name: (
            summary["num_statements"],
            summary["covered_lines"],
            summary["excluded_lines"],
            summary["num_branches"],
            summary["covered_branches"],
            summary["num_partial_branches"],
        )
        for name, measured in _keyed_posix(report["files"]).items()
        for summary in (measured["summary"],)
    }
    assert {
        name: (
            counted.statements,
            counted.executed,
            counted.excluded,
            counted.branches,
            counted.taken,
            counted.partial,
        )
        for name, counted in mine.items()
    } == theirs


def _posix(name: str) -> str:
    """A file's name as coverage.py gives it, with forward slashes."""
    return name.replace("\\", "/")


def _keyed_posix(files: dict[str, Any]) -> dict[str, Any]:
    """A JSON report's files keyed by their names with forward slashes."""
    return {_posix(name): held for name, held in files.items()}
