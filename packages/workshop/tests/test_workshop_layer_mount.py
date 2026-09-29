"""Layers at mount: the refusals first, then what a layer declares and names."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from livery.workshop import _checks, _layers
from livery.workshop._checks import (
    CheckRecord,
    GateContext,
    register_check,
    unregister_check,
)

_FAILURES = (BaseException,)


def _fake_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **modules: str
) -> None:
    """Importable packages ``acme.<name>`` with the given module bodies."""
    site = tmp_path / "site"
    (site / "acme").mkdir(parents=True, exist_ok=True)
    for name, body in modules.items():
        (site / "acme" / name).mkdir(exist_ok=True)
        (site / "acme" / name / "__init__.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(site))
    import importlib

    importlib.invalidate_caches()
    for name in modules:
        monkeypatch.delitem(__import__("sys").modules, f"acme.{name}", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "acme", raising=False)


def _contract(root: Path, layers: str) -> None:
    (root / "workshop.toml").write_text(f"[workspace]\nlayers = {layers}\n")


@pytest.fixture
def restored_checks():
    state = _checks.snapshot()
    yield
    _checks.restore(state)


def _noop(ctx: GateContext) -> None:
    del ctx


# The refusals first.


def test_an_incompatible_api_version_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(tmp_path, monkeypatch, old="WORKSHOP_API_VERSION = 99\n")
    import acme.old  # type: ignore[import-not-found]

    with pytest.raises(
        RuntimeError,
        match="declares workshop API version 99; this workshop is version 1",
    ):
        _layers.check_api_version("acme.old", acme.old)
    # Mount refuses it before anything registers.
    _contract(tmp_path, '["livery.workshop", "acme.old"]')
    from livery.footman import registry

    with registry.capture(), pytest.raises(RuntimeError, match="API version 99"):
        _layers.mount_layers(tmp_path)


def test_a_missing_dependency_and_a_misordered_one_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(
        tmp_path,
        monkeypatch,
        brand='WORKSHOP_DEPENDS = ("acme.base",)\n',
        base="",
    )
    _contract(tmp_path, '["livery.workshop", "acme.brand"]')
    (problem,) = _layers.closure_problems(tmp_path)
    assert problem == (
        "[workspace] layers lists acme.brand, which depends on acme.base, and"
        " does not list it; the gate's --fix adds it before acme.brand"
    )
    _contract(tmp_path, '["livery.workshop", "acme.brand", "acme.base"]')
    (problem,) = _layers.closure_problems(tmp_path)
    assert problem == (
        "[workspace] layers lists acme.base after acme.brand, which depends on"
        " it; move acme.base before acme.brand"
    )
    # The layering lint carries the closure: a workspace with the
    # contract at its root refuses through it.
    from livery.workshop._packages import verify_workspace

    with pytest.raises(ValueError, match=re.escape("move acme.base before acme.brand")):
        verify_workspace(tmp_path)


def test_a_root_without_a_contract_writes_nothing(tmp_path: Path) -> None:
    # The fix runs for a scoped gate whose root is not a workspace yet.
    assert _layers.write_layers(tmp_path) == []
    assert _layers.closure_problems(tmp_path) == []
    assert not (tmp_path / "workshop.toml").exists()


# Then what a layer declares, written and named.


def test_the_fix_adds_the_missing_layer_before_its_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(
        tmp_path,
        monkeypatch,
        brand='WORKSHOP_DEPENDS = ("acme.base",)\n',
        base="",
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = [\n    "livery.workshop",\n    "acme.brand",\n]\n'
    )
    assert _layers.write_layers(tmp_path) == [
        "  layering: [workspace] layers gains acme.base before acme.brand, which"
        " requires it"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert '    "acme.base",  # required by acme.brand\n    "acme.brand",\n' in text
    assert _layers.closure_problems(tmp_path) == []
    assert _layers.write_layers(tmp_path) == []  # idempotent
    assert _layers.requirers(tmp_path)["acme.base"] == ("acme.brand",)
    # The inline list form takes the entry the same way.
    _contract(tmp_path, '["livery.workshop", "acme.brand"]')
    _layers.write_layers(tmp_path)
    assert (
        'layers = ["livery.workshop", "acme.base", "acme.brand"]'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_an_undeclared_layer_is_taken_at_the_current_version() -> None:
    import types

    _layers.check_api_version("acme.quiet", types.ModuleType("acme.quiet"))


def test_the_gate_names_what_a_layer_registered_and_withdrew(restored_checks) -> None:
    assert _checks.narrowings() == ()
    register_check(CheckRecord("brand-lint", "lint", _noop, layer="acme.brand"))
    unregister_check("typecheck", by="acme.brand")
    assert _checks.narrowings() == (
        "  brand-lint: registered by acme.brand",
        "  typecheck: withdrawn by acme.brand",
    )
    # A layer withdrawing its own check narrows nothing the base owned.
    unregister_check("brand-lint", by="acme.brand")
    assert _checks.narrowings() == ("  typecheck: withdrawn by acme.brand",)
    # Registering the name again clears the withdrawal.
    register_check(CheckRecord("typecheck", "typecheck", _noop))
    assert _checks.narrowings() == ()


def test_the_doctor_lists_installed_layers_the_contract_does_not_mount() -> None:
    advertised = [
        ("livery.workshop", "livery-workshop"),
        ("livery.forge", "livery-forge"),
        ("acme.brand", "acme-brand"),
        ("acme.brand.extra", "acme-brand"),
        ("livery.footman", "livery-footman"),
        ("footman.docs", "livery-footman"),
    ]
    assert _layers.available_layers(
        ("livery.workshop", "livery.forge"), advertised
    ) == [("acme.brand", "acme-brand")]
    assert (
        _layers.available_layers(
            ("livery.workshop", "livery.forge", "acme.brand"), advertised
        )
        == []
    )


# The tool declaration and the contributions by target: refusals and
# the unlisted arms first.


def test_a_layer_declaring_tools_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(tmp_path, monkeypatch, odd='WORKSHOP_TOOLS = "docker"\n')
    _contract(tmp_path, '["livery.workshop", "acme.odd"]')
    with pytest.raises(RuntimeError, match=r"acme\.odd.*WORKSHOP_TOOLS"):
        _layers.layer_tools(tmp_path)
    from livery.footman.context import Failed
    from livery.workshop._tools import requirements

    with pytest.raises(Failed, match=r"acme\.odd"):
        requirements(tmp_path)


def test_an_unlisted_layers_tools_never_enter_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import requirements, tool_names

    _fake_layers(tmp_path, monkeypatch, tooled='WORKSHOP_TOOLS = ("docker>=27",)\n')
    _contract(tmp_path, '["livery.workshop"]')
    assert "layer acme.tooled" not in {r.site for r in requirements(tmp_path)}
    assert "docker" not in tool_names(tmp_path)
    # Listed, the layer is the fourth site, named as the kinds are.
    _contract(tmp_path, '["livery.workshop", "acme.tooled"]')
    found = [r for r in requirements(tmp_path) if r.site == "layer acme.tooled"]
    assert [(r.name, r.floor) for r in found] == [("docker", "27")]
    assert "docker" in tool_names(tmp_path)
    assert "    tools: docker>=27" in _layers.describe_layers(tmp_path)


def test_a_layer_declaring_contributions_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(tmp_path, monkeypatch, odd='WORKSHOP_FOR = ["acme.python"]\n')
    _contract(tmp_path, '["livery.workshop", "acme.odd"]')
    with pytest.raises(RuntimeError, match=r"acme\.odd.*WORKSHOP_FOR"):
        _layers.contributions(tmp_path)


def _house(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A house contributing to a python layer, and the python layer, importable."""
    _fake_layers(
        tmp_path,
        monkeypatch,
        house='WORKSHOP_FOR = {"acme.python": "acme.house_python"}\n',
        house_python="GRAFTED = True\n",
        python="",
    )


def _mount(root: Path) -> None:
    from livery.footman import registry

    with registry.capture():
        _layers.mount_layers(root)


def test_a_contribution_for_an_unlisted_target_never_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    _house(tmp_path, monkeypatch)
    _contract(tmp_path, '["livery.workshop", "acme.house"]')
    _mount(tmp_path)
    assert "acme.house_python" not in sys.modules
    assert _layers.resolved_targets(tmp_path)["acme.house"] == ()
    assert "    for: acme.python" not in _layers.describe_layers(tmp_path)


def test_a_contribution_mounts_once_both_are_listed_whichever_is_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    _house(tmp_path, monkeypatch)
    for listed in (
        '["livery.workshop", "acme.python", "acme.house"]',
        '["livery.workshop", "acme.house", "acme.python"]',
    ):
        monkeypatch.delitem(sys.modules, "acme.house_python", raising=False)
        _contract(tmp_path, listed)
        _mount(tmp_path)
        assert sys.modules["acme.house_python"].GRAFTED is True
        assert _layers.resolved_targets(tmp_path)["acme.house"] == ("acme.python",)
    assert "    for: acme.python" in _layers.describe_layers(tmp_path)


def test_a_contribution_module_that_does_not_import_refuses_naming_all_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_layers(
        tmp_path,
        monkeypatch,
        house='WORKSHOP_FOR = {"acme.python": "acme.absent_module"}\n',
        python="",
    )
    _contract(tmp_path, '["livery.workshop", "acme.python", "acme.house"]')
    with pytest.raises(
        RuntimeError, match=r"acme\.house.*acme\.absent_module.*acme\.python"
    ):
        _mount(tmp_path)


def test_the_fix_records_for_once_and_a_deleted_name_stays_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _house(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = [\n    "livery.workshop",\n    "acme.python",\n'
        '    "acme.house",  # the opinions\n]\n'
    )
    assert _layers.write_layers(tmp_path) == [
        "  layering: [workspace] layers records acme.house for acme.python"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert (
        '    { import = "acme.house", for = ["acme.python"] },  # the opinions\n'
        in text
    )
    assert _layers.write_layers(tmp_path) == []  # written once
    # From then on the list is the truth: a deleted name stays deleted.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', "for = []")
    )
    assert _layers.write_layers(tmp_path) == []
    assert _layers.resolved_targets(tmp_path)["acme.house"] == ()
    assert _layers.closure_problems(tmp_path) == []
    # A name the list does not carry, or the layer does not declare, refuses.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', 'for = ["acme.cpp"]')
    )
    (problem,) = _layers.closure_problems(tmp_path)
    assert problem == (
        "[workspace] layers: the entry for acme.house names acme.cpp in `for`,"
        " and does not list it; list it, or remove it from `for`"
    )
    _contract(tmp_path, '["livery.workshop", "acme.python", "acme.house"]')
    # The inline form takes the entry the same way, and a table entry
    # without `for` gains the key.
    _layers.write_layers(tmp_path)
    assert (
        'layers = ["livery.workshop", "acme.python", { import = "acme.house",'
        ' for = ["acme.python"] }]' in (tmp_path / "workshop.toml").read_text()
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = [\n    "livery.workshop",\n    "acme.python",\n'
        '    { import = "acme.house", dist = "acme-house" },\n]\n'
    )
    _layers.write_layers(tmp_path)
    assert (
        '    { import = "acme.house", dist = "acme-house", for = ["acme.python"] },\n'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_a_root_without_a_contract_lists_no_layers_and_declares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A verb handed a root with no workshop.toml, as the env check's
    # tests do, reads an empty list rather than a missing file.
    monkeypatch.setattr(_layers, "workspace_root", lambda start=None: tmp_path)
    assert _layers.layer_entries(tmp_path) == ()
    assert _layers.layer_targets(tmp_path) == {}
    assert _layers.layer_tools(tmp_path) == {}
    assert _layers.resolved_targets(tmp_path) == {}
