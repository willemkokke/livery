"""The line format: what a part holds, how it reads back, and how parts merge."""

from __future__ import annotations

import json
from typing import Any

import pytest

from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage._format import (
    Branch,
    Entry,
    Measurer,
    Numbers,
    OtherVersion,
    Part,
    PartError,
    Range,
    Region,
)

COVERAGE_PY = Measurer(
    "coverage.py",
    "7.16.2",
    "CPython 3.14",
    fmt.PRESENCE,
    fmt.TARGETS,
    ("function", "class"),
    (fmt.LINES, fmt.BRANCHES),
)
GCOV = Measurer(
    "gcov", "14.2", "", fmt.COUNTS, fmt.INDEXES, ("function",), (fmt.LINES,)
)
LLVM = Measurer(
    "llvm-cov", "", "", fmt.COUNTS, fmt.INDEXES, ("function",), (fmt.LINES,), True
)
LINES_ONLY = Measurer("lines", counts=fmt.COUNTS)


def _raw(**files: Any) -> dict[str, Any]:
    """A part of coverage.py's as JSON holds it, with *files*."""
    return {
        "format": 2,
        "unit": "packages/x",
        "measurer": {
            "name": "coverage.py",
            "version": "7.16.2",
            "runtime": "CPython 3.14",
            "counts": "presence",
            "branches": "targets",
            "regions": ["function", "class"],
            "judges": ["lines", "branches"],
            "columns": False,
        },
        "contexts": ["", "t1"],
        "files": files,
    }


def _entry(**keys: Any) -> dict[str, Any]:
    return {"digest": "d" * 40, "statements": "1-4", **keys}


# --- refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "the part: is [], and a table is wanted"),
        (
            {**_raw(), "extra": 1},
            "the part: holds extra, which format 2 has no key for; its keys are"
            " format, unit, measurer, contexts, files",
        ),
        ({**_raw(), "unit": ""}, "unit: is '', and text that is not empty is wanted"),
        ({**_raw(), "measurer": None}, "measurer: is None, and a table is wanted"),
        (
            {**_raw(), "measurer": {**_raw()["measurer"], "counts": "often"}},
            "measurer.counts: is 'often', and one of presence, counts is wanted",
        ),
        (
            {**_raw(), "measurer": {**_raw()["measurer"], "judges": ["branches"]}},
            "measurer.judges: leaves out lines, which every figure judges",
        ),
        (
            {**_raw(), "measurer": {**_raw()["measurer"], "judges": ["rows"]}},
            "measurer.judges[0]: is 'rows', and one of lines, branches is wanted",
        ),
        ({**_raw(), "contexts": "t1"}, "contexts: is 't1', and a list is wanted"),
        (
            _raw(**{"a\\b.py": _entry()}),
            'files["a\\\\b.py"]: is no path from the workspace root with forward'
            " slashes",
        ),
        (_raw(**{"/a.py": _entry()}), 'files["/a.py"]: is no path from the workspace'),
        (_raw(**{"C:/a.py": _entry()}), 'files["C:/a.py"]: is no path from the'),
        (_raw(**{"a/../b.py": _entry()}), 'files["a/../b.py"]: is no path from the'),
        (_raw(**{"a//b.py": _entry()}), 'files["a//b.py"]: is no path from the'),
        ({**_raw(), "contexts": [3]}, "contexts[0]: is 3, and text is wanted"),
        (_raw(**{"a.py": {"statements": "1"}}), 'files["a.py"].digest: is None'),
        (
            _raw(**{"a.py": _entry(statements="1-x")}),
            "files[\"a.py\"].statements: '1-x' is not a line or a range of lines",
        ),
        (
            _raw(**{"a.py": _entry(statements="3-1")}),
            "'3-1' is not a range of lines from 1 up",
        ),
        (
            _raw(**{"a.py": _entry(statements=4)}),
            'files["a.py"].statements: is 4, and text is wanted',
        ),
        (
            _raw(**{"a.py": _entry(hits="1,9")}),
            'files["a.py"].hits: names lines that are no statements: 9',
        ),
        (
            _raw(**{"a.py": _entry(counts=[[1, 2]])}),
            'files["a.py"]: holds counts, which format 2 has no key for',
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, None, 0, 1]])}),
            'files["a.py"].branches[0][1]: is None, and a line or a negative exit'
            " is wanted",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, True, 0, 1]])}),
            "branches[0][1]: is True, and a line or a negative exit is wanted",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, 3, 0]])}),
            "branches[0]: is no [from, to, index, taken, why?] branch",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, 3, 0, 1], [2, 4, 0, 0]])}),
            "branches[1][2]: repeats branch 0 of line 2",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, 3, 0, -1]])}),
            "branches[0][3]: is -1, and a whole number from 0 is wanted",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[False, 3, 0, 1]])}),
            "branches[0][0]: is False, and a whole number from 1 is wanted",
        ),
        (
            _raw(**{"a.py": _entry(branches="2")}),
            "branches: is '2', and a list is wanted",
        ),
        (
            _raw(**{"a.py": _entry(branches=[[2, 3, 0, 0, 7]])}),
            "branches[0][4]: is 7, and text is wanted",
        ),
        (
            _raw(**{"a.py": _entry(spans=[[3, 1]])}),
            "spans[0][1]: is 1, and a whole number from 3 is wanted",
        ),
        (_raw(**{"a.py": _entry(spans=[[3]])}), "spans[0]: is no [first, last] pair"),
        (
            _raw(**{"a.py": _entry(**{"branch-counts": [[2, 2, 3]]})}),
            "branch-counts[0][2]: is 3, more than the line's 2",
        ),
        (
            _raw(**{"a.py": _entry(**{"branch-counts": [[2, 2]]})}),
            "branch-counts[0]: is no [line, branches, taken] count",
        ),
        (
            _raw(**{"a.py": _entry(regions=[["module", "m", 1, "1-4"]])}),
            "regions[0][0]: is 'module', and coverage.py names function, class",
        ),
        (
            _raw(**{"a.py": _entry(regions=[["function", "f", 1]])}),
            "regions[0]: is no [kind, name, start, lines, calls?] region",
        ),
        (
            _raw(**{"a.py": _entry(regions=[["function", "", 1, "2"]])}),
            "regions[0][1]: is '', and text that is not empty is wanted",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["!!"], "lines": []})}),
            "reached.sets[0]: '!!' is not base64",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["gA=="], "lines": []})}),
            "reached.sets[0]: 'gA==' ends inside a number",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["BQ=="], "lines": []})}),
            "reached.sets[0]: names context 5, and the part names 2",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["AA=="], "lines": [[1]]})}),
            "reached.lines[0]: is no [line, set] pair",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["AA=="], "lines": [[1, 3]]})}),
            "reached.lines[0][1]: is 3, and the part has 1 sets",
        ),
        (
            {**_raw(), "measurer": {**_raw()["measurer"], "columns": "yes"}},
            "measurer.columns: is 'yes', and true or false is wanted",
        ),
        (
            _raw(**{"a.py": _entry(reached={"lines": [], "every": 1})}),
            "reached: holds every, which format 2 has no key for",
        ),
    ],
)
def test_a_part_off_the_format_refuses_naming_its_source_and_the_key(
    raw: Any, message: str
) -> None:
    with pytest.raises(PartError) as refused:
        fmt.decode(raw, source="the probe's part")
    assert str(refused.value).startswith("the probe's part: ")
    assert message in str(refused.value)


def test_a_part_of_format_version_1_refuses_and_names_its_version() -> None:
    with pytest.raises(OtherVersion) as refused:
        fmt.decode({**_raw(), "format": 1}, source="the probe's part")
    assert refused.value.version == 1
    assert str(refused.value) == (
        "the probe's part: a part of format 1; this workshop reads format 2"
    )
    # Its stored form too, and a part with no version at all.
    with pytest.raises(OtherVersion, match="a part of format 1; this workshop"):
        fmt.from_stored({**_raw(), "format": 1}, {}, source="the probe's part")
    with pytest.raises(OtherVersion, match="a part of format None"):
        fmt.decode({"unit": "x"}, source="the probe's part")


def test_a_measurer_without_columns_leaves_ranges_empty() -> None:
    # A reading of ranges by a measurer that reads no columns is off the
    # format; one that reads them takes them.
    raw = _raw(**{"a.py": _entry(ranges=[[1, 1, 1, 4, 0]])})
    with pytest.raises(
        PartError,
        match=r'files\["a\.py"\]\.ranges: names ranges, and coverage\.py reads no'
        " columns",
    ):
        fmt.decode(raw, source="p")
    columns = {**raw, "measurer": {**raw["measurer"], "columns": True}}
    read = fmt.decode(columns, source="p").files["a.py"]
    assert read.ranges == (Range(1, 1, 1, 4, 0),)
    # coverage.py's measurer and gcov's say so, and their parts hold none.
    assert not COVERAGE_PY.columns and not GCOV.columns and LLVM.columns
    written = fmt.encode(_rich())
    assert written["measurer"]["columns"] is False
    assert all("ranges" not in entry for entry in written["files"].values())


def test_a_range_outside_its_file_refuses() -> None:
    # Off the format whatever the text: a range that ends before it
    # starts, one that names column 0, and one named twice.
    for ranges, message in (
        ([[2, 5, 1, 9, 0]], r"ranges\[0\]\[2\]: is 1, and a whole number from 2"),
        ([[2, 5, 2, 4, 0]], r"ranges\[0\]\[3\]: is 4, and a whole number from 5"),
        ([[2, 0, 2, 4, 0]], r"ranges\[0\]\[1\]: is 0, and a whole number from 1"),
        ([[2, 1, 2, 4, 0], [2, 1, 2, 4, 1]], r"ranges\[1\]: repeats the range 2:1-2:4"),
        ([[2, 1, 2, 4]], r"ranges\[0\]: is no \[start line, start column, end line"),
    ):
        raw = _raw(**{"a.py": _entry(ranges=ranges)})
        raw["measurer"]["columns"] = True
        with pytest.raises(PartError, match=message):
            fmt.decode(raw, source="p")
    # Against the text it was measured at: a line it lacks, and a column
    # past a line's end. A column counts bytes, and the end column is
    # the one after the last, so a range may end one past the line.
    text = "int a;\nint é;\n"
    fits = Entry(digest="", ranges=(Range(1, 1, 1, 7, 1), Range(2, 5, 2, 8, 0)))
    fmt.check_ranges(fits, text, source="a.c")
    for item, message in (
        (
            Range(2, 1, 3, 2, 0),
            "a.c: ranges[0]: 2:1-3:2 lies outside the file: the file has 2 lines",
        ),
        (
            Range(1, 8, 1, 8, 0),
            "a.c: ranges[0]: 1:8-1:8 lies outside the file: line 1 has 6 columns",
        ),
        (
            Range(2, 1, 2, 9, 0),
            "a.c: ranges[0]: 2:1-2:9 lies outside the file: line 2 has 7 columns",
        ),
    ):
        with pytest.raises(PartError) as refused:
            fmt.check_ranges(Entry(digest="", ranges=(item,)), text, source="a.c")
        assert str(refused.value) == message


def test_a_measurer_s_branches_match_how_it_names_them() -> None:
    lines = {
        **_raw(**{"a.py": {"digest": "", "counts": [[1, 3]], "statements": "1"}}),
        "measurer": {"name": "lines", "counts": "counts", "branches": "none"},
    }
    assert fmt.decode(lines, source="p").files["a.py"].counts == {1: 3}
    lines["files"]["a.py"]["branches"] = [[1, None, 0, 1]]
    with pytest.raises(
        PartError, match="branches: names branches, and lines reads none"
    ):
        fmt.decode(lines, source="p")
    gcov = {
        **_raw(**{"a.cpp": {"digest": "", "statements": "1-2"}}),
        "measurer": {"name": "gcov", "counts": "counts", "branches": "indexes"},
    }
    gcov["files"]["a.cpp"]["branches"] = [[1, 2, 0, 1]]
    with pytest.raises(PartError, match=r"branches\[0\]\[1\]: is 2, and gcov numbers"):
        fmt.decode(gcov, source="p")
    gcov["files"]["a.cpp"]["counts"] = [[1, 2, 3]]
    with pytest.raises(PartError, match=r"counts\[0\]: is no \[line, runs\] pair"):
        fmt.decode(gcov, source="p")


# --- what a part holds ---------------------------------------------------


def test_range_strings_and_packed_sets_hold_their_lines() -> None:
    assert fmt.ranges([]) == ""
    assert fmt.ranges([5, 1, 2, 3, 3, 9]) == "1-3,5,9"
    assert fmt.parse_ranges("") == frozenset()
    assert fmt.parse_ranges("1-3,5,9") == {1, 2, 3, 5, 9}
    with pytest.raises(ValueError, match="'' is not a line or a range of lines"):
        fmt.parse_ranges("1,,2")
    for held in (set(), {0}, {3, 4, 5}, {0, 127, 128, 20000}):
        assert fmt.unpack_set(fmt.pack_set(held)) == held
    assert fmt.pack_set({0, 1, 300}) == "AACqAg=="
    # The git blob id of the text, as `git hash-object` gives it, and the
    # one git stores for a CRLF checkout of it.
    assert fmt.digest(b"a = 1\r\n") == fmt.digest(b"a = 1\n")
    assert fmt.digest(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert fmt.digest(b"a = 1\n") == "1337a530cbc1bd7d20aee2d80f1f174a9182417d"


def _rich() -> Part:
    """A part of every key the format has, coverage.py's and gcov's."""
    python = Entry(
        digest="a" * 40,
        statements=frozenset({1, 2, 3, 5, 6}),
        counts={1: 1, 2: 1, 3: 1, 6: 1},
        excluded=frozenset({8, 9}),
        spans=((6, 7),),
        branches=(
            Branch(2, 3, 0, 1),
            Branch(2, 5, 1, 0, "line 2 didn't jump to line 5"),
            Branch(6, -1, 0, 1, fmt.WAIVED),
            Branch(6, 8, 1, 1),
        ),
        regions=(Region("function", "f", 1, frozenset({2, 3})),),
        reached={1: frozenset({0}), 2: frozenset({0, 1}), 3: frozenset({0, 1})},
    )
    return Part("packages/x", COVERAGE_PY, {"packages/x/a.py": python}, ("", "t1"))


def test_a_part_reads_back_what_it_wrote() -> None:
    part = _rich()
    written = fmt.encode(part)
    assert written["files"]["packages/x/a.py"]["hits"] == "1-3,6"
    assert written["files"]["packages/x/a.py"]["reached"] == {
        "sets": [fmt.pack_set({0}), fmt.pack_set({0, 1})],
        "lines": [[1, 0], [2, 1], [3, 1]],
    }
    assert fmt.decode(json.loads(json.dumps(written)), source="p") == part
    native = Part(
        "packages/n",
        GCOV,
        {
            "packages/n/a.cpp": Entry(
                digest="b" * 40,
                statements=frozenset({1, 2, 4}),
                counts={1: 7, 2: 3},
                branches=(Branch(2, None, 0, 3), Branch(2, None, 1, 0, fmt.THROW)),
                branch_counts=((4, 2, 1),),
                regions=(Region("function", "main", 1, frozenset({1, 2, 3, 4}), 7),),
            )
        },
    )
    written = fmt.encode(native)
    assert written["files"]["packages/n/a.cpp"]["counts"] == [[1, 7], [2, 3]]
    assert "hits" not in written["files"]["packages/n/a.cpp"]
    assert fmt.decode(json.loads(json.dumps(written)), source="p") == native


def _native() -> Part:
    """A part of llvm-cov's, with every key a native measurer fills."""
    return Part(
        "packages/n",
        LLVM,
        {
            "packages/n/a.cpp": Entry(
                digest="b" * 40,
                statements=frozenset({1, 2, 4}),
                counts={1: 7, 2: 3},
                excluded=frozenset({5}),
                branches=(Branch(2, None, 0, 3), Branch(2, None, 1, 0, fmt.THROW)),
                branch_counts=((4, 2, 1),),
                regions=(
                    Region("function", "main", 1, frozenset({1, 2, 3, 4}), 7),
                    Region("function", "unused", 6, frozenset({6}), 0),
                ),
                ranges=(Range(1, 12, 4, 2, 7), Range(2, 3, 2, 9, 0)),
            )
        },
    )


def test_a_stored_part_naming_facts_it_lacks_refuses_naming_the_content() -> None:
    kept, facts = fmt.stored(_native())
    (name,) = facts
    with pytest.raises(PartError) as refused:
        fmt.from_stored(kept, {}, source="the row")
    assert str(refused.value) == (
        f'the row: files["packages/n/a.cpp"].facts: names the fixed facts {name} of'
        " content bbbbbbbbbbbb, and no facts of that name are held"
    )
    assert fmt.facts_names(kept) == {name: "b" * 40}
    # A place past the facts' items, and a key the stored form lacks.
    entry = kept["files"]["packages/n/a.cpp"]
    for extra, message in (
        ({"taken": [[9, 1]]}, "taken: names place 9, and its fixed facts hold 2"),
        ({"range-counts": [[3, 1]]}, "range-counts: names place 3, and its fixed"),
        ({"calls": [[1]]}, r"calls\[0\]: is no \[place, number\] pair"),
        ({"statements": "1"}, "holds statements, which format 2 has no key for"),
    ):
        broken = {**kept, "files": {"packages/n/a.cpp": {**entry, **extra}}}
        with pytest.raises(PartError, match=message):
            fmt.from_stored(broken, facts, source="the row")


def test_a_part_reads_back_from_its_stored_form_its_facts_kept_apart() -> None:
    part = _rich()
    kept, facts = fmt.stored(part)
    (name,) = facts
    assert kept["files"]["packages/x/a.py"] == {
        "digest": "a" * 40,
        "facts": name,
        "hits": "1-3,6",
        "taken": "1,3-4",
        "reached": fmt.encode(part)["files"]["packages/x/a.py"]["reached"],
    }
    assert facts[name] == {
        "statements": "1-3,5-6",
        "excluded": "8-9",
        "spans": [[6, 7]],
        "branches": [
            [2, 3, 0],
            [2, 5, 1, "line 2 didn't jump to line 5"],
            [6, -1, 0, fmt.WAIVED],
            [6, 8, 1],
        ],
        "regions": [["function", "f", 1, "2-3"]],
    }
    # The facts' name is the git blob id of their text, so a record that
    # keeps them as a file of that text names the file as git does.
    assert name == fmt.facts_name(facts[name])
    assert name == fmt.digest(fmt.facts_text(facts[name]).encode("utf-8"))
    assert fmt.from_stored(json.loads(json.dumps(kept)), facts, source="p") == part
    native = _native()
    kept, facts = fmt.stored(native)
    (name,) = facts
    assert kept["files"]["packages/n/a.cpp"] == {
        "digest": "b" * 40,
        "facts": name,
        "counts": [[1, 7], [2, 3]],
        "taken": [[1, 3]],
        "counted": [[1, 1]],
        "calls": [[1, 7], [2, 0]],
        "range-counts": [[1, 7]],
    }
    assert facts[name]["ranges"] == [[1, 12, 4, 2], [2, 3, 2, 9]]
    assert fmt.from_stored(json.loads(json.dumps(kept)), facts, source="p") == native
    # A file the measurer could not read has no facts to name.
    unread = Part("packages/n", LLVM, {"packages/n/gone.cpp": Entry(digest="")})
    kept, facts = fmt.stored(unread)
    assert kept["files"] == {"packages/n/gone.cpp": {"digest": ""}} and facts == {}
    assert fmt.from_stored(kept, facts, source="p") == unread


def test_a_file_counts_as_coverage_py_counts_it() -> None:
    entry = _rich().files["packages/x/a.py"]
    counted = fmt.numbers(entry, COVERAGE_PY)
    # 5 statements, 4 run; 4 branches, 3 taken with the waived one, the
    # untaken one leaving a line that ran: partial.
    assert counted == Numbers(1, 5, 4, 2, 4, 3, 1, 4 + 3, 5 + 4)
    assert (counted.missing, counted.percent) == (1, 100.0 * 7 / 9)
    assert (counted.statements_percent, counted.branches_percent) == (80.0, 75.0)
    # A branch never taken from a line that never ran is missing, not
    # partial; a measurer that judges lines alone leaves branches out of
    # its figure, and a count of a line's branches counts each.
    native = Entry(
        digest="",
        statements=frozenset({1, 2, 3}),
        counts={1: 2},
        branches=(Branch(2, None, 0, 0),),
        branch_counts=((1, 2, 1), (3, 4, 0)),
    )
    counted = fmt.numbers(native, GCOV)
    assert counted == Numbers(1, 3, 1, 0, 1 + 6, 1, 1, 1, 3)
    assert native.partial == {1}
    assert Numbers().percent == 100.0
    assert counted + Numbers(files=1, statements=1) == Numbers(
        2, 4, 1, 0, 7, 1, 1, 1, 3
    )


def test_a_figure_prints_as_coverage_py_prints_it() -> None:
    assert [fmt.shown(value) for value in (0.0, 0.2, 50.5, 51.5, 99.7, 100.0)] == [
        "0",
        "1",
        "50",
        "52",
        "99",
        "100",
    ]
    assert fmt.shown(200 / 3, 1) == "66.7"
    assert fmt.shown(99.96, 1) == "99.9"


def test_a_region_counts_its_body_lines_alone() -> None:
    entry = Entry(
        digest="",
        statements=frozenset({1, 2, 3, 4, 6, 7}),
        counts={1: 1, 2: 1, 3: 1, 6: 1},
        excluded=frozenset({9}),
        branches=(
            Branch(3, 4, 0, 0, "never"),
            Branch(3, 6, 1, 1),
            Branch(7, -6, 0, 0, "x"),
        ),
        regions=(
            Region("class", "K", 1, frozenset({3, 4})),
            Region("function", "K.m", 2, frozenset({3, 4})),
            Region("function", "f", 5, frozenset({6, 7})),
        ),
        reached={3: frozenset({0}), 6: frozenset({0})},
    )
    rows = fmt.region_rows(entry, "function")
    assert [region.name if region else None for region, _ in rows] == ["K.m", "f", None]
    method, function, outside = (narrowed for _, narrowed in rows)
    assert (method.statements, method.excluded, len(method.branches)) == (
        frozenset({3, 4}),
        frozenset(),
        2,
    )
    assert method.reached == {3: {0}}
    assert (function.statements, function.missing) == (
        frozenset({6, 7}),
        frozenset({7}),
    )
    # The lines of no function: the `class` and `def` lines, and what
    # the source excludes.
    assert (outside.statements, outside.excluded, outside.branches) == (
        frozenset({1, 2}),
        frozenset({9}),
        (),
    )
    assert [
        region.name if region else None for region, _ in fmt.region_rows(entry, "class")
    ] == ["K", None]


def test_a_measurer_is_named_with_its_version_and_runtime() -> None:
    assert COVERAGE_PY.label == "coverage.py 7.16.2 on CPython 3.14"
    assert GCOV.label == "gcov 14.2"
    assert Measurer("probe").label == "probe"
    assert Measurer("probe", "1", "CPython 3.14.6").identity == (
        "probe",
        "1",
        "CPython 3.14",
    )
    assert Measurer("probe").identity == ("probe", "", "")


# --- merging -----------------------------------------------------------------


def _reading(
    unit: str,
    *,
    digest: str = "a" * 40,
    measurer: Measurer = COVERAGE_PY,
    contexts: tuple[str, ...] = (),
    **keys: Any,
) -> Part:
    entry = Entry(digest=digest, statements=frozenset({1, 2, 3, 4}), **keys)
    return Part(unit, measurer, {"packages/x/a.py": entry}, contexts)


def test_readings_at_two_digests_merge_the_current_and_name_the_other() -> None:
    current = _reading("packages/x", counts={1: 1})
    older = _reading("tests", digest="b" * 40, counts={2: 1})
    combined = fmt.merge([older, current], {"packages/x/a.py": "a" * 40})
    merged = combined.files["packages/x/a.py"]
    assert (merged.units, merged.entry.executed) == (("packages/x",), frozenset({1}))
    assert combined.left_out == (
        "packages/x/a.py: left out the reading of tests by coverage.py 7.16.2 on"
        " CPython 3.14 at content bbbbbbbbbbbb; the union keeps the one of"
        " packages/x by coverage.py 7.16.2 on CPython 3.14 at content"
        " aaaaaaaaaaaa",
    )
    # Without the checkout's digest, the content most readings share
    # wins, and an unread content is named so.
    lost = _reading("tests", digest="", counts={2: 1})
    combined = fmt.merge([lost, current, _reading("other", counts={3: 1})])
    assert combined.files["packages/x/a.py"].entry.executed == {1, 3}
    assert combined.left_out[0].startswith(
        "packages/x/a.py: left out the reading of tests by coverage.py 7.16.2 on"
        " CPython 3.14 at unread content;"
    )


def test_readings_of_two_runtimes_never_merge() -> None:
    newer = _reading("packages/x", counts={1: 1})
    older = _reading(
        "packages/x",
        measurer=Measurer("coverage.py", "7.16.2", "CPython 3.11", judges=(fmt.LINES,)),
        counts={2: 1},
    )
    combined = fmt.merge([newer, older, _reading("tests", counts={4: 1})])
    assert combined.files["packages/x/a.py"].entry.executed == {1, 4}
    assert "by coverage.py 7.16.2 on CPython 3.11" in combined.left_out[0]


def test_a_merge_adds_runs_and_keeps_each_branch_s_sentence() -> None:
    one = _reading(
        "packages/x",
        counts={1: 1, 2: 1},
        excluded=frozenset({8}),
        spans=((3, 4),),
        branches=(Branch(2, 3, 0, 0, "never jumped"), Branch(2, 4, 1, 0, "nor here")),
        branch_counts=((1, 2, 1),),
        regions=(Region("function", "f", 1, frozenset({2}), None),),
        reached={1: frozenset({0}), 2: frozenset({1})},
        contexts=("", "t1"),
    )
    two = _reading(
        "tests",
        counts={1: 2, 3: 1},
        excluded=frozenset({9}),
        spans=((3, 4),),
        branches=(Branch(2, 3, 0, 1), Branch(2, 4, 1, 0, "nor here")),
        branch_counts=((1, 2, 2), (3, 2, 0)),
        regions=(Region("function", "f", 1, frozenset({2, 3}), 4),),
        reached={1: frozenset({0}), 2: frozenset({0})},
        contexts=("t2", "t1"),
    )
    combined = fmt.merge([one, two])
    assert combined.contexts == ("", "t1", "t2")
    merged = combined.files["packages/x/a.py"]
    entry = merged.entry
    assert merged.units == ("packages/x", "tests")
    assert dict(entry.counts) == {1: 3, 2: 1, 3: 1}
    assert entry.excluded == {8, 9}
    assert entry.spans == ((3, 4),)
    # A branch's sentence depends on the source alone, so a taken branch
    # keeps it, and a page shows it only while the branch never ran.
    assert entry.branches == (
        Branch(2, 3, 0, 1, "never jumped"),
        Branch(2, 4, 1, 0, "nor here"),
    )
    assert entry.branch_counts == ((1, 2, 2), (3, 2, 0))
    assert entry.regions == (Region("function", "f", 1, frozenset({2, 3}), 4),)
    # Each context by its name: t1 was index 1 in one part and in the
    # other, t2 index 0 in the second part.
    assert dict(entry.reached) == {1: {0, 2}, 2: {1, 2}}
    assert combined.left_out == ()
    # Regions read without calls stay without.
    alone = fmt.merge(
        [one, _reading("tests", regions=one.files["packages/x/a.py"].regions)]
    )
    assert alone.files["packages/x/a.py"].entry.regions[0].calls is None
    # A range is one by where it is, its counts added.
    stretch = Range(2, 1, 2, 5, 1)
    ranged = fmt.merge(
        [
            _reading("packages/x", measurer=LLVM, ranges=(stretch,)),
            _reading(
                "tests",
                measurer=LLVM,
                ranges=(Range(2, 1, 2, 5, 2), Range(3, 1, 3, 4, 0)),
            ),
        ]
    )
    assert ranged.files["packages/x/a.py"].entry.ranges == (
        Range(2, 1, 2, 5, 3),
        Range(3, 1, 3, 4, 0),
    )
