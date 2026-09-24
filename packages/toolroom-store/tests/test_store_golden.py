"""The golden render: a render change fails here until the goldens move with it.

The golden records are small and shaped to exercise the renderer: a
verb gained and one withdrawn across versions, an option added and one
dropped, a platform that read a version and lacked an option, a nested
verb, a choice, a negation and a default. Each version's stub is
checked in beside the records, rendered exactly as a consumer renders
the version it locks, and `fm tools.goldens` is the one way the goldens
move.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.toolroom.store import Catalogue, Record, observations

GOLDENS = Path(__file__).resolve().parent / "goldens"


def golden_records() -> list[Record]:
    """The golden records, by name."""
    return [
        Record.load(path)
        for path in sorted((GOLDENS / "records").iterdir())
        if (path / "tool.json").is_file()
    ]


def golden_path(name: str, version: str) -> Path:
    """Where *name*'s stub at *version* is checked in."""
    return GOLDENS / "stubs" / name / f"{version}.pyi.golden"


def _cases() -> list[tuple[str, str]]:
    return [
        (record.name, found.version)
        for record in golden_records()
        for found in observations(record)
    ]


def test_the_goldens_cover_what_the_renderer_must_keep_deliberate():
    records = {record.name: record for record in golden_records()}
    assert set(records) == {"demo", "solo_inproc"}
    demo = records["demo"]
    assert [o.version for o in observations(demo)] == ["1.0.0", "1.1.0", "2.0.0"]
    newest = observations(demo)[-1]
    assert set(newest.verbs) == {"", "build", "compose.up"}
    root = newest.verbs[""]["options"]
    assert root["color"]["choices"] == ["auto", "always", "never"]
    assert newest.verbs["build"]["options"]["clean"]["negation"] == "--dirty"
    assert "fork" in newest.verbs["build"]["options"]  # arrived in 1.1.0
    assert "jobs" not in newest.verbs["build"]["options"]  # went in 1.1.0


@pytest.mark.parametrize(("name", "version"), _cases())
def test_a_golden_record_renders_to_its_checked_in_stub(name: str, version: str):
    path = golden_path(name, version)
    assert path.is_file(), f"{path} is missing; run `fm tools.goldens` and commit it"
    rendered = Catalogue.of_records(GOLDENS / "records").stub(name, version)
    assert rendered == path.read_text(encoding="utf-8"), (
        f"the renderer moved for {name} {version}: run `fm tools.goldens` and"
        " commit the goldens in the same change"
    )


def test_every_checked_in_golden_belongs_to_a_version():
    """A golden nobody renders is a stale claim; the set is exactly the versions read."""
    expected = {golden_path(name, version) for name, version in _cases()}
    assert set((GOLDENS / "stubs").rglob("*.pyi.golden")) == expected
