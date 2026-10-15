"""The parts the measurers hand over, and the union the gate job writes.

Every measurer hands the coverage extension its readings as parts in
the line format (`_format`), one part per unit it measured, in one of
two ways. A measurer that reads its measurement inside its own step
leaves each unit's part in the workspace, under `PARTS`, as
``<measurer>/<unit>.json``: the cmake extension's test does, for a
native suite. A measurer whose run leaves data for later answers this
extension's ``coverage.measurers`` query with a `Reduction`, which
makes the parts when it is asked: the pytest extension does, for
coverage.py's data. A check leg gathers both for each unit it ran; the
gate job's union merges every leg's parts with the records' and writes
the merged files to `UNION`, which the floors read. On a machine the
floors' preview and the coverage pages read the parts the last run
left, both ways.

`native` tells which units the reductions are asked for: every unit
that holds the runtime's language; any other is native, and its
measurer leaves its parts itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from livery.extensions.coverage._format import (
    FORMAT,
    Combined,
    Measurer,
    Merged,
    Part,
    PartError,
    decode,
    encode,
)
from livery.footman import fail
from livery.workshop import Query

if TYPE_CHECKING:
    from livery.workshop import Package

#: Where a measurer leaves the parts it read inside its own step, one
#: file per unit, under a directory named after the measurer.
PARTS = ".workshop/coverage/parts"

#: Where the gate job's union writes the merged files the floors read.
UNION = ".workshop/coverage/union.json"


class Reduction(Protocol):
    """How an extension reduces the data its measurer left to parts.

    An extension answers ``coverage.measurers`` with a reduction for
    each measurer it reduces, by the measurer's name. Both methods
    return parts as JSON holds them, in the line format; a part off the
    format refuses, naming the reduction and the key. A reduction that
    cannot reduce what it finds raises ``ValueError`` naming the cause,
    and its caller stops on it.
    """

    def leg(
        self, root: Path, units: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """One part per unit, in the units' order, from the data a check leg left.

        Each unit is ``{"unit": <its path>, "tests": <the prefix its own
        tests' contexts start with>, "within": [<the path prefixes of its
        closure>]}``, and a unit without ``within`` takes every file. A
        unit's part holds the files of its closure the data names, each
        as the unit's own tests and the code outside every test reached
        it, and names the contexts that reached each line wherever the
        data names a test. The record numbers the contexts when it
        stores the part, so a reduction numbers them as it likes.
        """
        ...

    def left(self, root: Path) -> list[dict[str, Any]]:
        """The parts of the data the measurer's last run on a machine left at *root*."""
        ...


#: Who reduces the data a measurer left: each listed extension answers
#: with its reductions, by the measurer's name.
MEASURERS: Query[Mapping[str, Reduction]] = Query("coverage.measurers")


def measurers_by_name(
    answers: Mapping[str, Any], lookup: object
) -> dict[str, Reduction]:
    """``coverage.measurers``' rule: each answering extension's reductions, by measurer.

    Raises:
        ValueError: when two extensions reduce one measurer, naming both.
    """
    del lookup
    found: dict[str, Reduction] = {}
    by: dict[str, str] = {}
    for extension, reductions in answers.items():
        for name, reduction in dict(reductions).items():
            if name in found:
                raise ValueError(
                    f"{by[name]} and {extension} both reduce the {name} measurer;"
                    " one extension reduces each"
                )
            found[str(name)] = reduction
            by[str(name)] = extension
    return found


def reduced(
    call: Callable[[], list[dict[str, Any]]], source: str
) -> list[dict[str, Any]]:
    """What *call*, a reduction, returns; one that refuses stops the caller, named."""
    try:
        return call()
    except ValueError as error:
        fail(f"coverage: {source} refuses: {error}")


def native(package: Package) -> bool:
    """Whether *package*'s suite is native, measured by its build's measurer.

    A suite is native when its package does not hold the runtime's
    language. A native suite's measurer leaves its parts itself, so the
    reductions are never asked for it, and a package whose suite left
    no part is not measured. The workspace's own tests hold the root's
    set, so they are native while that set does not hold the runtime's
    language.
    """
    from livery.workshop._packages import holds_runtime_language

    return not holds_runtime_language(package)


def part_files(root: Path) -> list[Path]:
    """Every part a measurer left under `PARTS` at *root*, in name order."""
    return sorted((root / PARTS).glob("*/*.json"))


def read_part(path: Path, root: Path) -> Part:
    """The part in the file *path*, decoded.

    Raises:
        PartError: when the file does not parse, or holds a part off
            the format; the message names the file from *root* and the
            key at fault. ``OtherVersion``, one of them, names a part of
            another format version.
    """
    source = path.relative_to(root).as_posix()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PartError(f"{source}: the part does not parse ({error})") from None
    return decode(raw, source=source)


@dataclass(frozen=True)
class Unioned:
    """What the gate job's union wrote: the merged files and the units they came from.

    Attributes:
        files: Each file's merged reading, by its path from the root.
        units: The units whose parts the union merged.
        left_out: A line for each reading the merge left out.
    """

    files: Mapping[str, Merged]
    units: tuple[str, ...]
    left_out: tuple[str, ...] = ()


def write_union(root: Path, combined: Combined, units: Sequence[str]) -> Path:
    """Write *combined*, the union of *units*' parts, to `UNION`; the path.

    The merged files are grouped into one part per measurer, each
    numbering *combined*'s contexts, so the file reads back with the
    line format's own reader.
    """
    by_measurer: dict[Measurer, dict[str, Any]] = {}
    for name, merged in combined.files.items():
        by_measurer.setdefault(merged.measurer, {})[name] = merged.entry
    parts = [
        encode(Part(".", measurer, files, combined.contexts))
        for measurer, files in sorted(
            by_measurer.items(), key=lambda item: item[0].identity
        )
    ]
    path = root / UNION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": FORMAT,
                "units": sorted(set(units)),
                "parts": parts,
                "left-out": list(combined.left_out),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def read_union(root: Path) -> Unioned | None:
    """The union the gate job wrote at *root*; ``None`` when it wrote none.

    Raises:
        PartError: when the file is not a union this workshop wrote,
            naming the file and the key at fault.
    """
    path = root / UNION
    if not path.is_file():
        return None
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PartError(f"{UNION}: the union does not parse ({error})") from None
    parts = raw.get("parts") if isinstance(raw, dict) else None
    units = raw.get("units") if isinstance(raw, dict) else None
    if not isinstance(parts, list) or not isinstance(units, list):
        raise PartError(f"{UNION}: holds no parts and units; a union writes both")
    files: dict[str, Merged] = {}
    for index, item in enumerate(parts):
        part = decode(item, source=f"{UNION}: parts[{index}]")
        for name, entry in part.files.items():
            files[name] = Merged(part.measurer, entry, ())
    left_out: Any = raw.get("left-out", [])
    return Unioned(
        files,
        tuple(str(unit) for unit in units),
        tuple(str(line) for line in left_out) if isinstance(left_out, list) else (),
    )
