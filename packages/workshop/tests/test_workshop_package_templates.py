"""Templates: what an extension declares `fm new.package` creates, refusals first.

An installed extension, or the base, declares ``[templates.<name>]``
with a ``doc``, the ``extensions`` a package born from it lists,
``extends``, the template whose files come first, its creation options,
and the answers it fixes for the options of the templates it extends;
``fm new.package <name> <template> --<option>=<answer>`` answers the
options, each checked before anything is written. A workspace offers
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
from workshop_installed import skip_unless_installed

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
    assert "cpp-package" not in text
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
    # conan declares cpp-package, which lists cmake and conan.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "geometry", "cpp-package")
    assert str(caught.value) == (
        f"the template cpp-package needs cmake ({distribution_of('cmake')}) and"
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
        render_member(root, "geometry", "cpp-package")
    assert str(caught.value) == (
        f"the template cpp-package needs cmake ({distribution_of('cmake')}), which"
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

    skip_unless_installed("cmake")
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
        new_package("geometry", "cpp-package")
    assert (calls, handed) == ([], [])
    assert not member.exists()
    # Listed and installed: the template renders, and the one lock and
    # sync are the wiring's, after the member exists.
    _workspace_lists(root, "cmake", "conan")
    new_package("geometry", "cpp-package")
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


def test_a_template_listing_an_extension_that_composes_nothing_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path / "acme", "acme.lint", "acme.lua")
    # acme.lint's step names acme.lua, so acme.lua composes something in a
    # package and acme.lint does not.
    _declaring(
        tmp_path,
        monkeypatch,
        lua=(
            '[templates.lua-module]\ndoc = "a lua module"\n'
            'extensions = ["acme.lint", "acme.lua"]\n\n'
            '[templates.lua-app]\ndoc = "a lua app"\nextensions = ["acme.lua"]\n'
            'extends = "lua-module"\n'
        ),
        lint=(
            '[phases.lint.acme-lint]\nmain = "{package}._steps:noop"\n'
            'extensions = ["acme.lua"]\n'
        ),
    )
    before = _files(root)
    refusal = (
        "the template lua-module lists acme.lint, which composes nothing in a"
        " package: its steps judge every package that holds acme.lua; a"
        " package's list refuses it, so acme.lua, which declares the template,"
        " drops it from the template's extensions"
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == refusal
    # Through the template it extends too, whose files a birth writes.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app")
    assert str(caught.value) == refusal
    assert _files(root) == before


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

    # cmake and nanobind take in every extension the four templates need.
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
        "  cpp-package: ",
        "  nanobind-module: ",
        "  python-library: ",
        "  workshop-extension: ",
    ):
        assert f"\n{line}" in text
    assert not (root / "packages").exists()


@pytest.mark.parametrize(
    ("kind", "written"),
    [
        ("package-python", "`fm new.package thing python-library`"),
        ("package-cpp-conan", "`fm new.package thing cpp-package`"),
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


# Creation options: the refusals first, each before anything is written.

#: A lua module whose flavour is required, its rocks counted, and a lua
#: app that extends it, fixes the count, and adds a switch of its own.
_OPTIONS = (
    '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n\n'
    "[templates.lua-module.options.flavour]\n"
    'doc = "the lua the module targets"\n'
    'choices = ["lua", "luajit"]\n\n'
    "[templates.lua-module.options.rock-count]\n"
    'doc = "how many rocks it starts with"\ntype = "int"\ndefault = 1\n\n'
    '[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n'
    'extends = "lua-module"\nfixes = { rock-count = 0 }\n\n'
    "[templates.lua-app.options.daemon]\n"
    'doc = "whether the app runs in the background"\ntype = "bool"\n'
    "default = false\n"
)


def _lua(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str = _OPTIONS
) -> Path:
    """A workspace listing acme.lua, which declares *table*; its root."""
    root = _workspace(tmp_path / "acme", "acme.lua")
    _declaring(tmp_path, monkeypatch, lua=table)
    return root


#: Where an option off its shape is named, before what is wrong with it.
_FLAVOUR = "templates.lua-module.options.flavour"


@pytest.mark.parametrize(
    ("option", "refusal"),
    [
        (
            'type = "str"\n',
            f"{_FLAVOUR}.doc is one line of text saying what the answer decides,"
            " which fm new.package's help and completion show",
        ),
        (
            'doc = "the lua"\n',
            f"{_FLAVOUR} takes choices, the answers it takes, or type, one of str,"
            " int, bool: one of the two",
        ),
        (
            'doc = "the lua"\nchoices = ["lua"]\ntype = "str"\n',
            f"{_FLAVOUR} takes choices, the answers it takes, or type",
        ),
        (
            'doc = "the lua"\nchoices = ["lua", "lua"]\n',
            f"{_FLAVOUR}.choices is a list of the answers it takes, each once",
        ),
        (
            'doc = "the lua"\ntype = "float"\n',
            f"{_FLAVOUR}.type is 'float'; it takes one of str, int, bool",
        ),
        (
            'doc = "the lua"\nchoices = ["lua"]\ndefault = "luajit"\n',
            f"{_FLAVOUR}.default does not answer it: --flavour is 'luajit', which"
            " is not one of its choices: lua",
        ),
        (
            'doc = "the lua"\ntype = "int"\ndefault = true\n',
            f"{_FLAVOUR}.default does not answer it: --flavour is True, which is"
            " no int",
        ),
        (
            'doc = "the lua"\ntype = "str"\nhelp = "x"\n',
            f"[{_FLAVOUR}] has no key 'help': it takes choices, default, doc, type",
        ),
    ],
)
def test_an_option_off_its_shape_refuses_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, option: str, refusal: str
) -> None:
    table = (
        '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n\n'
        f"[templates.lua-module.options.flavour]\n{option}"
    )
    root = _lua(tmp_path, monkeypatch, table)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert refusal in str(caught.value)
    assert not (root / "packages").exists()


@pytest.mark.parametrize(
    ("table", "refusal"),
    [
        (
            '[templates.lua-module.options.Flavour]\ndoc = "x"\ntype = "str"\n',
            "options.Flavour is no option name: lower-case letters, digits and"
            " hyphens, starting with a letter, as --<option> spells it",
        ),
        (
            '[templates.lua-module.options.class]\ndoc = "x"\ntype = "str"\n',
            "options.class is a Python keyword once its hyphens are underscores,"
            " which the template variable it renders as cannot be",
        ),
        (
            '[templates.lua-module.fixes]\nflavour = ["lua"]\n',
            "fixes.flavour is a list (['lua']); it takes a string or an integer or"
            " true or false",
        ),
    ],
)
def test_an_option_name_or_a_fix_off_its_shape_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str, refusal: str
) -> None:
    root = _lua(
        tmp_path,
        monkeypatch,
        '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n\n' + table,
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert f"templates.lua-module.{refusal}" in str(caught.value)


def test_a_missing_required_answer_refuses_naming_the_option_and_its_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lua(tmp_path, monkeypatch)
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == (
        "the template lua-module needs --flavour, the lua the module targets:"
        " give `--flavour=<answer>`, one of lua, luajit"
    )
    # Through the template that extends it too.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app")
    assert str(caught.value).startswith("the template lua-app needs --flavour,")
    assert _files(root) == before


def test_an_answer_outside_the_choices_or_the_type_refuses_naming_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lua(tmp_path, monkeypatch)
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module", {"flavour": "moon"})
    assert str(caught.value) == (
        "the template lua-module takes no such answer: --flavour is 'moon', which"
        " is not one of its choices: lua, luajit"
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(
            root, "thing", "lua-module", {"flavour": "lua", "rock_count": "two"}
        )
    assert str(caught.value) == (
        "the template lua-module takes no such answer: --rock-count is 'two',"
        " which is no int"
    )
    assert _files(root) == before


def test_an_answer_to_an_option_the_template_does_not_take_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lua(tmp_path, monkeypatch)
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module", {"flavour": "lua", "daemon": True})
    assert str(caught.value) == (
        "the template lua-module takes no option --daemon; it takes: --flavour,"
        " --rock-count"
    )
    # An option the template it extends declares and it fixes is not one
    # it takes: the fixed answer cannot be overridden.
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app", {"flavour": "lua", "rock_count": 3})
    assert str(caught.value) == (
        "the template lua-app takes no option --rock-count; it takes: --flavour,"
        " --daemon"
    )
    assert _files(root) == before


@pytest.mark.parametrize(
    ("app", "refusal"),
    [
        (
            '[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n'
            'extends = "lua-module"\nfixes = { colour = "red" }\n',
            "the template lua-app fixes colour, which no template it extends"
            " declares as an option; it fixes only those",
        ),
        (
            '[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n'
            'extends = "lua-module"\nfixes = { flavour = "moon" }\n',
            "the template lua-app fixes an answer the option refuses: --flavour is"
            " 'moon', which is not one of its choices: lua, luajit",
        ),
        (
            '[templates.lua-app]\ndoc = "a lua app"\nextensions = []\n'
            'extends = "lua-module"\n\n[templates.lua-app.options.flavour]\n'
            'doc = "the lua"\ntype = "str"\n',
            "the template lua-app declares the option flavour, which lua-module"
            " declares; a template takes the options of the one it extends, and"
            " fixes an answer with fixes",
        ),
    ],
)
def test_a_chain_whose_options_disagree_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: str, refusal: str
) -> None:
    table = _OPTIONS.split("[templates.lua-app]")[0] + app
    root = _lua(tmp_path, monkeypatch, table)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-app", {"flavour": "lua"})
    assert str(caught.value) == refusal
    assert not (root / "packages").exists()


def test_an_option_whose_variable_every_birth_sets_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _lua(
        tmp_path,
        monkeypatch,
        '[templates.lua-module]\ndoc = "a lua module"\nextensions = []\n\n'
        '[templates.lua-module.options.package-name]\ndoc = "x"\ntype = "str"\n'
        'default = ""\n',
    )
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "thing", "lua-module")
    assert str(caught.value) == (
        "the template lua-module has an option whose variable is package_name,"
        " which every birth sets already; the extension that declares it renames"
        " the option"
    )


# What the answers render.


def test_the_answers_render_as_variables_and_an_empty_file_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._seeds import create_package

    root = _lua(tmp_path, monkeypatch)
    site = tmp_path / "site" / "acme" / "lua" / "content" / "templates"
    (site / "lua-module" / "module.txt.jinja").write_text(
        "{{ flavour }} with {{ rock_count }} rocks\n", "utf-8"
    )
    # A file whose whole body depends on an answer.
    (site / "lua-app" / "daemon.txt.jinja").write_text(
        "{% if daemon %}runs in the background\n{% endif %}", "utf-8"
    )
    data = {"package_name": "acme-thing", "package_description": "A thing."}

    def born(name: str, template: str, answers: dict[str, object]) -> Path:
        create_package(root, root / "packages" / name, template, data, answers)
        return root / "packages" / name

    lib = born("lib", "lua-module", {"flavour": "luajit", "rock_count": 3})
    assert (lib / "module.txt").read_text() == "luajit with 3 rocks\n"
    # The extended template's options and the fixed answer render too.
    app = born("app", "lua-app", {"flavour": "lua"})
    assert (app / "module.txt").read_text() == "lua with 0 rocks\n"
    assert not (app / "daemon.txt").exists()
    background = born("bg", "lua-app", {"flavour": "lua", "daemon": True})
    assert (background / "daemon.txt").read_text() == "runs in the background\n"


def test_the_template_s_options_are_the_parameters_fm_new_package_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect
    import typing

    from livery.workshop._templates import template_options

    root = _lua(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    # A fixed option is none of the template's.
    assert [p.name for p in template_options("lua-app")] == ["flavour", "daemon"]
    flavour, rocks = template_options("lua-module")
    assert flavour.kind is inspect.Parameter.KEYWORD_ONLY
    assert flavour.default is inspect.Parameter.empty
    literal, marker = typing.get_args(flavour.annotation)
    assert typing.get_args(literal) == ("lua", "luajit")
    assert marker.text == "the lua the module targets"
    assert (rocks.name, rocks.default) == ("rock_count", 1)
    assert typing.get_args(rocks.annotation)[0] is int
    with pytest.raises(_FAILURES) as caught:
        template_options("lua-rock")
    assert str(caught.value).startswith("no installed template is named lua-rock;")


#: A tasks file that mounts the ``new`` verbs alone.
_NEW = (
    "from livery.footman import include\n"
    "from livery.workshop._templates import new\n\n"
    "include(new)\n"
)


def test_fm_new_package_takes_the_template_s_options_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman.testing import Runner

    root = _lua(tmp_path, monkeypatch)
    tasks = tmp_path / "tasks.py"
    tasks.write_text(_NEW, "utf-8")

    def refused(line: str) -> str:
        result = Runner().invoke(line, tasks=tasks, cwd=root)
        assert result.exit_code == 64, result.stderr
        return result.stderr.strip()

    assert refused("new.package thing lua-module --flavor=lua") == (
        "fm: new.package: unknown option --flavor — did you mean '--flavour'?"
    )
    assert refused("new.package thing lua-module --flavour=moon") == (
        "fm: new.package: --flavour must be one of lua|luajit (got 'moon')"
    )
    assert refused("new.package thing lua-module") == (
        "fm: new.package: missing required option(s) of <template> lua-module:"
        " --flavour"
    )
    assert refused("new.package thing lua-app --flavour=lua --rock-count=3") == (
        "fm: new.package: unknown option --rock-count (<template> lua-app takes"
        " --flavour, --daemon)"
    )
    assert not (root / "packages").exists()


def test_completion_lists_the_template_s_options_and_their_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from livery.footman import _complete, _suggest

    root = _lua(tmp_path, monkeypatch)
    (root / "tasks.py").write_text(_NEW, "utf-8")
    monkeypatch.chdir(root)
    specs = [
        json.loads(line)
        for line in _suggest._chosen("answers", "lua-app", ["new", "package"], {})
    ]
    assert [(spec["name"], spec.get("choices")) for spec in specs] == [
        ("flavour", ["lua", "luajit"]),
        ("daemon", None),
    ]
    names = {"partial": "--", "bash": False, "used": []}
    assert _complete._chosen_rows(specs, names) == [
        "--flavour\tthe lua the module targets",
        "--flavour=\tthe lua the module targets",
        "--daemon\twhether the app runs in the background",
    ]
    choices = {"partial": "--flavour=", "bash": False, "used": []}
    assert _complete._chosen_rows(specs, choices) == [
        "--flavour=lua",
        "--flavour=luajit",
    ]


# The templates the workshop's wheel declares.

#: Each template the workshop's wheel declares: the extension declaring
#: it, the list a package born from it writes, and the template it
#: extends.
AVAILABLE = {
    "cpp-package": ("conan", ("cmake", "conan"), ""),
    "nanobind-module": ("nanobind", ("nanobind",), "python-library"),
    "python-library": ("python", ("python",), ""),
    "workshop-extension": ("livery.workshop", ("python",), "python-library"),
}

#: Each template's creation options, each with its choices or type and
#: its default, and the answers its chain fixes.
OPTIONS = {
    "cpp-package": (
        {
            "package-type": (
                (
                    "library",
                    "application",
                    "header-library",
                    "static-library",
                    "shared-library",
                ),
                "str",
                "library",
            )
        },
        {},
    ),
    "nanobind-module": ({}, {"command": ""}),
    "python-library": ({"command": ((), "str", "")}, {}),
    "workshop-extension": ({"app": ((), "str", "")}, {"command": ""}),
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


def test_the_available_templates_take_these_options() -> None:
    from livery.workshop._seeds import (
        available_templates,
        chain_options,
        template_chain,
    )

    found: dict[str, Any] = {}
    for name in available_templates():
        options, fixed = chain_options(template_chain(name))
        found[name] = (
            {
                option.name: (option.choices, option.type, option.default)
                for option in options.values()
            },
            fixed,
        )
        # Each option says what its answer decides.
        assert all(option.doc for option in options.values())
    assert found == OPTIONS


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
        "cpp-package": ("cpp-package",),
        "nanobind-module": ("python-library", "nanobind-module"),
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
    render_member(root, "tool", "python-library", {"command": "tool"})
    with pytest.raises(_FAILURES) as caught:
        start(root, "tool", (), root)
    assert (
        f"packages/tool: {scripts_dir(root / '.venv')} holds no script tool;"
        " `fm sync` installs the workspace's packages there"
    ) in str(caught.value)


def test_python_library_with_a_command_births_a_script_fm_run_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._pythons import scripts_dir
    from livery.workshop._queries import EXECUTABLES, answer
    from livery.workshop._run import start

    root = _workspace(tmp_path / "acme", "python")
    render_member(root, "tool", "python-library", {"command": "tool"})
    tool = _member(root, "tool")
    # The script the package's pyproject.toml declares is its executable.
    assert answer(tool, EXECUTABLES) == ("tool",)
    assert (tool.directory / "tests" / "test_tool_cli.py").is_file()
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


# A C++ package, born for each package type and built.

_TOOLCHAIN = ("cmake", "ninja", "ctest", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)


@pytest.mark.skipif(
    bool(_MISSING_TOOLS) or sys.platform == "win32",
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing"
    if _MISSING_TOOLS
    else "the Windows leg builds with MSVC, which this test does not enter",
)
@pytest.mark.parametrize(
    "package_type",
    ["library", "application", "header-library", "static-library", "shared-library"],
)
def test_cpp_package_builds_and_tests_each_package_type(
    tmp_path: Path, package_type: str
) -> None:
    skip_unless_installed("cmake")
    root = _workspace(tmp_path / "acme", "cmake", "conan")
    render_member(root, "tool", "cpp-package", {"package_type": package_type})
    tool = root / "packages" / "tool"
    contract = (tool / "workshop.toml").read_text("utf-8")
    assert 'extensions = ["cmake", "conan"]' in contract
    recipe = (tool / "conanfile.py").read_text()
    assert f'package_type = "{package_type}"' in recipe
    for argv in (
        ["cmake", "--preset", "release"],
        ["cmake", "--build", "--preset", "release"],
        ["ctest", "--preset", "release"],
    ):
        done = subprocess.run(
            argv, cwd=tool, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stdout + done.stderr
    if package_type != "application":
        assert not (tool / "src" / "main.cpp").exists()
        return
    ran = subprocess.run(
        [str(tool / "build" / "release" / "tool")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (ran.returncode, ran.stdout) == (0, "acme-tool 0.0.0\n")
