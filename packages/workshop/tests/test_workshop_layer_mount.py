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
    before, withdrawn = dict(_checks._CHECKS), dict(_checks._WITHDRAWN)
    yield
    _checks._CHECKS.clear()
    _checks._CHECKS.update(before)
    _checks._WITHDRAWN.clear()
    _checks._WITHDRAWN.update(withdrawn)


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
