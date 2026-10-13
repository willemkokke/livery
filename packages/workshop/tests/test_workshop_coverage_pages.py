"""The coverage pages: fed parts in the line format, drawn by the coverage extension."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage import _pages, _parts, _render
from livery.extensions.coverage._format import (
    Branch,
    Entry,
    Measurer,
    Merged,
    Part,
    Range,
    Region,
)
from livery.extensions.coverage._record import Unit
from livery.footman import Failed
from workshop_coverage_meter import COVERAGE_PY, install_meter, meter, stored_unit
from workshop_coverage_pages import read_page

_FAILURES = (SystemExit, Failed)

#: A module with one branch never taken from a line that ran.
SOURCE = "a = 1\nif a:\n    b = 2\nc = (\n    3\n)\n"
MODULE = "packages/core/src/acme/core/mod.py"
OTHER = "packages/core/src/acme/core/other.py"


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listed: str = '["docs", "acme.meter", "coverage"]',
) -> Path:
    """A workspace whose member ``core`` holds ``mod.py`` and an htmlcov report."""
    install_meter(tmp_path, monkeypatch)
    root = tmp_path / "ws"
    core = root / "packages" / "core"
    (core / "src" / "acme" / "core").mkdir(parents=True)
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")
    (core / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-core"\n\n[docs]\n'
        'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    (core / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    (root / MODULE).write_text(SOURCE)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    # A leg's job runner names its point and leg; this is no gate job.
    monkeypatch.delenv("WORKSHOP_POINT", raising=False)
    monkeypatch.delenv("WORKSHOP_LEG", raising=False)
    return root


def _entry(root: Path, name: str = MODULE, *, digest: str | None = None) -> Entry:
    """coverage.py's reading of ``mod.py``: line 2's jump to 4 never ran."""
    content = (root / name).read_bytes() if (root / name).is_file() else b""
    return Entry(
        digest=fmt.digest(content) if digest is None else digest,
        statements=frozenset({1, 2, 3, 4}),
        counts={1: 1, 2: 1, 3: 1, 4: 1},
        spans=((4, 6),),
        branches=(
            Branch(2, 3, 0, 1),
            Branch(
                2,
                4,
                1,
                0,
                "line 2 didn't jump to line 4 because the condition on line 2 was"
                " always true",
            ),
        ),
    )


def _meter(root: Path, **held: Any) -> None:
    """Leave *held* where the fake measurer reads it."""
    meter(root, **held)


def _left(root: Path, *parts: Part) -> None:
    _meter(root, left=[fmt.encode(part) for part in parts])


def _render_pages(root: Path) -> list[str]:
    from livery.extensions.docs import _site as _docs

    return _docs.render_coverage_pages(root)


# --- refusals ----------------------------------------------------------------


def test_a_measured_file_the_checkout_lacks_is_named_on_a_desk_and_red_in_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    gone = "packages/core/src/acme/core/gone.py"
    _left(
        root,
        Part(".", COVERAGE_PY, {MODULE: _entry(root), gone: Entry(digest="")}),
    )
    # Inside CI the data is the record of the tree being built: red.
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: object())
    with pytest.raises(
        _FAILURES,
        match=f"the measured data names {gone}, which the checkout does not have",
    ):
        _render_pages(root)
    # On a desk the data outlived a move: the index names the file, and
    # the rest is drawn.
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    assert _render_pages(root) == ["core"]
    out = capsys.readouterr().out
    assert (
        f"  coverage: the local data names {gone}, which the checkout no longer"
        " has; its package's index states the absence until"
    ) in out
    index = read_page(root / "packages/core/htmlcov/index.html")
    assert index.absent == [gone]
    assert [row["File"] for row in index.rows] == [MODULE, "Total"]


def test_a_file_at_another_digest_says_it_was_measured_at_other_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _left(root, Part(".", COVERAGE_PY, {MODULE: _entry(root, digest="0" * 40)}))
    assert _render_pages(root) == ["core"]
    tree = root / "packages/core/htmlcov"
    index = read_page(tree / "index.html")
    # The numbers are the measurement's; the lines are not drawn over
    # content that is not what was measured.
    (row, _total) = index.rows
    assert (row["statements"], row["branches"], row["partial"]) == ("4", "2", "1")
    page = read_page(tree / row["href"])
    assert page.lines == {}
    checkout = fmt.digest(SOURCE.encode())
    assert page.notice == (
        "This file was measured at other content: the measurement is of blob"
        f" 000000000000, and the checkout holds blob {checkout[:12]}. Its lines"
        " are not drawn; its numbers are the measurement's."
    )


def test_a_file_changed_since_its_measurement_is_drawn_without_its_tests_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    # The measurement names the tests that reached each line of content
    # the checkout no longer holds.
    measured = replace(
        _entry(root, digest="0" * 40),
        reached={1: frozenset({0}), 2: frozenset({1}), 3: frozenset({1})},
    )
    _left(root, Part(".", COVERAGE_PY, {MODULE: measured}, ("", "t1")))
    assert _render_pages(root) == ["core"]
    tree = root / "packages/core/htmlcov"
    (row, _total) = read_page(tree / "index.html").rows
    page = read_page(tree / row["href"])
    # Drawn over other text, the lines and their tests would name the
    # wrong lines: neither is drawn, and the page says why.
    assert page.lines == {} and page.contexts == []
    checkout = fmt.digest(SOURCE.encode())
    assert page.notice == (
        "This file was measured at other content: the measurement is of blob"
        f" 000000000000, and the checkout holds blob {checkout[:12]}. Its lines"
        " and the tests that reached them are not drawn; its numbers are the"
        " measurement's."
    )


def test_a_part_of_another_format_version_is_named_and_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    earlier = {**fmt.encode(Part(".", COVERAGE_PY, {})), "format": 1}
    _meter(
        root, left=[earlier, fmt.encode(Part(".", COVERAGE_PY, {MODULE: _entry(root)}))]
    )
    assert _render_pages(root) == ["core"]
    assert (
        "  coverage: the arcs measurer's data on this machine: a part of format 1;"
        " this workshop reads format 2; the pages draw without it\n"
    ) in capsys.readouterr().out
    index = read_page(root / "packages/core/htmlcov/index.html")
    assert [row["File"] for row in index.rows] == [MODULE, "Total"]


def test_a_source_written_with_crlf_draws_the_lines_and_the_digest_of_its_lf_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    lf = SOURCE.encode()
    crlf = SOURCE.replace("\n", "\r\n").encode()
    assert fmt.digest(crlf) == fmt.digest(lf)
    # The part was measured at the text git stores: the LF form, which
    # a Windows checkout holds with CRLF.
    _left(root, Part(".", COVERAGE_PY, {MODULE: _entry(root, digest=fmt.digest(lf))}))
    tree = root / "packages/core/htmlcov"
    drawn: dict[bytes, list[tuple[int, str, str, list[str]]]] = {}
    for content in (lf, crlf):
        (root / MODULE).write_bytes(content)
        assert _render_pages(root) == ["core"]
        href = read_page(tree / "index.html").rows[0]["href"]
        assert b"\r" not in (tree / href).read_bytes()
        page = read_page(tree / href)
        assert page.notice == ""
        drawn[content] = [
            (number, line.category, line.source, line.jumps)
            for number, line in page.lines.items()
        ]
    assert drawn[crlf] == drawn[lf]
    assert [number for number, *_ in drawn[lf]] == [1, 2, 3, 4, 5, 6]


def test_a_reduction_that_refuses_stops_the_pages_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    _meter(root, refuse=f"{MODULE}: the part reads 1 of 2 statements")
    with pytest.raises(
        _FAILURES,
        match=(
            "coverage: the arcs measurer's data on this machine refuses:"
            f" {MODULE}: the part reads 1 of 2 statements"
        ),
    ):
        _render_pages(root)


def test_a_part_off_the_format_stops_the_pages_naming_its_source_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    broken = fmt.encode(Part(".", COVERAGE_PY, {MODULE: _entry(root)}))
    broken["files"][MODULE]["hits"] = "1-9"
    _meter(root, left=[broken])
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: the arcs measurer's data on this machine:"
            r' files\["packages/core/src/acme/core/mod\.py"\]\.hits: names lines'
            r" that are no statements: 5-9"
        ),
    ):
        _render_pages(root)


def test_one_measurer_is_reduced_by_one_extension() -> None:
    first, second = object(), object()
    assert _parts.measurers_by_name({"pytest": {"arcs": first}}, None) == {
        "arcs": first
    }
    with pytest.raises(
        ValueError,
        match=re.escape("pytest and acme.meter both reduce the arcs measurer"),
    ):
        _parts.measurers_by_name(
            {"pytest": {"arcs": first}, "acme.meter": {"arcs": second}}, None
        )


def test_a_package_no_measurement_reaches_states_the_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    stale = root / "packages/core/htmlcov"
    stale.mkdir()
    (stale / "index.html").write_text("an older report")
    # No data at all, then data of another package alone.
    assert _render_pages(root) == []
    _left(root, Part(".", COVERAGE_PY, {"packages/other/x.py": _entry(root)}))
    assert _render_pages(root) == []
    out = capsys.readouterr().out
    assert (
        out.count("  coverage: core: no measured data; its page states the absence")
        == 2
    )
    assert not stale.exists()


# --- the floor each index states ---------------------------------------------


#: What the index says of a fixed floor, after the floor itself.
_JUDGED = "; the gate job judges the union of every leg against it."


def _core(root: Path, coverage: str = "") -> Path:
    """Rewrite core's contract with *coverage* as its ``[coverage]`` body; its path.

    The contracts this process read are dropped too: a rewrite of the
    same size within the clock's tick keeps the file's stamp.
    """
    from livery.workshop._contract import forget_contracts

    core = root / "packages" / "core"
    table = f"\n[coverage]\n{coverage}" if coverage else ""
    (core / "workshop.toml").write_text(
        f'kind = "python"\nname = "acme-core"\n{table}\n[docs]\n'
        'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    forget_contracts()
    return core


def test_each_index_states_the_package_s_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.coverage._floors import floor_status
    from livery.extensions.coverage._marks import Mark
    from livery.workshop._packages import discover_packages

    root = _workspace(tmp_path, monkeypatch)
    _left(root, Part(".", COVERAGE_PY, {MODULE: _entry(root)}))

    def stated(coverage: str = "", marks: dict[str, Mark] | None = None) -> str:
        _core(root, coverage)
        (core,) = discover_packages(root)
        return floor_status(core, marks)

    # The packages nothing judges first: on purpose, and for want of tests.
    assert stated("required = false\n") == (
        "Not judged: its [coverage] table sets required = false."
    )
    assert stated() == "Not judged: the package has no tests and names no floor."
    (root / "packages/core/tests").mkdir()
    assert stated() == f"Floor: 100.0% by default, with an epsilon of 0.5%{_JUDGED}"
    assert stated("floor = 90\nepsilon = 1\n") == (
        f"Floor: 90.0%, with an epsilon of 1.0%{_JUDGED}"
    )
    # Under auto-ratchet: the mode alone where the marks were not read,
    # then the mark the store holds, or none yet.
    ratchet = 'floor = "auto-ratchet"\n'
    assert stated(ratchet) == (
        "Floor: the mark on the state store, with an epsilon of 0.5%."
    )
    assert stated(ratchet, {}) == (
        "Floor: the mark on the state store, which the next CI run records."
    )
    mark = Mark("packages/core", 88.25, "ratchet", "run 7", "", "")
    assert stated(ratchet, {"packages/core": mark}) == (
        "Floor: the mark on the state store, 88.2% (ratchet by run 7), with an"
        " epsilon of 0.5%."
    )
    # The index draws the line under its heading.
    _core(root, "floor = 90\n")
    assert _render_pages(root) == ["core"]
    index = read_page(root / "packages/core/htmlcov/index.html")
    assert index.floor == f"Floor: 90.0%, with an epsilon of 0.5%{_JUDGED}"


def test_the_marks_are_read_for_a_ratcheted_package_in_the_merge_gate_job_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.coverage import _marks
    from livery.workshop._packages import discover_packages

    root = _workspace(tmp_path, monkeypatch)
    (root / "packages/core/tests").mkdir()
    read: list[Path] = []

    def _read(at: Path) -> tuple[dict[str, _marks.Mark] | None, str]:
        read.append(at)
        return {}, ""

    monkeypatch.setattr(_marks, "marks", _read)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: object())
    monkeypatch.setenv("WORKSHOP_POINT", "merge")
    monkeypatch.setenv("WORKSHOP_LEG", "gate")
    # A fixed floor reads nothing, nor does a ratchet outside that job.
    _core(root, "floor = 90\n")
    assert _pages._marks_read(root, discover_packages(root)) is None
    _core(root, 'floor = "auto-ratchet"\n')
    monkeypatch.setenv("WORKSHOP_LEG", "docs")
    assert _pages._marks_read(root, discover_packages(root)) is None
    assert read == []
    monkeypatch.setenv("WORKSHOP_LEG", "gate")
    assert _pages._marks_read(root, discover_packages(root)) == {}
    assert read == [root]


# --- main's record, in the merge point's gate job ------------------------------


def _unit(path: str, *parts: Part, run: str = "7") -> Unit:
    """A unit of main's record: its parts as the leg made them, in their stored form."""
    return stored_unit(path, [fmt.encode(part) for part in parts], run=run)


#: A native measurer as the cmake extension's parts name it.
_GCOV = Measurer("gcov", counts=fmt.COUNTS)


def test_main_s_record_is_drawn_from_the_parts_the_legs_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The stored parts need no listed measurer: the legs made them.
    root = _workspace(tmp_path, monkeypatch, '["docs", "coverage"]')
    (root / OTHER).write_text("x = 1\ny = 2\nz = 3\n")
    native = "packages/core/src/native.cpp"
    (root / native).write_text("int f() {\n  return 1;\n}\n")
    other = Entry(
        digest=fmt.digest((root / OTHER).read_bytes()),
        statements=frozenset({1, 2, 3}),
        counts={1: 1, 3: 1},
    )
    lines = Entry(
        digest=fmt.digest((root / native).read_bytes()),
        statements=frozenset({1, 2}),
        counts={1: 3},
    )
    beyond = Entry(digest="", statements=frozenset({1}), counts={1: 1})
    held = {
        "check-a": {
            "packages/core": _unit(
                "packages/core",
                Part("packages/core", COVERAGE_PY, {MODULE: _entry(root)}),
            ),
            "tests": _unit(
                "tests",
                Part(
                    "tests",
                    COVERAGE_PY,
                    {OTHER: other, "packages/other/x.py": beyond},
                ),
                run="6",
            ),
            "packages/native": _unit(
                "packages/native", Part("packages/native", _GCOV, {native: lines})
            ),
        }
    }
    monkeypatch.setattr(
        _pages, "_stored", lambda root: (held, ["main/check-b: nothing recorded"])
    )
    assert _render_pages(root) == ["core"]
    assert "  coverage: main/check-b: nothing recorded\n" in capsys.readouterr().out
    tree = root / "packages/core/htmlcov"
    index = read_page(tree / "index.html")
    assert [row["File"] for row in index.rows] == [MODULE, OTHER, native, "Total"]
    assert index.notes[0] == (
        "Measured on check-a, from main's coverage record: 3 unit(s) from 2 run(s)."
    )
    # The counting measurer's counts are drawn beside each line that ran.
    native_row = next(row for row in index.rows if row["File"] == native)
    page = read_page(tree / native_row["href"])
    assert [(n, line.category, line.runs) for n, line in page.lines.items()] == [
        (1, "run", "3\N{MULTIPLICATION SIGN}"),
        (2, "mis", ""),
        (3, "", ""),
    ]
    assert page.notes == ["Measured by gcov while the suites of packages/native ran."]
    # A stored part off the format stops the pages, naming the row and
    # the key.
    broken = _unit(
        "packages/core", Part("packages/core", COVERAGE_PY, {MODULE: _entry(root)})
    )
    broken.parts[0]["files"][MODULE]["hits"] = "1-9"
    held["check-a"]["packages/core"] = broken
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: main's record on check-a, packages/core, part 0:"
            r' files\["packages/core/src/acme/core/mod\.py"\]\.hits: names lines'
            r" that are no statements: 5-9"
        ),
    ):
        _render_pages(root)


def test_each_line_s_tests_are_drawn_from_the_parts_main_s_record_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, '["docs", "coverage"]')
    tests = "packages/core/tests/test_mod.py::"
    first, second, gone = (f"{tests}test_{name}" for name in ("a", "b", "gone"))
    # Each leg's row numbers its contexts its own way, and one's names a
    # test that is gone, which reached no line.
    one = replace(
        _entry(root),
        reached={
            1: frozenset({0}),
            2: frozenset({1}),
            3: frozenset({1}),
            4: frozenset({0}),
        },
    )
    two = replace(
        _entry(root),
        reached={1: frozenset({0}), 2: frozenset({1}), 3: frozenset({1, 2})},
    )
    held = {
        "check-a": {
            "packages/core": _unit(
                "packages/core",
                Part("packages/core", COVERAGE_PY, {MODULE: one}, ("", first, gone)),
            )
        },
        "check-b": {
            "packages/core": _unit(
                "packages/core",
                Part("packages/core", COVERAGE_PY, {MODULE: two}, ("", second, first)),
            )
        },
    }
    monkeypatch.setattr(_pages, "_stored", lambda root: (held, []))
    assert _render_pages(root) == ["core"]
    tree = root / "packages/core/htmlcov"
    (row, _total) = read_page(tree / "index.html").rows
    page = read_page(tree / row["href"])
    # Each line lists the tests of every leg that reached it, by name.
    assert {number: line.label for number, line in page.lines.items()} == {
        1: "(empty)",
        2: "2 ctx",
        3: "2 ctx",
        4: "(empty)",
        5: "",
        6: "",
    }
    assert page.lines[2].contexts(page.contexts) == [first, second]
    assert page.lines[3].contexts(page.contexts) == [first, second]
    assert page.contexts == ["(empty)", first, second]
    # A stored row whose set names a context its part does not number
    # stops the pages, naming the row, the file and the set.
    broken = held["check-a"]["packages/core"]
    broken.parts[0]["files"][MODULE]["reached"]["sets"][0] = fmt.pack_set({7})
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: main's record on check-a, packages/core, part 0:"
            r' files\["packages/core/src/acme/core/mod\.py"\]\.reached\.sets\[0\]:'
            r" names context 7, and the part names 3"
        ),
    ):
        _render_pages(root)


def test_a_count_only_line_is_drawn_without_claiming_which_branch_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A measurer that counts a line's branches without naming them, as a
    # Cobertura report's condition coverage does: each leg ran one of
    # line 2's two branches, and nothing says whether they were the same
    # one, so the union keeps the most any leg took, and the page names
    # how many ran and no branch.
    root = _workspace(tmp_path, monkeypatch, '["docs", "coverage"]')
    native = "packages/core/src/native.cpp"
    (root / native).write_text("int f(int a) {\n  return a ? 1 : 2;\n}\n")
    engine = Measurer("dotnet-coverage", counts=fmt.COUNTS)

    def _leg(runs: int) -> Unit:
        entry = Entry(
            digest=fmt.digest((root / native).read_bytes()),
            statements=frozenset({1, 2}),
            counts={1: runs, 2: runs},
            branch_counts=((2, 2, 1),),
        )
        return _unit(
            "packages/native", Part("packages/native", engine, {native: entry})
        )

    held = {
        "check-a": {"packages/native": _leg(1)},
        "check-b": {"packages/native": _leg(2)},
    }
    monkeypatch.setattr(_pages, "_stored", lambda root: (held, []))
    assert _render_pages(root) == ["core"]
    tree = root / "packages/core/htmlcov"
    index = read_page(tree / "index.html")
    (row, _total) = index.rows
    assert (row["branches"], row["partial"]) == ("2", "1")
    line = read_page(tree / row["href"]).lines[2]
    assert line.category == "par"
    assert line.runs == "3\N{MULTIPLICATION SIGN}"
    assert line.jumps == ["1 of 2"]
    assert line.whys == ["1 of line 2's 2 branches ran"]
    assert "↛" not in line.shown and "branch " not in line.shown


#: A one-line ``if``/``else`` whose ``if`` arm never ran, as llvm-cov
#: reads it: the function's body, the condition, and each arm.
_CLAMP = "int clamp(int v, int h) {\n  if (v > h) { return h; } else { return v; }\n}\n"
_LLVM = Measurer(
    "llvm-cov",
    counts=fmt.COUNTS,
    branches=fmt.INDEXES,
    regions=("function",),
    columns=True,
)


def _clamp(root: Path, *ranges: Range) -> dict[str, dict[str, Unit]]:
    """Main's record holding llvm-cov's reading of ``native.cpp`` with *ranges*."""
    native = "packages/core/src/native.cpp"
    (root / native).write_text(_CLAMP)
    entry = Entry(
        digest=fmt.digest(_CLAMP.encode()),
        statements=frozenset({1, 2, 3}),
        counts={1: 1, 2: 1, 3: 1},
        ranges=ranges,
    )
    part = Part("packages/native", _LLVM, {native: entry})
    return {"check-a": {"packages/native": _unit("packages/native", part)}}


def test_a_line_with_an_unrun_range_is_drawn_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, '["docs", "coverage"]')
    # A range outside the text it was measured at stops the pages first,
    # naming the file and the range.
    held = _clamp(root, Range(4, 1, 4, 2, 0))
    monkeypatch.setattr(_pages, "_stored", lambda root: (held, []))
    with pytest.raises(
        _FAILURES,
        match=(
            r"coverage: packages/core/src/native\.cpp, as llvm-cov measured it:"
            r" ranges\[0\]: 4:1-4:2 lies outside the file: the file has 3 lines"
        ),
    ):
        _render_pages(root)
    # Every line ran, and line 2's `if` arm never did: the line is drawn
    # partial, and the arm's code alone is marked.
    held = _clamp(
        root,
        Range(1, 25, 3, 2, 1),
        Range(2, 7, 2, 12, 1),
        Range(2, 14, 2, 27, 0),
        Range(2, 33, 2, 46, 1),
    )
    assert _render_pages(root) == ["core"]
    tree = root / "packages/core/htmlcov"
    (row, _total) = read_page(tree / "index.html").rows
    page = read_page(tree / row["href"])
    assert [(number, line.category) for number, line in page.lines.items()] == [
        (1, "run"),
        (2, "par"),
        (3, "run"),
    ]
    markup = (tree / row["href"]).read_text(encoding="utf-8")
    marked = re.findall(r'<span class="(?:[^"]* )?nr">([^<]*)</span>', markup)
    assert "".join(marked) == "{ return h; }"


def test_the_store_is_read_only_in_the_merge_point_s_gate_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    calls: list[list[str]] = []

    def _rows(root: Path, labels: list[str]) -> tuple[dict[str, Any], list[str]]:
        calls.append(labels)
        return {"check-a": {}}, ["main/check-b: nothing recorded"]

    monkeypatch.setattr(_pages, "stored_rows", _rows)
    monkeypatch.setattr("livery.workshop._points.check_legs", lambda root: ["check-a"])
    monkeypatch.setattr(
        "livery.git.remote_snapshot", lambda *a, **k: contextlib.nullcontext()
    )
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.setenv("WORKSHOP_POINT", "merge")
    monkeypatch.setenv("WORKSHOP_LEG", "gate")
    assert _pages._stored(tmp_path) is None
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: object())
    for point, leg in (("gate", "docs"), ("merge", "docs")):
        monkeypatch.setenv("WORKSHOP_POINT", point)
        monkeypatch.setenv("WORKSHOP_LEG", leg)
        assert _pages._stored(tmp_path) is None
    monkeypatch.setenv("WORKSHOP_POINT", "merge")
    monkeypatch.setenv("WORKSHOP_LEG", "gate")
    assert _pages._stored(tmp_path) == (
        {"check-a": {}},
        ["main/check-b: nothing recorded"],
    )
    assert calls == [["check-a"]]


# --- what the pages draw -----------------------------------------------------


def _drawn(
    name: str, entry: Entry, measurer: Measurer, text: str | None
) -> _render.Drawn:
    return _render.Drawn(
        name, Merged(measurer, entry, ("packages/core",)), text, entry.digest
    )


def test_a_counting_measurer_s_branches_and_counts_are_drawn_by_number(
    tmp_path: Path,
) -> None:
    gcov = Measurer(
        "gcov", "14.2", "", fmt.COUNTS, fmt.INDEXES, ("function",), (fmt.LINES,)
    )
    entry = Entry(
        digest="",
        statements=frozenset({1, 2, 3, 4}),
        counts={1: 12, 2: 5, 4: 1},
        branches=(
            Branch(2, None, 0, 5),
            Branch(2, None, 1, 0),
            Branch(2, None, 2, 0, fmt.THROW),
        ),
        branch_counts=((4, 2, 1),),
        regions=(Region("function", "main", 1, frozenset({2, 3, 4}), 12),),
    )
    text = "int main() {\n  if (x) y();\n  z();\n  return a ? b : c;\n}\n"
    totals = _render.render(
        tmp_path / "tree",
        _render.Report(
            "packages/n", (_drawn("packages/n/main.cpp", entry, gcov, text),)
        ),
    )
    # Its figure is its lines alone.
    assert (totals.covered, totals.coverable) == (3, 4)
    index = read_page(tmp_path / "tree/index.html")
    assert index.title == "Coverage for packages/n: 75%"
    page = read_page(tmp_path / "tree" / index.rows[0]["href"])
    assert page.lines[1].runs == "12\N{MULTIPLICATION SIGN}"
    assert page.lines[2].jumps == ["branch 1", "branch 2 (throw)"]
    assert page.lines[2].whys == [
        "branch 1 of line 2 never ran",
        "branch 2 (throw) of line 2 never ran",
    ]
    assert page.lines[4].jumps == ["1 of 2"]
    assert page.lines[4].whys == ["1 of line 4's 2 branches ran"]
    assert page.lines[3].category == "mis"
    # A measurer of functions alone gets a function index and no class one.
    assert (tmp_path / "tree/function_index.html").is_file()
    assert not (tmp_path / "tree/class_index.html").exists()


def test_contexts_are_listed_per_line_and_the_empty_one_alone_is_named(
    tmp_path: Path,
) -> None:
    entry = Entry(
        digest="",
        statements=frozenset({1, 2, 3}),
        counts={1: 1, 2: 1, 3: 1},
        branches=(Branch(3, -1, 0, 1), Branch(3, 4, 1, 0)),
        reached={1: frozenset({0}), 2: frozenset({0, 1, 2}), 3: frozenset({2})},
    )
    text = "a = 1\nb = 2\nif b:\n    c = 3\n"
    _render.render(
        tmp_path / "tree",
        _render.Report(
            "packages/x",
            (_drawn("packages/x/m.py", entry, COVERAGE_PY, text),),
            contexts=("", "t10", "t9"),
        ),
    )
    index = read_page(tmp_path / "tree/index.html")
    page = read_page(tmp_path / "tree" / index.rows[0]["href"])
    assert page.lines[1].label == "(empty)"
    assert page.lines[2].label == "3 ctx"
    # Sorted as people read them: the empty one first, t9 before t10.
    assert page.lines[2].contexts(page.contexts) == ["(empty)", "t9", "t10"]
    assert page.lines[3].contexts(page.contexts) == ["t9"]
    # A line whose only jump never ran falls back to coverage.py's wording.
    assert page.lines[3].whys == ["line 3 didn't jump to line 4"]


def test_a_line_whose_branches_all_never_ran_jumps_anywhere(tmp_path: Path) -> None:
    entry = Entry(
        digest="",
        statements=frozenset({1, 2}),
        counts={1: 1},
        branches=(Branch(1, -1, 0, 0, ""), Branch(1, 2, 1, 0, "")),
    )
    _render.render(
        tmp_path / "tree",
        _render.Report(
            "packages/x",
            (_drawn("packages/x/m.py", entry, COVERAGE_PY, "if f():\n    g()\n"),),
        ),
    )
    index = read_page(tmp_path / "tree/index.html")
    page = read_page(tmp_path / "tree" / index.rows[0]["href"])
    assert page.lines[1].jumps == ["1 ↛ anywhere"]
    assert page.lines[1].whys == [
        "line 1 didn't jump anywhere: it always raised an exception."
    ]


def test_the_tree_is_written_afresh_with_its_shared_assets(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "left-behind.html").write_text("old")
    lines = Measurer("lines", counts=fmt.COUNTS)
    files = tuple(
        _drawn(
            f"packages/x/{name}",
            Entry(digest="", statements=frozenset({1}), counts={1: 1}),
            lines,
            "x\n",
        )
        for name in ("b.txt", "a.weird-ext")
    )
    _render.render(tree, _render.Report("packages/x", files, notes=("A note.",)))
    assert not (tree / "left-behind.html").exists()
    assert (tree / "coverage.css").read_bytes() == (
        _render.ASSETS / "coverage.css"
    ).read_bytes()
    assert (tree / "coverage.js").read_bytes() == (
        _render.ASSETS / "coverage.js"
    ).read_bytes()
    index = read_page(tree / "index.html")
    assert index.notes == ["A note."]
    # Files in order, each page linked to the one before and after it,
    # the first and last to the index; a lines report has no branch
    # columns and no region indexes.
    assert [row["File"] for row in index.rows] == [
        "packages/x/a.weird-ext",
        "packages/x/b.txt",
        "Total",
    ]
    assert "branches" not in index.rows[0]
    assert index.body["data-prev"] == index.rows[1]["href"]
    assert index.body["data-next"] == index.rows[0]["href"]
    assert sorted(path.name for path in tree.glob("*_index.html")) == []
    first = (tree / index.rows[0]["href"]).read_text()
    assert 'href="index.html" rel="prev"' in first
    assert f'href="{index.rows[1]["href"]}" rel="next"' in first
    # A file no lexer claims is drawn as its plain text.
    assert read_page(tree / index.rows[0]["href"]).lines[1].source == "x"
    assert _render.page_name("packages/x/a.py") != _render.page_name("packages/y/a.py")
