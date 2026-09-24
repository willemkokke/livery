"""Read a record in its earlier form, one directory per tool, for the conversion.

The earlier form is ``records/<tool>/tool.json`` and
``records/<tool>/deltas/<nnnn>-<version>.json``, a delta carrying a
verb whole when one option of it changed. Only ``fm
tools.convert-records`` reads it, to write the line form the store
reads ([livery.toolroom.store.Record][]) and to prove that every
version resolves to the same surface, absences and date afterwards.
Nothing else reaches for this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.bench import _surfaces
from livery.toolroom.store import Artifact, Layout, Record, RecordDelta


@dataclass(frozen=True)
class Resolved:
    """One version as the earlier form resolved it: the whole surface, or none."""

    version: str
    date: str
    platforms: tuple[str, ...]
    extractor: int
    help: str
    verbs: dict[str, dict[str, Any]]
    absent: dict[str, dict[str, tuple[str, ...]]]


def _canonical(verb: dict[str, Any]) -> dict[str, Any]:
    return {
        "help": verb["help"],
        "wraps": verb["wraps"],
        "positional": verb["positional"],
        "lead": verb["lead"],
        "options": {
            name: {
                "flags": list(option["flags"]),
                "negation": option["negation"],
                "help": option["help"],
                "type": option["type"],
                "default": option["default"],
                "choices": list(option["choices"]),
            }
            for name, option in sorted(verb["options"].items())
        },
    }


def is_directory_record(path: Path) -> bool:
    """Whether *path* is a record in the earlier form."""
    return path.is_dir() and (path / "tool.json").is_file()


def read_directory(
    directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Resolved | None]]:
    """The tool axis, the deltas as written, and each version resolved.

    The resolution is the earlier form's own: a verb named in a delta
    replaces the verb whole, a verb set to null is withdrawn, and the
    description is inherited until a delta sets it.
    """
    tool = json.loads((directory / "tool.json").read_text("utf-8"))
    files = sorted((directory / "deltas").glob("*.json"))
    deltas = [json.loads(path.read_text("utf-8")) for path in files]
    resolved: list[Resolved | None] = []
    help_ = ""
    verbs: dict[str, dict[str, Any]] = {}
    for delta in deltas:
        surface = delta.get("surface")
        if surface is None:
            resolved.append(None)
            continue
        if "help" in surface:
            help_ = surface["help"]
        for name, verb in (surface.get("verbs") or {}).items():
            if verb is None:
                verbs.pop(name, None)
            else:
                verbs[name] = _canonical(verb)
        resolved.append(
            Resolved(
                delta["version"],
                delta.get("date", ""),
                tuple(surface["platforms"]),
                int(surface["extractor"]),
                help_,
                {name: verbs[name] for name in sorted(verbs)},
                {
                    verb: {option: tuple(who) for option, who in options.items()}
                    for verb, options in (surface.get("absent") or {}).items()
                },
            )
        )
    return tool, deltas, resolved


def convert(directory: Path, *, prime: str = "") -> Record:
    """The record under *directory*, as the line form carries it.

    Each version's resolved surface is stated against the one before
    it, one option at a time, through the bench's own writer, so the
    record converts the way a fresh reading would have written it.
    *prime* is stamped on the axis when given.
    """
    tool, deltas, resolved = read_directory(directory)
    axis = Record.from_json(
        {**tool, **({"prime": prime} if prime else {})},
        (),
        where=f"{directory.name}/tool.json",
    )
    readings: list[_surfaces._Reading] = []
    for delta, found in zip(deltas, resolved, strict=True):
        arrived = RecordDelta(
            0,
            delta["version"],
            delta.get("date", ""),
            {
                host: Artifact.from_json(raw, where=f"{directory.name} {host}")
                for host, raw in delta.get("artifacts", {}).items()
            },
            Layout.from_json(delta.get("layout", {}), where=f"{directory.name} layout"),
            {
                host: Layout.from_json(raw, where=f"{directory.name} {host}")
                for host, raw in delta.get("host_layouts", {}).items()
            },
        )
        readings.append(
            _surfaces._Reading(
                arrived,
                None if found is None else {"help": found.help, "verbs": found.verbs},
                [] if found is None else list(found.platforms),
                _surfaces.EXTRACTOR if found is None else found.extractor,
                {} if found is None else _surfaces._flatten(found.absent),
            )
        )
    return _surfaces._assemble(axis, readings)
