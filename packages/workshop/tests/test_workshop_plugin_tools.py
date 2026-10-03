"""The tools a project's plugins declare: read from source, never imported."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.footman.api import Failed


def _plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """A plugin module `acme_plugin` the project's rung mounts as `acme`."""
    site = tmp_path / "site"
    site.mkdir()
    # Importing it would raise: the read must never import.
    (site / "acme_plugin.py").write_text("raise RuntimeError('imported')\n" + body)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(sys.modules, "acme_plugin", raising=False)
    import importlib.metadata

    real = importlib.metadata.entry_points

    def fake(group: str | None = None) -> list[object]:
        found: list[object] = list(real(group=group)) if group else []
        if group == "footman.tasks":
            found.append(SimpleNamespace(name="acme", value="acme_plugin"))
        return found

    monkeypatch.setattr(importlib.metadata, "entry_points", fake)
    from livery.footman import _config  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(_config, "project_builtin", lambda root: ("acme",))
    return tmp_path


def test_a_tools_declaration_off_the_shape_refuses_naming_the_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import plugin_tools

    root = _plugin(tmp_path, monkeypatch, 'TOOLS = "docker"\n')
    with pytest.raises(
        Failed, match="plugin acme: TOOLS in acme_plugin is not a literal"
    ):
        plugin_tools(root)


def test_a_plugins_tools_are_read_without_importing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import plugin_tools

    root = _plugin(tmp_path, monkeypatch, 'TOOLS = ("docker?", "tea@!windows")\n')
    assert plugin_tools(root) == {"acme": ("docker?", "tea@!windows")}
    assert "acme_plugin" not in sys.modules


def test_this_repository_takes_docker_from_the_forge_plugin() -> None:
    from livery.workshop._tools import plugin_tools, requirements

    root = Path(__file__).resolve().parents[3]
    assert plugin_tools(root)["livery.forge"] == ("docker?",)
    docker = [r for r in requirements(root) if r.name == "docker"]
    assert [(r.site, r.optional) for r in docker] == [("plugin livery.forge", True)]
