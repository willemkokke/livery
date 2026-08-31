"""The layer walk reads the contract and mounts only what it names."""

from __future__ import annotations

from pathlib import Path

from livery.workshop import layer_names, mount_layers, workspace_root

ROOT = Path(__file__).resolve().parents[3]


def test_this_workspace_declares_the_workshop_as_its_base() -> None:
    assert workspace_root(ROOT / "packages") == ROOT
    assert layer_names(ROOT) == ("livery.workshop",)


def test_outside_a_workspace_there_are_no_layers(tmp_path: Path) -> None:
    assert workspace_root(tmp_path) is None
    assert layer_names(tmp_path) == ()
    assert mount_layers(tmp_path) == ()


def test_the_workshop_never_mounts_itself() -> None:
    # The one declared layer is this package, so the walk mounts
    # nothing further; a second layer in the list would be grafted.
    assert mount_layers(ROOT) == ()


def test_a_contract_without_layers_names_none(tmp_path: Path) -> None:
    (tmp_path / "livery.toml").write_text('[forge]\nkind = "github"\n')
    assert layer_names(tmp_path) == ()
