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
LINES_ONLY = Measurer("lines", counts=fmt.COUNTS)


def _raw(**files: Any) -> dict[str, Any]:
    """A part of coverage.py's as JSON holds it, with *files*."""
    return {
        "format": 1,
        "unit": "packages/x",
        "measurer": {
            "name": "coverage.py",
            "version": "7.16.2",
            "runtime": "CPython 3.14",
            "counts": "presence",
            "branches": "targets",
            "regions": ["function", "class"],
            "judges": ["lines", "branches"],
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
            "the part: holds extra, which format 1 has no key for; its keys are"
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
            'files["a.py"]: holds counts, which format 1 has no key for',
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
            _raw(**{"a.py": _entry(reached={"lines": [], "every": 1})}),
            "reached: holds every, which format 1 has no key for",
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


def test_a_part_of_another_format_version_is_its_own_refusal() -> None:
    with pytest.raises(OtherVersion) as refused:
        fmt.decode({**_raw(), "format": 2}, source="the probe's part")
    assert refused.value.version == 2
    assert str(refused.value) == (
        "the probe's part: a part of format 2; this workshop reads format 1"
    )
    # A part with no version at all is of another version too.
    with pytest.raises(OtherVersion, match="a part of format None"):
        fmt.decode({"unit": "x"}, source="the probe's part")


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


def test_a_merge_ors_runs_and_keeps_a_sentence_only_while_never_taken() -> None:
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
    assert entry.branches == (Branch(2, 3, 0, 1), Branch(2, 4, 1, 0, "nor here"))
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
