"""Templates: what an extension declares, each one a verb, refusals first.

An installed extension, or the base, declares ``[templates.<name>]``
with a ``doc``, the ``extensions`` a package born from it lists,
``extends``, the template whose files come first, its creation options,
and the answers it fixes for the options of the templates it extends.
Each template the workspace offers is a verb made from its declaration,
``fm new.package.<template> <name> --<option>=<answer>``, and every
answer is checked before anything is written. A workspace offers
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
from livery.footman import Group
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


def _mounted(root: Path) -> Group:
    """The tree a process started now at *root* mounts, as footman holds it.

    The points' jobs start as a new process has them, and the verbs the
    mount makes, the templates' among them, land in the tree returned.
    The workshop's test guard puts the steps, the jobs and the mount's
    records back after the test.
    """
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    _points.restore(_points.snapshot(initial=True))
    with registry.capture() as tree:
        mount_extensions(root)
    return tree


def _package_verbs(tree: Group) -> Group:
    """The group of *tree* that holds the templates' verbs, ``new.package``."""
    return tree.groups["new"].groups["package"]


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
    skip_unless_installed("python")
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


def test_the_base_s_extension_template_refuses_naming_python_until_it_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The base declares workshop-extension, which lists python and extends
    # python's python-library. In a workspace that does not list python,
    # whose environment holds none of python's distribution, its verb
    # exists, and the birth refuses before it writes anything, naming
    # python, its distribution and the list to write it in.
    from livery.workshop._seeds import installed_templates

    withheld(monkeypatch, "python")
    root = _workspace(tmp_path / "acme")
    assert list(installed_templates(root)) == ["workshop-extension"]
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "plugin", "workshop-extension")
    assert str(caught.value) == (
        "the template workshop-extension extends python-library, which no"
        " installed extension declares, and needs python"
        " (livery-extensions-python), which the workspace's lists do not hold:"
        " list it in [workspace] extensions and run `fm sync`, then create the"
        " package again"
    )
    assert _files(root) == before
    # Listed and not installed yet, the refusal names the sync.
    _workspace_lists(root, "python")
    before = _files(root)
    with pytest.raises(_FAILURES) as caught:
        render_member(root, "plugin", "workshop-extension")
    assert str(caught.value) == (
        "the template workshop-extension extends python-library, which no"
        " installed extension declares, and the workspace's lists hold python"
        " (livery-extensions-python), which no installed distribution declares"
        " yet: run `fm sync`, which installs it, then create the package again"
    )
    assert _files(root) == before


def test_a_template_no_available_extension_declares_refuses_listing_the_installed(
    tmp_path: Path,
) -> None:
    skip_unless_installed("python")
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
    skip_unless_installed("conan")
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
    skip_unless_installed("nanobind")
    native = _workspace(tmp_path / "native", "nanobind")
    render_member(native, "ext", "nanobind-module")
    assert _files(root) == before


def test_a_template_needing_an_extension_listed_and_not_installed_refuses_naming_fm_sync(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root lists cmake and conan, and no installed distribution
    # declares cmake yet: a creation installs nothing, so it names the
    # sync that does.
    skip_unless_installed("conan")
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
    from workshop_composed import compose_into
    from workshop_members import template_verb, wired_here

    skip_unless_installed("cmake", "conan")
    # conan declares cpp-package, so its verb exists; the template lists
    # cmake too, which the root's list does not hold yet.
    root = compose_into(_workspace(tmp_path / "acme", "conan"))
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
    create = template_verb(root, "cpp-package")
    # The refusal first: nothing is locked, synced or handed on.
    with pytest.raises(_FAILURES, match="list it in \\[workspace\\] extensions"):
        create("geometry", package_type="library")
    assert (calls, handed) == ([], [])
    assert not member.exists()
    # Listed and installed: the template renders, and the one lock and
    # sync are the wiring's, after the member exists.
    _workspace_lists(root, "cmake", "conan")
    create("geometry", package_type="library")
    assert [argv[1:] for argv in handed] == [["new.wire", "geometry"]]
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
    skip_unless_installed("nanobind")
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
            " born from the template is, which the template's verb shows as its"
            " help",
        ),
        (
            '[templates.lua-module]\ndoc = "a lua module"\n',
            "templates.lua-module.extensions is missing: the extensions a package"
            " born from the template lists, [] for none",
        ),
        (
            '[templates.Lua]\ndoc = "a lua module"\nextensions = []\n',
            "templates.Lua is no template name: lower-case letters, digits and"
            " hyphens, starting with a letter, as fm new.package.<template>"
            " spells it",
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


def test_fm_new_package_alone_is_a_group_naming_each_template_s_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("nanobind")
    from livery.footman.testing import Runner
    from livery.workshop._seeds import installed_templates

    # cmake and nanobind take in every extension the four templates need.
    root = _workspace(tmp_path / "acme", "cmake", "nanobind")
    tree = _mounted(root)
    # The refusal first: a group runs nothing, and names its verbs.
    result = Runner().invoke("new.package", tasks=tree, cwd=root)
    assert result.exit_code == 64, result.stderr
    assert "'new.package' is a group, not a task" in result.stderr
    for name in AVAILABLE:
        assert f"new.package.{name}" in result.stderr
    # The removed spelling, a template after the package's name, meets
    # the same refusal.
    result = Runner().invoke("new.package thing python-library", tasks=tree, cwd=root)
    assert result.exit_code == 64, result.stderr
    assert "'new.package' is a group, not a task" in result.stderr
    assert not (root / "packages").exists()
    # Its help lists each template's verb with the template's doc.
    result = Runner().invoke("new.package --help", tasks=tree, cwd=root)
    assert result.exit_code == 0, result.stderr
    listed = " ".join(result.stdout.split())
    for name, template in installed_templates(root).items():
        assert f"new.package.{name} Create {template.doc}." in listed


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
            " which the template's verb shows in its help and completion",
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
            '[templates.lua-module.options.name]\ndoc = "x"\ntype = "str"\n',
            "options.name is taken: --name is the package's name, the word every"
            " template's verb takes first; rename the option",
        ),
        (
            '[templates.lua-module.options.help]\ndoc = "x"\ntype = "bool"\n',
            "options.help is taken: --help is footman's own flag, which shows the"
            " verb's help on any line; rename the option",
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


# Each installed template is a verb, made from its declaration.


def test_a_template_s_verb_refuses_an_answer_its_options_do_not_take(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman.testing import Runner

    root = _lua(tmp_path, monkeypatch)
    tree = _mounted(root)

    def refused(line: str) -> str:
        result = Runner().invoke(line, tasks=tree, cwd=root)
        assert result.exit_code == 64, result.stderr
        return result.stderr.strip()

    # A required option without an answer is named.
    assert refused("new.package.lua-module thing") == (
        "fm: new.package.lua-module: missing required option(s): --flavour"
    )
    # An answer outside an option's choices names them.
    assert refused("new.package.lua-module thing --flavour=moon") == (
        "fm: new.package.lua-module: --flavour must be one of lua|luajit (got 'moon')"
    )
    # An option the template does not take is an unknown option.
    assert refused("new.package.lua-module thing --flavor=lua") == (
        "fm: new.package.lua-module: unknown option --flavor — did you mean"
        " '--flavour'?"
    )
    # The answer lua-app's chain fixes is no option of its verb.
    assert refused("new.package.lua-app thing --flavour=lua --rock-count=3") == (
        "fm: new.package.lua-app: unknown option --rock-count (task options come"
        " right after their task; globals go before the first task)"
    )
    # A template no installed extension declares has no verb.
    assert refused("new.package.lua-rock thing") == (
        "fm: no task named 'new.package.lua-rock' — did you mean"
        " 'new.package.lua-app'? (new.package has: new.package.lua-app,"
        " new.package.lua-module, new.package.workshop-extension)"
    )
    assert not (root / "packages").exists()


def test_a_template_s_verb_refuses_while_its_chain_needs_an_extension_no_list_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman.testing import Runner

    # acme.lua is listed, so the verbs of its templates exist; lua-module
    # lists acme.rocks, which no list holds.
    root = _workspace(tmp_path / "acme", "acme.lua")
    _declaring(tmp_path, monkeypatch, lua=_ROCKS)
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: root
    )
    before = _files(root)
    result = Runner().invoke(
        "new.package.lua-module thing", tasks=_mounted(root), cwd=root
    )
    assert result.exit_code == 1, result.stderr
    assert (
        "the template lua-module needs acme.rocks (acme-rocks), which the"
        " workspace's lists do not hold: list it in [workspace] extensions and"
        " run `fm sync`, then create the package again"
    ) in result.stderr
    assert _files(root) == before
    # conan declares cpp-package, which lists cmake as well.
    skip_unless_installed("conan")
    native = _workspace(tmp_path / "native", "conan")
    monkeypatch.setattr(
        "livery.workshop._templates.workspace_root", lambda start=None: native
    )
    result = Runner().invoke(
        "new.package.cpp-package geometry", tasks=_mounted(native), cwd=native
    )
    assert result.exit_code == 1, result.stderr
    assert (
        f"the template cpp-package needs cmake ({distribution_of('cmake')}),"
        " which the workspace's lists do not hold: list it in [workspace]"
        " extensions and run `fm sync`, then create the package again"
    ) in result.stderr
    assert not (native / "packages").exists()


def test_a_template_s_verb_takes_its_chain_s_options_less_the_fixed_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect
    import typing

    from workshop_members import template_verb

    root = _lua(tmp_path, monkeypatch)
    module = inspect.signature(template_verb(root, "lua-module")).parameters
    app = inspect.signature(template_verb(root, "lua-app")).parameters
    # The package's name, then each option by its variable. lua-app
    # extends lua-module and fixes its rock count, so its verb offers
    # lua-module's flavour and its own switch, and no rock count.
    assert list(module) == ["name", "flavour", "rock_count"]
    assert list(app) == ["name", "flavour", "daemon"]
    flavour = module["flavour"]
    assert flavour.kind is inspect.Parameter.KEYWORD_ONLY
    assert flavour.default is inspect.Parameter.empty
    literal, marker = typing.get_args(flavour.annotation)
    assert typing.get_args(literal) == ("lua", "luajit")
    assert marker.text == "the lua the module targets"
    rocks = module["rock_count"]
    assert (typing.get_args(rocks.annotation)[0], rocks.default) == (int, 1)
    daemon = app["daemon"]
    assert (typing.get_args(daemon.annotation)[0], daemon.default) == (bool, False)


def test_footman_s_manifest_lists_each_template_verb_with_its_options_and_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as registry
    from livery.footman import task_tree
    from livery.footman._complete import complete
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    root = _lua(tmp_path, monkeypatch)
    _points.restore(_points.snapshot(initial=True))
    # The tree the background refresh builds: what the plugin's mount
    # registered, through footman's own manifest.
    with registry.capture():
        mount_extensions(root)
        tree = task_tree()
    verbs = tree["groups"]["new"]["groups"]["package"]["tasks"]

    def params(name: str) -> list[tuple[str, str, object]]:
        return [
            (param["name"], param["kind"], param.get("choices"))
            for param in verbs[name]["params"]
        ]

    assert params("lua-module") == [
        ("name", "positional", None),
        ("flavour", "option", ["lua", "luajit"]),
        ("rock-count", "option", None),
    ]
    assert params("lua-app") == [
        ("name", "positional", None),
        ("flavour", "option", ["lua", "luajit"]),
        ("daemon", "flag", None),
    ]
    assert verbs["lua-app"]["help"] == "Create a lua app."

    # Completion answers from the manifest alone: it starts no process.
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"completion started a process: {args}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert complete(tree, ["new.package.lua-module", "thing", "--flavour="]) == [
        "--flavour=lua",
        "--flavour=luajit",
    ]
    offered = complete(tree, ["new.package.lua-app", "thing", "--"])
    assert any(row.startswith("--flavour") for row in offered), offered
    assert any(row.startswith("--daemon") for row in offered), offered
    assert not any(row.startswith("--rock-count") for row in offered), offered


def test_the_template_verbs_follow_the_installed_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._seeds import installed_templates

    root = _workspace(tmp_path / "acme", "acme.lua")
    _declaring(tmp_path, monkeypatch, lua=_OPTIONS)
    install = withheld(monkeypatch, "acme.lua")
    _points.restore(_points.snapshot(initial=True))
    with registry.capture() as tree:
        # acme.lua is listed and not installed: the base's verb alone.
        mount_extensions(root)
        package = _package_verbs(tree)
        assert list(package.tasks) == ["workshop-extension"]
        # Installed, the verbs of its templates appear: one per template
        # the workspace offers.
        install()
        mount_extensions(root)
        assert sorted(package.tasks) == sorted(installed_templates(root))
        assert sorted(package.tasks) == ["lua-app", "lua-module", "workshop-extension"]
        made = package.tasks["lua-module"]
        # A mount that finds the same templates keeps each verb.
        mount_extensions(root)
        assert package.tasks["lua-module"] is made
        # Taken away again, its verbs go.
        reinstall = withheld(monkeypatch, "acme.lua")
        mount_extensions(root)
        assert list(package.tasks) == ["workshop-extension"]
        # Installed and off the list, they go too.
        reinstall()
        mount_extensions(root)
        assert "lua-module" in package.tasks
        _workspace_lists(root)
        mount_extensions(root)
        assert list(package.tasks) == ["workshop-extension"]


def test_the_generator_remakes_a_changed_verb_and_keeps_a_task_it_did_not_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses
    import inspect

    from livery.workshop._seeds import installed_templates
    from livery.workshop._templates import generate_template_verbs

    root = _lua(tmp_path, monkeypatch)
    templates = installed_templates(root)
    tree = Group("root")
    package = tree.group("new").group("package")

    @package.task(name="lua-rock")
    def lua_rock(name: str) -> None:
        """A verb of an extension's own at a template's address."""
        del name

    generate_template_verbs({**templates, "lua-rock": templates["lua-app"]}, into=tree)
    assert inspect.unwrap(package.tasks["lua-rock"]) is inspect.unwrap(lua_rock)
    app, module = package.tasks["lua-app"], package.tasks["lua-module"]
    # A template whose doc changed gets its verb made again; the others
    # stay as they are.
    changed = dataclasses.replace(templates["lua-module"], doc="a moon module")
    generate_template_verbs({**templates, "lua-module": changed}, into=tree)
    assert package.tasks["lua-app"] is app
    assert package.tasks["lua-module"] is not module
    assert (inspect.unwrap(package.tasks["lua-module"]).__doc__ or "").startswith(
        "Create a moon module.\n"
    )
    # A template that is gone loses its verb; the task the generator did
    # not make stays.
    generate_template_verbs({"lua-module": changed}, into=tree)
    assert sorted(package.tasks) == ["lua-module", "lua-rock"]


# The templates the livery distributions declare.

#: Each template the base and the livery extensions declare: the
#: extension declaring it, the list a package born from it writes, and
#: the template it extends.
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
    skip_unless_installed("nanobind")
    from livery.workshop._seeds import available_templates

    found = {
        name: (template.extension, template.extensions, template.extends)
        for name, template in available_templates().items()
    }
    assert found == AVAILABLE
    # Each one says what a package born from it is.
    assert all(template.doc for template in available_templates().values())


def test_the_available_templates_take_these_options() -> None:
    skip_unless_installed("nanobind")
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
    # The other distributions installed here, nanobind's among them,
    # declare templates of their own beside the workshop's.
    from livery.workshop._seeds import TEMPLATES, available_templates, template_files

    skip_unless_installed("python")
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
    assert shipped <= declared


def test_the_chain_runs_from_the_template_extended_to_the_one_born() -> None:
    skip_unless_installed("nanobind")
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
    skip_unless_installed("nanobind")
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

    skip_unless_installed("python")
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

    skip_unless_installed("python")
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

    skip_unless_installed("python")
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


def test_the_docs_seeds_live_once_in_the_base_tree() -> None:
    # Every package's birth writes package-base first, the one home of
    # the docs seeds; a copy in a template would shadow the base's and
    # rot separately.
    from livery.workshop._seeds import available_templates, template_files

    seeds = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "livery"
        / "workshop"
        / "content"
        / "seeds"
    )
    base = seeds / "package-base" / "docs"
    assert (base / "nav.toml").is_file()
    assert (base / "index.md.jinja").is_file()
    assert sorted(tree.name for tree in seeds.iterdir()) == ["package-base", "project"]
    for template in available_templates().values():
        assert not (template_files(template) / "docs").exists(), template.name
