"""Templates: what an extension declares `fm new.package` creates, refusals first.

An installed extension, or the base, declares ``[templates.<name>]``
with a ``doc``, the ``extensions`` a package born from it lists, and
``extends``, the template whose files come first. A workspace offers
the base's templates and those of the extensions its installed set
holds, and a birth needs every extension the chain names held and
installed: a creation installs nothing. The birth writes the base's
``package-base`` seeds, then each template of the chain, the born one
last, and the contract lists the born template's extensions in their
canonical form. A template is read at birth alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import livery.footman as footman
from livery.workshop._extensions import distribution_of
from livery.workshop._packages import Package, discover_packages
from livery.workshop._templates import render_member
from workshop_extension_fakes import fake_extensions, withheld

_FAILURES = (BaseException,)

#: The contract of every workspace here.
CONTRACT = """\
[workspace]
name = "acme"
description = "The acme monorepo (virtual root)."
namespace = "acme"
authors = [{ name = "A", email = "a@example.com" }]
copyright-year = "2026"
extensions = []

[forge]
kind = "github"
owner = "acme"
"""


@pytest.fixture(autouse=True)
def _as_fm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run under the runner ``fm``, whatever the suite's process is named."""
    monkeypatch.setattr(footman, "prog", lambda: "fm")


def _workspace(root: Path, *listed: str) -> Path:
    """A workspace named acme at *root* whose root lists *listed*, with no member."""
    root.mkdir(parents=True)
    spelled = ", ".join(f'"{name}"' for name in listed)
    contract = CONTRACT.replace("extensions = []", f"extensions = [{spelled}]")
    (root / "workshop.toml").write_text(contract, "utf-8")
    return root


def _files(root: Path) -> dict[str, bytes]:
    """Every file under *root* but the environment's, by its path there."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".venv" not in path.relative_to(root).parts
    }


def _member(root: Path, name: str) -> Package:
    """The member under ``packages/<name>``, as discovery reads it."""
    (found,) = [p for p in discover_packages(root) if p.member == name]
    return found


def _declaring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **declarations: str
) -> Path:
    """Fake extensions ``acme.<name>`` declaring *declarations*.

    Each declaration is the ``[templates]`` part of the extension's
    ``extension.toml``. Every template the fakes declare gets a
    directory of files with one file, ``NOTE.md``, naming it. The
    directory the fakes live in.
    """
    import tomllib

    fake_extensions(
        tmp_path,
        monkeypatch,
        **{name: "[extension]\n\n" + text for name, text in declarations.items()},
    )
    site = tmp_path / "site" / "acme"
    for name, text in declarations.items():
        for template in tomllib.loads(text).get("templates", {}):
            files = site / name / "content" / "templates" / template
            files.mkdir(parents=True, exist_ok=True)
            (files / "NOTE.md").write_text(f"{template}\n", "utf-8")
    return site


# The refusals first, in the order a birth judges them, each before
# anything is written.

#: A template listing an extension nothing installs, and one extending it.
_ROCKS = (
    '[templates.lua-module]\ndoc = "a lua module"\nextensions = ["acme.rocks"]\n\n'
    '[templates.lua-app]\ndoc = "a lua app"\nextensions = ["acme.lua"]\n'
    'extends = "lua-module"\n'
)


def test_a_template_listed_and_not_synced_refuses_naming_fm_sync(
    tmp_path: Path,
) -> None:
    # The root lists acme.lua, which no installed distribution declares
    # yet, so the template it may declare is unknown until a sync.
    root = _workspace(tmp_path / "acme", "acme.lua", "python")
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == (
        "no installed extension declares the template lua-module, and the"
        " workspace's lists hold acme.lua (acme-lua), which no installed"
        " distribution declares yet: run `fm sync`, which installs it, then"
        " create the package again"
    )
    assert _files(root) == before


def test_a_template_extending_one_a_listed_and_unsynced_extension_may_declare_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "acme.lua", "acme.rocks")
    _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-app]\ndoc = "a lua app"\nextensions = ["acme.lua"]\n'
            'extends = "lua-module"\n'
        ),
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app")
    assert str(caught.value) == (
        "the template lua-app extends lua-module, which no installed extension"
        " declares, and the workspace's lists hold acme.rocks (acme-rocks), which"
        " no installed distribution declares yet: run `fm sync`, which installs"
        " it, then create the package again"
    )
    assert not (root / "packages").exists()


def test_a_template_no_available_extension_declares_refuses_listing_the_installed(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path / "acme", "python")
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "package-python")
    text = str(caught.value)
    assert text.startswith(
        "no installed template is named package-python; the installed templates are:\n"
    )
    # The base's and python's: conan declares the C++ templates, and the
    # workspace's lists do not hold it.
    assert "\n  python-library: a python library" in text
    assert "\n  workshop-extension: " in text
    assert "cpp-library" not in text
    assert not (root / "packages" / "thing").exists()


def test_a_template_whose_declarer_is_not_installed_refuses_naming_it_its_distribution_and_the_root_list(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "python")
    _declaring(
        tmp_path,
        monkeypatch,
        lua='[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n',
    )
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == (
        "the template lua-module needs acme.lua (acme-lua), which the workspace's"
        " lists do not hold: list it in [workspace] extensions and run `fm sync`,"
        " then create the package again"
    )
    # conan declares cpp-library, which lists cmake and conan.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "geometry", "cpp-library")
    assert str(caught.value) == (
        f"the template cpp-library needs cmake ({distribution_of('cmake')}) and"
        f" conan ({distribution_of('conan')}), which the workspace's lists do not"
        " hold: list them in [workspace] extensions and run `fm sync`, then create"
        " the package again"
    )
    assert _files(root) == before


def test_a_template_whose_chain_lists_an_uninstalled_extension_refuses_naming_each_with_its_distribution(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "acme.lua")
    _declaring(tmp_path, monkeypatch, lua=_ROCKS)
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == (
        "the template lua-module needs acme.rocks (acme-rocks), which the"
        " workspace's lists do not hold: list it in [workspace] extensions and"
        " run `fm sync`, then create the package again"
    )
    # Through the template it extends too.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app")
    assert str(caught.value).startswith(
        "the template lua-app needs acme.rocks (acme-rocks), which the"
        " workspace's lists do not hold:"
    )
    # nanobind-module extends python's python-library: nanobind requires
    # python, so listing nanobind alone takes the whole chain in.
    native = _workspace(tmp_path / "native", "nanobind")
    render_member(native, "ext", "nanobind-module")
    assert _files(root) == before


def test_a_template_needing_an_extension_listed_and_not_installed_refuses_naming_fm_sync(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root lists cmake and conan, and no installed distribution
    # declares cmake yet: a creation installs nothing, so it names the
    # sync that does.
    root = _workspace(tmp_path / "acme", "cmake", "conan")
    withheld(monkeypatch, "cmake")
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "geometry", "cpp-library")
    assert str(caught.value) == (
        f"the template cpp-library needs cmake ({distribution_of('cmake')}), which"
        " no installed distribution declares yet: run `fm sync`, which installs"
        " it, then create the package again"
    )
    assert _files(root) == before


def test_a_creation_runs_no_lock_or_sync_before_it_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import new_package
    from workshop_composed import compose_into
    from workshop_members import wired_here

    root = compose_into(_workspace(tmp_path / "acme", "python"))
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    member = root / "packages" / "geometry"
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "livery.workshop._uv.run_uv",
        lambda *args, root: calls.append((f"uv {args[0]}", member.is_dir())),
    )
    monkeypatch.setattr(
        "livery.workshop._tool_tasks.sync_tools",
        lambda root, **_kwargs: calls.append(("tools", member.is_dir())),
    )
    handed = wired_here(monkeypatch, root)
    # The refusal first: nothing is locked, synced or handed on.
    with pytest.raises(_FAILURES, match="list them in \\[workspace\\] extensions"):
        new_package("geometry", "cpp-library")
    assert (calls, handed) == ([], [])
    assert not member.exists()
    # Listed and installed: the template renders, and the one lock and
    # sync are the wiring's, after the member exists.
    _workspace_lists(root, "cmake", "conan")
    new_package("geometry", "cpp-library")
    assert [argv[1:] for argv in handed] == [["new.package", "geometry", "--resume"]]
    assert calls == [("uv lock", True), ("uv sync", True), ("tools", True)]
    assert (member / "CMakeLists.txt").is_file()


def _workspace_lists(root: Path, *listed: str) -> None:
    """Write *listed* as the root list of the workspace at *root*."""
    contract = root / "workshop.toml"
    text = contract.read_text("utf-8")
    start = text.index("extensions = [")
    end = text.index("]\n", start) + 1
    spelled = ", ".join(f'"{name}"' for name in listed)
    contract.write_text(text[:start] + f"extensions = [{spelled}]" + text[end:])


def test_the_listed_templates_are_the_bases_and_the_installed_sets(
    tmp_path: Path,
) -> None:
    from livery.workshop._seeds import installed_templates

    root = _workspace(tmp_path / "acme")
    # A root listing nothing offers the base's own.
    assert list(installed_templates(root)) == ["workshop-extension"]
    _workspace_lists(root, "python")
    assert list(installed_templates(root)) == [
        "python-cli",
        "python-library",
        "workshop-extension",
    ]
    # A package's list joins the installed set, and its set's templates
    # are offered with it: nanobind requires python and conan.
    member = root / "packages" / "ext"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text('extensions = ["nanobind"]\n', "utf-8")
    assert list(installed_templates(root)) == list(AVAILABLE)
    # Outside a workspace, the base's alone.
    assert list(installed_templates(tmp_path)) == ["workshop-extension"]


def test_two_extensions_declaring_one_template_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme")
    declared = '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n'
    _declaring(tmp_path, monkeypatch, lua=declared, rocks=declared)
    # Any birth refuses: which template the name means is unknown.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "python-library")
    assert (
        "the template lua-module is declared by both acme.lua and acme.rocks;"
        " one extension declares a template, so one of them renames it"
    ) in str(caught.value)
    assert not (root / "packages" / "thing").exists()


def test_an_extends_cycle_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme")
    _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n'
            'extends = "lua-rock"\n\n'
            '[templates.lua-rock]\ndoc = "a lua rock"\nextensions = []\n'
            'extends = "lua-module"\n'
        ),
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert (
        "the template lua-module reaches an extends cycle: lua-module extends"
        " lua-rock extends lua-module; each template extends one that does not"
        " lead back to it"
    ) in str(caught.value)
    assert not (root / "packages" / "thing").exists()


def test_a_template_extending_one_nobody_declares_refuses_naming_the_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "acme.lua")
    _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n'
            'extends = "lua-base"\n'
        ),
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    text = str(caught.value)
    assert (
        "the template lua-module extends lua-base, which no installed extension"
        " declares; the installed templates are:"
    ) in text
    # The workspace offers the base's and acme.lua's own.
    assert "\n  lua-module: a lua module" in text
    assert "python-library" not in text


def test_a_template_without_its_files_refuses_naming_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "acme.lua")
    site = _declaring(
        tmp_path,
        monkeypatch,
        lua='[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n',
    )
    files = (site / "lua" / "content" / "templates" / "lua-module").resolve()
    shutil.rmtree(files)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert (
        f"the template lua-module has no files: {files} is missing beside"
        " extension.toml, which declares it"
    ) in str(caught.value)


@pytest.mark.parametrize(
    ("table", "refusal"),
    [
        (
            "[templates.lua-module]\nextensions = []\n",
            "templates.lua-module.doc is missing: one line saying what a package"
            " born from the template is, which fm new.package names",
        ),
        (
            '[templates.lua-module]\ndoc = "a lua module"\n',
            "templates.lua-module.extensions is missing: the extensions a package"
            " born from the template lists, [] for none",
        ),
        (
            '[templates.Lua]\ndoc = "a lua module"\nextensions = []\n',
            "templates.Lua is no template name: lower-case letters, digits and"
            " hyphens, starting with a letter, the word fm new.package takes",
        ),
    ],
)
def test_a_template_off_its_shape_refuses_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str, refusal: str
) -> None:
    root = _workspace(tmp_path / "acme")
    _declaring(tmp_path, monkeypatch, lua=table)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert refusal in str(caught.value)


def test_a_template_under_a_for_table_refuses_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A template names the extensions it needs in its own list, so no
    # [for.<target>] table declares one.
    from livery.workshop._declaration import DeclarationError
    from livery.workshop._extensions import declaration

    _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[for.python.templates.lua-module]\ndoc = "a lua module"\nextensions = []\n'
        ),
    )
    with pytest.raises(DeclarationError) as caught:
        declaration("acme.lua")
    assert (
        "'templates': it takes categories, ci, deletes, fragments, phases,"
        " queries, replaces, toolroom"
    ) in str(caught.value)


def test_the_base_s_templates_take_the_grammar_an_extension_declares_one_in() -> None:
    import tomllib

    from livery.workshop._contract_keys import BASE_FILE, judge

    table = tomllib.loads(BASE_FILE.read_text("utf-8"))["templates"]
    judged = judge(
        {"templates": table}, contract="extension", where=str(BASE_FILE), listed=None
    )
    assert judged == []


def test_new_package_without_a_template_refuses_naming_the_installed_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._templates import new_package

    # cmake and nanobind take in every extension the six templates need.
    root = _workspace(tmp_path / "acme", "cmake", "nanobind")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(_FAILURES) as caught:
        new_package("thing")
    text = str(caught.value)
    assert (
        "name the template packages/thing is born from:"
        " `fm new.package thing <template>`; the installed templates are:"
    ) in text
    for line in (
        "  cpp-executable: ",
        "  cpp-library: ",
        "  nanobind-module: ",
        "  python-cli: ",
        "  python-library: ",
        "  workshop-extension: ",
    ):
        assert f"\n{line}" in text
    assert not (root / "packages").exists()


@pytest.mark.parametrize(
    ("kind", "written"),
    [
        ("package-python", "`fm new.package thing python-library`"),
        ("package-cpp-conan", "`fm new.package thing cpp-library`"),
        ("package-python-nanobind", "`fm new.package thing nanobind-module`"),
        ("package-extension", "`fm new.package thing workshop-extension`"),
        ("package-lua", "`fm new.package thing <template>`"),
    ],
)
def test_new_package_with_kind_refuses_naming_the_template_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, written: str
) -> None:
    from livery.workshop._templates import new_package

    root = _workspace(tmp_path / "acme", "python")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    with pytest.raises(_FAILURES) as caught:
        new_package("thing", "python-library", kind=kind)
    text = str(caught.value)
    assert (
        "--kind is not an option: a package is born from a template, the word"
        f" after its name; write {written}"
    ) in text
    assert "\n  python-library: " in text
    assert not (root / "packages").exists()


def test_a_contract_writing_template_refuses(tmp_path: Path) -> None:
    from livery.workshop._contract import load_contract

    root = _workspace(tmp_path / "acme", "python")
    render_member(root, "thing", "python-library")
    contract = root / "packages" / "thing" / "workshop.toml"
    contract.write_text(
        contract.read_text("utf-8") + 'template = "package-extension"\n', "utf-8"
    )
    with pytest.raises(_FAILURES) as caught:
        load_contract(contract)
    assert (
        "template is not a key: a package is what its extensions and its own"
        " files say, and the template it was born from is read at its birth"
        " alone; delete it"
    ) in str(caught.value)


# The templates the workshop's wheel declares.

#: Each template the workshop's wheel declares: the extension declaring
#: it, the list a package born from it writes, and the template it
#: extends.
AVAILABLE = {
    "cpp-executable": ("conan", ("cmake", "conan"), "cpp-library"),
    "cpp-library": ("conan", ("cmake", "conan"), ""),
    "nanobind-module": ("nanobind", ("nanobind",), "python-library"),
    "python-cli": ("python", ("python",), "python-library"),
    "python-library": ("python", ("python",), ""),
    "workshop-extension": ("livery.workshop", ("python",), "python-library"),
}


def test_the_available_templates_are_these() -> None:
    from livery.workshop._seeds import available_templates

    found = {
        name: (template.extension, template.extensions, template.extends)
        for name, template in available_templates().items()
    }
    assert found == AVAILABLE
    # Each one says what a package born from it is.
    assert all(template.doc for template in available_templates().values())


def test_every_template_directory_the_workshop_ships_is_a_declared_template() -> None:
    from livery.workshop._seeds import TEMPLATES, available_templates, template_files

    installed = available_templates()
    declared = {template_files(template) for template in installed.values()}
    assert all(directory.is_dir() for directory in declared)
    source = Path(__file__).resolve().parents[1] / "src" / "livery"
    shipped = {
        directory.resolve()
        for content in source.rglob("content")
        if content.is_dir() and (content / TEMPLATES).is_dir()
        for directory in (content / TEMPLATES).iterdir()
    }
    assert shipped == declared


def test_the_chain_runs_from_the_template_extended_to_the_one_born() -> None:
    from livery.workshop._seeds import template_chain

    chains = {
        name: tuple(template.name for template in template_chain(name))
        for name in AVAILABLE
    }
    assert chains == {
        "cpp-executable": ("cpp-library", "cpp-executable"),
        "cpp-library": ("cpp-library",),
        "nanobind-module": ("python-library", "nanobind-module"),
        "python-cli": ("python-library", "python-cli"),
        "python-library": ("python-library",),
        "workshop-extension": ("python-library", "workshop-extension"),
    }


def test_a_template_s_file_wins_over_the_one_it_extends_and_over_package_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._seeds import create_package

    root = _workspace(tmp_path / "acme", "acme.lua")
    site = _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-base]\ndoc = "a lua package"\nextensions = []\n\n'
            '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n'
            'extends = "lua-base"\n'
        ),
    )
    templates = site / "lua" / "content" / "templates"
    (templates / "lua-base" / "docs").mkdir()
    (templates / "lua-base" / "docs" / "index.md").write_text("the base's\n")
    (templates / "lua-base" / "kept.txt").write_text("kept\n")
    thing = root / "packages" / "thing"
    data = {"package_name": "acme-thing", "package_description": "A thing."}
    create_package(root, thing, "lua-module", data)
    # The born template's file over its parent's, and the parent's over
    # package-base's docs page.
    assert (thing / "NOTE.md").read_text() == "lua-module\n"
    assert (thing / "docs" / "index.md").read_text() == "the base's\n"
    assert (thing / "kept.txt").read_text() == "kept\n"
    assert (thing / "docs" / "nav.toml").is_file()


def test_the_contract_lists_the_template_s_canonical_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._seeds import create_package

    root = _workspace(tmp_path / "acme", "acme.lua", "nanobind")
    site = _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-module]\ndoc = "a lua module"\n'
            'extensions = ["python", "nanobind", "cpp"]\n'
        ),
    )
    files = site / "lua" / "content" / "templates" / "lua-module"
    (files / "workshop.toml.jinja").write_text(
        "extensions = {{ package_extensions }}\n", "utf-8"
    )
    thing = root / "packages" / "thing"
    data = {"package_name": "acme-thing", "package_description": "A thing."}
    create_package(root, thing, "lua-module", data)
    # nanobind requires python and cpp, so the minimal list is nanobind's.
    assert (thing / "workshop.toml").read_text("utf-8") == 'extensions = ["nanobind"]\n'


# python answers the executables fm run starts from [project.scripts].


def test_a_python_package_without_scripts_has_no_executable(tmp_path: Path) -> None:
    from livery.workshop._queries import EXECUTABLES, answer
    from livery.workshop._run import start

    root = _workspace(tmp_path / "acme", "python")
    render_member(root, "echo", "python-library")
    assert answer(_member(root, "echo"), EXECUTABLES) == ()
    with pytest.raises(_FAILURES) as caught:
        start(root, "echo", (), root)
    assert "packages/echo has no executable" in str(caught.value)


def test_python_s_run_step_refuses_a_script_the_environment_lacks(
    tmp_path: Path,
) -> None:
    from livery.workshop._pythons import scripts_dir
    from livery.workshop._run import start

    root = _workspace(tmp_path / "acme", "python")
    render_member(root, "tool", "python-cli")
    with pytest.raises(_FAILURES) as caught:
        start(root, "tool", (), root)
    assert (
        f"packages/tool: {scripts_dir(root / '.venv')} holds no script tool;"
        " `fm sync` installs the workspace's packages there"
    ) in str(caught.value)


def test_python_cli_births_a_script_fm_run_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._pythons import scripts_dir
    from livery.workshop._queries import EXECUTABLES, answer
    from livery.workshop._run import start

    root = _workspace(tmp_path / "acme", "python")
    render_member(root, "tool", "python-cli")
    tool = _member(root, "tool")
    # The script the package's pyproject.toml declares is its executable.
    assert answer(tool, EXECUTABLES) == ("tool",)
    # The born command line runs, as the script and `python -m` run it.
    done = subprocess.run(
        [sys.executable, "-m", "acme.tool", "one", "two"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(tool.directory / "src")},
    )
    assert (done.returncode, done.stdout) == (0, "hello from acme-tool one two\n")
    # fm run starts the script the environment holds with the words after
    # the dashes, and exits with its code.
    script = scripts_dir(root / ".venv") / (
        "tool.exe" if sys.platform == "win32" else "tool"
    )
    script.parent.mkdir(parents=True)
    script.write_text("")
    started: list[list[str]] = []
    original = footman.run

    def run(argv: list[str] | str, *args: Any, **options: Any) -> object:
        if isinstance(argv, str) or argv[:1] != [str(script)]:
            return original(argv, *args, **options)
        assert options.get("capture") is False and options.get("nofail") is True
        started.append(argv)
        return SimpleNamespace(code=5)

    monkeypatch.setattr("livery.footman.run", run)
    assert start(root, "tool", ("one", "two"), root) == 5
    assert started == [[str(script), "one", "two"]]


# The two new templates, each born and run.

_TOOLCHAIN = ("cmake", "ninja", "ctest", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)


@pytest.mark.skipif(
    bool(_MISSING_TOOLS) or sys.platform == "win32",
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing"
    if _MISSING_TOOLS
    else "the Windows leg builds with MSVC, which this test does not enter",
)
def test_cpp_executable_births_an_executable_that_builds_and_runs(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path / "acme", "cmake", "conan")
    render_member(root, "tool", "cpp-executable")
    tool = root / "packages" / "tool"
    contract = (tool / "workshop.toml").read_text("utf-8")
    assert 'extensions = ["cmake", "conan"]' in contract
    assert 'package_type = "application"' in (tool / "conanfile.py").read_text()
    for argv in (
        ["cmake", "--preset", "release"],
        ["cmake", "--build", "--preset", "release"],
        ["ctest", "--preset", "release"],
    ):
        done = subprocess.run(
            argv, cwd=tool, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stdout + done.stderr
    ran = subprocess.run(
        [str(tool / "build" / "release" / "tool")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (ran.returncode, ran.stdout) == (0, "acme-tool 0.0.0\n")
