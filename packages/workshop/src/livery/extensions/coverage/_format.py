"""The line format, version 2: what one measurer read of one unit, file by file.

A part holds what one measurer read for one unit on one machine: a
package's suite, or the whole data a run left. Every measurer reduces
to it, whatever language it measures: coverage.py's arcs, gcov's
counts, llvm-cov's regions and a Cobertura report each become a part,
and the coverage pages draw parts alone. A part is JSON:

```json
{"format": 2, "unit": "packages/workshop",
 "measurer": {"name": "coverage.py", "version": "7.16.2",
              "runtime": "CPython 3.14", "counts": "presence",
              "branches": "targets", "regions": ["function", "class"],
              "judges": ["lines", "branches"], "columns": false},
 "contexts": ["", "packages/workshop/tests/test_graph.py::test_walk"],
 "files": {"packages/workshop/src/livery/workshop/_graph.py": {
   "digest": "<the file's git blob id>",
   "statements": "1-12,15,17-40", "hits": "1-9,15,17-30",
   "excluded": "41-48", "spans": [[20, 23]],
   "branches": [[12, 13, 0, 1, "line 12 didn't jump to line 13 ..."],
                [12, 15, 1, 0, "line 12 didn't jump to line 15 ..."]],
   "regions": [["function", "Graph.walk", 41, "42-60"]],
   "reached": {"sets": ["AAE="], "lines": [[9, 0]]}}}}
```

A file is named by its path from the workspace root with forward
slashes, whichever platform measured it. A set of lines is a range
string, ``1-12,15,17-40``. Per file:

- ``digest``: the git blob id of the content measured, read as text:
  its bytes with each CRLF as LF, the blob git stores for a text file,
  so a checkout with CRLF line endings and one with LF give one
  digest. Empty when the measurer could not read the file.
- ``statements``: the lines that can run. ``hits``: the statements that
  ran, for a measurer whose ``counts`` is ``presence``; ``counts``:
  ``[line, runs]`` pairs for one whose ``counts`` is ``counts``, a
  statement left out having run no time.
- ``excluded``: the lines the source excludes from measuring.
- ``spans``: ``[first, last]`` for each statement written over lines.
- ``branches``: ``[from, to, index, taken, why?]``. ``to`` is the line
  the branch jumps to, a negative number for the exit of the function
  that starts on that line, or null for a measurer that numbers a
  line's branches instead; ``index`` is the branch's place among its
  line's; ``taken`` how many times it ran; ``why`` the sentence that
  says why the branch would not run, which a page shows while it never
  did, or a tag: ``waived`` for a branch the source waives, counted as
  taken, ``throw`` for the path an exception takes. The sentence
  depends on the source alone, so every reading of one content gives
  a branch the same one.
- ``branch-counts``: ``[line, branches, taken]`` for a measurer that
  counts a line's branches without naming them.
- ``regions``: ``[kind, name, start, lines, calls?]``, a function or a
  class: its first line, the lines of its body, and how many times it
  was called when the measurer counts calls.
- ``ranges``: ``[start line, start column, end line, end column,
  count]`` for a measurer whose ``columns`` is true: each stretch of
  code it counts on its own, such as the two arms of an ``if`` written
  on one line, and how many times it ran. A column counts bytes from
  1, and the end column is the one after the stretch's last. A
  measurer without columns leaves ``ranges`` out.
- ``reached``: the contexts that reached each line, only where the
  measurer recorded them. ``sets`` are sets of indexes into the part's
  ``contexts``, each sorted, delta-coded as unsigned LEB128 and
  base64-encoded; ``lines`` pairs a line with the index of its set.

A part names each context once, and its ``contexts`` are its
numbering: a set holds a context by its place in the list. A record
keeps a unit's numbering from one reading to the next (`numbering`): a
context keeps its index whether or not the next reading names it
again, and a context the record has not numbered yet takes the next
index. Two readings of a unit a run apart then keep a line's set of
tests byte for byte wherever the same tests reached it, and git can
store the second as a small difference from the first; a numbering
sorted afresh would move the index of every context after a new
test's. `renumbered` moves a part
to another numbering.

A record keeps each part in its stored form, which `stored` writes and
`from_stored` reads back. A file's fixed facts, what its content and
its measurer decide whatever ran, move to a table of their own, named
by the git blob id of the table's JSON (`facts_name`) and kept once
however many parts name it. The file keeps its digest, that name, and
what ran:

```json
{"digest": "<the file's git blob id>", "facts": "<the facts' name>",
 "hits": "1-9,15,17-30", "taken": "1,3-5",
 "reached": {"sets": ["AAE="], "lines": [[9, 0]]}}
```

The facts hold ``statements``, ``excluded`` and ``spans`` as a part
holds them, and ``branches``, ``branch-counts``, ``regions`` and
``ranges`` without the number each item ends in: a branch's ``taken``,
a count's ``taken``, a region's ``calls``, a range's ``count``. What
ran is ``hits`` or ``counts`` and ``reached`` as a part holds them,
and each item of the facts' lists by its place in the list, counted
from 1: ``taken``, the branches that ran, a range string of places for
a measurer that reads presence and ``[place, taken]`` pairs for one
that counts; ``counted``, ``[place, taken]`` for each count of a
line's branches with any taken; ``calls``, ``[place, calls]`` for each
region whose calls are counted; and ``range-counts``, ``[place,
count]`` for each range that ran.

[livery.extensions.coverage._format.decode][] reads a part and refuses
one off the format, naming where it came from and the key;
[livery.extensions.coverage._format.encode][] writes one.
[livery.extensions.coverage._format.merge][] combines parts across
units and legs, [livery.extensions.coverage._format.numbers][] counts a
file as coverage.py counts it, and
[livery.extensions.coverage._format.region_rows][] narrows a file to
its functions or classes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

#: The version of the line format this module reads and writes. A part
#: of another version is named and refused, never guessed at.
FORMAT = 2

#: A measurer that reads whether a line ran, and one that reads how
#: many times.
PRESENCE = "presence"
COUNTS = "counts"

#: How a measurer names a line's branches: by the line each one jumps
#: to, by its index among the line's branches, or not at all.
TARGETS = "targets"
INDEXES = "indexes"
NO_BRANCHES = "none"

#: What a measurer's figure judges: its statements, and its branches
#: where it names them well enough to judge.
LINES = "lines"
BRANCHES = "branches"

#: The tags a branch carries in place of a sentence: a branch the
#: source waives, which counts as taken, and the path an exception
#: takes.
WAIVED = "waived"
THROW = "throw"

#: Line numbers, and a path to a key inside a part.
Lines = frozenset[int]
KeyPath = tuple[str | int, ...]


class PartError(ValueError):
    """A part off the line format: the message names its source and the key."""


class OtherVersion(PartError):
    """A part of another format version, which this reader does not read.

    Attributes:
        version: The version the part names.
    """

    def __init__(self, source: str, version: object) -> None:
        """Name *source* and the *version* it holds."""
        self.version = version
        super().__init__(
            f"{source}: a part of format {version!r}; this workshop reads"
            f" format {FORMAT}"
        )


@dataclass(frozen=True)
class Measurer:
    """Who read a part, and what the reading holds.

    Attributes:
        name: The measurer: ``coverage.py``, ``gcov``.
        version: Its version, ``7.16.2``; empty when the reading does
            not say.
        runtime: What it ran on, ``CPython 3.14``; empty for none.
        counts: ``presence`` when it reads whether a line ran,
            ``counts`` when it reads how many times.
        branches: ``targets`` when it names the line each branch jumps
            to, ``indexes`` when it numbers a line's branches, ``none``
            when it reads no branch by name.
        regions: The kinds of region it names: ``function``, ``class``.
        judges: What its figure judges: ``lines``, and ``branches`` as
            well for coverage.py.
        columns: Whether it reads columns, so its readings hold
            ranges: llvm-cov does; coverage.py and gcov do not.
    """

    name: str
    version: str = ""
    runtime: str = ""
    counts: str = PRESENCE
    branches: str = NO_BRANCHES
    regions: tuple[str, ...] = ()
    judges: tuple[str, ...] = (LINES,)
    columns: bool = False

    @property
    def identity(self) -> tuple[str, str, str]:
        """What two readings of one file share to merge: name, version, runtime minor.

        Two Python minors read one source's statements and branches
        apart, so a reading under 3.11 and one under 3.14 never merge.
        """
        family, _, version = self.runtime.partition(" ")
        minor = ".".join(version.split(".")[:2])
        return (self.name, self.version, f"{family} {minor}".strip())

    @property
    def label(self) -> str:
        """The measurer as a page names it: ``coverage.py 7.16.2 on CPython 3.14``."""
        named = f"{self.name} {self.version}".strip()
        return f"{named} on {self.runtime}" if self.runtime else named


@dataclass(frozen=True)
class Branch:
    """One branch of a line.

    Attributes:
        source: The line the branch leaves.
        target: The line it jumps to, a negative number for the exit of
            the function that starts on that line, or None when the
            measurer numbers branches instead.
        index: Its place among its line's branches, from 0.
        taken: How many times it ran.
        why: The sentence that says why it would not run, shown while
            it never did, or a tag: ``waived`` or ``throw``.
    """

    source: int
    target: int | None
    index: int
    taken: int
    why: str = ""


@dataclass(frozen=True)
class Region:
    """A function or a class.

    Attributes:
        kind: ``function`` or ``class``.
        name: Its qualified name: ``Graph.walk``.
        start: The line it starts on, its ``def`` or ``class`` line.
        lines: The lines of its body; a nested function's are its own.
        calls: How many times it was called, when the measurer counts
            calls.
    """

    kind: str
    name: str
    start: int
    lines: Lines
    calls: int | None = None


@dataclass(frozen=True)
class Range:
    """A stretch of code a measurer that reads columns counts on its own.

    A column counts bytes from 1, and the end column is the one after
    the stretch's last: ``Range(6, 21, 6, 37, 0)`` is the 16 bytes of
    line 6 from column 21, which never ran.

    Attributes:
        start_line: The line it starts on.
        start_column: The column it starts at.
        end_line: The line it ends on.
        end_column: The column after its last.
        count: How many times it ran.
    """

    start_line: int
    start_column: int
    end_line: int
    end_column: int
    count: int = 0

    @property
    def extent(self) -> tuple[int, int, int, int]:
        """Where it is: its start line and column, then its end line and column."""
        return (self.start_line, self.start_column, self.end_line, self.end_column)

    @property
    def shown(self) -> str:
        """Where it is as a refusal names it: ``6:21-6:37``."""
        return (
            f"{self.start_line}:{self.start_column}-{self.end_line}:{self.end_column}"
        )


@dataclass(frozen=True)
class Entry:
    """What a measurer read of one file.

    Attributes:
        digest: The git blob id of the content measured, its CRLF line
            endings read as LF; empty when the measurer could not read
            the file.
        statements: The lines that can run.
        counts: How many times each statement ran, the ones that ran
            alone; a presence measurer's are 1.
        excluded: The lines the source excludes from measuring.
        spans: Each statement written over several lines, its first and
            last line.
        branches: The branches the measurer names, in line order.
        branch_counts: For a measurer that counts a line's branches
            without naming them: the line, its branches, and how many
            of them ran.
        regions: The functions and classes.
        reached: The contexts that reached each line, as indexes into
            the part's contexts; empty where none were recorded.
        ranges: The stretches of code a measurer that reads columns
            counts on its own, in the order of where they are.
    """

    digest: str
    statements: Lines = frozenset()
    counts: Mapping[int, int] = field(default_factory=dict)
    excluded: Lines = frozenset()
    spans: tuple[tuple[int, int], ...] = ()
    branches: tuple[Branch, ...] = ()
    branch_counts: tuple[tuple[int, int, int], ...] = ()
    regions: tuple[Region, ...] = ()
    reached: Mapping[int, Lines] = field(default_factory=dict)
    ranges: tuple[Range, ...] = ()

    @property
    def executed(self) -> Lines:
        """The statements that ran."""
        return frozenset(
            line for line in self.statements if self.counts.get(line, 0) > 0
        )

    @property
    def missing(self) -> Lines:
        """The statements that never ran."""
        return self.statements - self.executed

    @property
    def partial(self) -> Lines:
        """The lines that ran with a branch, or some of their code, that never did."""
        missing = self.missing
        found = {
            branch.source
            for branch in self.branches
            if branch.taken == 0 and branch.source not in missing
        }
        found.update(
            line
            for line, total, taken in self.branch_counts
            if taken < total and line not in missing
        )
        if self.ranges:
            executed = self.executed
            found.update(line for line in unrun_columns(self) if line in executed)
        return frozenset(found)


@dataclass(frozen=True)
class Part:
    """One measurer's reading of one unit.

    Attributes:
        unit: The unit read: a suite's path, or ``.`` for the whole data
            a run left.
        measurer: Who read it.
        files: What it read of each file, by the file's path from the
            workspace root with forward slashes.
        contexts: The contexts a line's ``reached`` indexes name; the
            empty string is the context outside every test.
    """

    unit: str
    measurer: Measurer
    files: Mapping[str, Entry]
    contexts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Numbers:
    """A file's or a region's numbers as coverage.py counts them; they add up.

    Attributes:
        files: How many files they count.
        statements: The statements.
        executed: The statements that ran.
        excluded: The excluded lines.
        branches: The branches.
        taken: The branches that ran, a waived one among them.
        partial: The branches that never ran from a line that did.
        covered: What the figure counts as covered: the statements that
            ran, and the branches taken where the measurer judges them.
        coverable: What the figure counts in all.
    """

    files: int = 0
    statements: int = 0
    executed: int = 0
    excluded: int = 0
    branches: int = 0
    taken: int = 0
    partial: int = 0
    covered: int = 0
    coverable: int = 0

    @property
    def missing(self) -> int:
        """The statements that never ran."""
        return self.statements - self.executed

    @property
    def percent(self) -> float:
        """The figure: what is covered over what can be, 100 when nothing can."""
        return _percent(self.covered, self.coverable)

    @property
    def statements_percent(self) -> float:
        """The statements that ran over the statements, 100 when there are none."""
        return _percent(self.executed, self.statements)

    @property
    def branches_percent(self) -> float:
        """The branches taken over the branches, 100 when there are none."""
        return _percent(self.taken, self.branches)

    def __add__(self, other: Numbers) -> Numbers:
        """The numbers of both, added field by field."""
        return Numbers(
            self.files + other.files,
            self.statements + other.statements,
            self.executed + other.executed,
            self.excluded + other.excluded,
            self.branches + other.branches,
            self.taken + other.taken,
            self.partial + other.partial,
            self.covered + other.covered,
            self.coverable + other.coverable,
        )


def _percent(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 100.0


def shown(percent: float, precision: int = 0) -> str:
    """*percent* as coverage.py prints it, without the percent sign.

    Rounded to *precision* decimals, except that only nothing is 0 and
    only everything is 100: 99.7 at precision 0 prints 99, and 0.2
    prints 1.
    """
    near = 1.0 / 10**precision
    if 0 < percent < near:
        percent = near
    elif 100.0 - near < percent < 100:
        percent = 100.0 - near
    else:
        percent = round(percent, precision)
    return f"{percent:.{precision}f}"


def numbers(entry: Entry, measurer: Measurer) -> Numbers:
    """*entry*'s numbers, as coverage.py counts a file.

    A branch counts as taken when it ran or the source waives it; a
    partial branch is one never taken from a line that ran. The figure
    counts the branches only where the measurer judges them: a native
    measurer's figure is its lines alone, and its branches reach the
    pages, never the floors.
    """
    executed = entry.executed
    missing = entry.statements - executed
    branches = len(entry.branches) + sum(total for _, total, _ in entry.branch_counts)
    taken = sum(1 for branch in entry.branches if branch.taken > 0) + sum(
        min(ran, total) for _, total, ran in entry.branch_counts
    )
    partial = sum(
        1
        for branch in entry.branches
        if branch.taken == 0 and branch.source not in missing
    ) + sum(
        total - ran
        for line, total, ran in entry.branch_counts
        if ran < total and line not in missing
    )
    judged = BRANCHES in measurer.judges
    return Numbers(
        files=1,
        statements=len(entry.statements),
        executed=len(executed),
        excluded=len(entry.excluded),
        branches=branches,
        taken=taken,
        partial=partial,
        covered=len(executed) + (taken if judged else 0),
        coverable=len(entry.statements) + (branches if judged else 0),
    )


def narrowed(entry: Entry, keep: Callable[[int], bool]) -> Entry:
    """*entry* narrowed to the lines *keep* holds, as coverage.py narrows a region.

    A branch belongs to the line it leaves and a range to the line it
    starts on, so a region keeps the branches its own lines leave and
    the ranges they start; the regions themselves go.
    """
    return Entry(
        digest=entry.digest,
        statements=frozenset(line for line in entry.statements if keep(line)),
        counts={line: ran for line, ran in entry.counts.items() if keep(line)},
        excluded=frozenset(line for line in entry.excluded if keep(line)),
        spans=tuple(span for span in entry.spans if keep(span[0])),
        branches=tuple(branch for branch in entry.branches if keep(branch.source)),
        branch_counts=tuple(item for item in entry.branch_counts if keep(item[0])),
        reached={line: held for line, held in entry.reached.items() if keep(line)},
        ranges=tuple(item for item in entry.ranges if keep(item.start_line)),
    )


def region_rows(entry: Entry, kind: str) -> list[tuple[Region | None, Entry]]:
    """*entry* narrowed to each region of *kind*, then to the lines outside them all.

    The regions in the order the measurer names them, then ``None``
    with the lines outside every region of *kind*: a ``def`` or
    ``class`` line counts there, since a region holds its body's lines
    alone.
    """
    regions = [region for region in entry.regions if region.kind == kind]
    inside: set[int] = set()
    for region in regions:
        inside.update(region.lines)
    rows: list[tuple[Region | None, Entry]] = [
        (region, narrowed(entry, region.lines.__contains__)) for region in regions
    ]
    rows.append((None, narrowed(entry, lambda line: line not in inside)))
    return rows


# --- encoding -------------------------------------------------------------------


def ranges(lines: Iterable[int]) -> str:
    """*lines* as a range string: ``1-12,15,17-40``; empty for none."""
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


def parse_ranges(text: str) -> Lines:
    """The lines a range string names.

    Raises:
        ValueError: when a piece is not a line or a range of lines,
            naming the piece.
    """
    found: set[int] = set()
    for piece in text.split(","):
        if not piece and text == "":
            continue
        first, dash, last = piece.partition("-")
        if not first.isdigit() or (dash and not last.isdigit()):
            raise ValueError(f"{piece!r} is not a line or a range of lines")
        start, end = int(first), int(last) if dash else int(first)
        if start < 1 or end < start:
            raise ValueError(f"{piece!r} is not a range of lines from 1 up")
        found.update(range(start, end + 1))
    return frozenset(found)


def pack_set(indexes: Iterable[int]) -> str:
    """A set of indexes as the format keeps it: sorted, delta-coded, LEB128, base64."""
    packed = bytearray()
    previous = -1
    for value in sorted(set(indexes)):
        delta = value - previous - 1
        while True:
            low = delta & 0x7F
            delta >>= 7
            packed.append(low | (0x80 if delta else 0))
            if not delta:
                break
        previous = value
    return base64.b64encode(bytes(packed)).decode("ascii")


def unpack_set(text: str) -> Lines:
    """The indexes a packed set holds.

    Raises:
        ValueError: when *text* is not base64, or its bytes end inside
            a number.
    """
    try:
        packed = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as error:
        raise ValueError(f"{text!r} is not base64 ({error})") from None
    found: set[int] = set()
    previous = -1
    delta = shift = 0
    for index, byte in enumerate(packed):
        delta |= (byte & 0x7F) << shift
        shift += 7
        if byte & 0x80:
            if index == len(packed) - 1:
                raise ValueError(f"{text!r} ends inside a number")
            continue
        previous += delta + 1
        found.add(previous)
        delta = shift = 0
    return frozenset(found)


def digest(content: bytes) -> str:
    """The git blob id of *content* as text: what a part's ``digest`` names.

    Each CRLF in *content* counts as LF, so the digest is the blob id
    git stores for a text file whatever line endings a checkout gives
    it: Git for Windows checks text out with CRLF, and a file measured
    there has the digest of the same file measured on Linux.
    """
    text = content.replace(b"\r\n", b"\n")
    header = f"blob {len(text)}\0".encode("ascii")
    return hashlib.sha1(header + text, usedforsecurity=False).hexdigest()


def encode(part: Part) -> dict[str, Any]:
    """*part* as JSON holds it, every list in its one order."""
    measurer = part.measurer
    files: dict[str, Any] = {}
    for name in sorted(part.files):
        files[name] = _encoded_entry(part.files[name], measurer)
    return {
        "format": FORMAT,
        "unit": part.unit,
        "measurer": {
            "name": measurer.name,
            "version": measurer.version,
            "runtime": measurer.runtime,
            "counts": measurer.counts,
            "branches": measurer.branches,
            "regions": list(measurer.regions),
            "judges": list(measurer.judges),
            "columns": measurer.columns,
        },
        "contexts": list(part.contexts),
        "files": files,
    }


def _encoded_entry(entry: Entry, measurer: Measurer) -> dict[str, Any]:
    found: dict[str, Any] = {"digest": entry.digest}
    if entry.statements:
        found["statements"] = ranges(entry.statements)
    ran = sorted((line, runs) for line, runs in entry.counts.items() if runs > 0)
    if ran and measurer.counts == PRESENCE:
        found["hits"] = ranges(line for line, _ in ran)
    elif ran:
        found["counts"] = [[line, runs] for line, runs in ran]
    if entry.excluded:
        found["excluded"] = ranges(entry.excluded)
    if entry.spans:
        found["spans"] = [list(span) for span in sorted(entry.spans)]
    if entry.branches:
        found["branches"] = [
            [branch.source, branch.target, branch.index, branch.taken]
            + ([branch.why] if branch.why else [])
            for branch in sorted(entry.branches, key=_branch_order)
        ]
    if entry.branch_counts:
        found["branch-counts"] = [list(item) for item in sorted(entry.branch_counts)]
    if entry.regions:
        found["regions"] = [
            [region.kind, region.name, region.start, ranges(region.lines)]
            + ([region.calls] if region.calls is not None else [])
            for region in entry.regions
        ]
    if entry.ranges:
        found["ranges"] = [
            [*item.extent, item.count]
            for item in sorted(entry.ranges, key=_range_order)
        ]
    if entry.reached:
        sets: dict[Lines, int] = {}
        lines: list[list[int]] = []
        for line in sorted(entry.reached):
            held = entry.reached[line]
            lines.append([line, sets.setdefault(held, len(sets))])
        found["reached"] = {"sets": [pack_set(held) for held in sets], "lines": lines}
    return found


def _branch_order(branch: Branch) -> tuple[int, int]:
    return (branch.source, branch.index)


def _range_order(item: Range) -> tuple[int, int, int, int]:
    return item.extent


# --- decoding -------------------------------------------------------------------


def decode(raw: object, *, source: str) -> Part:
    """Read *raw*, a part as JSON loads it, refusing one off the format.

    *source* names where the part came from in every refusal, beside
    the key at fault: ``files["a.py"].branches[2]``.

    Raises:
        OtherVersion: when the part is of another format version.
        PartError: when the part is off the format.
    """
    reader = _Reader(source)
    table = reader.table(raw, ())
    version = table.get("format")
    if version != FORMAT:
        raise OtherVersion(source, version)
    reader.known(table, (), ("format", "unit", "measurer", "contexts", "files"))
    unit = reader.text(table.get("unit"), ("unit",), empty=False)
    measurer = reader.measurer(table.get("measurer"), ("measurer",))
    contexts = reader.contexts(table.get("contexts", []), ("contexts",))
    files = {
        reader.file_name(name): reader.entry(
            value, ("files", name), measurer, len(contexts)
        )
        for name, value in reader.table(table.get("files"), ("files",)).items()
    }
    return Part(unit, measurer, files, tuple(contexts))


def _key(path: KeyPath) -> str:
    """*path* as the refusal names it: ``files["a.py"].branches[2]``."""
    shown_path = ""
    for part in path:
        if isinstance(part, int):
            shown_path += f"[{part}]"
        elif shown_path and not part.replace("-", "").isalnum():
            shown_path += f"[{json.dumps(part)}]"
        else:
            shown_path += f".{part}" if shown_path else part
    return shown_path or "the part"


class _Reader:
    """Read a part's values, refusing one off the format at its key."""

    def __init__(self, source: str) -> None:
        self.source = source

    def refuse(self, path: KeyPath, problem: str) -> PartError:
        return PartError(f"{self.source}: {_key(path)}: {problem}")

    def table(self, raw: object, path: KeyPath) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise self.refuse(path, f"is {raw!r}, and a table is wanted")
        return cast("dict[str, Any]", raw)

    def known(
        self, table: Mapping[str, Any], path: KeyPath, keys: Sequence[str]
    ) -> None:
        unknown = sorted(set(table) - set(keys))
        if unknown:
            raise self.refuse(
                path,
                f"holds {', '.join(unknown)}, which format {FORMAT} has no key"
                f" for; its keys are {', '.join(keys)}",
            )

    def file_name(self, name: str) -> str:
        """*name* when it is a path from the workspace root with forward slashes."""
        parts = name.split("/")
        if (
            "\\" in name
            or name.startswith("/")
            or ":" in parts[0]
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise self.refuse(
                ("files", name),
                "is no path from the workspace root with forward slashes",
            )
        return name

    def text(self, raw: object, path: KeyPath, *, empty: bool = True) -> str:
        if not isinstance(raw, str) or not (empty or raw):
            wanted = "text" if empty else "text that is not empty"
            raise self.refuse(path, f"is {raw!r}, and {wanted} is wanted")
        return raw

    def texts(self, raw: object, path: KeyPath) -> list[str]:
        items = self.listed(raw, path)
        return [self.text(item, (*path, index)) for index, item in enumerate(items)]

    def contexts(self, raw: object, path: KeyPath) -> list[str]:
        """A numbering: texts, each named once, as a set holds a context by place."""
        names = self.texts(raw, path)
        seen: set[str] = set()
        for index, name in enumerate(names):
            if name in seen:
                raise self.refuse(
                    (*path, index), f"repeats {name!r}; a numbering names each once"
                )
            seen.add(name)
        return names

    def listed(self, raw: object, path: KeyPath) -> list[Any]:
        if not isinstance(raw, list):
            raise self.refuse(path, f"is {raw!r}, and a list is wanted")
        return raw

    def number(self, raw: object, path: KeyPath, *, least: int) -> int:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < least:
            raise self.refuse(
                path, f"is {raw!r}, and a whole number from {least} is wanted"
            )
        return raw

    def lines(self, raw: object, path: KeyPath) -> Lines:
        text = self.text(raw, path)
        try:
            return parse_ranges(text)
        except ValueError as error:
            raise self.refuse(path, str(error)) from None

    def choice(self, raw: object, path: KeyPath, values: Sequence[str]) -> str:
        if raw not in values:
            listed = ", ".join(values)
            raise self.refuse(path, f"is {raw!r}, and one of {listed} is wanted")
        return cast("str", raw)

    def boolean(self, raw: object, path: KeyPath) -> bool:
        if not isinstance(raw, bool):
            raise self.refuse(path, f"is {raw!r}, and true or false is wanted")
        return raw

    def measurer(self, raw: object, path: KeyPath) -> Measurer:
        table = self.table(raw, path)
        keys = (
            "name",
            "version",
            "runtime",
            "counts",
            "branches",
            "regions",
            "judges",
            "columns",
        )
        self.known(table, path, keys)
        judges = self.texts(table.get("judges", [LINES]), (*path, "judges"))
        for index, judged in enumerate(judges):
            self.choice(judged, (*path, "judges", index), (LINES, BRANCHES))
        if LINES not in judges:
            raise self.refuse(
                (*path, "judges"), "leaves out lines, which every figure judges"
            )
        return Measurer(
            name=self.text(table.get("name"), (*path, "name"), empty=False),
            version=self.text(table.get("version", ""), (*path, "version")),
            runtime=self.text(table.get("runtime", ""), (*path, "runtime")),
            counts=self.choice(
                table.get("counts"), (*path, "counts"), (PRESENCE, COUNTS)
            ),
            branches=self.choice(
                table.get("branches"),
                (*path, "branches"),
                (TARGETS, INDEXES, NO_BRANCHES),
            ),
            regions=tuple(self.texts(table.get("regions", []), (*path, "regions"))),
            judges=tuple(judges),
            columns=self.boolean(table.get("columns", False), (*path, "columns")),
        )

    def entry(
        self, raw: object, path: KeyPath, measurer: Measurer, contexts: int
    ) -> Entry:
        table = self.table(raw, path)
        ran = "hits" if measurer.counts == PRESENCE else "counts"
        keys = ("digest", "statements", ran, "excluded", "spans", "branches")
        self.known(
            table, path, (*keys, "branch-counts", "regions", "ranges", "reached")
        )
        statements = self.lines(table.get("statements", ""), (*path, "statements"))
        if measurer.counts == PRESENCE:
            hits = self.lines(table.get("hits", ""), (*path, "hits"))
            counts = dict.fromkeys(hits, 1)
        else:
            counts = self.counts(table.get("counts", []), (*path, "counts"))
        strays = sorted(set(counts) - statements)
        if strays:
            raise self.refuse(
                (*path, ran), f"names lines that are no statements: {ranges(strays)}"
            )
        return Entry(
            digest=self.text(table.get("digest"), (*path, "digest")),
            statements=statements,
            counts=counts,
            excluded=self.lines(table.get("excluded", ""), (*path, "excluded")),
            spans=self.spans(table.get("spans", []), (*path, "spans")),
            branches=self.branches(
                table.get("branches", []), (*path, "branches"), measurer
            ),
            branch_counts=self.branch_counts(
                table.get("branch-counts", []), (*path, "branch-counts")
            ),
            regions=self.regions(
                table.get("regions", []), (*path, "regions"), measurer
            ),
            reached=self.reached(table.get("reached"), (*path, "reached"), contexts),
            ranges=self.ranges(table.get("ranges", []), (*path, "ranges"), measurer),
        )

    def counts(self, raw: object, path: KeyPath) -> dict[int, int]:
        found: dict[int, int] = {}
        for index, item in enumerate(self.listed(raw, path)):
            pair = self.listed(item, (*path, index))
            if len(pair) != 2:
                raise self.refuse((*path, index), "is no [line, runs] pair")
            line = self.number(pair[0], (*path, index, 0), least=1)
            runs = self.number(pair[1], (*path, index, 1), least=0)
            if runs:
                found[line] = runs
        return found

    def spans(self, raw: object, path: KeyPath) -> tuple[tuple[int, int], ...]:
        found: list[tuple[int, int]] = []
        for index, item in enumerate(self.listed(raw, path)):
            pair = self.listed(item, (*path, index))
            if len(pair) != 2:
                raise self.refuse((*path, index), "is no [first, last] pair")
            first = self.number(pair[0], (*path, index, 0), least=1)
            last = self.number(pair[1], (*path, index, 1), least=first)
            found.append((first, last))
        return tuple(found)

    def branches(
        self, raw: object, path: KeyPath, measurer: Measurer
    ) -> tuple[Branch, ...]:
        items = self.listed(raw, path)
        if items and measurer.branches == NO_BRANCHES:
            raise self.refuse(path, f"names branches, and {measurer.name} reads none")
        found: list[Branch] = []
        seen: set[tuple[int, int]] = set()
        for index, item in enumerate(items):
            at = (*path, index)
            fields = self.listed(item, at)
            if len(fields) not in (4, 5):
                raise self.refuse(at, "is no [from, to, index, taken, why?] branch")
            source = self.number(fields[0], (*at, 0), least=1)
            target = fields[1]
            if measurer.branches == INDEXES and target is not None:
                raise self.refuse(
                    (*at, 1), f"is {target!r}, and {measurer.name} numbers branches"
                )
            if measurer.branches == TARGETS and (
                isinstance(target, bool) or not isinstance(target, int) or target == 0
            ):
                raise self.refuse(
                    (*at, 1), f"is {target!r}, and a line or a negative exit is wanted"
                )
            place = self.number(fields[2], (*at, 2), least=0)
            if (source, place) in seen:
                raise self.refuse((*at, 2), f"repeats branch {place} of line {source}")
            seen.add((source, place))
            found.append(
                Branch(
                    source=source,
                    target=cast("int | None", target),
                    index=place,
                    taken=self.number(fields[3], (*at, 3), least=0),
                    why=self.text(fields[4], (*at, 4)) if len(fields) == 5 else "",
                )
            )
        return tuple(sorted(found, key=_branch_order))

    def branch_counts(
        self, raw: object, path: KeyPath
    ) -> tuple[tuple[int, int, int], ...]:
        found: list[tuple[int, int, int]] = []
        for index, item in enumerate(self.listed(raw, path)):
            at = (*path, index)
            fields = self.listed(item, at)
            if len(fields) != 3:
                raise self.refuse(at, "is no [line, branches, taken] count")
            line = self.number(fields[0], (*at, 0), least=1)
            total = self.number(fields[1], (*at, 1), least=1)
            ran = self.number(fields[2], (*at, 2), least=0)
            if ran > total:
                raise self.refuse((*at, 2), f"is {ran}, more than the line's {total}")
            found.append((line, total, ran))
        return tuple(found)

    def regions(
        self, raw: object, path: KeyPath, measurer: Measurer
    ) -> tuple[Region, ...]:
        found: list[Region] = []
        for index, item in enumerate(self.listed(raw, path)):
            at = (*path, index)
            fields = self.listed(item, at)
            if len(fields) not in (4, 5):
                raise self.refuse(at, "is no [kind, name, start, lines, calls?] region")
            kind = self.text(fields[0], (*at, 0))
            if kind not in measurer.regions:
                named = ", ".join(measurer.regions) or "none"
                raise self.refuse(
                    (*at, 0), f"is {kind!r}, and {measurer.name} names {named}"
                )
            found.append(
                Region(
                    kind=kind,
                    name=self.text(fields[1], (*at, 1), empty=False),
                    start=self.number(fields[2], (*at, 2), least=1),
                    lines=self.lines(fields[3], (*at, 3)),
                    calls=self.number(fields[4], (*at, 4), least=0)
                    if len(fields) == 5
                    else None,
                )
            )
        return tuple(found)

    def ranges(
        self, raw: object, path: KeyPath, measurer: Measurer
    ) -> tuple[Range, ...]:
        items = self.listed(raw, path)
        if items and not measurer.columns:
            raise self.refuse(
                path, f"names ranges, and {measurer.name} reads no columns"
            )
        found: list[Range] = []
        seen: set[tuple[int, int, int, int]] = set()
        for index, item in enumerate(items):
            at = (*path, index)
            fields = self.listed(item, at)
            if len(fields) != 5:
                raise self.refuse(
                    at,
                    "is no [start line, start column, end line, end column, count]"
                    " range",
                )
            start_line = self.number(fields[0], (*at, 0), least=1)
            start_column = self.number(fields[1], (*at, 1), least=1)
            end_line = self.number(fields[2], (*at, 2), least=start_line)
            end_column = self.number(
                fields[3], (*at, 3), least=start_column if end_line == start_line else 1
            )
            found_range = Range(
                start_line,
                start_column,
                end_line,
                end_column,
                self.number(fields[4], (*at, 4), least=0),
            )
            if found_range.extent in seen:
                raise self.refuse(at, f"repeats the range {found_range.shown}")
            seen.add(found_range.extent)
            found.append(found_range)
        return tuple(sorted(found, key=_range_order))

    def sets(
        self, table: Mapping[str, Any], path: KeyPath, contexts: int
    ) -> list[Lines]:
        """The sets of the ``reached`` *table*, each naming indexes below *contexts*."""
        found: list[Lines] = []
        for index, text in enumerate(
            self.texts(table.get("sets", []), (*path, "sets"))
        ):
            try:
                held = unpack_set(text)
            except ValueError as error:
                raise self.refuse((*path, "sets", index), str(error)) from None
            beyond = sorted(value for value in held if value >= contexts)
            if beyond:
                raise self.refuse(
                    (*path, "sets", index),
                    f"names context {beyond[0]}, and the part names {contexts}",
                )
            found.append(held)
        return found

    def reached(self, raw: object, path: KeyPath, contexts: int) -> dict[int, Lines]:
        if raw is None:
            return {}
        table = self.table(raw, path)
        self.known(table, path, ("sets", "lines"))
        sets = self.sets(table, path, contexts)
        found: dict[int, Lines] = {}
        for index, item in enumerate(
            self.listed(table.get("lines", []), (*path, "lines"))
        ):
            at = (*path, "lines", index)
            pair = self.listed(item, at)
            if len(pair) != 2:
                raise self.refuse(at, "is no [line, set] pair")
            line = self.number(pair[0], (*at, 0), least=1)
            held_at = self.number(pair[1], (*at, 1), least=0)
            if held_at >= len(sets):
                raise self.refuse(
                    (*at, 1), f"is {held_at}, and the part has {len(sets)} sets"
                )
            found[line] = sets[held_at]
        return found


# --- the numbering of contexts ----------------------------------------------------


def numbering(known: Sequence[str], contexts: Iterable[str]) -> tuple[str, ...]:
    """*known*, then each of *contexts* it lacks, sorted: a numbering that only appends.

    A record numbers a unit's contexts this way at every write, *known*
    being the numbering of the unit's row before it: each context keeps
    its index, a test that is gone among them, and each new one takes
    the next.
    """
    held = set(known)
    return (*known, *sorted(set(contexts) - held))


def numbered(
    raw: Mapping[str, Any], known: Sequence[str], *, source: str
) -> dict[str, Any]:
    """*raw*, a part as JSON holds it or in its stored form, numbered to extend *known*.

    Its contexts take `numbering` of *known* and its own, so every
    context of *known* keeps its index and its own new ones follow.

    Raises:
        PartError: as `renumbered` does, naming *source*.
    """
    reader = _Reader(source)
    table = reader.table(raw, ())
    own = reader.contexts(table.get("contexts", []), ("contexts",))
    return renumbered(table, numbering(known, own), source=source)


def renumbered(
    raw: Mapping[str, Any], order: Sequence[str], *, source: str
) -> dict[str, Any]:
    """*raw*, a part as JSON or in its stored form, its contexts numbered by *order*.

    Each set of each file's ``reached`` names the contexts it named
    before, each now by its place in *order*; a context *order* adds
    names no line, and nothing else in *raw* changes. *order* names
    each context once and keeps every context of *raw*: a numbering
    only appends, so one that drops a context is refused, never
    guessed.

    Raises:
        PartError: when *order* repeats a context or drops one of
            *raw*'s, naming it; when a set names an index past *raw*'s
            contexts, naming the file and the set; and when the
            contexts or a ``reached`` are off the format. Each names
            *source*.
    """
    reader = _Reader(source)
    table = reader.table(raw, ())
    contexts = reader.contexts(table.get("contexts", []), ("contexts",))
    wanted = reader.contexts(list(order), ("numbering",))
    place = {name: index for index, name in enumerate(wanted)}
    for index, name in enumerate(contexts):
        if name not in place:
            raise reader.refuse(
                ("contexts", index),
                f"is {name!r}, and the numbering drops it; a numbering only"
                " appends, so every context keeps its index",
            )
    moved = [place[name] for name in contexts]
    files: dict[str, Any] = {}
    for name, value in reader.table(table.get("files", {}), ("files",)).items():
        entry = reader.table(value, ("files", name))
        if entry.get("reached") is None:
            files[name] = entry
            continue
        path: KeyPath = ("files", name, "reached")
        held = reader.table(entry["reached"], path)
        reader.known(held, path, ("sets", "lines"))
        sets = [
            pack_set(moved[index] for index in found)
            for found in reader.sets(held, path, len(contexts))
        ]
        files[name] = {**entry, "reached": {**held, "sets": sets}}
    return {**table, "contexts": wanted, "files": files}


# --- merging --------------------------------------------------------------------


@dataclass(frozen=True)
class Merged:
    """One file's readings, merged.

    Attributes:
        measurer: The measurer whose readings merged.
        entry: The merged reading.
        units: The units whose readings merged, in the order met.
    """

    measurer: Measurer
    entry: Entry
    units: tuple[str, ...]


@dataclass(frozen=True)
class Combined:
    """Parts merged across units and legs.

    Attributes:
        files: Each file's merged reading, by path.
        contexts: The contexts the merged readings' ``reached`` indexes
            name, sorted.
        left_out: A line for each reading left out of its file's merge:
            one taken at other content, or by another measurer.
    """

    files: Mapping[str, Merged]
    contexts: tuple[str, ...]
    left_out: tuple[str, ...]


def merge(
    parts: Iterable[Part],
    current: Mapping[str, str] | None = None,
    *,
    contexts: bool = True,
) -> Combined:
    """Merge *parts*: each file's readings at one content and by one measurer.

    *current* maps a file to its digest in the checkout. A file merges
    the readings that share its current digest and one measurer
    identity ([livery.extensions.coverage._format.Measurer.identity][]),
    the identity most readings carry winning; without a reading at the
    current digest, the digest and identity most readings share win.
    Every reading left out is named in ``left_out``.

    The merge: statements and excluded lines are the union, hits are
    or-ed and counts added, a branch is one by its line and index with
    its runs added and its sentence kept, a count of a line's branches
    keeps the most taken, a region is one by its kind, name and start
    with its calls added, a range is one by where it is with its counts
    added, and the contexts that reached a line are the union, by name.
    With *contexts* false the merge leaves the contexts out, for a
    reader that counts alone: no merged reading holds a ``reached``,
    and the result names no context.
    """
    readings: dict[str, list[tuple[Part, Entry]]] = {}
    names: set[str] = set()
    for part in parts:
        if contexts:
            # A context that reached no line, a test that is gone, keeps
            # its index in its part's numbering and is no merged context.
            used: set[int] = set()
            for entry in part.files.values():
                for held in entry.reached.values():
                    used.update(held)
            names.update(part.contexts[index] for index in used)
        for name, entry in part.files.items():
            readings.setdefault(name, []).append((part, entry))
    numbered_by = tuple(sorted(names))
    index_of = {name: index for index, name in enumerate(numbered_by)}
    files: dict[str, Merged] = {}
    left_out: list[str] = []
    for name in sorted(readings):
        chosen, others = _chosen(readings[name], (current or {}).get(name))
        kept = _reading(*chosen[0])
        left_out.extend(
            f"{name}: left out the reading of {_reading(part, entry)}; the union"
            f" keeps the one of {kept}"
            for part, entry in others
        )
        files[name] = Merged(
            measurer=chosen[0][0].measurer,
            entry=_merged(
                [
                    (
                        _reindexed(entry.reached, part.contexts, index_of)
                        if contexts
                        else {},
                        entry,
                    )
                    for part, entry in chosen
                ]
            ),
            units=tuple(dict.fromkeys(part.unit for part, _ in chosen)),
        )
    return Combined(files, numbered_by, tuple(left_out))


def _reading(part: Part, entry: Entry) -> str:
    """A reading as a left-out line names it: its unit, measurer and content."""
    content = f"content {entry.digest[:12]}" if entry.digest else "unread content"
    return f"{part.unit} by {part.measurer.label} at {content}"


def _chosen(
    readings: list[tuple[Part, Entry]], current: str | None
) -> tuple[list[tuple[Part, Entry]], list[tuple[Part, Entry]]]:
    """The readings of one file that merge, and the ones left out."""
    groups: dict[tuple[str, tuple[str, str, str]], list[tuple[Part, Entry]]] = {}
    for part, entry in readings:
        groups.setdefault((entry.digest, part.measurer.identity), []).append(
            (part, entry)
        )

    def rank(
        key: tuple[str, tuple[str, str, str]],
    ) -> tuple[bool, int, str, tuple[str, ...]]:
        return (key[0] != current, -len(groups[key]), key[0], key[1])

    best = min(groups, key=rank)
    return groups[best], [item for key in groups if key != best for item in groups[key]]


def _reindexed(
    reached: Mapping[int, Lines], contexts: Sequence[str], index_of: Mapping[str, int]
) -> dict[int, Lines]:
    """*reached*'s indexes moved from a part's contexts to the merged ones."""
    return {
        line: frozenset(index_of[contexts[index]] for index in held)
        for line, held in reached.items()
    }


def _merged(readings: list[tuple[dict[int, Lines], Entry]]) -> Entry:
    """One file's readings at one digest and by one measurer, merged."""
    statements: set[int] = set()
    excluded: set[int] = set()
    counts: dict[int, int] = {}
    spans: set[tuple[int, int]] = set()
    branches: dict[tuple[int, int], Branch] = {}
    branch_counts: dict[int, tuple[int, int]] = {}
    regions: dict[tuple[str, str, int], Region] = {}
    counted: dict[tuple[int, int, int, int], int] = {}
    reached: dict[int, set[int]] = {}
    for held, entry in readings:
        statements.update(entry.statements)
        excluded.update(entry.excluded)
        for line, runs in entry.counts.items():
            counts[line] = counts.get(line, 0) + runs
        spans.update(entry.spans)
        for branch in entry.branches:
            key = (branch.source, branch.index)
            known = branches.get(key)
            if known is None:
                branches[key] = branch
                continue
            branches[key] = Branch(
                branch.source,
                branch.target,
                branch.index,
                known.taken + branch.taken,
                known.why or branch.why,
            )
        for line, total, ran in entry.branch_counts:
            was_total, was_ran = branch_counts.get(line, (0, 0))
            branch_counts[line] = (max(was_total, total), max(was_ran, ran))
        for region in entry.regions:
            key_of = (region.kind, region.name, region.start)
            seen = regions.get(key_of)
            if seen is None:
                regions[key_of] = region
                continue
            calls = (
                None
                if seen.calls is None and region.calls is None
                else (seen.calls or 0) + (region.calls or 0)
            )
            regions[key_of] = Region(
                region.kind, region.name, region.start, seen.lines | region.lines, calls
            )
        for item in entry.ranges:
            counted[item.extent] = counted.get(item.extent, 0) + item.count
        for line, indexes in held.items():
            reached.setdefault(line, set()).update(indexes)
    return Entry(
        digest=readings[0][1].digest,
        statements=frozenset(statements),
        counts=counts,
        excluded=frozenset(excluded),
        spans=tuple(sorted(spans)),
        branches=tuple(sorted(branches.values(), key=_branch_order)),
        branch_counts=tuple(
            (line, total, ran) for line, (total, ran) in sorted(branch_counts.items())
        ),
        regions=tuple(regions.values()),
        reached={line: frozenset(indexes) for line, indexes in sorted(reached.items())},
        ranges=tuple(
            Range(*extent, count) for extent, count in sorted(counted.items())
        ),
    )


# --- ranges against their text ------------------------------------------------------


def unrun_columns(entry: Entry) -> dict[int, tuple[tuple[int, int | None], ...]]:
    """The columns of each line its ranges say never ran, as ``(start, end)`` pairs.

    A column belongs to the innermost range that holds it, the one that
    starts last, and of two that start together the one that ends
    first, as llvm-cov draws its regions; a column no range holds is
    not judged. Columns count bytes from 1, and a stretch's end is the
    column after its last, or None when it runs to the end of the line.
    A line with no such stretch is left out.
    """
    by_line: dict[int, list[Range]] = {}
    for item in entry.ranges:
        for line in range(item.start_line, item.end_line + 1):
            by_line.setdefault(line, []).append(item)
    found: dict[int, tuple[tuple[int, int | None], ...]] = {}
    for line, held in sorted(by_line.items()):
        if all(item.count for item in held):
            continue
        spans = [
            (
                item.start_column if item.start_line == line else 1,
                item.end_column if item.end_line == line else None,
                item,
            )
            for item in held
        ]
        cuts = sorted(
            {start for start, _, _ in spans}
            | {end for _, end, _ in spans if end is not None}
        )
        unrun: list[tuple[int, int | None]] = []
        for place, start in enumerate(cuts):
            end = cuts[place + 1] if place + 1 < len(cuts) else None
            holding = [
                item
                for first, last, item in spans
                if first <= start
                and (last is None or (end is not None and last >= end))
            ]
            if not holding:
                continue
            inner = min(holding, key=_innermost)
            if inner.count:
                continue
            if unrun and unrun[-1][1] == start:
                unrun[-1] = (unrun[-1][0], end)
            else:
                unrun.append((start, end))
        if unrun:
            found[line] = tuple(unrun)
    return found


def _innermost(item: Range) -> tuple[int, int, int, int]:
    """Rank a range holding a column: the one that starts last, then ends first."""
    return (-item.start_line, -item.start_column, item.end_line, item.end_column)


def check_ranges(entry: Entry, text: str, *, source: str) -> None:
    """Refuse a range of *entry* outside *text*, the content it was measured at.

    *text*'s lines are counted as the pages count them, a CRLF or a lone
    CR ending a line as LF does, and each line's columns are its bytes
    in UTF-8, the end column one past the last.

    Raises:
        PartError: when a range starts or ends beyond the file's lines,
            or beyond its line's columns, naming *source* and the range.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    widths = [len(line.encode("utf-8")) for line in lines]
    for index, item in enumerate(entry.ranges):
        problem = ""
        if item.end_line > len(widths):
            problem = f"the file has {len(widths)} lines"
        elif item.start_column > widths[item.start_line - 1] + 1:
            problem = (
                f"line {item.start_line} has {widths[item.start_line - 1]} columns"
            )
        elif item.end_column > widths[item.end_line - 1] + 1:
            problem = f"line {item.end_line} has {widths[item.end_line - 1]} columns"
        if problem:
            raise PartError(
                f"{source}: ranges[{index}]: {item.shown} lies outside the file:"
                f" {problem}"
            )


# --- the stored form ------------------------------------------------------------


def facts_name(facts: Mapping[str, Any]) -> str:
    """The name of a file's fixed facts: the git blob id of `facts_text` of them.

    A record that keeps the facts as a file of that text keeps them
    under the name git gives the file.
    """
    return digest(facts_text(facts).encode("utf-8"))


def facts_text(facts: Mapping[str, Any]) -> str:
    """*facts* as one JSON text: keys sorted, no space between items."""
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


def stored(part: Part) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """*part* in its stored form, and the fixed facts it names, by name.

    A file with no fixed facts, one the measurer could not read, names
    none. A measurer that reads presence keeps its branches that ran as
    a range string of places while none ran more than once, which its
    readings never do; ``[place, taken]`` pairs keep the rest.
    """
    full = encode(part)
    presence = part.measurer.counts == PRESENCE
    facts: dict[str, dict[str, Any]] = {}
    files: dict[str, Any] = {}
    for name, entry in full["files"].items():
        fixed, ran = _split(entry, presence=presence)
        if fixed:
            key = facts_name(fixed)
            facts[key] = fixed
            ran["facts"] = key
        files[name] = ran
    return {**full, "files": files}, facts


def _split(
    entry: Mapping[str, Any], *, presence: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """An encoded entry's fixed facts, and what ran with the file's digest."""
    fixed = {
        key: entry[key] for key in ("statements", "excluded", "spans") if key in entry
    }
    ran = {
        key: entry[key]
        for key in ("digest", "hits", "counts", "reached")
        if key in entry
    }
    branches: list[list[Any]] = entry.get("branches", [])
    if branches:
        fixed["branches"] = [[*item[:3], *item[4:]] for item in branches]
        taken = [[place, item[3]] for place, item in enumerate(branches, 1) if item[3]]
        if taken and presence and all(runs == 1 for _, runs in taken):
            ran["taken"] = ranges(place for place, _ in taken)
        elif taken:
            ran["taken"] = taken
    for key, kept, into in (
        ("branch-counts", 2, "counted"),
        ("regions", 4, "calls"),
        ("ranges", 4, "range-counts"),
    ):
        items: list[list[Any]] = entry.get(key, [])
        if not items:
            continue
        fixed[key] = [item[:kept] for item in items]
        pairs = [
            [place, item[kept]]
            for place, item in enumerate(items, 1)
            if len(item) > kept and (item[kept] or into == "calls")
        ]
        if pairs:
            ran[into] = pairs
    return fixed, ran


def facts_names(raw: Mapping[str, Any]) -> dict[str, str]:
    """The fixed facts a part in its stored form names, each with a file's digest.

    Each name maps to the digest of the first file that names it, for a
    refusal to name the content whose facts are missing. A value off
    the stored form names nothing here; `from_stored` refuses it.
    """
    found: dict[str, str] = {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return found
    for entry in cast("dict[str, Any]", files).values():
        if not isinstance(entry, dict):
            continue
        named = cast("dict[str, Any]", entry).get("facts")
        if isinstance(named, str) and named not in found:
            found[named] = str(cast("dict[str, Any]", entry).get("digest", ""))
    return found


def from_stored(raw: object, facts: Mapping[str, Any], *, source: str) -> Part:
    """Read *raw*, a part in its stored form, its files' fixed facts read from *facts*.

    *facts* maps a facts' name to the facts. Every refusal names
    *source* and the key at fault, as `decode`'s do.

    Raises:
        OtherVersion: when the part is of another format version.
        PartError: when a file names fixed facts *facts* does not hold,
            naming the facts and the file's content; when a place names
            no item of its facts; or when the part, its facts joined in,
            is off the format.
    """
    reader = _Reader(source)
    table = reader.table(raw, ())
    version = table.get("format")
    if version != FORMAT:
        raise OtherVersion(source, version)
    measurer = reader.measurer(table.get("measurer"), ("measurer",))
    files = {
        name: _joined(reader, value, ("files", name), facts, measurer)
        for name, value in reader.table(table.get("files"), ("files",)).items()
    }
    return decode({**table, "files": files}, source=source)


#: The keys of a file in the stored form.
_STORED_KEYS = (
    "digest",
    "facts",
    "hits",
    "counts",
    "taken",
    "counted",
    "calls",
    "range-counts",
    "reached",
)


def _joined(
    reader: _Reader,
    raw: object,
    path: KeyPath,
    facts: Mapping[str, Any],
    measurer: Measurer,
) -> dict[str, Any]:
    """A file of a part in its stored form, its fixed facts joined in."""
    entry = reader.table(raw, path)
    reader.known(entry, path, _STORED_KEYS)
    fixed: dict[str, Any] = {}
    if "facts" in entry:
        key = reader.text(entry["facts"], (*path, "facts"), empty=False)
        if key not in facts:
            content = str(entry.get("digest", ""))[:12] or "content that was not read"
            raise reader.refuse(
                (*path, "facts"),
                f"names the fixed facts {key} of content {content}, and no facts"
                " of that name are held",
            )
        fixed = reader.table(facts[key], ("facts", key))
    full: dict[str, Any] = {
        key: value
        for key, value in {**fixed, **entry}.items()
        if key not in _STORED_KEYS[4:8] and key != "facts"
    }
    branches = reader.listed(fixed.get("branches", []), ("facts", "branches"))
    taken = _places(reader, entry.get("taken", []), (*path, "taken"), len(branches))
    if branches:
        joined: list[list[Any]] = []
        for place, item in enumerate(branches, 1):
            fields = reader.listed(item, ("facts", "branches", place))
            joined.append([*fields[:3], taken.get(place, 0), *fields[3:]])
        full["branches"] = joined
    for key, into in (
        ("branch-counts", "counted"),
        ("regions", "calls"),
        ("ranges", "range-counts"),
    ):
        items = reader.listed(fixed.get(key, []), ("facts", key))
        numbers_of = _places(reader, entry.get(into, []), (*path, into), len(items))
        if not items:
            continue
        joined = []
        for place, item in enumerate(items, 1):
            fields = reader.listed(item, ("facts", key, place))
            if place in numbers_of:
                fields = [*fields, numbers_of[place]]
            elif into != "calls":
                # A region whose calls are not counted keeps none; a
                # count left out is 0.
                fields = [*fields, 0]
            joined.append(fields)
        full[key] = joined
    return full


def _places(reader: _Reader, raw: object, path: KeyPath, count: int) -> dict[int, int]:
    """The numbers a stored file keeps by place: a range string reads as 1 each."""
    found: dict[int, int] = {}
    if isinstance(raw, str):
        found = dict.fromkeys(reader.lines(raw, path), 1)
    else:
        for index, item in enumerate(reader.listed(raw, path)):
            pair = reader.listed(item, (*path, index))
            if len(pair) != 2:
                raise reader.refuse((*path, index), "is no [place, number] pair")
            place = reader.number(pair[0], (*path, index, 0), least=1)
            found[place] = reader.number(pair[1], (*path, index, 1), least=0)
    beyond = sorted(place for place in found if place > count)
    if beyond:
        raise reader.refuse(
            path, f"names place {beyond[0]}, and its fixed facts hold {count}"
        )
    return found
