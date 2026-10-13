"""Draw the site's coverage pages from the measured data, in the line format.

The docs extension asks its ``docs.coverage-pages`` query for the
function that draws the coverage pages. This extension answers in its
``[for.docs]`` table, so the answer is asked only while docs is listed:
`renderer` answers with `render_coverage_pages`.

The pages are drawn from parts in the line format (`_format`) by
`_render`. In the merge point's gate job the parts are main's coverage
record on every check leg, read from their stored form with the fixed
facts the record holds, as the legs made them. Anywhere else
they are the data the last gate run left on this machine: the parts a
native suite's measurer left in the workspace, and the parts each
listed extension's reduction makes of its measurer's data, the pytest
extension's of coverage.py's (`_parts`). Each package's index states
its floor (`_floors.floor_status`).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import livery.footman as footman
from livery import git
from livery.extensions.coverage._format import (
    Merged,
    OtherVersion,
    Part,
    PartError,
    check_ranges,
    decode,
    digest,
    from_stored,
    merge,
)
from livery.extensions.coverage._render import Drawn, Report, render
from livery.footman import fail
from livery.workshop import answer

if TYPE_CHECKING:
    from livery.extensions.coverage._marks import Mark
    from livery.extensions.coverage._record import Unit
    from livery.workshop._packages import Package

#: The tree each package's pages are drawn into, beside its contract.
TREE = "htmlcov"


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
    its page says it was measured at other content; a file measured at
    the checkout's content with a range outside its text refuses,
    naming the file and the range. Each package's index states its
    floor: the floor its contract names, the mark it ratchets to, read
    in the merge point's gate job, or that nothing judges it. Returns
    the members drawn.
    """
    from livery.extensions.coverage._floors import floor_status
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
    marks = _marks_read(root, packages)
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
                floor=floor_status(package, marks),
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
    stored = _stored(root)
    found = _local(root) if stored is None else _from_record(*stored, prefixes=prefixes)
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


def _deploying() -> bool:
    """Whether this is the merge point's gate job, which publishes the site."""
    from livery.workshop._ci_context import LEG_VARIABLE, POINT_VARIABLE, run_context

    return (
        run_context() is not None
        and os.environ.get(POINT_VARIABLE) == "merge"
        and os.environ.get(LEG_VARIABLE) == "gate"
    )


def _marks_read(root: Path, packages: tuple[Package, ...]) -> Mapping[str, Mark] | None:
    """The marks on the state store, read where the record is; else None.

    Read in the merge point's gate job, and only for a package under
    auto-ratchet: anywhere else the pages name the mode without its
    value, as a machine's preview does. A store that cannot be read is
    None too.
    """
    from livery.extensions.coverage._floors import coverage_policy
    from livery.extensions.coverage._marks import marks

    ratcheted = any(
        policy is not None and policy.ratchet
        for policy in (coverage_policy(package) for package in packages)
    )
    if not ratcheted or not _deploying():
        return None
    return marks(root)[0]


def _stored(root: Path) -> tuple[dict[str, dict[str, Unit]], list[str]] | None:
    """In the merge point's gate job, main's record on every check leg; else None.

    Returns the units by leg and the misses. The gate job draws the
    pages after its union wrote the record, for the site it publishes.
    Anywhere else nothing is pulled: a pull request's docs job builds
    without the legs' data by design, and a machine's build draws the
    local data a gate run left, or states the absence.
    """
    from livery.workshop._ci_context import state_store
    from livery.workshop._points import check_legs

    if not _deploying():
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
    from livery.extensions.coverage._record import MAIN, recorded

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
    held: dict[str, dict[str, Unit]],
    misses: list[str],
    *,
    prefixes: tuple[str, ...],
) -> _Measured:
    """The parts of main's record, as the legs made them.

    Each part is narrowed to the files under *prefixes* first, the files
    the pages draw, so no other file is read, and read from its stored
    form with the fixed facts its unit holds. A part off the format, or
    naming facts the record does not hold, stops the pages, naming the
    row and the key.
    """
    found = _Measured(misses=list(misses))
    runs: set[str] = set()
    for label, units in held.items():
        for unit in units.values():
            runs.add(unit.run)
            for index, raw in enumerate(unit.parts):
                files = raw.get("files")
                if not isinstance(files, dict):
                    files = {}
                narrowed = {
                    name: entry
                    for name, entry in files.items()
                    if str(name).startswith(prefixes)
                }
                if not narrowed:
                    continue
                source = f"main's record on {label}, {unit.path}, part {index}"
                found.parts.extend(
                    _decoded(
                        [{**raw, "files": narrowed}],
                        [source],
                        found.misses,
                        facts=unit.facts,
                    )
                )
    legs = ", ".join(held) or "no leg"
    found.notes = (
        f"Measured on {legs}, from main's coverage record:"
        f" {sum(len(units) for units in held.values())} unit(s) from"
        f" {len(runs)} run(s).",
    )
    return found


def _local(root: Path) -> _Measured:
    """The parts of the data the last gate run left at *root*.

    The parts each listed extension's reduction makes of its measurer's
    data, then the parts a native suite's measurer left in the
    workspace.
    """
    from livery.extensions.coverage._parts import (
        MEASURERS,
        part_files,
        read_part,
        reduced,
    )

    found = _Measured(
        notes=("Measured on this machine, from the data its last gate run left.",)
    )
    for name, reduction in sorted(answer(root, MEASURERS).items()):
        source = f"the {name} measurer's data on this machine"
        raw = reduced(partial(reduction.left, root), source)
        found.parts.extend(_decoded(raw, [source] * len(raw), found.misses))
    for path in part_files(root):
        try:
            found.parts.append(read_part(path, root))
        except OtherVersion as error:
            found.misses.append(f"{error}; the pages draw without it")
        except PartError as error:
            fail(f"coverage: {error}")
    return found


def _decoded(
    raw: Sequence[object],
    sources: Sequence[str],
    misses: list[str],
    *,
    facts: Mapping[str, Any] | None = None,
) -> list[Part]:
    """*raw*'s parts, read; one of another version is named in *misses* and skipped.

    With *facts*, each part is in its stored form and its files' fixed
    facts are read from them. A part off the format refuses the pages,
    naming its source and the key at fault.
    """
    parts: list[Part] = []
    for item, source in zip(raw, sources, strict=True):
        try:
            parts.append(
                decode(item, source=source)
                if facts is None
                else from_stored(item, facts, source=source)
            )
        except OtherVersion as error:
            misses.append(f"{error}; the pages draw without it")
        except PartError as error:
            fail(f"coverage: {error}")
    return parts


def _drawn(root: Path, name: str, merged: Merged, checkout: str) -> Drawn:
    """*name* to draw: with its text when the checkout holds the content measured.

    The text is the file's bytes as UTF-8, its line endings whatever
    the checkout gave it; the renderer reads a CRLF as LF. A range
    outside the text refuses the pages, naming the file and the range.
    """
    if merged.entry.digest != checkout:
        return Drawn(name, merged, None, checkout)
    text = (root / name).read_bytes().decode("utf-8", errors="replace")
    try:
        check_ranges(
            merged.entry,
            text,
            source=f"{name}, as {merged.measurer.label} measured it",
        )
    except PartError as error:
        fail(f"coverage: {error}")
    return Drawn(name, merged, text, checkout)


def _head(root: Path) -> str:
    """The commit the checkout stands on; empty without one."""
    return git.head_sha(root) if git.has_commits(root) else ""
