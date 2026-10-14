"""What a native measurer read of each source file, and the part it becomes.

A measured test run of the gate build is read by its compiler family's
measurer, and each reader here turns that measurer's output into a
`Reading` per source file: gcov's JSON (`from_gcov_json`), llvm-cov's
JSON export (`from_llvm_json`), and the Cobertura report Microsoft's
engine writes (`from_cobertura`). `write_part` writes a package's
readings as its part in the coverage extension's line format, under
`PARTS`, where coverage gathers it.

Each reader keeps the most its measurer gives. gcov gives each line's
count, each line's branches by index with the path an exception takes
tagged, and its functions under their demangled names. llvm-cov gives
each line's count as its own line view counts it, each condition's
true and false branches, its functions, whose names `demangle` turns
readable, each code region as a range, with a macro counted where it
is called, and the code the preprocessor removed as excluded lines. A
Cobertura report gives each line's count, how many of a line's
branches ran, and its methods. The floors judge the lines alone; the
rest reaches the pages.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from livery.footman import fail

if TYPE_CHECKING:
    from livery.workshop import Package


#: Where a measured test run leaves each package's part, under a
#: directory named after its measurer, in the line format the coverage
#: extension reads: inside the workspace's ``.workshop`` directory,
#: which the base keeps out of git.
PARTS = ".workshop/coverage/parts"

#: The line format's version the parts are written in.
FORMAT = 2

#: The tag of a branch an exception takes.
THROW = "throw"

#: What each compiler family's measurer reads, as a part names it: its
#: name, how it counts and names branches, the regions it names,
#: whether it reads columns, and what its figure judges.
MEASURERS: dict[str, dict[str, Any]] = {
    "gcov": {
        "name": "gcov",
        "branches": "indexes",
        "columns": False,
    },
    "llvm": {
        "name": "llvm-cov",
        "branches": "indexes",
        "columns": True,
    },
    "msvc": {
        "name": "dotnet-coverage",
        "branches": "none",
        "columns": False,
    },
}

#: The kinds of region llvm-cov's JSON export numbers: code, a macro's
#: call, and code the preprocessor removed.
CODE_REGION = 0
EXPANSION_REGION = 1
SKIPPED_REGION = 2


@dataclass
class Reading:
    """What a native measurer read of one source file.

    Attributes:
        lines: How many times each line ran, every line the measurer
            instruments, run or not.
        excluded: The lines the preprocessor removed from the build.
        branches: Each branch's runs and tag by its line and its index
            among the line's branches; the tag is ``throw`` for the
            path an exception takes, else empty.
        branch_counts: For a measurer that counts a line's branches
            without naming them: the line's branches, and how many ran.
        functions: Each function's last line and calls, by its name and
            first line; the calls are None for a measurer that counts
            none.
        ranges: How many times each stretch of code ran, by its start
            line and column and its end line and the column after its
            last; columns count bytes from 1.
    """

    lines: dict[int, int] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    branches: dict[tuple[int, int], tuple[int, str]] = field(default_factory=dict)
    branch_counts: dict[int, tuple[int, int]] = field(default_factory=dict)
    functions: dict[tuple[str, int], tuple[int, int | None]] = field(
        default_factory=dict
    )
    ranges: dict[tuple[int, int, int, int], int] = field(default_factory=dict)

    def add(self, other: Reading) -> None:
        """Add *other*, another counter file's reading of the same source, to this one.

        Counts add up; a count of a line's branches keeps the most of
        each.
        """
        for line, runs in other.lines.items():
            self.lines[line] = self.lines.get(line, 0) + runs
        self.excluded |= other.excluded
        for (line, index), (taken, tag) in other.branches.items():
            self.add_branch(line, index, taken, tag)
        for line, (total, ran) in other.branch_counts.items():
            was_total, was_ran = self.branch_counts.get(line, (0, 0))
            self.branch_counts[line] = (max(was_total, total), max(was_ran, ran))
        for (name, start), (end, calls) in other.functions.items():
            self.add_function(name, start, end, calls)
        for extent, count in other.ranges.items():
            self.ranges[extent] = self.ranges.get(extent, 0) + count

    def add_branch(self, line: int, index: int, taken: int, tag: str) -> None:
        """Count *taken* runs of branch *index* of *line*, tagged *tag*."""
        was, was_tag = self.branches.get((line, index), (0, ""))
        self.branches[(line, index)] = (was + taken, was_tag or tag)

    def add_function(self, name: str, start: int, end: int, calls: int | None) -> None:
        """Count *calls* of the function *name* from line *start* to line *end*.

        Calls a measurer counts add up; a function no reading counted
        the calls of keeps None.
        """
        was_end, was_calls = self.functions.get((name, start), (end, None))
        total = (
            None
            if was_calls is None and calls is None
            else (was_calls or 0) + (calls or 0)
        )
        self.functions[(name, start)] = (max(was_end, end), total)


# --- gcov ---------------------------------------------------------------------------


def from_gcov_json(text: str) -> dict[str, Reading]:
    """What gcov's JSON output names, per file as gcov names it; a non-document refuses.

    gcov prints one document per counter file, one per line, or one
    whole. A line gcov names twice, and a file two documents name, has
    its counts added. Each line's branches are numbered in gcov's
    order, the ones gcov marks as an exception's tagged ``throw``,
    when gcov ran with ``--branch-probabilities``; a function is named
    as gcov demangles it.
    """
    readings: dict[str, Reading] = {}
    for document in _json_documents(text):
        files = document.get("files") if isinstance(document, dict) else None
        if not isinstance(files, list):
            fail("gcov printed no files table; its JSON format has one")
        for entry in files:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("file", ""))
            rows = entry.get("lines")
            if not name or not isinstance(rows, list):
                continue
            readings.setdefault(name, Reading()).add(_gcov_file(entry, rows))
    return readings


def _gcov_file(entry: dict[str, Any], rows: list[Any]) -> Reading:
    """One file of a gcov document, read."""
    found = Reading()
    for row in rows:
        if not isinstance(row, dict) or "line_number" not in row:
            continue
        line = int(row["line_number"])
        found.lines[line] = found.lines.get(line, 0) + int(row.get("count", 0))
        for index, branch in enumerate(row.get("branches") or []):
            if isinstance(branch, dict):
                tag = THROW if branch.get("throw") else ""
                found.add_branch(line, index, int(branch.get("count", 0)), tag)
    for function in entry.get("functions") or []:
        if not isinstance(function, dict):
            continue
        name = str(function.get("demangled_name") or function.get("name") or "")
        start = int(function.get("start_line", 0))
        end = int(function.get("end_line", start))
        if name and start >= 1:
            found.add_function(
                name, start, max(start, end), int(function.get("execution_count", 0))
            )
    return found


def _json_documents(text: str) -> list[Any]:
    """Every JSON document in *text*, one per line or one whole; non-JSON refuses."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except ValueError:
        pass
    documents: list[Any] = []
    for line in stripped.splitlines():
        if not line.strip():
            continue
        try:
            documents.append(json.loads(line))
        except ValueError:
            fail(f"gcov printed a line that is not JSON: {line[:120]}")
    return documents


# --- llvm-cov -----------------------------------------------------------------------


def from_llvm_json(text: str) -> dict[str, Reading]:
    """What ``llvm-cov export -format=text`` prints, per file as llvm-cov names it.

    A line's count is the one llvm-cov's own line view gives, the view
    its lcov export prints, read from the file's segments
    (`line_counts`). Each branch region is a condition: its true
    branch numbered twice its place among its line's conditions, its
    false branch one more. A function's code regions in its own file
    give its first and last line, and its calls are its count; its
    name is llvm-cov's, a mangled one until `demangle` reads it. A
    function's code regions become ranges, and a macro's call counts
    as a range where it is called, never inside the macro; regions of
    one stretch, as each instance of a template gives, add their
    counts. Its skipped regions, the code the preprocessor removed,
    mark their lines excluded.

    Raises:
        Failed: when *text* is not llvm-cov's JSON export, naming why.
    """
    try:
        document = json.loads(text)
    except ValueError as error:
        fail(f"llvm-cov printed no JSON export ({error})")
    exports = document.get("data") if isinstance(document, dict) else None
    if not isinstance(exports, list):
        fail("llvm-cov printed no data table; its JSON export has one")
    readings: dict[str, Reading] = {}
    for export in exports:
        if not isinstance(export, dict):
            continue
        for entry in export.get("files") or []:
            if not isinstance(entry, dict) or not entry.get("filename"):
                continue
            found = readings.setdefault(str(entry["filename"]), Reading())
            for line, runs in line_counts(entry.get("segments") or []).items():
                found.lines[line] = found.lines.get(line, 0) + runs
            for line, index, taken in _conditions(entry.get("branches") or []):
                found.add_branch(line, index, taken, "")
        kinds: dict[tuple[str, tuple[int, int, int, int]], int] = {}
        for function in export.get("functions") or []:
            if isinstance(function, dict):
                _llvm_function(function, readings, kinds)
    return readings


def line_counts(segments: Iterable[Any]) -> dict[int, int]:
    """Each line's count from a file's segments, as llvm-cov's line view counts it.

    A segment is ``[line, column, count, has count, starts a region,
    is a gap]``. A line is counted when a region with a count starts
    on it, or the last segment before it carries a count, unless the
    line starts a region the preprocessor skipped; its count is the
    most of the carried count and the counts of the regions starting
    on it, gaps aside. The lines run from the first to the last line a
    segment names.
    """
    ordered = [item for item in segments if isinstance(item, list) and len(item) >= 6]
    counts: dict[int, int] = {}
    wrapped: list[Any] | None = None
    current: list[list[Any]] = []
    line = 1
    index = 0
    while index < len(ordered):
        if current:
            wrapped = current[-1]
        current = []
        while index < len(ordered) and ordered[index][0] == line:
            current.append(ordered[index])
            index += 1
        mapped, runs = _line_stats(current, wrapped)
        if mapped:
            counts[line] = runs
        line += 1
    return counts


def _line_stats(here: list[list[Any]], wrapped: list[Any] | None) -> tuple[bool, int]:
    """Whether a line is counted, and its count, from its segments and the last one."""
    starts = [item for item in here if item[3] and item[4] and not item[5]]
    skipped = bool(here) and not here[0][3] and bool(here[0][4])
    mapped = not skipped and (bool(wrapped is not None and wrapped[3]) or bool(starts))
    mapped = mapped or any(item[3] and item[4] for item in here)
    if not mapped:
        return False, 0
    runs = int(wrapped[2]) if wrapped is not None else 0
    for item in starts:
        runs = max(runs, int(item[2]))
    return True, runs


def _conditions(branches: Iterable[Any]) -> list[tuple[int, int, int]]:
    """Each condition's two branches: its line, the branch's index, and its runs.

    A branch region is ``[line, column, end line, end column, true
    count, false count, ...]``; regions of one stretch, as each
    instance of a template gives, add their counts. A line's conditions
    are numbered in the order of their columns.
    """
    counted: dict[tuple[int, int, int, int], tuple[int, int]] = {}
    for item in branches:
        if not isinstance(item, list) or len(item) < 6:
            continue
        extent = (int(item[0]), int(item[1]), int(item[2]), int(item[3]))
        was_true, was_false = counted.get(extent, (0, 0))
        counted[extent] = (was_true + int(item[4]), was_false + int(item[5]))
    found: list[tuple[int, int, int]] = []
    place: dict[int, int] = {}
    for extent, (true, false) in sorted(counted.items()):
        line = extent[0]
        index = place.get(line, 0)
        place[line] = index + 1
        found += [(line, 2 * index, true), (line, 2 * index + 1, false)]
    return found


def _llvm_function(
    function: dict[str, Any],
    readings: dict[str, Reading],
    kinds: dict[tuple[str, tuple[int, int, int, int]], int],
) -> None:
    """Read one function record's regions into the readings of the files they lie in.

    A region is ``[line, column, end line, end column, count, file id,
    expanded file id, kind]``; a file id indexes the record's
    ``filenames``, and a region inside a macro's body lies in a file id
    an expansion region expands to. *kinds* keeps which kind each range
    was counted from: a code region and a macro's call on one stretch
    count the code region's alone, as llvm-cov does.
    """
    filenames = [str(name) for name in function.get("filenames") or []]
    regions = [
        item
        for item in function.get("regions") or []
        if isinstance(item, list) and len(item) >= 8 and int(item[5]) < len(filenames)
    ]
    expanded = {int(item[6]) for item in regions if item[7] == EXPANSION_REGION}
    own = [item for item in regions if int(item[5]) not in expanded]
    for item in own:
        name = filenames[int(item[5])]
        reading = readings.setdefault(name, Reading())
        extent = (int(item[0]), int(item[1]), int(item[2]), int(item[3]))
        kind = int(item[7])
        if kind == SKIPPED_REGION:
            reading.excluded.update(range(extent[0], extent[2] + 1))
            continue
        if kind not in (CODE_REGION, EXPANSION_REGION):
            continue
        seen = kinds.get((name, extent))
        if seen is None or (seen == EXPANSION_REGION and kind == CODE_REGION):
            kinds[(name, extent)] = kind
            reading.ranges[extent] = int(item[4])
        elif seen == kind:
            reading.ranges[extent] += int(item[4])
    body = [item for item in own if int(item[5]) == 0 and item[7] == CODE_REGION]
    if not body or not filenames:
        return
    readings.setdefault(filenames[0], Reading()).add_function(
        str(function.get("name", "")).rpartition(":")[2],
        min(int(item[0]) for item in body),
        max(int(item[2]) for item in body),
        int(function.get("count", 0)),
    )


def demangle(
    readings: Mapping[str, Reading], demangled: Callable[[list[str]], list[str]]
) -> None:
    """Rename every function of *readings* to its demangled name, in place.

    *demangled* answers each of the names it is given with the name
    to show, in order, as ``llvm-cxxfilt`` does on its standard input;
    an answer of another length leaves every name as it is.
    """
    names = sorted(
        {name for reading in readings.values() for name, _ in reading.functions}
    )
    if not names:
        return
    answered = demangled(names)
    if len(answered) != len(names):
        return
    shown = dict(zip(names, answered, strict=True))
    for reading in readings.values():
        functions = dict(reading.functions)
        reading.functions.clear()
        for (name, start), (end, calls) in functions.items():
            reading.add_function(shown[name] or name, start, end, calls)


# --- Cobertura ----------------------------------------------------------------------


def from_cobertura(text: str) -> dict[str, Reading]:
    """What a Cobertura report names, per file; a report that is not XML refuses.

    A class's ``filename`` is taken as it is when absolute, else under
    the report's first ``<source>``. A line a class names twice, under
    one of its methods and again under the class, counts once, at its
    most; two classes of one file add their counts. A line's
    ``condition-coverage`` of ``(k/n)`` is how many of its n branches
    ran, and each method is a function over the lines it names.
    """
    try:
        document = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        fail(f"the coverage report is not XML ({error})")
    sources = [
        (node.text or "").strip() for node in document.iter("source") if node.text
    ]
    readings: dict[str, Reading] = {}
    for klass in document.iter("class"):
        name = klass.get("filename", "")
        if not name:
            continue
        if not _absolute(name) and sources:
            name = (
                sources[0].rstrip("\\/") + ("\\" if "\\" in sources[0] else "/") + name
            )
        readings.setdefault(name, Reading()).add(_cobertura_class(klass))
    return readings


#: How a Cobertura line names its branches that ran: ``50% (1/2)``.
CONDITIONS = re.compile(r"\((\d+)/(\d+)\)")


def _cobertura_class(klass: ElementTree.Element) -> Reading:
    """One class of a Cobertura report, read."""
    found = Reading()
    for row in klass.iter("line"):
        number = row.get("number")
        if number is None:
            continue
        line = int(number)
        found.lines[line] = max(found.lines.get(line, 0), int(row.get("hits", "0")))
        conditions = CONDITIONS.search(row.get("condition-coverage", ""))
        if conditions is not None and int(conditions.group(2)) > 0:
            ran, total = int(conditions.group(1)), int(conditions.group(2))
            was_total, was_ran = found.branch_counts.get(line, (0, 0))
            found.branch_counts[line] = (
                max(was_total, total),
                max(was_ran, min(ran, total)),
            )
    for method in klass.iter("method"):
        lines = [
            int(row.get("number", "0"))
            for row in method.iter("line")
            if row.get("number", "0").isdigit()
        ]
        name = method.get("name", "")
        signature = method.get("signature", "")
        if signature and "(" not in name:
            name += signature
        if name and lines and min(lines) >= 1:
            # A Cobertura report counts no calls.
            found.add_function(name, min(lines), max(lines), None)
    return found


def _absolute(name: str) -> bool:
    return Path(name).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", name) is not None


# --- the part -----------------------------------------------------------------------


def relativise(readings: Mapping[str, Reading], root: Path) -> dict[str, Reading]:
    """*readings* keyed by workspace-relative posix paths; files outside *root* dropped.

    A measurer names files the way the compiler saw them, absolute and
    with the host's separators; the row and the floors want the path
    the repository knows. Two names of one file add up.
    """
    base = root.resolve()
    kept: dict[str, Reading] = {}
    for name, reading in readings.items():
        candidate = Path(PureWindowsPath(name).as_posix() if "\\" in name else name)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            relative = candidate.resolve().relative_to(base).as_posix()
        except ValueError:
            continue
        kept.setdefault(relative, Reading()).add(reading)
    return kept


def within(readings: Mapping[str, Reading], package: Package) -> dict[str, Reading]:
    """The files of *readings* under *package*, keyed as they are."""
    prefix = f"{package.path}/"
    return {name: found for name, found in readings.items() if name.startswith(prefix)}


def write_part(
    root: Path, package_path: str, readings: Mapping[str, Reading], family: str
) -> Path:
    """Write *readings* as *package_path*'s part under *root*; the part's path.

    The part names its measurer by the compiler *family* that read it,
    counts how many times each line ran, and judges lines alone. Per
    file: the instrumented lines as its statements and the ones that
    ran with their counts, the lines the preprocessor removed that are
    no statement as excluded, the branches by index, the counts of a
    line's branches, the functions, the ranges for a measurer that
    reads columns, and the git blob id of the file's text with each
    CRLF read as LF, so a Windows checkout and a Linux one give one
    digest. A line number below 1 is no line and is left out.
    """
    from livery import git

    measurer = MEASURERS.get(family, {"name": family, "branches": "none"})
    files = {
        name: _entry(root / name, reading, columns=bool(measurer.get("columns")))
        for name, reading in sorted(readings.items())
    }
    part = {
        "format": FORMAT,
        "unit": package_path,
        "measurer": {
            "name": measurer["name"],
            "version": "",
            "runtime": "",
            "counts": "counts",
            "branches": measurer["branches"],
            "regions": ["function"],
            "judges": ["lines"],
            "columns": bool(measurer.get("columns")),
        },
        "contexts": [],
        "files": files,
    }
    path = root / PARTS / str(measurer["name"]) / f"{git.slug(package_path)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(part, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _entry(source: Path, reading: Reading, *, columns: bool) -> dict[str, Any]:
    """One file's entry in the line format, from what its measurer read."""
    held = {line: runs for line, runs in reading.lines.items() if line >= 1}
    entry: dict[str, Any] = {
        "digest": _digest(source.read_bytes()) if source.is_file() else ""
    }
    if held:
        entry["statements"] = _ranges(held)
    ran = [[line, runs] for line, runs in sorted(held.items()) if runs > 0]
    if ran:
        entry["counts"] = ran
    excluded = {line for line in reading.excluded if line >= 1} - set(held)
    if excluded:
        entry["excluded"] = _ranges(excluded)
    branches = [
        [line, None, index, taken] + ([tag] if tag else [])
        for (line, index), (taken, tag) in sorted(reading.branches.items())
        if line >= 1
    ]
    if branches:
        entry["branches"] = branches
    counted = [
        [line, total, ran]
        for line, (total, ran) in sorted(reading.branch_counts.items())
        if line >= 1 and total >= 1
    ]
    if counted:
        entry["branch-counts"] = counted
    functions = [
        ["function", name, start, _ranges(range(start, end + 1))]
        + ([calls] if calls is not None else [])
        for (name, start), (end, calls) in sorted(
            reading.functions.items(), key=lambda item: (item[0][1], item[0][0])
        )
        if start >= 1
    ]
    if functions:
        entry["regions"] = functions
    if columns and reading.ranges:
        entry["ranges"] = [
            [*extent, count] for extent, count in sorted(reading.ranges.items())
        ]
    return entry


def remove_parts(root: Path, package_path: str) -> None:
    """Delete *package_path*'s parts of an earlier run under *root*, any measurer's."""
    from livery import git

    for stale in (root / PARTS).glob(f"*/{git.slug(package_path)}.json"):
        stale.unlink()


def _ranges(lines: Iterable[int]) -> str:
    """*lines* as the line format's range string: ``1-12,15``."""
    ordered = sorted(set(lines))
    found: list[str] = []
    index = 0
    while index < len(ordered):
        start = end = ordered[index]
        while index + 1 < len(ordered) and ordered[index + 1] == end + 1:
            index += 1
            end = ordered[index]
        found.append(str(start) if start == end else f"{start}-{end}")
        index += 1
    return ",".join(found)


def _digest(content: bytes) -> str:
    """The git blob id of *content* as text, the line format's ``digest``.

    Each CRLF counts as LF: Git for Windows checks text out with CRLF,
    and a file measured there has the digest of the same file measured
    on Linux.
    """
    text = content.replace(b"\r\n", b"\n")
    header = f"blob {len(text)}\0".encode("ascii")
    return hashlib.sha1(header + text, usedforsecurity=False).hexdigest()
