"""A fake measurer, and parts in the line format, for the docs suite's coverage pages.

The coverage extension adds a step to each of the docs extension's
phases, and asks its ``coverage.measurers`` query for each measurer's
reduction of the data a run left. The fake extension ``acme.meter``
answers for ``arcs`` while coverage is listed. A native measurer leaves
its parts in the workspace itself, and `leave_part` leaves one there as
it would; `stored_unit` makes a record's unit of parts as JSON holds
them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from extensions_docs_workspace import fake_extensions, fake_steps
from livery import git
from livery.extensions.coverage import _format as fmt
from livery.extensions.coverage._parts import PARTS
from livery.extensions.coverage._record import Unit

#: The fake measurer's declaration.
METER = """
[extension]
api-version = 1

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

#: A native measurer as the cmake extension's parts name it: counts per
#: line, and lines alone judged.
GCOV = fmt.Measurer("gcov", counts=fmt.COUNTS)


def install_meter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the fake measurer ``acme.meter`` for the test alone."""
    fake_extensions(tmp_path, monkeypatch, meter=METER)
    fake_steps(tmp_path, "meter", METER_STEPS)


def leave_part(
    root: Path,
    unit: str,
    files: Mapping[str, Mapping[int, int]],
    *,
    measurer: fmt.Measurer = GCOV,
) -> Path:
    """Leave *measurer*'s part of *unit* where a native measurer leaves it; its path.

    Each file's statements are the lines *files* gives it, each with
    the times it ran; its digest is the file's own where *root* holds
    it, else empty. The part lands under ``PARTS``, in a directory named
    after the measurer, as ``<unit's slug>.json``.
    """
    entries: dict[str, fmt.Entry] = {}
    for name, lines in files.items():
        source = root / name
        entries[name] = fmt.Entry(
            digest=fmt.digest(source.read_bytes()) if source.is_file() else "",
            statements=frozenset(lines),
            counts={line: runs for line, runs in lines.items() if runs > 0},
        )
    path = root / PARTS / measurer.name / f"{git.slug(unit)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = fmt.encode(fmt.Part(unit, measurer, entries))
    path.write_text(json.dumps(encoded, sort_keys=True) + "\n", encoding="utf-8")
    return path


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
