"""Reduce coverage.py's measurements to the line format the coverage pages draw.

While coverage is listed, the coverage extension asks its
``coverage.measurers`` query which extension reduces the stored rows of
each measurer, and this extension answers for ``arcs``, coverage.py's
arcs, in its ``[for.coverage]`` table:
[livery.extensions.pytest._coverage.measurers][].
[livery.extensions.pytest._coverage.Arcs][] turns each stored row, or
the data a run left at the workspace root, into a part in the line
format, version 1: per file, coverage.py's own analysis of it under the
workspace's ``.coveragerc``. A part holds the statements and the ones
that ran, the excluded lines, the statements written over several
lines, each branch of a line with more than one exit with the sentence
that says why an untaken one never ran, the functions and classes, and,
where the data names them, the tests that reached each line. A branch
the source waives with ``# pragma: no branch`` counts as taken, as
coverage.py counts it.

coverage.py's parser reads each file once, however many rows name it.
Before a part is handed over, each file's numbers are checked against
coverage.py's own analysis of the same data, and a file whose numbers
differ refuses the reduction, naming the file.
"""

from __future__ import annotations

import base64
import hashlib
import platform
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from coverage import Coverage, CoverageData
    from coverage.python import PythonFileReporter
    from coverage.results import Analysis

#: The measurer a stored row names for coverage.py's arcs.
MEASURER = "arcs"

#: The line format version the parts are written in.
FORMAT = 1

#: The data a run leaves at the workspace root, and the configuration
#: coverage.py reads there.
DATA_FILE = ".coverage"
CONFIG_FILE = ".coveragerc"

#: The tag of a branch the source waives, which counts as taken.
WAIVED = "waived"

#: The unit of the data a run left: the whole of it.
LEFT_UNIT = "."


def measurers(root: Path) -> dict[str, Arcs]:
    """Answer ``coverage.measurers``: this extension reduces coverage.py's arcs."""
    del root
    return {MEASURER: Arcs()}


class Arcs:
    """coverage.py's arcs, reduced to parts in the line format."""

    def rows(
        self, root: Path, rows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """One part per stored row, in the rows' order.

        Each row is ``{"unit": <the unit's path>, "files": {<file>: <arcs>}}``,
        an arc a pair of line numbers and a file's path relative to
        *root*.

        Raises:
            ValueError: when a row is not a unit's arcs, or a file's
                numbers differ from coverage.py's, naming it.
        """
        from coverage import CoverageData

        reducer = _Reducer(root)
        parts: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            unit, files = _row(row, index)
            data = CoverageData(no_disk=True)
            try:
                data.add_arcs(files)
                parts.append(reducer.part(unit, data, {name: name for name in files}))
            finally:
                data.close(force=True)
        return parts

    def left(self, root: Path) -> list[dict[str, Any]]:
        """The part of the data coverage.py left at *root*; none when it left none.

        A file the data names outside *root* is no file of the
        workspace and is left out; one inside it that the checkout no
        longer has is in the part with an empty digest and nothing
        else, since its source cannot be read.

        Raises:
            ValueError: when a file's numbers differ from coverage.py's,
                naming it.
        """
        from coverage import CoverageData

        if not (root / DATA_FILE).is_file():
            return []
        data = CoverageData(basename=str(root / DATA_FILE))
        try:
            data.read()
            names: dict[str, str] = {}
            for measured in data.measured_files():
                relative = _relative(measured, root)
                if relative is not None:
                    names[relative] = measured
            return [_Reducer(root).part(LEFT_UNIT, data, names, contexts=True)]
        finally:
            data.close(force=True)


def _row(
    row: Mapping[str, Any], index: int
) -> tuple[str, dict[str, set[tuple[int, int]]]]:
    """A stored row's unit and its arcs per file.

    Raises:
        ValueError: when the row holds no unit's path or no files of arcs.
    """
    unit = row.get("unit")
    files = row.get("files")
    if not isinstance(unit, str) or not unit or not isinstance(files, Mapping):
        raise ValueError(
            f"row {index} is no unit's arcs: it holds a unit's path and its files"
        )
    found: dict[str, set[tuple[int, int]]] = {}
    for name, arcs in cast("Mapping[str, Iterable[Sequence[int]]]", files).items():
        # A part names a file with forward slashes, whichever platform's
        # leg measured it.
        held = found.setdefault(name.replace("\\", "/"), set())
        held.update((arc[0], arc[1]) for arc in arcs)
    return unit, found


def _relative(filename: str, root: Path) -> str | None:
    """*filename* from the workspace root, with forward slashes; None outside it."""
    name = filename.replace("\\", "/")
    if not Path(name).is_absolute():
        return name
    try:
        return Path(name).relative_to(root).as_posix()
    except ValueError:
        return None


@dataclass(frozen=True)
class _Static:
    """What a file's source says, read once: its digest, reporter, spans and regions."""

    digest: str
    reporter: PythonFileReporter
    spans: list[list[int]]
    regions: list[list[Any]]


class _Reducer:
    """Reduce coverage.py data to parts, reading each file's source once."""

    def __init__(self, root: Path) -> None:
        from coverage import Coverage, __version__

        config = root / CONFIG_FILE
        self.root = root
        self.coverage: Coverage = Coverage(
            data_file=None, config_file=str(config) if config.is_file() else False
        )
        self.version = __version__
        self.known: dict[str, _Static | None] = {}
        runtime = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.measurer: dict[str, Any] = {
            "name": "coverage.py",
            "version": __version__,
            "runtime": f"{platform.python_implementation()} {runtime}",
            "counts": "presence",
            "branches": "targets",
            "regions": ["function", "class"],
            "judges": ["lines", "branches"],
        }

    def static(self, name: str) -> _Static | None:
        """The static facts of the file at *name*; None when the checkout has none.

        Raises:
            ValueError: when coverage.py cannot parse the file, naming it.
        """
        if name in self.known:
            return self.known[name]
        from coverage.exceptions import NotPython
        from coverage.python import PythonFileReporter

        path = self.root / name
        if not path.is_file():
            self.known[name] = None
            return None
        reporter = PythonFileReporter(str(path), self.coverage)
        try:
            multiline = reporter.multiline_map()
            regions = [
                [region.kind, region.name, region.start, _ranges(region.lines)]
                for region in reporter.code_regions()
            ]
        except NotPython as error:
            raise ValueError(f"{name}: coverage.py cannot read it ({error})") from None
        last: dict[int, int] = {}
        for line, first in multiline.items():
            last[first] = max(last.get(first, first), line)
        found = _Static(
            digest=_digest(path.read_bytes()),
            reporter=reporter,
            spans=[[first, end] for first, end in sorted(last.items()) if end > first],
            regions=regions,
        )
        self.known[name] = found
        return found

    def part(
        self,
        unit: str,
        data: CoverageData,
        names: Mapping[str, str],
        *,
        contexts: bool = False,
    ) -> dict[str, Any]:
        """The part of *unit* from *data*, each file by its name and its key in *data*.

        With *contexts*, the part names the contexts that reached each
        line, where *data* recorded any beside the empty one.
        """
        from coverage.results import analysis_from_file_reporter

        recorded = sorted(data.measured_contexts()) if contexts else []
        if recorded in ([], [""]):
            recorded = []
        index_of = {context: index for index, context in enumerate(recorded)}
        files: dict[str, Any] = {}
        for name in sorted(names):
            static = self.static(name)
            if static is None:
                files[name] = {"digest": ""}
                continue
            key = names[name]
            analysis = analysis_from_file_reporter(data, 0, static.reporter, key)
            reached = data.contexts_by_lineno(key) if recorded else {}
            files[name] = self.entry(name, analysis, static, reached, index_of)
        return {
            "format": FORMAT,
            "unit": unit,
            "measurer": self.measurer,
            "contexts": recorded,
            "files": files,
        }

    def entry(
        self,
        name: str,
        analysis: Analysis,
        static: _Static,
        reached: Mapping[int, Iterable[str]],
        index_of: Mapping[str, int],
    ) -> dict[str, Any]:
        """One file's entry from coverage.py's analysis of it, checked against it."""
        statements = analysis.statements
        excluded = analysis.excluded
        branching = {line for line, exits in analysis.exit_counts.items() if exits > 1}
        branches: list[list[Any]] = []
        place: dict[int, int] = {}
        possible = sorted(
            arc
            for arc in analysis.arc_possibilities_set
            if arc[0] in branching and arc[1] not in excluded
        )
        for source, target in possible:
            index = place.get(source, 0)
            place[source] = index + 1
            if (source, target) in analysis.arcs_executed_set:
                branches.append([source, target, index, 1])
            elif source in analysis.no_branch:
                branches.append([source, target, index, 1, WAIVED])
            else:
                why = static.reporter.missing_arc_description(source, target)
                branches.append([source, target, index, 0, why])
        hits = analysis.executed & statements
        self.check(name, analysis, hits, branches)
        found: dict[str, Any] = {"digest": static.digest}
        if statements:
            found["statements"] = _ranges(statements)
        if hits:
            found["hits"] = _ranges(hits)
        if excluded:
            found["excluded"] = _ranges(excluded)
        if static.spans:
            found["spans"] = static.spans
        if branches:
            found["branches"] = branches
        if static.regions:
            found["regions"] = static.regions
        if reached:
            found["reached"] = _reached(reached, index_of)
        return found

    def check(
        self, name: str, analysis: Analysis, hits: set[int], branches: list[list[Any]]
    ) -> None:
        """Refuse an entry whose numbers differ from coverage.py's, naming the file.

        Raises:
            ValueError: when they differ.
        """
        missing = analysis.statements - hits
        mine = (
            len(analysis.statements),
            len(hits),
            len(branches),
            sum(1 for branch in branches if branch[3]),
            sum(1 for branch in branches if not branch[3] and branch[0] not in missing),
        )
        numbers = analysis.numbers
        theirs = (
            numbers.n_statements,
            numbers.n_executed,
            numbers.n_branches,
            numbers.n_executed_branches,
            numbers.n_partial_branches,
        )
        if mine != theirs:
            raise ValueError(
                f"{name}: the part reads {_counted(mine)}, and coverage.py"
                f" {self.version} reads {_counted(theirs)}; a part whose figure"
                " is not coverage.py's is never handed over"
            )


def _counted(counts: tuple[int, int, int, int, int]) -> str:
    """Numbers as a refusal names them, with the figure they make."""
    statements, ran, branches, taken, partial = counts
    whole = statements + branches
    figure = 100.0 * (ran + taken) / whole if whole else 100.0
    return (
        f"{ran} of {statements} statements and {taken} of {branches} branches"
        f" run, {partial} partial ({figure:.2f}%)"
    )


def _ranges(lines: Iterable[int]) -> str:
    """*lines* as the line format's range string: ``1-12,15,17-40``."""
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


def _reached(
    by_line: Mapping[int, Iterable[str]], index_of: Mapping[str, int]
) -> dict[str, Any]:
    """The contexts that reached each line, as the line format keeps them."""
    sets: dict[frozenset[int], int] = {}
    lines: list[list[int]] = []
    for line in sorted(by_line):
        held = frozenset(index_of[context] for context in by_line[line])
        lines.append([line, sets.setdefault(held, len(sets))])
    return {"sets": [_packed(held) for held in sets], "lines": lines}


def _packed(indexes: Iterable[int]) -> str:
    """A set of indexes sorted, delta-coded as unsigned LEB128, base64-encoded."""
    packed = bytearray()
    previous = -1
    for value in sorted(indexes):
        delta = value - previous - 1
        while True:
            low = delta & 0x7F
            delta >>= 7
            packed.append(low | (0x80 if delta else 0))
            if not delta:
                break
        previous = value
    return base64.b64encode(bytes(packed)).decode("ascii")


def _digest(content: bytes) -> str:
    """The git blob id of *content* as text, the line format's ``digest``.

    Each CRLF in *content* counts as LF, so the digest is the blob id
    git stores for a text file whatever line endings a checkout gives
    it: Git for Windows checks text out with CRLF, and a file reduced
    there has the digest of the same file reduced on Linux.
    """
    text = content.replace(b"\r\n", b"\n")
    header = f"blob {len(text)}\0".encode("ascii")
    return hashlib.sha1(header + text, usedforsecurity=False).hexdigest()
