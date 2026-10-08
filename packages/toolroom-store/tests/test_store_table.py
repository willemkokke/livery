"""The `[toolroom]` table: read from `workshop.toml` or `toolroom.toml`, and its schema."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from livery.toolroom.store import (
    MODES,
    SCHEMA_FRAGMENT,
    TABLE,
    TableError,
    read_table,
)

# --- the refusals ---------------------------------------------------------------


def test_a_file_that_is_not_toml_refuses_naming_it(tmp_path: Path) -> None:
    (tmp_path / "toolroom.toml").write_text("[toolroom\n", encoding="utf-8")
    with pytest.raises(TableError, match=r"toolroom\.toml: not TOML: "):
        read_table(tmp_path)


def test_a_toolroom_key_that_is_no_table_refuses_naming_the_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text('toolroom = "ruff"\n', encoding="utf-8")
    with pytest.raises(
        TableError, match=r"workshop\.toml: \[toolroom\] is str; it must be a table"
    ):
        read_table(tmp_path)


# --- the reader -----------------------------------------------------------------


def test_a_directory_with_neither_file_has_an_empty_table(tmp_path: Path) -> None:
    assert read_table(tmp_path) == {}
    (tmp_path / "toolroom.toml").write_text("[other]\nkey = 1\n", encoding="utf-8")
    assert read_table(tmp_path) == {}


def test_toolroom_toml_holds_the_table_where_there_is_no_workshop_toml(
    tmp_path: Path,
) -> None:
    (tmp_path / "toolroom.toml").write_text(
        '[toolroom]\nindex = "records"\nrequires = ["ruff>=0.16"]\n', encoding="utf-8"
    )
    assert read_table(tmp_path) == {"index": "records", "requires": ["ruff>=0.16"]}


def test_workshop_toml_wins_over_toolroom_toml(tmp_path: Path) -> None:
    (tmp_path / "toolroom.toml").write_text(
        '[toolroom]\nrequires = ["ruff"]\n', encoding="utf-8"
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\n\n[toolroom]\nrequires = ["git"]\n', encoding="utf-8"
    )
    assert read_table(tmp_path) == {"requires": ["git"]}
    # A workshop.toml without the table is still the one read.
    (tmp_path / "workshop.toml").write_text("[workspace]\n", encoding="utf-8")
    assert read_table(tmp_path) == {}


# --- the schema fragment --------------------------------------------------------


def test_the_fragment_declares_the_table_alone_in_each_contract() -> None:
    declared = tomllib.loads(SCHEMA_FRAGMENT.read_text(encoding="utf-8"))
    assert set(declared) == {"contract"}
    assert set(declared["contract"]) == {"root", "package", "extension"}
    for tables in declared["contract"].values():
        assert set(tables) == {TABLE}


def test_the_modes_the_fragment_names_are_the_ones_the_store_reads() -> None:
    declared = tomllib.loads(SCHEMA_FRAGMENT.read_text(encoding="utf-8"))
    modes = declared["contract"]["root"][TABLE]["modes"]["*"]["values"]
    assert tuple(modes) == tuple(MODES)
