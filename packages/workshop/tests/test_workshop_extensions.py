"""The extension walk reads the contract and mounts only what it names."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import extension_names, workspace_root
from livery.workshop._extensions import mount_extensions

ROOT = Path(__file__).resolve().parents[3]


# The refusals first.


def test_a_contract_without_extensions_refuses_printing_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import _registry as registry
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
    from livery.footman import _registry as registry
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


def test_a_package_listing_an_uninstalled_extension_brings_its_distribution_into_the_dev_group(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop._extensions import distribution_of, unmounted
    from workshop_composed import IDENTITY, composed_groups
    from workshop_extension_fakes import package_contract, withheld
    from workshop_installed import skip_unless_installed

    skip_unless_installed("cmake")
    install = withheld(monkeypatch, "cmake")
    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n" + IDENTITY + "extensions = []\n"
    )
    package_contract(tmp_path, "geometry", '["cmake"]')
    # Not installed: the mount names it with the package's list, the sync
    # is told to install it, and its distribution, by its name, joins the
    # dev group the lock reads.
    with registry.capture():
        mount_extensions(tmp_path)
    assert (
        "extension 'cmake' is listed in the `extensions` of packages/geometry,"
        " and no installed distribution declares it"
    ) in capsys.readouterr().err
    assert unmounted(tmp_path) == ("cmake",)
    assert "livery-extensions-cmake" in composed_groups(tmp_path)["dev"]
    # Installed, as the sync installs it: the mount finds it, and the dev
    # group names the distribution that declares it.
    install()
    with registry.capture():
        mount_extensions(tmp_path)
    assert unmounted(tmp_path) == ()
    assert distribution_of("cmake") in composed_groups(tmp_path)["dev"]


def test_a_test_needing_an_extension_not_installed_skips_naming_its_distribution() -> (
    None
):
    from workshop_installed import skip_unless_installed

    # A short name nothing installs is taken at the family's
    # distribution, and an import path at its own.
    with pytest.raises(pytest.skip.Exception) as skipped:
        skip_unless_installed("python", "no-such-extension", "acme.missing")
    assert str(skipped.value) == (
        "not installed here: no-such-extension"
        " (livery-extensions-no-such-extension), acme.missing (acme-missing)"
    )
    # Every extension installed: the test runs on.
    skip_unless_installed("python")


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
    from workshop_installed import skip_unless_installed

    assert workspace_root(ROOT / "packages") == ROOT
    # docs is in the set through mkdocstrings' declaration.
    skip_unless_installed("mkdocstrings")
    # The installed set, in composition order: the root's set, docs
    # through mkdocstrings among it, and python, which the packages' sets
    # hold.
    assert extension_names(ROOT) == (
        "basedpyright",
        "changelog",
        "clang-format",
        "clang-tidy",
        "claude",
        "coverage",
        "docs",
        "mkdocstrings",
        "mypy",
        "pyrefly",
        "pytest",
        "python",
        "ruff",
        "ty",
    )


def test_outside_a_workspace_there_are_no_extensions(tmp_path: Path) -> None:
    assert workspace_root(tmp_path) is None
    assert extension_names(tmp_path) == ()
    assert mount_extensions(tmp_path) == ()


def test_the_git_plugin_mount_tolerates_a_missing_entry_point_and_mounts_otherwise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import livery.footman as footman
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_git_plugin

    # A checkout that just moved past the plugin's arrival has no entry
    # point until it syncs: the mount says so and lets the sync run.
    def refuse(source: str) -> None:
        raise ValueError(f"plugin({source!r}): no 'footman.tasks' entry point matches")

    with monkeypatch.context() as patched:
        patched.setattr(footman, "plugin", refuse)
        why = mount_git_plugin()
    assert "the git plugin is not mounted (plugin('livery.git')" in why
    assert "sync` installs it" in why
    assert "note: the git plugin is not mounted" in capsys.readouterr().err
    # Installed, it mounts into the captured tree with nothing to say.
    with registry.capture():
        assert mount_git_plugin() == ""


def test_the_listed_extensions_mount_and_the_base_never_does(tmp_path: Path) -> None:
    from livery.footman import _registry as registry

    # Scratch contracts naming the extensions the workshop's own wheel
    # ships, so the suite needs nothing beyond the package's own
    # dependencies when it runs against the wheel.
    contract = tmp_path / "workshop.toml"
    contract.write_text('[workspace]\nextensions = ["cpp"]\n')
    # The mount lands in a captured tree, never the process global.
    with registry.capture():
        assert mount_extensions(tmp_path) == ("cpp",)
    # With python beside it, in composition order, whatever the list's.
    contract.write_text('[workspace]\nextensions = ["python", "cpp"]\n')
    with registry.capture():
        assert mount_extensions(tmp_path) == ("cpp", "python")
    # An extension mounts with every extension it requires.
    contract.write_text('[workspace]\nextensions = ["conan"]\n')
    with registry.capture():
        assert mount_extensions(tmp_path) == ("cpp", "conan")


def test_entries_are_names_or_tables_with_their_distribution(tmp_path: Path) -> None:
    from livery.workshop._extensions import extension_entries, stack_entries

    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n"
        "extensions = [\n"
        '  "livery.forge",\n'
        '  { name = "cpp", without = [] },\n'
        "]\n"
    )
    # The distribution is the one whose entry point declares the name,
    # in composition order.
    assert extension_entries(tmp_path) == (
        ("cpp", "livery-workshop"),
        ("livery.forge", "livery-forge"),
    )
    # The stack is the base, then the installed set.
    assert stack_entries(tmp_path)[0] == ("livery.workshop", "livery-workshop")
    assert extension_names(tmp_path) == ("cpp", "livery.forge")


def test_render_injections_deduplicate_member_extensions(tmp_path: Path) -> None:
    from livery.workshop._templates import render_injections

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["livery.forge", "cpp"]\n'
    )
    answers = {
        "packages": [
            {"dir": "forge", "name": "livery-forge", "dev": "livery-forge[extra]"}
        ]
    }
    injections = render_injections(tmp_path, answers)
    # A member rides the roster's spelling, and a wheel shipping two
    # extensions, the base and cpp, is one requirement.
    assert injections["extension_requirements"] == ["livery-workshop"]
    assert injections["extension_imports"] == [
        "livery.workshop",
        "cpp",
        "livery.forge",
    ]


def test_declared_but_unmounted_extensions_teach_the_rerender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A run that has the base's verbs without the workshop's plugin load
    # mounts no listed extension; the gap teaches instead of silently
    # narrowing the tree.
    from livery.workshop import _extensions
    from livery.workshop._env_tasks import _warn_unmounted_extensions

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.brand"]\n'
    )
    monkeypatch.setattr(_extensions, "MOUNTED", False)
    _warn_unmounted_extensions(tmp_path)
    err = capsys.readouterr().err
    assert "acme.brand" in err
    assert "sync` to compose tasks.py and install the environment again" in err
    # Mounted, or an empty list: silence.
    monkeypatch.setattr(_extensions, "MOUNTED", True)
    _warn_unmounted_extensions(tmp_path)
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr(_extensions, "MOUNTED", False)
    _warn_unmounted_extensions(tmp_path)
    assert capsys.readouterr().err == ""


def test_an_uninstalled_extension_is_taken_at_the_family_s_distribution() -> None:
    from livery.workshop._extensions import distribution_of

    # A short name is one of the base's family, never the index's
    # package of that bare name; an import path folds its dots.
    assert distribution_of("not-installed-anywhere") == (
        "livery-extensions-not-installed-anywhere"
    )
    assert distribution_of("acme.thing") == "acme-thing"
    assert distribution_of("ruff") == "livery-extensions-ruff"


def test_an_entry_point_whose_metadata_moved_is_taken_at_the_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import shutil
    from importlib.metadata import PathDistribution

    from livery.workshop import _extensions
    from livery.workshop._extensions import GROUP, distribution_of

    # The entry points are read from a dist-info directory, then an
    # install moves the distribution to another: the entry point still
    # names a distribution, and that distribution's metadata reads
    # nothing, so its name is None.
    info = tmp_path / "livery_extensions_x-0.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: livery-extensions-x\nVersion: 0.0.0\n")
    (info / "entry_points.txt").write_text(
        f"[{GROUP}]\nx = livery.extensions.x:plugin\nacme.x = acme.x:plugin\n"
    )
    entries = {entry.name: entry for entry in PathDistribution(info).entry_points}
    shutil.rmtree(info)
    monkeypatch.setattr(_extensions, "_declared", lambda: entries)
    assert entries["x"].dist is not None and entries["x"].dist.name is None
    assert distribution_of("x") == "livery-extensions-x"
    assert distribution_of("acme.x") == "acme-x"


@pytest.fixture
def registry_state():
    from livery.workshop import _steps

    state = _steps.snapshot()
    yield
    _steps.restore(state)
