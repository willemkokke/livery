"""A fake measurer for the coverage tests, and parts in the line format to hand it.

The coverage extension asks its ``coverage.measurers`` query for each
measurer's reduction of the data a run left. The fake extension
``acme.meter`` answers for ``arcs`` while coverage is listed: its
reduction hands back the parts a test left in ``meter.json`` at the
workspace root, and writes what a leg asked of it to ``asked.json``.
A record's unit keeps its parts in the stored form: `stored_unit`
makes one of parts as JSON holds them, and `ran` reads one back.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage._record import Unit
from workshop_extension_fakes import fake_extensions, fake_steps

#: The fake measurer's declaration.
METER = """
[extension]
api-version = 1
levels = ["workspace"]

[for.coverage.queries]
"coverage.measurers" = "{package}._steps:measurers"
"""

#: The fake measurer's reduction: the parts a test left, by unit.
METER_STEPS = """
import json
from pathlib import Path


class Meter:
    def held(self, root):
        found = json.loads((Path(root) / "meter.json").read_text())
        if "refuse" in found:
            raise ValueError(found["refuse"])
        return found

    def leg(self, root, units):
        held = self.held(root)
        (Path(root) / "asked.json").write_text(json.dumps(list(units)))
        parts = held.get("units", {})
        return [parts[unit["unit"]] for unit in units][: held.get("cut", None)]

    def left(self, root):
        if not (Path(root) / "meter.json").is_file():
            return []
        return self.held(root).get("left", [])


def measurers(root):
    return {"arcs": Meter()}


def noop(ctx):
    del ctx
"""

#: coverage.py's measurer as a part names it.
COVERAGE_PY = fmt.Measurer(
    "coverage.py",
    "7.16.2",
    "CPython 3.14",
    fmt.PRESENCE,
    fmt.TARGETS,
    ("function", "class"),
    (fmt.LINES, fmt.BRANCHES),
)

#: A native measurer as the cmake extension's parts name it: counts per
#: line, and lines alone judged.
GCOV = fmt.Measurer("gcov", counts=fmt.COUNTS)


def install_meter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the fake measurer ``acme.meter`` for the test alone."""
    fake_extensions(tmp_path, monkeypatch, meter=METER)
    fake_steps(tmp_path, "meter", METER_STEPS)


def meter(root: Path, **held: Any) -> None:
    """Leave *held* where the fake measurer reads it: its units, left or refusal."""
    (root / "meter.json").write_text(json.dumps(held))


def asked(root: Path) -> list[dict[str, Any]]:
    """What a leg last asked the fake measurer for."""
    found: list[dict[str, Any]] = json.loads((root / "asked.json").read_text())
    return found


def part(
    root: Path,
    unit: str,
    files: dict[str, list[int]],
    *,
    measurer: fmt.Measurer = COVERAGE_PY,
) -> dict[str, Any]:
    """*measurer*'s reading of *files* as JSON holds it: every line a statement.

    Each file under *root* counts each of its lines as a statement, and
    the lines given ran once; its digest is the file's own.
    """
    entries: dict[str, fmt.Entry] = {}
    for name, ran in files.items():
        content = (root / name).read_bytes()
        entries[name] = fmt.Entry(
            digest=fmt.digest(content),
            statements=frozenset(range(1, content.count(b"\n") + 1)),
            counts=dict.fromkeys(ran, 1),
        )
    return fmt.encode(fmt.Part(unit, measurer, entries))


def stored_unit(
    path: str,
    parts: Sequence[dict[str, Any]],
    *,
    closure: str = "k" * 64,
    run: str = "7",
    sha: str = "a" * 40,
) -> Unit:
    """The unit at *path* holding *parts*, parts as JSON holds them, as a record does.

    Each part is kept in its stored form, and the unit holds the fixed
    facts they name.
    """
    held: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    for raw in parts:
        kept, named = fmt.stored(fmt.decode(raw, source="a test's part"))
        held.append(kept)
        facts.update(named)
    return Unit(path, closure, run, sha, tuple(held), facts)


def read_parts(unit: Unit) -> list[fmt.Part]:
    """*unit*'s parts, read from their stored form with its facts."""
    return [
        fmt.from_stored(raw, unit.facts, source=f"{unit.path}, part {index}")
        for index, raw in enumerate(unit.parts)
    ]


def ran(unit: Unit) -> dict[str, set[int]]:
    """The lines *unit*'s parts say ran, by file."""
    found: dict[str, set[int]] = {}
    for decoded in read_parts(unit):
        for name, entry in decoded.files.items():
            found.setdefault(name, set()).update(entry.executed)
    return found
