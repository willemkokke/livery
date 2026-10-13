"""The line format, version 1: what one measurer read of one unit, file by file.

A part holds what one measurer read for one unit on one machine: a
package's suite, or the whole data a run left. Every measurer reduces
to it, whatever language it measures: coverage.py's arcs, gcov's
counts and a Cobertura report each become a part, and the coverage
pages draw parts alone. A part is JSON:

```json
{"format": 1, "unit": "packages/workshop",
 "measurer": {"name": "coverage.py", "version": "7.16.2",
              "runtime": "CPython 3.14", "counts": "presence",
              "branches": "targets", "regions": ["function", "class"],
              "judges": ["lines", "branches"]},
 "contexts": ["", "packages/workshop/tests/test_graph.py::test_walk|run"],
 "files": {"packages/workshop/src/livery/workshop/_graph.py": {
   "digest": "<the file's git blob id>",
   "statements": "1-12,15,17-40", "hits": "1-9,15,17-30",
   "excluded": "41-48", "spans": [[20, 23]],
   "branches": [[12, 13, 0, 1], [12, 15, 1, 0, "line 12 didn't jump ..."]],
   "regions": [["function", "Graph.walk", 41, "42-60"]],
   "reached": {"sets": ["AAE="], "lines": [[9, 0]]}}}}
```

A set of lines is a range string, ``1-12,15,17-40``. Per file:

- ``digest``: the git blob id of the content measured; empty when the
  measurer could not read the file.
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
  says why an untaken branch never ran, or a tag: ``waived`` for a
  branch the source waives, counted as taken, ``throw`` for the path
  an exception takes.
- ``branch-counts``: ``[line, branches, taken]`` for a measurer that
  counts a line's branches without naming them.
- ``regions``: ``[kind, name, start, lines, calls?]``, a function or a
  class: its first line, the lines of its body, and how many times it
  was called when the measurer counts calls.
- ``reached``: the contexts that reached each line, only where the
  measurer recorded them. ``sets`` are sets of indexes into the part's
  ``contexts``, each sorted, delta-coded as unsigned LEB128 and
  base64-encoded; ``lines`` pairs a line with the index of its set.

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
#: of another version is named and skipped, never guessed at.
FORMAT = 1

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
    """

    name: str
    version: str = ""
    runtime: str = ""
    counts: str = PRESENCE
    branches: str = NO_BRANCHES
    regions: tuple[str, ...] = ()
    judges: tuple[str, ...] = (LINES,)

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
        why: The sentence that says why it never ran, or a tag:
            ``waived`` or ``throw``.
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
class Entry:
    """What a measurer read of one file.

    Attributes:
        digest: The git blob id of the content measured; empty when the
            measurer could not read the file.
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
        """The lines that ran with a branch that never did."""
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
        return frozenset(found)


@dataclass(frozen=True)
class Part:
    """One measurer's reading of one unit.

    Attributes:
        unit: The unit read: a suite's path, or ``.`` for the whole data
            a run left.
        measurer: Who read it.
        files: What it read of each file, by the file's path from the
            workspace root.
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

    A branch belongs to the line it leaves, so a region keeps the
    branches its own lines leave; the regions themselves go.
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
    """The git blob id of *content*: what a part's ``digest`` names."""
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


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
    contexts = reader.texts(table.get("contexts", []), ("contexts",))
    files = {
        name: reader.entry(value, ("files", name), measurer, len(contexts))
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

    def text(self, raw: object, path: KeyPath, *, empty: bool = True) -> str:
        if not isinstance(raw, str) or not (empty or raw):
            wanted = "text" if empty else "text that is not empty"
            raise self.refuse(path, f"is {raw!r}, and {wanted} is wanted")
        return raw

    def texts(self, raw: object, path: KeyPath) -> list[str]:
        items = self.listed(raw, path)
        return [self.text(item, (*path, index)) for index, item in enumerate(items)]

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

    def measurer(self, raw: object, path: KeyPath) -> Measurer:
        table = self.table(raw, path)
        keys = ("name", "version", "runtime", "counts", "branches", "regions", "judges")
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
        )

    def entry(
        self, raw: object, path: KeyPath, measurer: Measurer, contexts: int
    ) -> Entry:
        table = self.table(raw, path)
        ran = "hits" if measurer.counts == PRESENCE else "counts"
        keys = ("digest", "statements", ran, "excluded", "spans", "branches")
        self.known(table, path, (*keys, "branch-counts", "regions", "reached"))
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

    def reached(self, raw: object, path: KeyPath, contexts: int) -> dict[int, Lines]:
        if raw is None:
            return {}
        table = self.table(raw, path)
        self.known(table, path, ("sets", "lines"))
        sets: list[Lines] = []
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
            sets.append(held)
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


def merge(parts: Iterable[Part], current: Mapping[str, str] | None = None) -> Combined:
    """Merge *parts*: each file's readings at one content and by one measurer.

    *current* maps a file to its digest in the checkout. A file merges
    the readings that share its current digest and one measurer
    identity ([livery.extensions.coverage._format.Measurer.identity][]),
    the identity most readings carry winning; without a reading at the
    current digest, the digest and identity most readings share win.
    Every reading left out is named in ``left_out``.

    The merge: statements and excluded lines are the union, hits are
    or-ed and counts added, a branch is one by its line and index with
    its runs added and its sentence kept only while it never ran, a
    count of a line's branches keeps the most taken, a region is one by
    its kind, name and start with its calls added, and the contexts
    that reached a line are the union, by name.
    """
    readings: dict[str, list[tuple[Part, Entry]]] = {}
    names: set[str] = set()
    for part in parts:
        names.update(part.contexts)
        for name, entry in part.files.items():
            readings.setdefault(name, []).append((part, entry))
    contexts = tuple(sorted(names))
    index_of = {name: index for index, name in enumerate(contexts)}
    files: dict[str, Merged] = {}
    left_out: list[str] = []
    for name in sorted(readings):
        chosen, others = _chosen(readings[name], (current or {}).get(name))
        kept = _reading(*chosen[0])
        left_out.extend(
            f"{name}: left out the reading of {_reading(part, entry)}; the pages"
            f" draw the one of {kept}"
            for part, entry in others
        )
        files[name] = Merged(
            measurer=chosen[0][0].measurer,
            entry=_merged(
                [
                    (_reindexed(entry.reached, part.contexts, index_of), entry)
                    for part, entry in chosen
                ]
            ),
            units=tuple(dict.fromkeys(part.unit for part, _ in chosen)),
        )
    return Combined(files, contexts, tuple(left_out))


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
            taken = known.taken + branch.taken
            branches[key] = Branch(
                branch.source,
                branch.target,
                branch.index,
                taken,
                "" if taken else known.why or branch.why,
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
    )
