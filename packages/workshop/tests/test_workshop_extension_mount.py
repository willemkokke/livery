"""Extensions at mount: the refusals first, then what an extension declares."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from livery.workshop import _checks, _extensions
from livery.workshop._checks import (
    CheckRecord,
    GateContext,
    register_check,
    unregister_check,
)

_FAILURES = (BaseException,)


def _fake_extensions(
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
    # Each fake declares itself as the extension named by its import path.
    from importlib.metadata import EntryPoint

    real = _extensions._declared  # pyright: ignore[reportPrivateUsage]
    fakes = {
        f"acme.{name}": EntryPoint(f"acme.{name}", f"acme.{name}", _extensions.GROUP)
        for name in modules
    }
    previous = dict(real())
    monkeypatch.setattr(_extensions, "_declared", lambda: {**previous, **fakes})


def _contract(root: Path, extensions: str) -> None:
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")


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
    _fake_extensions(tmp_path, monkeypatch, old="API_VERSION = 99\n")
    import acme.old  # type: ignore[import-not-found]

    with pytest.raises(
        RuntimeError,
        match="declares workshop API version 99; this workshop is version 1",
    ):
        _extensions.check_api_version("acme.old", acme.old)
    # Mount refuses it before anything registers.
    _contract(tmp_path, '["acme.old"]')
    from livery.footman import registry

    with registry.capture(), pytest.raises(RuntimeError, match="API version 99"):
        _extensions.mount_extensions(tmp_path)


def test_a_missing_dependency_and_a_misordered_one_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(
        tmp_path,
        monkeypatch,
        brand='REQUIRES = ("acme.base",)\n',
        base="",
    )
    _contract(tmp_path, '["acme.brand"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions lists acme.brand, which depends on acme.base, and"
        " does not list it; the gate's --fix adds it before acme.brand"
    )
    _contract(tmp_path, '["acme.brand", "acme.base"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions lists acme.base after acme.brand, which depends on"
        " it; move acme.base before acme.brand"
    )
    # The layering lint carries the closure: a workspace with the
    # contract at its root refuses through it.
    from livery.workshop._packages import verify_workspace

    with pytest.raises(ValueError, match=re.escape("move acme.base before acme.brand")):
        verify_workspace(tmp_path)


def test_a_root_without_a_contract_writes_nothing(tmp_path: Path) -> None:
    # The fix runs for a scoped gate whose root is not a workspace yet.
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.closure_problems(tmp_path) == []
    assert not (tmp_path / "workshop.toml").exists()


# Then what an extension declares, written and named.


def test_the_fix_adds_the_missing_extension_before_its_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(
        tmp_path,
        monkeypatch,
        brand='REQUIRES = ("acme.base",)\n',
        base="",
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.brand",\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions gains acme.base before acme.brand, which"
        " requires it"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert '    "acme.base",  # required by acme.brand\n    "acme.brand",\n' in text
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []  # idempotent
    assert _extensions.requirers(tmp_path)["acme.base"] == ("acme.brand",)
    # The inline list form takes the entry the same way.
    _contract(tmp_path, '["acme.brand"]')
    _extensions.write_extensions(tmp_path)
    assert (
        'extensions = ["acme.base", "acme.brand"]'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_an_undeclared_extension_is_taken_at_the_current_version() -> None:
    import types

    _extensions.check_api_version("acme.quiet", types.ModuleType("acme.quiet"))


def test_the_gate_names_what_a_extension_registered_and_withdrew(
    restored_checks,
) -> None:
    assert _checks.narrowings() == ()
    register_check(CheckRecord("brand", "lint", _noop, extension="acme.brand"))
    unregister_check("typecheck.mypy", by="acme.brand")
    assert _checks.narrowings() == (
        "  lint.brand: registered by acme.brand",
        "  typecheck.mypy: withdrawn by acme.brand",
    )
    # An extension withdrawing its own check narrows nothing the base owned.
    unregister_check("lint.brand", by="acme.brand")
    assert _checks.narrowings() == ("  typecheck.mypy: withdrawn by acme.brand",)
    # Registering the name again clears the withdrawal.
    register_check(CheckRecord("mypy", "typecheck", _noop))
    assert _checks.narrowings() == ()


def test_the_doctor_lists_installed_extensions_the_contract_does_not_mount() -> None:
    advertised = [
        ("docs", "livery-workshop"),
        ("livery.forge", "livery-forge"),
        ("acme.brand", "acme-brand"),
    ]
    assert _extensions.available_extensions(("livery.forge",), advertised) == [
        ("docs", "livery-workshop"),
        ("acme.brand", "acme-brand"),
    ]
    assert (
        _extensions.available_extensions(
            ("docs", "livery.forge", "acme.brand"), advertised
        )
        == []
    )


def test_a_footman_plugin_is_not_offered_as_an_extension() -> None:
    # footman's own task providers declare no extension: never offered,
    # however many verbs they add.
    offered = {name for name, _ in _extensions.available_extensions(())}
    assert "footman.self" not in offered and "footman.janitor" not in offered
    assert "docs" in offered


def test_an_extension_listed_at_the_wrong_level_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(
        tmp_path,
        monkeypatch,
        native='LEVELS = ("package",)\n',
        site="",
    )
    # A package-level extension in the workspace list: the mount refuses.
    _contract(tmp_path, '["acme.native"]')
    from livery.footman import registry

    with registry.capture(), pytest.raises(BaseException) as caught:
        _extensions.mount_extensions(tmp_path)
    assert "declares the levels package" in str(caught.value)
    assert (
        "[workspace] extensions lists acme.native, which is listed in a"
        " package's `extensions` alone; move it there"
    ) in _extensions.closure_problems(tmp_path)
    # A workspace-level extension in a package's list: named the same way.
    _contract(tmp_path, "[]")
    member = tmp_path / "packages" / "core"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'kind = "python"\nextensions = ["acme.site"]\n'
    )
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` lists acme.site, which is listed in"
        " [workspace] extensions alone; move it there"
    ]


# The tool declaration and the contributions by target: refusals and
# the unlisted arms first.


def test_a_extension_declaring_tools_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(tmp_path, monkeypatch, odd='TOOLS = "docker"\n')
    _contract(tmp_path, '["acme.odd"]')
    with pytest.raises(RuntimeError, match=r"acme\.odd.*TOOLS"):
        _extensions.extension_tools(tmp_path)
    from livery.footman.context import Failed
    from livery.workshop._tools import requirements

    with pytest.raises(Failed, match=r"acme\.odd"):
        requirements(tmp_path)


def test_an_unlisted_extensions_tools_never_enter_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import requirements, tool_names

    _fake_extensions(tmp_path, monkeypatch, tooled='TOOLS = ("docker>=27",)\n')
    _contract(tmp_path, "[]")
    assert "extension acme.tooled" not in {r.site for r in requirements(tmp_path)}
    assert "docker" not in tool_names(tmp_path)
    # Listed, the extension is the fourth site, named as the kinds are.
    _contract(tmp_path, '["acme.tooled"]')
    found = [r for r in requirements(tmp_path) if r.site == "extension acme.tooled"]
    assert [(r.name, r.floor) for r in found] == [("docker", "27")]
    assert "docker" in tool_names(tmp_path)
    assert "    tools: docker>=27" in _extensions.describe_extensions(tmp_path)


def test_a_extension_declaring_contributions_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(tmp_path, monkeypatch, odd='FOR = ["acme.python"]\n')
    _contract(tmp_path, '["acme.odd"]')
    with pytest.raises(RuntimeError, match=r"acme\.odd.*FOR"):
        _extensions.contributions(tmp_path)


def _house(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A house contributing to a python extension, and that extension, importable."""
    _fake_extensions(
        tmp_path,
        monkeypatch,
        house='FOR = {"acme.python": "acme.house_python"}\n',
        house_python="GRAFTED = True\n",
        python="",
    )


def _mount(root: Path) -> None:
    from livery.footman import registry

    with registry.capture():
        _extensions.mount_extensions(root)


def test_a_for_naming_a_target_the_extension_declares_nothing_for_mounts_and_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mount goes on without the target, and the layering check,
    # which runs inside a working fm, names the entry.
    import sys

    _house(tmp_path, monkeypatch)
    _fake_extensions(tmp_path, monkeypatch, cpp="")
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    "acme.cpp",\n    { name = "acme.house", for = ["acme.cpp"] },\n]\n'
    )
    _mount(tmp_path)
    assert "acme.house_python" not in sys.modules
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and acme.house declares no contribution for it; remove it from `for`"
    )


def test_a_contribution_for_an_unlisted_target_never_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    _house(tmp_path, monkeypatch)
    _contract(tmp_path, '["acme.house"]')
    _mount(tmp_path)
    assert "acme.house_python" not in sys.modules
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    assert "    for: acme.python" not in _extensions.describe_extensions(tmp_path)


def test_a_contribution_mounts_once_both_are_listed_whichever_is_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    _house(tmp_path, monkeypatch)
    for listed in (
        '["acme.python", "acme.house"]',
        '["acme.house", "acme.python"]',
    ):
        monkeypatch.delitem(sys.modules, "acme.house_python", raising=False)
        _contract(tmp_path, listed)
        _mount(tmp_path)
        assert sys.modules["acme.house_python"].GRAFTED is True
        assert _extensions.resolved_targets(tmp_path)["acme.house"] == ("acme.python",)
    assert "    for: acme.python" in _extensions.describe_extensions(tmp_path)


def test_a_contribution_module_that_does_not_import_refuses_naming_all_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_extensions(
        tmp_path,
        monkeypatch,
        house='FOR = {"acme.python": "acme.absent_module"}\n',
        python="",
    )
    _contract(tmp_path, '["acme.python", "acme.house"]')
    with pytest.raises(
        RuntimeError, match=r"acme\.house.*acme\.absent_module.*acme\.python"
    ):
        _mount(tmp_path)


def test_the_fix_records_for_once_and_a_deleted_name_stays_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _house(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    "acme.house",  # the opinions\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions records acme.house for acme.python"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert (
        '    { name = "acme.house", for = ["acme.python"] },  # the opinions\n' in text
    )
    assert _extensions.write_extensions(tmp_path) == []  # written once
    # From then on the list is the truth: a deleted name stays deleted.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', "for = []")
    )
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    assert _extensions.closure_problems(tmp_path) == []
    # A name the list does not carry, or the extension does not declare, refuses.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', 'for = ["acme.cpp"]')
    )
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and does not list it; list it, or remove it from `for`"
    )
    _contract(tmp_path, '["acme.python", "acme.house"]')
    # The inline form takes the entry the same way, and a table entry
    # without `for` gains the key.
    _extensions.write_extensions(tmp_path)
    assert (
        'extensions = ["acme.python", { name = "acme.house",'
        ' for = ["acme.python"] }]' in (tmp_path / "workshop.toml").read_text()
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    { name = "acme.house" },\n]\n'
    )
    _extensions.write_extensions(tmp_path)
    assert (
        '    { name = "acme.house", for = ["acme.python"] },\n'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_a_root_without_a_contract_lists_no_extensions_and_declares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A verb handed a root with no workshop.toml, as the env check's
    # tests do, reads an empty list rather than a missing file.
    monkeypatch.setattr(_extensions, "workspace_root", lambda start=None: tmp_path)
    assert _extensions.extension_entries(tmp_path) == ()
    assert _extensions.extension_targets(tmp_path) == {}
    assert _extensions.extension_tools(tmp_path) == {}
    assert _extensions.resolved_targets(tmp_path) == {}
