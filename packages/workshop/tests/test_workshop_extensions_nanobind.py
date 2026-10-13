"""The nanobind extension in the wheel: listed by a package, declaring nothing."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

# The refusal first: the workspace's list is no place for it.


def test_nanobind_in_the_workspace_list_is_named_as_package_level(
    tmp_path: Path,
) -> None:
    from livery.workshop._extensions import level_problems

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["nanobind"]\n')
    assert level_problems(tmp_path) == [
        "[workspace] extensions lists nanobind, which is listed in a package's"
        " `extensions` alone; move it there"
    ]


def test_the_wheel_ships_nanobind_at_a_package_level_declaring_nothing() -> None:
    from livery.workshop._declaration import Additions
    from livery.workshop._extensions import declaration

    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="workshop.extensions")
    }
    assert found["nanobind"] == "livery.extensions.nanobind"
    declared = declaration("nanobind")
    assert declared is not None
    assert declared.levels == ("package",)
    assert declared.requires == ()
    # Nothing to mount, install, answer, run, categorise or write: the
    # python-nanobind kind does all of it for its packages.
    assert declared.additions == Additions()
    assert declared.plugin == ""
    assert declared.tools == ()
    assert declared.queries == {}
    assert declared.phases == {}
    assert declared.categories == ()
    assert declared.fragments == ()
    assert declared.targets == {}
