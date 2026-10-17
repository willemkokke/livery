"""The line format: what a part holds, how it reads back, and how parts merge."""

from __future__ import annotations

import json
import random
import re
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
        "format": 3,
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


#: How a set's refusal ends when a number in it is not digits alone.
NOT_DIGITS = "which is no whole number in decimal; a set is digits and commas alone"


@pytest.mark.parametrize(
    ("spelled", "problem"),
    [
        # The older version's spelling: LEB128 bytes in base64.
        ("AACqAg==", f"'AACqAg==' holds 'AACqAg==', {NOT_DIGITS}"),
        ("AAE=", f"'AAE=' holds 'AAE=', {NOT_DIGITS}"),
        # A letter among the numbers.
        ("0,1a,2", f"'0,1a,2' holds '1a', {NOT_DIGITS}"),
        ("AKID", f"'AKID' holds 'AKID', {NOT_DIGITS}"),
        # A negative number takes the set back down: 0, then 4, then 3.
        (
            "0,3,-2",
            "'0,3,-2' steps from index 4 to index 3; each number counts the"
            " indexes a set skips, 0 or more, so its indexes rise",
        ),
        (
            "0,-1",
            "'0,-1' steps from index 0 to index 0; each number counts the"
            " indexes a set skips, 0 or more, so its indexes rise",
        ),
        (
            "-3",
            "'-3' steps to index -3; each number counts the indexes a set"
            " skips, 0 or more, so its indexes rise",
        ),
        # An empty number, a sign, a space, and a digit outside ASCII.
        ("0,,1", f"'0,,1' holds '', {NOT_DIGITS}"),
        ("1,", f"'1,' holds '', {NOT_DIGITS}"),
        (",", f"',' holds '', {NOT_DIGITS}"),
        ("-0", f"'-0' holds '-0', {NOT_DIGITS}"),
        ("+1", f"'+1' holds '+1', {NOT_DIGITS}"),
        ("0, 1", f"'0, 1' holds ' 1', {NOT_DIGITS}"),
        ("\u0661", f"'\u0661' holds '\u0661', {NOT_DIGITS}"),
        # A set has one spelling: no number starts with a 0 and goes on.
        (
            "01",
            "'01' holds '01', a number that starts with a 0; each set has one spelling",
        ),
        (
            "0,00",
            "'0,00' holds '00', a number that starts with a 0; each set has one"
            " spelling",
        ),
    ],
)
def test_a_set_off_its_spelling_refuses_naming_the_field(
    spelled: str, problem: str
) -> None:
    raw = _raw(**{"a.py": _entry(reached={"sets": ["0", spelled], "lines": []})})
    message = f'the row: files["a.py"].reached.sets[1]: {problem}'
    with pytest.raises(PartError) as refused:
        fmt.decode(raw, source="the row")
    assert str(refused.value) == message
    # The stored form reads its sets as a part does, and refuses alike.
    kept, facts = fmt.stored(fmt.decode(_raw(**{"a.py": _entry()}), source="p"))
    kept["files"]["a.py"]["reached"] = raw["files"]["a.py"]["reached"]
    with pytest.raises(PartError) as refused:
        fmt.from_stored(kept, facts, source="the row")
    assert str(refused.value) == message
    # A numbering never moves a set it cannot read.
    with pytest.raises(PartError) as refused:
        fmt.renumbered(raw, ["", "t1"], source="the row")
    assert str(refused.value) == message
    with pytest.raises(ValueError, match="^" + re.escape(problem) + "$"):
        fmt.unpack_set(spelled)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "the part: is [], and a table is wanted"),
        (
            {**_raw(), "extra": 1},
            "the part: holds extra, which format 3 has no key for; its keys are"
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
        (
            {**_raw(), "contexts": ["", "t1", ""]},
            "contexts[2]: repeats ''; a numbering names each once",
        ),
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
            'files["a.py"]: holds counts, which format 3 has no key for',
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
            _raw(**{"a.py": _entry(reached={"sets": ["5"], "lines": []})}),
            "reached.sets[0]: names context 5, and the part names 2",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["0"], "lines": [[1]]})}),
            "reached.lines[0]: is no [line, set] pair",
        ),
        (
            _raw(**{"a.py": _entry(reached={"sets": ["0"], "lines": [[1, 3]]})}),
            "reached.lines[0][1]: is 3, and the part has 1 sets",
        ),
        (
            {**_raw(), "measurer": {**_raw()["measurer"], "columns": "yes"}},
            "measurer.columns: is 'yes', and true or false is wanted",
        ),
        (
            _raw(**{"a.py": _entry(reached={"lines": [], "every": 1})}),
            "reached: holds every, which format 3 has no key for",
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


def test_a_part_whose_context_index_names_no_context_refuses_naming_the_file() -> None:
    # Line 2's set names context 2, and the part numbers two: "" and t1.
    sets = [fmt.pack_set({0}), fmt.pack_set({0, 1}), fmt.pack_set({2})]
    named = _entry(hits="1-2", reached={"sets": sets, "lines": [[1, 0], [2, 2]]})
    raw = _raw(**{"packages/x/a.py": named})
    message = (
        'the row: files["packages/x/a.py"].reached.sets[2]: names context 2, and'
        " the part names 2"
    )
    with pytest.raises(PartError) as refused:
        fmt.decode(raw, source="the row")
    assert str(refused.value) == message
    # The stored form keeps what ran as the part does, and refuses alike.
    kept, facts = fmt.stored(
        fmt.decode(_raw(**{"packages/x/a.py": _entry()}), source="p")
    )
    kept["files"]["packages/x/a.py"]["reached"] = named["reached"]
    with pytest.raises(PartError) as refused:
        fmt.from_stored(kept, facts, source="the row")
    assert str(refused.value) == message
    # A numbering never moves an index it cannot read.
    for move in (fmt.renumbered, fmt.numbered):
        with pytest.raises(PartError) as refused:
            move(raw, ["", "t1"], source="the row")
        assert str(refused.value) == message


def test_a_numbering_that_drops_a_known_context_refuses() -> None:
    raw = _raw(
        **{
            "a.py": _entry(
                hits="1",
                reached={"sets": [fmt.pack_set({1})], "lines": [[1, 0]]},
            )
        }
    )
    # t1 is the part's, and a numbering only appends: one without it
    # would leave line 1's set naming nothing.
    with pytest.raises(PartError) as refused:
        fmt.renumbered(raw, ["", "t2"], source="the row")
    assert str(refused.value) == (
        "the row: contexts[1]: is 't1', and the numbering drops it; a numbering"
        " only appends, so every context keeps its index"
    )
    # A numbering names each context once.
    with pytest.raises(PartError, match=r"numbering\[2\]: repeats 't1'"):
        fmt.renumbered(raw, ["", "t1", "t1"], source="the row")


def test_a_part_of_an_older_format_version_refuses_and_names_its_version() -> None:
    # Format 2 wrote each set as base64: its parts are named and never read.
    older = {
        **_raw(**{"a.py": _entry(reached={"sets": ["AAE="], "lines": [[1, 0]]})}),
        "format": 2,
    }
    with pytest.raises(OtherVersion) as refused:
        fmt.decode(older, source="the probe's part")
    assert refused.value.version == 2
    assert str(refused.value) == (
        "the probe's part: a part of format 2; this workshop reads format 3"
    )
    # Its stored form too, a part of format 1, and a part with no version.
    with pytest.raises(OtherVersion, match="a part of format 2; this workshop"):
        fmt.from_stored(older, {}, source="the probe's part")
    with pytest.raises(OtherVersion, match="a part of format 1; this workshop"):
        fmt.decode({**_raw(), "format": 1}, source="the probe's part")
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


def test_a_text_without_a_final_newline_holds_its_last_line() -> None:
    text = "int a;\nint b;"
    last = Entry(digest="", ranges=(Range(2, 1, 2, 7, 0),))
    fmt.check_ranges(last, text, source="a.c")
    with pytest.raises(PartError, match="the file has 2 lines"):
        fmt.check_ranges(
            Entry(digest="", ranges=(Range(3, 1, 3, 2, 0),)), text, source="a.c"
        )


def test_unrun_ranges_that_meet_are_one_stretch() -> None:
    met = Entry(digest="", ranges=(Range(1, 1, 1, 5, 0), Range(1, 5, 1, 9, 0)))
    assert fmt.unrun_columns(met) == {1: ((1, 9),)}


def test_a_range_that_ran_inside_one_that_never_did_leaves_its_line_unmarked() -> None:
    # Line 2 lies whole inside a range that ran, itself inside one that
    # never did: the innermost decides, and the line has no stretch.
    nested = Entry(digest="", ranges=(Range(1, 1, 3, 5, 0), Range(2, 1, 3, 1, 4)))
    assert fmt.unrun_columns(nested) == {1: ((1, None),), 3: ((1, 5),)}


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


def test_a_count_of_no_runs_reads_as_a_line_that_never_ran() -> None:
    lines = {
        **_raw(
            **{"a.py": {"digest": "", "counts": [[1, 0], [2, 3]], "statements": "1-2"}}
        ),
        "measurer": {"name": "lines", "counts": "counts", "branches": "none"},
    }
    entry = fmt.decode(lines, source="p").files["a.py"]
    assert entry.counts == {2: 3}
    assert entry.executed == {2} and entry.missing == {1}


# --- what a part holds ---------------------------------------------------


@pytest.mark.parametrize(
    ("held", "spelled"),
    [
        (set(), ""),
        ({0}, "0"),
        ({7}, "7"),
        ({0, 1, 2}, "0,0,0"),
        # Gaps: each number counts the indexes skipped since the last.
        ({3, 9, 10}, "3,5,0"),
        ({0, 1, 299}, "0,0,297"),
        ({0, 127, 128, 20000}, "0,126,0,19871"),
        # Large indexes take as many digits as they need.
        ({1_000_000, 10**12}, "1000000,999998999999"),
        ({2**64 + 1}, "18446744073709551617"),
    ],
)
def test_a_set_reads_back_from_its_one_spelling(held: set[int], spelled: str) -> None:
    assert fmt.pack_set(held) == spelled
    assert fmt.unpack_set(spelled) == held


def test_a_part_reads_back_each_line_s_set_the_empty_one_too() -> None:
    reached = {1: frozenset[int](), 2: frozenset({0}), 3: frozenset({0, 2, 3})}
    part = Part(
        "packages/x",
        COVERAGE_PY,
        {"a.py": Entry("d" * 40, frozenset({1, 2, 3}), {1: 1}, reached=reached)},
        ("", "t1", "t2", "t3"),
    )
    written = fmt.encode(part)
    assert written["files"]["a.py"]["reached"] == {
        "sets": ["", "0", "0,1,0"],
        "lines": [[1, 0], [2, 1], [3, 2]],
    }
    assert fmt.decode(json.loads(json.dumps(written)), source="p") == part


def test_a_large_random_set_is_digits_and_commas_alone_and_reads_back() -> None:
    picker = random.Random(5)
    # Dense runs among the first indexes, then sparse ones far out: every
    # width of number the set's text can hold.
    held = set(picker.sample(range(20_000), 15_000))
    held |= set(picker.sample(range(20_000, 10**9), 15_000))
    spelled = fmt.pack_set(held)
    assert re.fullmatch(r"[0-9,]+", spelled)
    assert fmt.unpack_set(spelled) == held
    assert spelled.count(",") == len(held) - 1


def test_range_strings_hold_their_lines_and_a_digest_is_the_git_blob_id() -> None:
    assert fmt.ranges([]) == ""
    assert fmt.ranges([5, 1, 2, 3, 3, 9]) == "1-3,5,9"
    assert fmt.parse_ranges("") == frozenset()
    assert fmt.parse_ranges("1-3,5,9") == {1, 2, 3, 5, 9}
    with pytest.raises(ValueError, match="'' is not a line or a range of lines"):
        fmt.parse_ranges("1,,2")
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
        ({"statements": "1"}, "holds statements, which format 3 has no key for"),
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


def test_a_stored_part_whose_branches_never_ran_keeps_them_in_its_facts_alone() -> None:
    entry = Entry(
        digest="a" * 40,
        statements=frozenset({1, 2, 3}),
        counts={1: 1},
        branches=(
            Branch(1, 2, 0, 0, "line 1 didn't jump to line 2"),
            Branch(1, 3, 1, 0, "line 1 didn't jump to line 3"),
        ),
    )
    part = Part("packages/x", COVERAGE_PY, {"packages/x/a.py": entry})
    kept, facts = fmt.stored(part)
    (name,) = facts
    assert kept["files"]["packages/x/a.py"] == {
        "digest": "a" * 40,
        "facts": name,
        "hits": "1",
    }
    assert facts[name]["branches"] == [
        [1, 2, 0, "line 1 didn't jump to line 2"],
        [1, 3, 1, "line 1 didn't jump to line 3"],
    ]
    assert fmt.from_stored(kept, facts, source="p") == part


def test_the_facts_a_part_names_pass_over_what_is_off_the_stored_form() -> None:
    assert fmt.facts_names({"files": ["packages/x/a.py"]}) == {}
    assert fmt.facts_names(
        {"files": {"packages/x/a.py": "facts", "packages/x/b.py": {"facts": "f"}}}
    ) == {"f": ""}


def test_a_numbering_keeps_each_context_s_index_and_appends_the_new_ones() -> None:
    # The row before numbered "", t2 and t5; this reading names "", t1
    # and t2, sorted as its reduction gave them: t1 is new and sorts
    # first, t5's test is gone.
    known = ("", "t2", "t5")
    assert fmt.numbering(known, ["t2", "", "t1"]) == ("", "t2", "t5", "t1")
    assert fmt.numbering((), ["t2", "", "t1"]) == ("", "t1", "t2")
    entry = Entry(
        digest="a" * 40,
        statements=frozenset({1, 2, 3}),
        counts={1: 1, 2: 1, 3: 1},
        reached={1: frozenset({0}), 2: frozenset({1, 2}), 3: frozenset({2})},
    )
    part = Part("packages/x", COVERAGE_PY, {"packages/x/a.py": entry}, ("", "t1", "t2"))
    moved = fmt.numbered(fmt.encode(part), known, source="p")
    # Each set names the contexts it named before, by their new places:
    # t2 keeps index 1, t5 keeps 2 and names no line, t1 takes 3.
    assert moved["contexts"] == ["", "t2", "t5", "t1"]
    assert moved["files"]["packages/x/a.py"]["reached"] == {
        "sets": [fmt.pack_set({0}), fmt.pack_set({1, 3}), fmt.pack_set({1})],
        "lines": [[1, 0], [2, 1], [3, 2]],
    }
    read = fmt.decode(moved, source="p")
    assert {
        line: {read.contexts[index] for index in held}
        for line, held in read.files["packages/x/a.py"].reached.items()
    } == {1: {""}, 2: {"t1", "t2"}, 3: {"t2"}}
    # Nothing else moves, and the stored form moves the same way.
    assert {**moved, "contexts": [], "files": {}} == {
        **fmt.encode(part),
        "contexts": [],
        "files": {},
    }
    kept, facts = fmt.stored(part)
    stored = fmt.numbered(kept, known, source="p")
    assert fmt.from_stored(stored, facts, source="p") == read
    # A part already numbered so is left as it is.
    assert fmt.numbered(moved, known, source="p") == moved
    # The merge reads the contexts by name, and one no line names, the
    # test that is gone, is no merged context.
    combined = fmt.merge([read])
    assert combined.contexts == ("", "t1", "t2")
    # Merged for the floors alone, the contexts stay out.
    counted = fmt.merge([read], contexts=False)
    assert counted.contexts == ()
    assert counted.files["packages/x/a.py"].entry.reached == {}
    assert counted.files["packages/x/a.py"].entry.executed == {1, 2, 3}


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


# --- carrying a reading across a change ---------------------------------------


#: A content and the one a change made of it: a line inserted above, the
#: last line removed, and the lines between left in place. The old
#: lines 1 to 5 are the new lines 2 to 6; the old line 6 is gone.
_OLD = "a = 1\nif a:\n    b = 2\nelse:\n    c = 3\nd = 4\n"
_NEW = "z = 0\na = 1\nif a:\n    b = 2\nelse:\n    c = 3\n"
#: The old content with its line 5 edited.
_EDITED = "a = 1\nif a:\n    b = 2\nelse:\n    c = 30\nd = 4\n"
_TEXTS = {fmt.digest(text.encode()): text for text in (_OLD, _NEW, _EDITED)}
OLD, NEW, EDITED = (fmt.digest(text.encode()) for text in (_OLD, _NEW, _EDITED))


def _source(digest: str) -> str:
    """The text of a content, as the union hands it to the merge."""
    return _TEXTS.get(digest, "")


def _at(
    unit: str,
    digest: str,
    *,
    measurer: Measurer = COVERAGE_PY,
    contexts: tuple[str, ...] = (),
    **keys: Any,
) -> Part:
    """*unit*'s reading of ``packages/x/a.py`` at *digest*."""
    entry = Entry(digest=digest, **keys)
    return Part(unit, measurer, {"packages/x/a.py": entry}, contexts)


#: The current content's branches, as every reading at it names them.
_NEW_BRANCHES = (
    Branch(3, 4, 0, 1),
    Branch(3, 6, 1, 0, "line 3 didn't jump to line 6"),
    Branch(6, -3, 0, 0, "line 6 didn't return from the function"),
)


def _kept(**keys: Any) -> Part:
    """The reading at the current content: its facts, and what *keys* say ran."""
    return _at(
        "packages/x",
        NEW,
        statements=frozenset({1, 2, 3, 4, 6}),
        branches=_NEW_BRANCHES,
        **keys,
    )


def _carried_line(digest: str, kept: int, total: int) -> str:
    return (
        "packages/x/a.py: carried the reading of tests by coverage.py 7.16.2 on"
        f" CPython 3.14 at content {digest[:12]} across the change: {kept} of"
        f" {total} measured line(s) kept, {total - kept} dropped"
    )


def _left_out_line(digest: str, current: str) -> str:
    return (
        "packages/x/a.py: left out the reading of tests by coverage.py 7.16.2 on"
        f" CPython 3.14 at content {digest[:12]}; the union keeps the one of"
        f" packages/x by coverage.py 7.16.2 on CPython 3.14 at content"
        f" {current[:12]}"
    )


def test_a_carried_reading_on_a_changed_line_is_dropped_and_the_counts_named() -> None:
    kept = _at(
        "packages/x", EDITED, statements=frozenset({1, 2, 3, 5, 6}), counts={1: 1}
    )
    # The old reading ran line 1, which the change left in place, and
    # line 5, which it edited.
    older = _at("tests", OLD, counts={1: 1, 5: 1})
    combined = fmt.merge([older, kept], {"packages/x/a.py": EDITED}, source=_source)
    assert combined.left_out == (_carried_line(OLD, 1, 2),)
    assert combined.carried == 1
    merged = combined.files["packages/x/a.py"]
    assert merged.units == ("packages/x", "tests")
    assert merged.entry.executed == {1}
    assert dict(merged.entry.counts) == {1: 2}
    # A line the change left in place that is no statement of the
    # current content, one a string now spans say, is dropped too.
    kept = _at("packages/x", NEW, statements=frozenset({1, 3, 4, 6}))
    older = _at("tests", OLD, counts={1: 1})
    combined = fmt.merge([older, kept], {"packages/x/a.py": NEW}, source=_source)
    assert combined.left_out == (_carried_line(OLD, 0, 1),)
    assert combined.files["packages/x/a.py"].entry.executed == frozenset()


def test_a_deleted_file_s_reading_is_left_out_as_one_at_other_content() -> None:
    newer = _kept(counts={1: 1})
    other = _at("other", NEW, statements=frozenset({1, 2, 3, 4, 6}), counts={1: 1})
    older = _at("tests", OLD, statements=frozenset({1, 2, 3, 5, 6}), counts={5: 1})
    # The checkout no longer has the file: no digest of it is current,
    # the readings at the content most of them share are kept, and
    # nothing is carried, both texts at hand or not.
    combined = fmt.merge([older, newer, other], {}, source=_source)
    assert combined.left_out == (_left_out_line(OLD, NEW),)
    assert combined.carried == 0
    assert combined.files["packages/x/a.py"].entry.executed == {1}
    # Nor when the checkout holds a content no reading was taken at.
    combined = fmt.merge(
        [older, newer, other], {"packages/x/a.py": "f" * 40}, source=_source
    )
    assert combined.left_out == (_left_out_line(OLD, NEW),)
    assert combined.carried == 0


def test_a_reading_whose_content_the_source_lacks_is_left_out() -> None:
    newer = _kept(counts={1: 1})
    older = _at("tests", OLD, counts={5: 1})
    current = {"packages/x/a.py": NEW}

    def without_old(digest: str) -> str:
        return "" if digest == OLD else _source(digest)

    def without_new(digest: str) -> str:
        return "" if digest == NEW else _source(digest)

    for source in (without_old, without_new, None):
        combined = fmt.merge([older, newer], current, source=source)
        assert combined.left_out == (_left_out_line(OLD, NEW),)
        assert combined.carried == 0
        assert combined.files["packages/x/a.py"].entry.executed == {1}


def test_a_reading_by_another_measurer_identity_is_never_carried() -> None:
    newer = _kept(counts={1: 1})
    older = _at(
        "tests",
        OLD,
        measurer=Measurer("coverage.py", "7.16.2", "CPython 3.11", judges=(fmt.LINES,)),
        counts={5: 1},
    )
    combined = fmt.merge([older, newer], {"packages/x/a.py": NEW}, source=_source)
    assert combined.left_out == (
        "packages/x/a.py: left out the reading of tests by coverage.py 7.16.2 on"
        f" CPython 3.11 at content {OLD[:12]}; the union keeps the one of"
        f" packages/x by coverage.py 7.16.2 on CPython 3.14 at content {NEW[:12]}",
    )
    assert combined.carried == 0
    assert combined.files["packages/x/a.py"].entry.executed == {1}


def test_a_branch_carries_only_where_both_ends_map_to_a_branch_of_the_content() -> None:
    newer = _kept(counts={1: 1, 2: 1, 3: 1, 4: 1})
    older = _at(
        "tests",
        OLD,
        counts={1: 1, 2: 1, 5: 1},
        branches=(
            # Both ends map, and the branch is matched by its target,
            # whatever place the old reading gave it among its line's.
            Branch(2, 5, 0, 1),
            Branch(2, 3, 1, 0, "line 2 didn't jump to line 3"),
            # The exit of the function starting on line 2, now line 3.
            Branch(5, -2, 0, 2),
            # One end on the removed line.
            Branch(5, 6, 1, 1),
            # Both ends map, but the current content has no such branch.
            Branch(1, 5, 0, 1),
        ),
    )
    combined = fmt.merge([older, newer], {"packages/x/a.py": NEW}, source=_source)
    assert combined.left_out == (_carried_line(OLD, 3, 3),)
    entry = combined.files["packages/x/a.py"].entry
    assert entry.branches == (
        Branch(3, 4, 0, 1),
        Branch(3, 6, 1, 1, "line 3 didn't jump to line 6"),
        Branch(6, -3, 0, 2, "line 6 didn't return from the function"),
    )
    assert entry.partial == frozenset()


def test_an_unchanged_line_s_reading_carries_across_an_insertion_and_a_deletion() -> (
    None
):
    newer = _kept(
        counts={1: 1, 2: 1, 3: 1, 4: 1},
        reached={1: frozenset({0}), 2: frozenset({0}), 3: frozenset({0})},
        contexts=("",),
    )
    older = _at(
        "tests",
        OLD,
        counts={1: 1, 2: 1, 5: 1, 6: 1},
        branches=(
            Branch(2, 3, 0, 0, "line 2 didn't jump to line 3"),
            Branch(2, 5, 1, 1),
        ),
        reached={1: frozenset({0}), 5: frozenset({0, 1}), 6: frozenset({1})},
        contexts=("t1", "t2"),
    )
    combined = fmt.merge([older, newer], {"packages/x/a.py": NEW}, source=_source)
    assert combined.left_out == (_carried_line(OLD, 3, 4),)
    assert combined.carried == 1
    assert combined.contexts == ("", "t1", "t2")
    merged = combined.files["packages/x/a.py"]
    assert merged.units == ("packages/x", "tests")
    entry = merged.entry
    assert entry.digest == NEW
    assert entry.statements == {1, 2, 3, 4, 6}
    assert entry.executed == {1, 2, 3, 4, 6}
    assert dict(entry.counts) == {1: 1, 2: 2, 3: 2, 4: 1, 6: 1}
    assert entry.branches == (
        Branch(3, 4, 0, 1),
        Branch(3, 6, 1, 1, "line 3 didn't jump to line 6"),
        Branch(6, -3, 0, 0, "line 6 didn't return from the function"),
    )
    # The tests that reached old line 1 reached new line 2, and old
    # line 5's new line 6; old line 6's are gone with the line.
    assert dict(entry.reached) == {1: {0}, 2: {0, 1}, 3: {0}, 6: {1, 2}}
    # A measurer that counts a line's branches without naming them
    # carries a count where the current content counts as many, and
    # adds its runs.
    counted = _at(
        "packages/x",
        NEW,
        measurer=GCOV,
        statements=frozenset({1, 2, 3, 4, 6}),
        counts={3: 4},
        branch_counts=((3, 2, 1),),
    )
    before = _at(
        "tests",
        OLD,
        measurer=GCOV,
        counts={2: 2, 5: 7},
        branch_counts=((2, 2, 2), (5, 3, 1)),
    )
    combined = fmt.merge([before, counted], {"packages/x/a.py": NEW}, source=_source)
    entry = combined.files["packages/x/a.py"].entry
    assert dict(entry.counts) == {3: 6, 6: 7}
    assert entry.branch_counts == ((3, 2, 2),)
    assert combined.left_out == (
        "packages/x/a.py: carried the reading of tests by gcov 14.2 at content"
        f" {OLD[:12]} across the change: 2 of 2 measured line(s) kept, 0 dropped",
    )


def test_a_numbered_branch_carries_by_its_place_and_one_on_a_removed_line_drops() -> (
    None
):
    counted = _at(
        "packages/x",
        NEW,
        measurer=GCOV,
        statements=frozenset({1, 2, 3, 4, 6}),
        counts={3: 2},
        branches=(Branch(3, None, 0, 2), Branch(3, None, 1, 0)),
    )
    # Old line 2 is new line 3, and old line 6 is gone with its branch.
    before = _at(
        "tests",
        OLD,
        measurer=GCOV,
        counts={2: 1, 6: 1},
        branches=(Branch(2, None, 1, 4), Branch(6, None, 0, 1)),
    )
    combined = fmt.merge([before, counted], {"packages/x/a.py": NEW}, source=_source)
    entry = combined.files["packages/x/a.py"].entry
    assert entry.branches == (Branch(3, None, 0, 2), Branch(3, None, 1, 4))
    assert dict(entry.counts) == {3: 3}
    assert combined.left_out == (
        "packages/x/a.py: carried the reading of tests by gcov 14.2 at content"
        f" {OLD[:12]} across the change: 1 of 2 measured line(s) kept, 1 dropped",
    )


def test_a_content_without_a_final_newline_carries_its_last_line() -> None:
    old_text = "a = 1\nb = 2"
    new_text = "z = 0\na = 1\nb = 2"
    texts = {fmt.digest(text.encode()): text for text in (old_text, new_text)}
    old, new = (fmt.digest(text.encode()) for text in (old_text, new_text))
    kept = _at("packages/x", new, statements=frozenset({1, 2, 3}), counts={1: 1})
    older = _at("tests", old, counts={2: 1})
    combined = fmt.merge(
        [older, kept], {"packages/x/a.py": new}, source=lambda held: texts[held]
    )
    assert combined.carried == 1
    # Old line 2, the last, is new line 3.
    assert combined.files["packages/x/a.py"].entry.executed == {1, 3}
