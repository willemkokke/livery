"""The extension walk reads the contract and mounts only what it names."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop.api import extension_names, mount_extensions, workspace_root

ROOT = Path(__file__).resolve().parents[3]


# The refusals first.


def test_a_contract_without_extensions_refuses_printing_the_line(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\n\n[forge]\nkind = "github"\n')
    # A reader has nothing to read; the mount refuses with the line to add.
    assert extension_names(tmp_path) == ()
    with pytest.raises(BaseException) as caught:
        mount_extensions(tmp_path)
    text = str(caught.value)
    assert "[workspace] extensions is required" in text
    assert "\n  extensions = []\n" in text


def test_an_uninstalled_extension_refuses_naming_its_distribution(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.missing"]\n'
    )
    with pytest.raises(BaseException) as caught:
        mount_extensions(tmp_path)
    text = str(caught.value)
    assert "'acme.missing'" in text and "workshop.extensions" in text
    assert "(acme-missing by its name)" in text


def test_a_branded_builtin_extension_is_the_apps_to_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    # Off the builtin set, the same absent extension still refuses.
    monkeypatch.setattr(_paths, "_builtin", ())
    with pytest.raises(BaseException, match=r"acme\.missing"):
        mount_extensions(tmp_path)


# Then the walk.


def test_this_workspace_lists_its_extensions_and_never_the_base() -> None:
    assert workspace_root(ROOT / "packages") == ROOT
    assert extension_names(ROOT) == (
        "docs",
        "livery.forge",
        "livery.toolroom.bench",
        "livery.footman",
    )


def test_outside_a_workspace_there_are_no_extensions(tmp_path: Path) -> None:
    assert workspace_root(tmp_path) is None
    assert extension_names(tmp_path) == ()
    assert mount_extensions(tmp_path) == ()


def test_the_listed_extensions_mount_and_the_base_never_does(tmp_path: Path) -> None:
    from livery.footman import registry

    # A scratch contract naming an extension the workshop itself depends
    # on, never this repository's, whose further extensions belong to its
    # dev group and are absent where the suite runs against the wheel
    # with the package's own dependencies alone.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["livery.forge"]\n'
    )
    # The mount lands in a captured tree, never the process global.
    with registry.capture():
        assert mount_extensions(tmp_path) == ("livery.forge",)


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
    assert "acme.brand" in err and "template.apply" in err
    # Mounted, or an empty list: silence.
    monkeypatch.setattr(_extensions, "MOUNTED", True)
    _warn_unmounted_extensions(tmp_path)
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr(_extensions, "MOUNTED", False)
    _warn_unmounted_extensions(tmp_path)
    assert capsys.readouterr().err == ""
