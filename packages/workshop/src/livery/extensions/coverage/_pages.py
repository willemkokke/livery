"""Draw the site's coverage pages from the measured data, in the line format.

The docs extension asks its ``docs.coverage-pages`` query for the
function that draws the coverage pages. This extension answers in its
``[for.docs]`` table, so the answer is asked only while docs is listed:
[livery.extensions.coverage._pages.renderer][] answers with
[livery.extensions.coverage._pages.render_coverage_pages][].

The pages are drawn from parts in the line format
([livery.extensions.coverage._format][]) by
[livery.extensions.coverage._render.render][]. A stored row of the
``lines`` measurer, a native suite's lines with how many times each
ran, is read here. A row of any other measurer, coverage.py's arcs say,
is reduced to parts by the extension that answers this extension's
``coverage.measurers`` query for that measurer, the pytest extension
for the arcs ([livery.extensions.coverage._pages.Reduction][]). In the
merge point's gate job the rows are main's coverage record on every
check leg; anywhere else the pages draw the data the last gate run left
on this machine.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import livery.footman as footman
from livery import git
from livery.extensions.coverage._format import (
    COUNTS,
    LINES,
    Entry,
    Measurer,
    Merged,
    OtherVersion,
    Part,
    PartError,
    decode,
    digest,
    merge,
)
from livery.extensions.coverage._render import Drawn, Report, render
from livery.footman import fail
from livery.workshop import Query, answer

if TYPE_CHECKING:
    from livery.workshop._coverage_store import Unit
    from livery.workshop._packages import Package


class Reduction(Protocol):
    """How an extension reduces one measurer's measurements to parts.

    An extension answers ``coverage.measurers`` with a reduction for
    each measurer it reduces, by the name a stored row carries. Both
    methods return parts as JSON holds them, in the line format; a part
    off the format refuses the pages, naming the reduction and the key.
    A reduction that cannot reduce what it is handed raises
    ``ValueError`` naming the file, and the pages stop on it.
    """

    def rows(
        self, root: Path, rows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """One part per stored row, in the rows' order.

        Each row is ``{"unit": <the unit's path>, "files": {<file>:
        <the measurer's pairs>}}``, a file's path relative to *root*.
        """
        ...

    def left(self, root: Path) -> list[dict[str, Any]]:
        """The parts of the data the measurer's last run left at *root*."""
        ...


#: Who reduces the stored rows of a measurer this extension does not
#: read itself: each listed extension answers with its reductions, by
#: the measurer's name.
MEASURERS: Query[Mapping[str, Reduction]] = Query("coverage.measurers")

#: The measurer this extension reads itself: a native suite's lines,
#: each with how many times it ran.
LINES_MEASURER = Measurer("lines", counts=COUNTS, judges=(LINES,))

#: The tree each package's pages are drawn into, beside its contract.
TREE = "htmlcov"


def measurers_by_name(
    answers: Mapping[str, Any], lookup: object
) -> dict[str, Reduction]:
    """``coverage.measurers``' rule: each answering extension's reductions, by measurer.

    Raises:
        ValueError: when two extensions reduce one measurer, or one
            answers for the lines measurer, which this extension reads
            itself; the message names the extensions.
    """
    del lookup
    found: dict[str, Reduction] = {}
    by: dict[str, str] = {}
    for extension, reductions in answers.items():
        for name, reduction in dict(reductions).items():
            if name == LINES_MEASURER.name:
                raise ValueError(
                    f"{extension} reduces the {name} measurer, whose rows the"
                    " coverage extension reads itself; answer for another measurer"
                )
            if name in found:
                raise ValueError(
                    f"{by[name]} and {extension} both reduce the {name} measurer;"
                    " one extension reduces each"
                )
            found[str(name)] = reduction
            by[str(name)] = extension
    return found


def renderer(root: Path) -> Callable[[Path, tuple[Package, ...]], list[str]]:
    """Answer ``docs.coverage-pages`` with the function that draws the pages.

    The docs extension asks with the workspace root, which the answer
    does not depend on, and calls the answer with the root and the
    packages that declare an ``htmlcov`` report.
    """
    del root
    return render_coverage_pages


def render_coverage_pages(root: Path, packages: tuple[Package, ...]) -> list[str]:
    """Draw each of *packages*' coverage pages from the measured data.

    Each package's tree goes to its ``htmlcov`` directory, drawn from
    the parts of its files: main's record on every check leg in the
    merge point's gate job, and the data the last gate run left on this
    machine anywhere else. A package no measurement reaches gets no
    tree, and its coverage page states the absence.

    A measured file the checkout no longer has is red inside CI, where
    the measurement is the record of the tree being built; on a
    machine, whose data outlives a moved file until the next gate run,
    the package's index names it and the rest is drawn. A file whose
    content differs from the content measured keeps its numbers, and
    its page says it was measured at other content. Returns the members
    drawn.
    """
    from livery.workshop._ci_context import run_context

    prefixes = tuple(f"{package.path}/" for package in packages)
    measured = _measured(root, prefixes)
    for miss in measured.misses:
        print(f"  coverage: {miss}")
    names = {name for part in measured.parts for name in part.files}
    digests = {
        name: digest((root / name).read_bytes())
        for name in sorted(names)
        if (root / name).is_file()
    }
    combined = merge(measured.parts, digests)
    for line in combined.left_out:
        print(f"  coverage: {line}")
    gone = sorted(name for name in combined.files if name not in digests)
    if gone and run_context() is not None:
        fail(
            f"coverage: the measured data names {', '.join(gone)}, which the"
            " checkout does not have; inside CI the data is the record of the"
            " tree being built, so a file it names and the tree lacks is red"
        )
    for name in gone:
        print(
            f"  coverage: the local data names {name}, which the checkout no"
            " longer has; its package's index states the absence until"
            f" `{footman.prog()} check` measures again"
        )
    head = _head(root)
    rendered: list[str] = []
    for package in packages:
        prefix = f"{package.path}/"
        drawn = tuple(
            _drawn(root, name, merged, digests[name])
            for name, merged in sorted(combined.files.items())
            if name.startswith(prefix) and name in digests
        )
        if not drawn:
            shutil.rmtree(package.directory / TREE, ignore_errors=True)
            print(
                f"  coverage: {package.member}: no measured data; its page states"
                " the absence"
            )
            continue
        notes = (*measured.notes, *((f"Drawn at commit {head[:12]}.",) if head else ()))
        render(
            package.directory / TREE,
            Report(
                title=package.path,
                files=drawn,
                contexts=combined.contexts,
                notes=notes,
                absent=tuple(name for name in gone if name.startswith(prefix)),
            ),
        )
        rendered.append(package.member)
    return rendered


@dataclass
class _Measured:
    """The parts the pages draw, the measurements left out, and where they came from."""

    parts: list[Part] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    notes: tuple[str, ...] = ()


def _measured(root: Path, prefixes: tuple[str, ...]) -> _Measured:
    """The parts of the files under *prefixes*: main's record, or this machine's."""
    reductions = answer(root, MEASURERS)
    stored = _stored(root)
    found = (
        _local(root, reductions)
        if stored is None
        else _from_record(root, reductions, *stored, prefixes=prefixes)
    )
    found.parts = [_within(part, prefixes) for part in found.parts]
    return found


def _within(part: Part, prefixes: tuple[str, ...]) -> Part:
    """*part* with the files under *prefixes* alone."""
    return Part(
        part.unit,
        part.measurer,
        {
            name: entry
            for name, entry in part.files.items()
            if name.startswith(prefixes)
        },
        part.contexts,
    )


def _stored(root: Path) -> tuple[dict[str, dict[str, Unit]], list[str]] | None:
    """In the merge point's gate job, main's record on every check leg; else None.

    Returns the units by leg and the misses. The gate job draws the
    pages after its union wrote the record, for the site it publishes.
    Anywhere else nothing is pulled: a pull request's docs job builds
    without the legs' data by design, and a machine's build draws the
    local data a gate run left, or states the absence.
    """
    from livery.workshop._ci_context import (
        LEG_VARIABLE,
        POINT_VARIABLE,
        run_context,
        state_store,
    )
    from livery.workshop._points import check_legs

    deploying = (
        os.environ.get(POINT_VARIABLE) == "merge"
        and os.environ.get(LEG_VARIABLE) == "gate"
    )
    if run_context() is None or not deploying:
        return None
    from livery.git import remote_snapshot

    with remote_snapshot(state_store(root), fetch=("coverage/main/",)):
        return stored_rows(root, check_legs(root))


def stored_rows(
    root: Path, labels: Sequence[str]
) -> tuple[dict[str, dict[str, Unit]], list[str]]:
    """Every unit of main's record on each leg in *labels*, by leg; and the misses.

    A leg whose record cannot be read, or holds nothing, is a named
    miss, never a refusal: the pages draw what there is.
    """
    from livery.workshop._coverage_store import MAIN, recorded

    found: dict[str, dict[str, Unit]] = {}
    misses: list[str] = []
    for label in labels:
        held = recorded(root, leg=label)
        if held.failed:
            misses.append(
                f"{MAIN}/{label}: the record could not be read ({held.reason});"
                " the pages draw without it"
            )
            continue
        if not held.units:
            misses.append(f"{MAIN}/{label}: nothing recorded")
            continue
        found[label] = dict(held.units)
    return found, misses


def _from_record(
    root: Path,
    reductions: Mapping[str, Reduction],
    held: dict[str, dict[str, Unit]],
    misses: list[str],
    *,
    prefixes: tuple[str, ...],
) -> _Measured:
    """The parts of main's record: lines rows read here, the rest reduced.

    Each row is narrowed to the files under *prefixes* first, the files
    the pages draw, so a reduction reads no other file.
    """
    found = _Measured(misses=list(misses))
    pending: dict[str, list[tuple[str, Unit]]] = {}
    runs: set[str] = set()
    for label, units in held.items():
        for unit in units.values():
            runs.add(unit.run)
            # A part names a file with forward slashes, whichever
            # platform's leg measured it.
            files: dict[str, list[tuple[int, int]]] = {}
            for name, pairs in unit.files.items():
                posix = name.replace("\\", "/")
                if posix.startswith(prefixes):
                    files[posix] = pairs
            if not files:
                continue
            if unit.measurer == LINES_MEASURER.name:
                found.parts.append(_lines_part(root, unit.path, files))
            else:
                narrowed = replace(unit, files=files)
                pending.setdefault(unit.measurer, []).append((label, narrowed))
    for name, rows in sorted(pending.items()):
        reduction = reductions.get(name)
        if reduction is None:
            found.misses.extend(
                f"{unit.path} on {label}: no listed extension reduces the {name}"
                " measurer's rows; the pages draw without it"
                for label, unit in rows
            )
            continue
        handed = [
            {
                "unit": unit.path,
                "files": {
                    file: [list(pair) for pair in pairs]
                    for file, pairs in unit.files.items()
                },
            }
            for _label, unit in rows
        ]
        raw = _reduced(partial(reduction.rows, root, handed), f"the {name} reduction")
        if len(raw) != len(rows):
            fail(
                f"coverage: the {name} reduction returned {len(raw)} part(s) for"
                f" {len(rows)} row(s); it returns one part per row"
            )
        sources = [
            f"the {name} reduction of {unit.path} on {label}" for label, unit in rows
        ]
        found.parts.extend(_decoded(raw, sources, found.misses))
    legs = ", ".join(held) or "no leg"
    found.notes = (
        f"Measured on {legs}, from main's coverage record:"
        f" {sum(len(units) for units in held.values())} unit(s) from"
        f" {len(runs)} run(s).",
    )
    return found


def _local(root: Path, reductions: Mapping[str, Reduction]) -> _Measured:
    """The parts of the data the last gate run left at *root*."""
    from livery.workshop._coverage_lines import read_parts

    found = _Measured(
        notes=("Measured on this machine, from the data its last gate run left.",)
    )
    for name, reduction in sorted(reductions.items()):
        source = f"the {name} measurer's data on this machine"
        raw = _reduced(partial(reduction.left, root), source)
        found.parts.extend(_decoded(raw, [source] * len(raw), found.misses))
    for unit, lines in sorted(read_parts(root).items()):
        files = {name: sorted(runs.items()) for name, runs in lines.items()}
        found.parts.append(_lines_part(root, unit, files))
    return found


def _reduced(
    call: Callable[[], list[dict[str, Any]]], source: str
) -> list[dict[str, Any]]:
    """What *call* returns; a reduction that refuses stops the pages, named."""
    try:
        return call()
    except ValueError as error:
        fail(f"coverage: {source} refuses: {error}")


def _decoded(
    raw: Sequence[object], sources: Sequence[str], misses: list[str]
) -> list[Part]:
    """*raw*'s parts, read; one of another version is named in *misses* and skipped.

    A part off the format refuses the pages, naming its source and the
    key at fault.
    """
    parts: list[Part] = []
    for item, source in zip(raw, sources, strict=True):
        try:
            parts.append(decode(item, source=source))
        except OtherVersion as error:
            misses.append(f"{error}; the pages draw without it")
        except PartError as error:
            fail(f"coverage: {error}")
    return parts


def _lines_part(
    root: Path, unit: str, files: Mapping[str, Sequence[tuple[int, int]]]
) -> Part:
    """A part of the lines measurer: each file's lines and how many times each ran.

    The measurement names no content, so each file's digest is the
    checkout's.
    """
    entries: dict[str, Entry] = {}
    for named, pairs in files.items():
        # A part names a file with forward slashes, whichever platform
        # measured it.
        name = named.replace("\\", "/")
        path = root / name
        counted = dict(pairs)
        entries[name] = Entry(
            digest=digest(path.read_bytes()) if path.is_file() else "",
            statements=frozenset(counted),
            counts={line: runs for line, runs in counted.items() if runs > 0},
        )
    return Part(unit, LINES_MEASURER, entries)


def _drawn(root: Path, name: str, merged: Merged, checkout: str) -> Drawn:
    """*name* to draw: with its text when the checkout holds the content measured.

    The text is the file's bytes as UTF-8, its line endings whatever
    the checkout gave it; the renderer reads a CRLF as LF.
    """
    if merged.entry.digest != checkout:
        return Drawn(name, merged, None, checkout)
    text = (root / name).read_bytes().decode("utf-8", errors="replace")
    return Drawn(name, merged, text, checkout)


def _head(root: Path) -> str:
    """The commit the checkout stands on; empty without one."""
    return git.head_sha(root) if git.has_commits(root) else ""
