"""The tools a project's plugins declare, in a data module of their own."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.footman import Failed


def _plugin(monkeypatch: pytest.MonkeyPatch, declared: object) -> None:
    """A plugin `acme` the project's rung mounts, declaring *declared* as its tools."""
    from livery.footman import _config, _entries  # pyright: ignore[reportPrivateUsage]

    entry = SimpleNamespace(
        name="acme",
        group="workshop.tools",
        value="acme_tools:TOOLS",
        load=lambda: declared,
    )
    monkeypatch.setattr(_entries, "_SCAN", (*_entries.installed_entry_points(), entry))
    monkeypatch.setattr(_config, "project_builtin", lambda root: ("acme",))


def test_a_tools_declaration_off_the_shape_refuses_naming_the_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import plugin_tools

    _plugin(monkeypatch, "docker")
    with pytest.raises(
        Failed,
        match=r"plugin acme: its workshop.tools entry point \(acme_tools:TOOLS\)",
    ):
        plugin_tools(tmp_path)


def test_a_plugins_tools_come_from_its_data_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import plugin_tools

    _plugin(monkeypatch, ("docker?", "tea@!windows"))
    assert plugin_tools(tmp_path) == {"acme": ("docker?", "tea@!windows")}


def test_this_repository_takes_docker_from_the_forge_plugin() -> None:
    from livery.workshop._tools import plugin_tools, requirements

    root = Path(__file__).resolve().parents[3]
    assert plugin_tools(root)["livery.forge"] == ("docker?",)
    docker = [r for r in requirements(root) if r.name == "docker"]
    assert [(r.site, r.optional) for r in docker] == [("plugin livery.forge", True)]


def test_footman_and_the_workshop_share_one_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.footman import _config, _entries  # pyright: ignore[reportPrivateUsage]
    from livery.workshop import _contract_keys, _extensions
    from livery.workshop._tools import plugin_tools

    scans: list[int] = []
    real = _entries._scan  # pyright: ignore[reportPrivateUsage]

    def counted() -> tuple[object, ...]:
        scans.append(1)
        return real()

    monkeypatch.setattr(_entries, "_scan", counted)
    _contract_keys.declarations.cache_clear()
    try:
        root = Path(__file__).resolve().parents[3]
        _config.project_builtin(root)
        _extensions.declaration("docs")
        _contract_keys.declarations()
        plugin_tools(root)
        _entries.installed_entry_points("footman.tasks")
    finally:
        _contract_keys.declarations.cache_clear()
    assert scans == [1]
