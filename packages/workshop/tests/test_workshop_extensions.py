"""The extension walk reads the contract and mounts only what it names."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop.api import extension_names, mount_extensions, workspace_root

ROOT = Path(__file__).resolve().parents[3]


# The refusals first.


def test_a_contract_without_extensions_refuses_printing_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import registry
    from livery.workshop._extensions import closure_problems

    (tmp_path / "workshop.toml").write_text('[workspace]\n\n[forge]\nkind = "github"\n')
    # The gate refuses it, printing the line to add.
    (problem,) = closure_problems(tmp_path)
    assert "[workspace] extensions is required" in problem
    assert "\n  extensions = []\n" in problem
    # The mount names it and goes on: every command mounts, the sync
    # that repairs an environment among them.
    assert extension_names(tmp_path) == ()
    with registry.capture():
        assert mount_extensions(tmp_path) == ()
    assert "[workspace] extensions is required" in capsys.readouterr().err


def test_an_uninstalled_extension_is_named_and_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import registry
    from livery.workshop._extensions import closure_problems

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.missing"]\n'
    )
    with registry.capture():
        assert mount_extensions(tmp_path) == ()
    err = capsys.readouterr().err
    assert "'acme.missing'" in err and "workshop.extensions" in err
    assert "(acme-missing by its name)" in err
    assert closure_problems(tmp_path) == [
        "[workspace] extensions lists acme.missing, which no installed"
        " distribution declares in workshop.extensions"
    ]


def test_a_branded_builtin_extension_is_the_apps_to_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A brand mounts its builtin set as the cascade's base rung, and
    # mount_extensions runs inside that very mount, so re-mounting a
    # sibling builtin would claim the same tasks twice in one rung.
    # The extension does not even exist here: skipped means never
    # looked up, which is the proof.
    from livery.footman import _paths  # pyright: ignore[reportPrivateUsage]

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.missing"]\n'
    )
    monkeypatch.setattr(_paths, "_builtin", ("livery.workshop", "acme.missing"))
    assert mount_extensions(tmp_path) == ()
    assert capsys.readouterr().err == ""
    # Off the builtin set, the same absent extension is named.
    monkeypatch.setattr(_paths, "_builtin", ())
    assert mount_extensions(tmp_path) == ()
    assert "acme.missing" in capsys.readouterr().err


# Then the walk.


def test_this_workspace_lists_its_extensions_and_never_the_base() -> None:
    assert workspace_root(ROOT / "packages") == ROOT
    assert extension_names(ROOT) == ("docs", "ruff", "basedpyright", "mypy")


def test_outside_a_workspace_there_are_no_extensions(tmp_path: Path) -> None:
    assert workspace_root(tmp_path) is None
    assert extension_names(tmp_path) == ()
    assert mount_extensions(tmp_path) == ()


def test_the_listed_extensions_mount_and_the_base_never_does(tmp_path: Path) -> None:
    from livery.footman import registry

    # A scratch contract naming the extension the workshop's own wheel
    # ships, so the suite needs nothing beyond the package's own
    # dependencies when it runs against the wheel.
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    # The mount lands in a captured tree, never the process global.
    with registry.capture():
        assert mount_extensions(tmp_path) == ("docs",)


def test_entries_are_names_or_tables_with_their_distribution(tmp_path: Path) -> None:
    from livery.workshop._extensions import extension_entries, stack_entries

    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n"
        "extensions = [\n"
        '  "livery.forge",\n'
        '  { name = "docs", for = [] },\n'
        "]\n"
    )
    # The distribution is the one whose entry point declares the name.
    assert extension_entries(tmp_path) == (
        ("livery.forge", "livery-forge"),
        ("docs", "livery-workshop"),
    )
    # The stack is the base, then the list.
    assert stack_entries(tmp_path)[0] == ("livery.workshop", "livery-workshop")
    assert extension_names(tmp_path) == ("livery.forge", "docs")


def test_render_injections_deduplicate_member_extensions(tmp_path: Path) -> None:
    from livery.workshop._templates import render_injections

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["livery.forge", "docs"]\n'
    )
    answers = {
        "packages": [
            {"dir": "forge", "name": "livery-forge", "dev": "livery-forge[extra]"}
        ]
    }
    injections = render_injections(tmp_path, answers)
    # A member rides the roster's spelling, and a wheel shipping two
    # extensions is one requirement.
    assert injections["extension_requirements"] == ["livery-workshop"]
    assert injections["extension_imports"] == [
        "livery.workshop",
        "livery.forge",
        "docs",
    ]


def test_declared_but_unmounted_extensions_teach_the_rerender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A tasks.py from before composition moved into it imports the
    # base and nothing else; the gap teaches instead of silently
    # narrowing the tree.
    from livery.workshop import _extensions
    from livery.workshop._env_tasks import _warn_unmounted_extensions

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.brand"]\n'
    )
    monkeypatch.setattr(_extensions, "MOUNTED", False)
    _warn_unmounted_extensions(tmp_path)
    err = capsys.readouterr().err
    assert "acme.brand" in err and "sync` to compose it again" in err
    # Mounted, or an empty list: silence.
    monkeypatch.setattr(_extensions, "MOUNTED", True)
    _warn_unmounted_extensions(tmp_path)
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr(_extensions, "MOUNTED", False)
    _warn_unmounted_extensions(tmp_path)
    assert capsys.readouterr().err == ""


def test_a_declaration_whose_checks_are_not_records_refuses_naming_what_it_found() -> (
    None
):
    from types import ModuleType

    from livery.workshop._extensions import register_declared_checks

    module = ModuleType("acme_declaration")
    module.CHECKS = ["lint.acme"]  # type: ignore[attr-defined]
    with pytest.raises(
        RuntimeError,
        match=r"extension 'acme' declares CHECKS as a list; it takes a tuple",
    ):
        register_declared_checks("acme", module)
    module.CHECKS = ("lint.acme",)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match=r"declares CHECKS with a str among them"):
        register_declared_checks("acme", module)


def test_declared_checks_register_under_the_listed_name(registry_state: None) -> None:
    from types import ModuleType

    from livery.workshop._checks import CheckRecord, GateContext, checks_by_name
    from livery.workshop._extensions import register_declared_checks

    def idle(ctx: GateContext) -> None:
        del ctx

    module = ModuleType("acme_declaration")
    assert register_declared_checks("acme", module) is False
    module.CHECKS = (  # type: ignore[attr-defined]
        CheckRecord("acme", "lint", idle, extension="whatever.it.says"),
    )
    assert register_declared_checks("acme", module) is True
    assert checks_by_name()["lint.acme"].extension == "acme"


def test_an_uninstalled_extension_is_taken_at_the_family_s_distribution() -> None:
    from livery.workshop._extensions import distribution_of

    # A short name is one of the base's family, never the index's
    # package of that bare name; an import path folds its dots.
    assert distribution_of("not-installed-anywhere") == (
        "livery-extensions-not-installed-anywhere"
    )
    assert distribution_of("acme.thing") == "acme-thing"
    assert distribution_of("ruff") == "livery-extensions-ruff"


@pytest.fixture
def registry_state():
    from livery.workshop import _checks

    state = _checks.snapshot()
    yield
    _checks.restore(state)
