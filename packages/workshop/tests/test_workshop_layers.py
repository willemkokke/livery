"""The layer walk reads the contract and mounts only what it names."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import layer_names, mount_layers, workspace_root

ROOT = Path(__file__).resolve().parents[3]


def test_this_workspace_declares_the_workshop_as_its_base() -> None:
    assert workspace_root(ROOT / "packages") == ROOT
    assert layer_names(ROOT) == ("livery.workshop", "livery.forge")


def test_outside_a_workspace_there_are_no_layers(tmp_path: Path) -> None:
    assert workspace_root(tmp_path) is None
    assert layer_names(tmp_path) == ()
    assert mount_layers(tmp_path) == ()


def test_the_workshop_never_mounts_itself() -> None:
    # The walk skips this package (importing it IS the base layer
    # arriving) and grafts only the further layers the contract names.
    assert mount_layers(ROOT) == ("livery.forge",)


def test_a_contract_without_layers_names_none(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text('[forge]\nkind = "github"\n')
    assert layer_names(tmp_path) == ()


def test_an_uninstalled_layer_teaches_its_name_and_the_dev_group(
    tmp_path: Path,
) -> None:
    from livery.workshop._layers import mount_layers

    (tmp_path / "workshop.toml").write_text('[workspace]\nlayers = ["acme.missing"]\n')
    with pytest.raises(RuntimeError) as caught:
        mount_layers(tmp_path)
    text = str(caught.value)
    assert "acme.missing" in text
    assert "acme-missing" in text and "dev group" in text


def test_layer_entries_derive_and_declare(tmp_path: Path) -> None:
    from livery.workshop._layers import layer_entries, layer_names

    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n"
        "layers = [\n"
        '  "livery.workshop",\n'
        '  { import = "brand.layer", dist = "brand-devkit" },\n'
        "]\n"
    )
    assert layer_entries(tmp_path) == (
        ("livery.workshop", "livery-workshop"),
        ("brand.layer", "brand-devkit"),
    )
    assert layer_names(tmp_path) == ("livery.workshop", "brand.layer")


def test_render_injections_deduplicate_member_layers(tmp_path: Path) -> None:
    from livery.workshop._templates import render_injections

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop", "livery.forge"]\n'
    )
    answers = {
        "packages": [
            {"dir": "forge", "name": "livery-forge", "dev": "livery-forge[extra]"}
        ]
    }
    injections = render_injections(tmp_path, answers)
    # A layer that is also a member rides the roster's spelling, once.
    assert injections["layer_requirements"] == ["livery-workshop"]
    assert injections["layer_imports"] == ["livery.workshop", "livery.forge"]


def test_a_branded_builtin_layer_is_the_apps_to_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A brand mounts its builtin set as the cascade's base rung, and
    # mount_layers runs inside that very mount, so re-mounting a
    # sibling builtin would claim the same tasks twice in one rung.
    # The layer does not even exist here: skipped means never
    # imported, which is the proof.
    from footman import _paths  # pyright: ignore[reportPrivateUsage]

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop", "acme.missing"]\n'
    )
    monkeypatch.setattr(_paths, "_builtin", ("livery.workshop", "acme.missing"))
    assert mount_layers(tmp_path) == ()
    # Off the builtin set, the same absent layer still refuses with
    # the teaching: the skip is the brand's, never a silent pardon.
    monkeypatch.setattr(_paths, "_builtin", ())
    with pytest.raises(RuntimeError, match=r"acme\.missing"):
        mount_layers(tmp_path)
