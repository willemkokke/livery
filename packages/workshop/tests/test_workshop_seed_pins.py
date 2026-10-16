"""What each package template births, composes and runs, pinned as data.

The installed templates birth a member: python's library and command
line, nanobind's module (a python package whose extension module is
C++), conan's C++ library and executable, and the base's workshop
extension. Each test states one thing the workshop does with such a
member: the files its birth writes, the extensions
it holds, the files a sync composes for a workspace of one python, one
nanobind and one C++ member, the tools a workspace requires, the members
each step judges, where a requirement on a sibling lands, what the
release wave and the lifecycle run, the guidance the workspace reads,
and what each file of a nanobind member is to it. A change that moves
one of these on purpose restates it here in the same change, so the
diff says what moved.

The goldens live under ``fixtures/templates/<template>/``,
``fixtures/three-seeds/`` and ``fixtures/three-seeds-guidance/``, each
file at its path in the workspace with ``.golden`` appended: without
the suffix, pytest would collect a seeded test module, the type
checkers would read it, and a seeded ``.gitignore`` would apply to the
fixtures beside it. When a render moves, the test writes the new files
under its temporary directory and names them (`workshop_goldens.match`).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from pathlib import Path, PurePath
from types import ModuleType
from typing import TypeVar

import pytest

import livery.footman as footman
from livery.footman import Failed
from livery.workshop import _lifecycle
from livery.workshop._packages import Package, discover_packages
from livery.workshop._templates import apply_project, render_member
from workshop_goldens import FIXTURES, match
from workshop_installed import skip_unless_installed
from workshop_members import mounted_now

_T = TypeVar("_T")

#: The suffix every golden here carries, which no tool reads.
GOLDEN = ".golden"

#: The templates a member is born from, each to its member's directory
#: here: the three the composing workspace holds, the base's extension
#: package, then the two that extend another.
TREES = {
    "python-library": "echo",
    "nanobind-module": "native",
    "cpp-library": "lib",
    "workshop-extension": "plugin",
    "python-cli": "cli",
    "cpp-executable": "tool",
}

#: The three templates whose members one workspace composes.
THREE = ("python-library", "nanobind-module", "cpp-library")

#: What the composing workspace lists, as its canonical list: two type
#: checkers, the C and C++ formatter and linter, the test runner, and
#: the python formatter and linter.
STACK = ("basedpyright", "clang-format", "clang-tidy", "mypy", "pytest", "ruff")

#: The contract of every workspace here; ``{listed}`` is its list.
CONTRACT = """\
[workspace]
name = "acme"
description = "The acme monorepo (virtual root)."
namespace = "acme"
authors = [{{ name = "A", email = "a@example.com" }}]
copyright-year = "2026"
extensions = [{listed}]

[forge]
kind = "github"
owner = "acme"

[ci]
runners = ["ubuntu-latest"]
required-context = "gate"
"""


@pytest.fixture(autouse=True)
def _as_a_fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start with the registries a process starts with, under the runner ``fm``.

    The isolation plugin puts back after the test what it found before.
    """
    from livery.workshop import _extensions, _points, _steps

    monkeypatch.setattr(footman, "prog", lambda: "fm")
    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))


def _workspace(root: Path, listed: tuple[str, ...] = ()) -> Path:
    """A workspace named acme at *root* that lists *listed* and has no member."""
    skip_unless_installed(*listed)
    root.mkdir(parents=True)
    spelled = ", ".join(f'"{name}"' for name in listed)
    (root / "workshop.toml").write_text(CONTRACT.format(listed=spelled), "utf-8")
    return root


def _lists_also(root: Path, *names: str) -> None:
    """Add *names* to *root*'s list, written as its canonical list."""
    from livery.workshop._extensions import list_in_root

    list_in_root(root, names)


def _born(root: Path, *templates: str) -> dict[str, Package]:
    """Birth one member of each of *templates* in *root*, as ``fm new.package`` does.

    The root lists what each template needs first, as a person does
    before the creation: a creation installs nothing. The members
    discovered, each by its template. The test skips while an extension
    a member holds is not installed.
    """
    from livery.workshop._seeds import chain_needs, template_chain

    skip_unless_installed(*(name for template in templates for name in HELD[template]))
    _lists_also(
        root,
        *(needed for name in templates for needed in chain_needs(template_chain(name))),
    )
    for template in templates:
        render_member(root, TREES[template], template)
    found = {package.member: package for package in discover_packages(root)}
    return {template: found[TREES[template]] for template in templates}


def _mounted(root: Path) -> tuple[str, ...]:
    """Mount *root*'s extensions as a process started there now would; the names."""
    from livery.workshop import _steps

    _steps.restore(_steps.snapshot(initial=True))
    return mounted_now(root)


def _files(directory: Path) -> dict[str, str]:
    """Every file under *directory*, by its path there, to its text."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes().decode("utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# The births.

#: The extensions each template's member holds, in
#: composition order: its list and what the list requires. nanobind
#: requires python, cpp and conan, so it comes after all three.
HELD = {
    "python-library": ("python",),
    "nanobind-module": ("cpp", "conan", "python", "nanobind"),
    "cpp-library": ("cmake", "cpp", "conan"),
    "workshop-extension": ("python",),
    "python-cli": ("python",),
    "cpp-executable": ("cmake", "cpp", "conan"),
}


def test_each_seed_s_member_holds_its_set(tmp_path: Path) -> None:
    from livery.workshop._composition import extension_set, order
    from livery.workshop._extensions import installed_declaration, installed_set

    root = _workspace(tmp_path / "acme")
    members = _born(root, *TREES)
    held = {
        template: order(extension_set(package), installed_declaration)
        for template, package in members.items()
    }
    assert held == HELD
    # The mount takes the union of the members' sets, in composition order.
    assert installed_set(root) == ("cmake", "cpp", "conan", "python", "nanobind")


#: The lines of each template's contract that say what its member is, by
#: key. The golden holds ``<key> = ...`` in their place. A contract writes
#: no kind and no template: its list says what the member holds.
IDENTIFYING = {
    "python-library": {"extensions": '["python"]'},
    "nanobind-module": {"extensions": '["nanobind"]'},
    "cpp-library": {"extensions": '["cmake", "conan"]'},
    "workshop-extension": {"extensions": '["python"]'},
    "python-cli": {"extensions": '["python"]'},
    "cpp-executable": {"extensions": '["cmake", "conan"]'},
}

_IDENTIFYING_LINE = re.compile(r"^(kind|extensions|template) = (.*)$", re.MULTILINE)


def test_each_template_births_what_its_tree_did(tmp_path: Path) -> None:
    # Each template births its own files over those of the template it
    # extends, after the package-base seeds.
    root = _workspace(tmp_path / "acme")
    _born(root, *TREES)
    born: dict[str, str] = {}
    identifying: dict[str, dict[str, str]] = {}
    for template, member in TREES.items():
        files = _files(root / "packages" / member)
        contract = files["workshop.toml"]
        identifying[template] = dict(_IDENTIFYING_LINE.findall(contract))
        files["workshop.toml"] = _IDENTIFYING_LINE.sub(r"\1 = ...", contract)
        born.update({f"{template}/{path}": text for path, text in files.items()})
    assert identifying == IDENTIFYING
    match(born, FIXTURES / "templates", tmp_path / "render", suffix=GOLDEN)


# The workspace of the three package seeds.


def _three_seeds(root: Path) -> tuple[dict[str, Package], tuple[str, ...]]:
    """A workspace listing `STACK` with a member of each of `THREE`, synced.

    The members by their templates, and the extensions the mount took, in
    its order. The members are born first, and a process started after
    them mounts and composes, as ``fm new.package`` hands the wiring to
    a runner started after the birth.
    """
    _workspace(root, STACK)
    members = _born(root, *THREE)
    mounted = _mounted(root)
    apply_project(root)
    return members, mounted


#: What a process started in the three seeds' workspace mounts, in
#: composition order: the list's set and the members' sets, each
#: extension after the ones it requires, ties alphabetical.
MOUNTED = (
    "basedpyright",
    "clang-format",
    "clang-tidy",
    "cmake",
    "cpp",
    "conan",
    "mypy",
    "pytest",
    "python",
    "nanobind",
    "ruff",
)

#: Every file a sync writes into the three seeds' workspace and git
#: tracks, with the fragments that compose it, or ``generated`` for a
#: file generated from the contract.
COMPOSED = (
    ".gitattributes: livery.workshop:.gitattributes",
    ".github/workflows/ci.yml: generated",
    ".github/workflows/nightly.yml: generated",
    ".github/workflows/release.yml: generated",
    ".gitignore: livery.workshop:.gitignore",
    ".taplo.toml: livery.workshop:.taplo.toml",
    ".vscode/extensions.json: livery.workshop:.vscode/extensions.json,"
    " basedpyright:basedpyright recommendations, ruff:ruff recommendations",
    ".vscode/settings.json: livery.workshop:.vscode/settings.json,"
    " basedpyright:step typecheck.basedpyright settings,"
    " ruff:step format.ruff settings",
    "conanws.yml: conan:conanws.yml",
    "mypy.ini: mypy:mypy.ini",
    "packages/lib/.clang-format: clang-format:step format.clang-format packages/lib",
    "packages/lib/.clang-tidy: clang-tidy:step lint.clang-tidy packages/lib",
    "packages/native/.clang-format:"
    " clang-format:step format.clang-format packages/native",
    "packages/native/.clang-tidy: clang-tidy:step lint.clang-tidy packages/native",
    "pyproject.toml: livery.workshop:pyproject.toml",
    "pyrightconfig.json: basedpyright:pyrightconfig.json",
    "pytest.toml: pytest:pytest.toml",
    "ruff.toml: ruff:ruff.toml",
    "setup.sh: generated",
    "tasks.py: livery.workshop:tasks.py",
)

#: The composed files whose bytes are pinned, by their path.
PINNED = (
    ".github/workflows/ci.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/release.yml",
    ".vscode/extensions.json",
    ".vscode/settings.json",
    "conanws.yml",
    "mypy.ini",
    "packages/lib/.clang-format",
    "packages/lib/.clang-tidy",
    "packages/native/.clang-format",
    "packages/native/.clang-tidy",
    "pyproject.toml",
    "pyrightconfig.json",
    "pytest.toml",
    "ruff.toml",
    "setup.sh",
)


def test_a_workspace_of_the_three_seeds_composes_these_files(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import generate
    from livery.workshop._shipped_files import outputs

    root = tmp_path / "acme"
    _members, mounted = _three_seeds(root)
    assert mounted == MOUNTED
    composed = sorted(
        [
            f"{output.path}: {', '.join(output.owners)}"
            for output in outputs(root)
            if not output.local
        ]
        + [f"{path}: generated" for path in generate(root)]
    )
    assert tuple(composed) == COMPOSED
    pinned = {path: (root / path).read_bytes().decode("utf-8") for path in PINNED}
    match(pinned, FIXTURES / "three-seeds", tmp_path / "render", suffix=GOLDEN)


#: What a workspace listing nothing requires as each seed's member joins
#: it: each requirement's name and site, each pair once, in the order
#: first required. The base requires uv; each native tool is required by
#: the extension in the member's set that runs it, in the mount's order,
#: and cmake by the two build steps that run it as well. The nanobind
#: member is of the python kind and lists nanobind.
REQUIRED = {
    "no member": (("uv", "the base"),),
    "python-library": (("uv", "the base"),),
    "nanobind-module": (
        ("uv", "the base"),
        ("conan", "extension conan"),
        ("cmake", "extension nanobind"),
        ("ninja", "extension nanobind"),
        ("conan", "extension nanobind"),
        ("cmake_conan", "extension nanobind"),
    ),
    "cpp-library": (
        ("uv", "the base"),
        ("cmake", "step build.configure"),
        ("cmake", "step build.compile"),
        ("cmake", "extension cmake"),
        ("ninja", "extension cmake"),
        ("conan", "extension conan"),
        ("cmake", "extension nanobind"),
        ("ninja", "extension nanobind"),
        ("conan", "extension nanobind"),
        ("cmake_conan", "extension nanobind"),
    ),
}

#: The tools the host must provide that the same workspaces name, on a
#: host that provides none of them.
HOST_TOOLS = {
    "no member": (),
    "python-library": (),
    "nanobind-module": ("c++", "cc"),
    "cpp-library": ("c++", "cc"),
}


def test_each_seed_s_member_requires_these_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._env_tasks import missing_host_tools
    from livery.workshop._tools import requirements

    root = _workspace(tmp_path / "acme")
    required: dict[str, tuple[tuple[str, str], ...]] = {}
    hosted: dict[str, tuple[str, ...]] = {}
    for joined in ("no member", *THREE):
        if joined in TREES:
            _born(root, joined)
        _mounted(root)
        required[joined] = tuple(
            dict.fromkeys((found.name, found.site) for found in requirements(root))
        )
        with monkeypatch.context() as patched:
            patched.setattr("shutil.which", lambda name, *_args, **_kw: None)
            hosted[joined] = missing_host_tools(root)
    assert required == REQUIRED
    assert hosted == HOST_TOOLS


#: What the root lists in a workspace whose own tests are python: the
#: composing workspace's list and python, as their canonical list.
PYTHON_ROOT = tuple(sorted((*STACK, "python")))

#: The root's set once a C++ library is born in a workspace listing
#: `STACK`: the root lists what cpp-library needs first, cmake and
#: conan, and conan requires cpp. No python among it.
NATIVE_ROOT_SET = (
    "basedpyright",
    "clang-format",
    "clang-tidy",
    "cmake",
    "cpp",
    "conan",
    "mypy",
    "pytest",
    "ruff",
)

#: The root's set there when the root lists `PYTHON_ROOT`.
PYTHON_ROOT_SET = (
    "basedpyright",
    "clang-format",
    "clang-tidy",
    "cmake",
    "cpp",
    "conan",
    "mypy",
    "pytest",
    "python",
    "ruff",
)

#: What each python step reads when a change touches the workspace's own
#: tests alone, in a workspace whose one member is a C++ library and
#: whose root lists python.
ROOT_TESTS_READ = {
    "format.ruff": ("tests",),
    "lint.ruff": ("tests",),
    "typecheck.basedpyright": (".",),
    "typecheck.mypy": (".",),
}

#: The tools the steps require in that workspace, each with its site, in
#: the steps' registration order: each extension's in composition order.
ROOT_TESTS_TOOLS = (
    ("basedpyright", "step typecheck.basedpyright"),
    ("clang_format", "step format.clang-format"),
    ("clang_tidy", "step lint.clang-tidy"),
    ("cmake", "step build.configure"),
    ("cmake", "step build.compile"),
    ("mypy", "step typecheck.mypy"),
    ("pytest", "step test.pytest"),
    ("pytest", "step examples.pytest"),
    ("ruff", "step format.ruff"),
    ("ruff", "step lint.ruff"),
)

#: The tools the steps require when the root lists the composing
#: workspace's list alone: the C++ library's steps, ruff's among them
#: for its recipe, and none of the steps that judge python alone.
NATIVE_TOOLS = (
    ("clang_format", "step format.clang-format"),
    ("clang_tidy", "step lint.clang-tidy"),
    ("cmake", "step build.configure"),
    ("cmake", "step build.compile"),
    ("ruff", "step format.ruff"),
    ("ruff", "step lint.ruff"),
)

#: The per-file ignores the claims render for the root's own files there.
ROOT_IGNORES = (
    ("tests/**", ("D1",)),
    ("tests/**/*_test.py", ("D1",)),
    ("tests/**/test_*.py", ("D1",)),
)


def _with_a_library(root: Path) -> tuple[Package, Package]:
    """A C++ library and the workspace's own tests in *root*, mounted."""
    from livery.workshop._packages import workspace_suite

    lib = _born(root, "cpp-library")["cpp-library"]
    (root / "tests").mkdir()
    (root / "tests" / "test_workspace.py").write_text(
        "def test_the_workspace() -> None:\n    pass\n", "utf-8"
    )
    _mounted(root)
    unit = workspace_suite(root)
    assert unit is not None
    return lib, unit


def _python_reads(root: Path, lib: Package, unit: Package) -> dict[str, object]:
    """What each path step naming python reads when only the root's tests change."""
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import PATHS, scoped_paths, steps_by_name

    ctx = PhaseContext(root=root, packages=(lib,), subset=(unit,))
    return {
        name: scoped_paths(ctx, name)
        for name, record in steps_by_name().items()
        if record.narrowing == PATHS and "python" in record.extensions
    }


def _step_tools(root: Path) -> tuple[tuple[str, str], ...]:
    """The tools the steps require at *root*, each with its site, once each."""
    from livery.workshop._tools import requirements

    return tuple(
        dict.fromkeys(
            (found.name, found.site)
            for found in requirements(root)
            if found.site.startswith("step ")
        )
    )


def test_a_root_listing_no_python_runs_no_suite_of_its_own_tests_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._composition import extension_set
    from livery.workshop._packages import holds_runtime_language
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import step_for, tested

    root = _workspace(tmp_path / "acme", STACK)
    lib, unit = _with_a_library(root)
    # The root's own tests hold the root's set, which holds no python.
    assert extension_set(unit) == NATIVE_ROOT_SET
    assert not holds_runtime_language(unit)
    # The set holds cmake, which the library's template needs, and
    # cmake's ctest step runs over one member at a time: never over the
    # root's own tests.
    assert tested((lib, unit)) == (lib,)
    capsys.readouterr()
    step_for("test.pytest").run(PhaseContext(root=root, packages=(lib,)))
    out = capsys.readouterr().out
    assert (
        "  test.pytest: tests skips (the root's set holds no python; list it in"
        " [workspace] extensions to run them)\n"
    ) in out
    assert "  test.pytest: no python package and no workspace tests to run\n" in out
    # A change to the root's tests alone reaches no step that names python
    # alone. ruff names conan too, for a recipe, and the root's set holds
    # conan, which the library's template needs, so ruff reads them.
    assert _python_reads(root, lib, unit) == {
        "format.ruff": ("tests",),
        "lint.ruff": ("tests",),
        "typecheck.basedpyright": (),
        "typecheck.mypy": (),
    }


def test_the_tool_profile_holds_only_what_the_lists_hold(tmp_path: Path) -> None:
    # The root's set and the library's hold no python: no python step
    # brings its tool.
    native = _workspace(tmp_path / "native", STACK)
    _with_a_library(native)
    assert _step_tools(native) == NATIVE_TOOLS
    # The root lists python: its own files are judged as python, and
    # the python steps bring theirs, though no member holds python.
    acme = _workspace(tmp_path / "acme", PYTHON_ROOT)
    _with_a_library(acme)
    assert _step_tools(acme) == ROOT_TESTS_TOOLS


def test_the_workspace_unit_holds_the_root_lists_set(tmp_path: Path) -> None:
    from livery.workshop._composition import extension_set
    from livery.workshop._packages import holds_runtime_language
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import per_file_ignores, scoped_suite, tested

    root = _workspace(tmp_path / "acme", PYTHON_ROOT)
    lib, unit = _with_a_library(root)
    # The unit lists nothing and holds the root's set, python among it,
    # while the library does not.
    assert unit.extensions == ()
    assert extension_set(unit) == PYTHON_ROOT_SET
    assert [p.path for p in (lib, unit) if holds_runtime_language(p)] == ["tests"]
    # The test phase runs the root's suite beside the library's.
    assert [package.path for package in tested((lib, unit))] == [
        "packages/lib",
        "tests",
    ]
    assert scoped_suite(PhaseContext(root=root, packages=(lib,)), "test.pytest") == (
        unit
    )
    # A change to the root's tests alone is a scoped run over that unit.
    assert _python_reads(root, lib, unit) == ROOT_TESTS_READ
    root_ignores = tuple(
        (pattern, codes)
        for pattern, codes in per_file_ignores([extension_set(lib)])
        if not pattern.startswith("packages/")
    )
    assert root_ignores == ROOT_IGNORES


#: Each step a process registers in the three seeds' workspace, in
#: registration order, with the extension that registered it and the
#: members it judges: the base's, then each extension's in composition
#: order. The build and test steps of the C++ library are cmake's.
JUDGED = (
    "drift.check (livery.workshop): echo, native, lib",
    "provenance.check (livery.workshop): echo, native, lib",
    "layering.graph (livery.workshop): echo, native, lib",
    "layering.base-imports-no-extension (livery.workshop): echo, native, lib",
    "layering.sibling-references (livery.workshop): echo, native, lib",
    "typecheck.basedpyright (basedpyright): echo, native",
    "format.clang-format (clang-format): native, lib",
    "lint.clang-tidy (clang-tidy): native, lib",
    "build.configure (cmake): lib",
    "build.compile (cmake): lib",
    "test.ctest (cmake): lib",
    "sync.conan (conan): native, lib",
    "typecheck.mypy (mypy): echo, native",
    "test.pytest (pytest): echo, native",
    "examples.pytest (pytest): echo, native",
    "layering.private-reaches (python): echo, native, lib",
    "layering.runner-terminal (python): echo, native, lib",
    "layering.declared-imports (python): echo, native, lib",
    "format.ruff (ruff): echo, native, lib",
    "lint.ruff (ruff): echo, native, lib",
)


def test_each_step_judges_these_members(tmp_path: Path) -> None:
    from livery.workshop._steps import judged_by, steps_by_name

    root = _workspace(tmp_path / "acme", STACK)
    members = tuple(_born(root, *THREE).values())
    _mounted(root)
    judged = tuple(
        f"{name} ({record.extension}): "
        + ", ".join(
            package.member for package in judged_by(record, members, quiet=True)
        )
        for name, record in steps_by_name().items()
    )
    assert judged == JUDGED


#: The members a requirement on a sibling is written for: the dependent
#: and the sibling, by directory, each pair of seeds once.
SIBLINGS = (
    ("echo", "core"),
    ("native", "lib"),
    ("native", "echo"),
    ("lib", "geometry"),
)

#: What declaring each pair's sibling at 0.1.0 does to its dependent:
#: each file written, then the lines that changed in it, or the refusal.
LANDED = {
    ("echo", "core"): (
        "pyproject.toml:",
        "+dependencies = [",
        '+    "acme-core>=0.1.0",',
        "+]",
    ),
    ("native", "lib"): (
        "conanfile.py:",
        '-    requires = ("fmt/[>=11.0]",)',
        '+    requires = ("fmt/[>=11.0]", "acme-lib/[>=0.1.0]")',
    ),
    ("native", "echo"): (
        "pyproject.toml:",
        "+dependencies = [",
        '+    "acme-echo>=0.1.0",',
        "+]",
    ),
    # The library's seeded recipe declares no requirement, so conan's
    # writer opens the tuple after the recipe class's attributes.
    ("lib", "geometry"): (
        "conanfile.py:",
        '+    requires = ("acme-geometry/[>=0.1.0]",)',
    ),
}


def test_the_sibling_requirement_lands_in_its_ecosystem_s_manifest(
    tmp_path: Path,
) -> None:
    skip_unless_installed("cmake")
    root = _workspace(tmp_path / "acme")
    _lists_also(root, "cmake", "nanobind")
    for name, template in (
        ("echo", "python-library"),
        ("core", "python-library"),
        ("native", "nanobind-module"),
        ("lib", "cpp-library"),
        ("geometry", "cpp-library"),
    ):
        render_member(root, name, template)
    packages = {package.member: package for package in discover_packages(root)}
    landed = {
        (dependent, sibling): _declared(packages[dependent], packages[sibling])
        for dependent, sibling in SIBLINGS
    }
    assert landed == LANDED


def _declared(package: Package, sibling: Package) -> tuple[str, ...]:
    """What declaring *sibling* at 0.1.0 does to *package*'s files, or the refusal."""
    before = _files(package.directory)
    try:
        written = _lifecycle.declare_requirement(package, sibling, "0.1.0")
    except Failed as refused:
        said = str(refused)
        for name in before:
            said = said.replace(str(package.directory / name), f"{package.path}/{name}")
        return (f"refused: {said}",)
    after = _files(package.directory)
    lines: list[str] = []
    for name in written:
        lines.append(f"{name}:")
        lines += [
            line
            for line in difflib.unified_diff(
                before[name].splitlines(), after[name].splitlines(), lineterm="", n=0
            )
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
    return tuple(lines)


# The release wave and the lifecycle.


def test_the_entry_installs_the_job_built_uv_members_after_the_tools(
    tmp_path: Path,
) -> None:
    from livery.workshop._entry import entry_script, native_members
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer

    root = _workspace(tmp_path / "acme")
    _born(root, *THREE)
    # The wheels job builds the nanobind member and the conan library
    # before the wave.
    assert answer(root, JOB_BUILT_MEMBERS) == {
        "packages/lib": "wheels",
        "packages/native": "wheels",
    }
    # Of those, uv installs the nanobind member alone: the entry's first uv
    # sync leaves it out, and the workshop's sync after the tools builds it.
    assert native_members(root) == ("acme-native",)
    lines = entry_script(root).split("\n")
    assert "_native=(--no-install-package acme-native)" in lines


def test_the_find_links_are_the_set_s_existing_dist_directories(
    tmp_path: Path,
) -> None:
    from livery.workshop._release_driver import MemberPlan, _wheel_dists

    root = _workspace(tmp_path / "acme")
    members = _born(root, *THREE)
    plans = tuple(MemberPlan(package, "0.1.0") for package in members.values())
    # Before any build, no member has a dist/ to name.
    assert _wheel_dists(plans) == ()
    # The python and nanobind builds write theirs; conan's writes none.
    for template in ("python-library", "nanobind-module"):
        (members[template].directory / "dist").mkdir()
    assert [path.relative_to(root).as_posix() for path in _wheel_dists(plans)] == [
        "packages/echo/dist",
        "packages/native/dist",
    ]


#: Where python's backend, conan's recipe code, cpp's answers and
#: nanobind's build are defined.
PYTHON = "livery.extensions.python._backend"
CONAN = "livery.extensions.conan._package"
CPP = "livery.extensions.cpp._language"
NANOBIND = "livery.extensions.nanobind._build"

#: The function each lifecycle entry ran for a nanobind member, which lists
#: nanobind, by the entry's name: the module and name that define it.
#: ``public_modules`` and ``compile_commands`` are the queries of those
#: names, asked through ``answer``, since no lifecycle entry fronts them. The
#: set answers in composition order, cpp, conan, python, then nanobind.
#: conan answers no version and no version file for the member's recipe, a
#: consumer's, and nanobind deletes conan's stamp, build and publish and
#: python's build, so none of them runs.
NANOBIND_BODIES = {
    "current_version": (f"{PYTHON}:current_version",),
    "version_files": (f"{PYTHON}:stamp_version",),
    "declared_requirements": (
        f"{CONAN}:declared_requirements",
        f"{PYTHON}:declared_requirements",
    ),
    "module_roots": (f"{CPP}:module_roots", f"{PYTHON}:module_roots"),
    # python's reads the module roots it names the public modules under.
    "public_modules": (
        f"{CPP}:public_modules",
        f"{PYTHON}:public_modules",
        f"{PYTHON}:module_roots",
    ),
    # No extension of the set answers it, and nothing else does.
    "compile_commands": (),
    "references": (f"{PYTHON}:references",),
    # The answer is whether the set has an isolated test; none runs.
    "has_isolated_test": (),
    "stamp": (f"{PYTHON}:stamp_version",),
    # Every writer of the set is called, in composition order: conan's
    # leaves the python sibling alone, and python's takes it, reading the
    # requirements the pyproject already declares.
    "declare_requirement": (
        f"{CONAN}:declare_requirement",
        f"{PYTHON}:declare_requirement",
        f"{PYTHON}:declared_requirements",
    ),
    "build": (f"{NANOBIND}:build_dist",),
    "publish": (f"{PYTHON}:publish_artifact",),
    "isolated_test": (f"{PYTHON}:isolated_test",),
    "verify": (f"{PYTHON}:verify",),
    # The set holds conan, so the sync phase runs conan's step.
    "sync": ("livery.extensions.conan._steps:profile",),
}

#: What the entries whose bodies ran answered, read from the member's seeds.
NANOBIND_ANSWERS = {
    "current_version": "0.0.0",
    "version_files": ("pyproject.toml", "src/acme/native/__init__.py"),
    # The pyproject declares no requirement; the recipe declares fmt.
    "declared_requirements": {"fmt": "[>=11.0]"},
    "module_roots": ("acme.native",),
    "public_modules": ("acme.native",),
    "compile_commands": None,
    "references": (
        "packages/native/src/acme/native/__init__.py",
        "packages/native/tests/test_native_package.py",
    ),
    "has_isolated_test": True,
    "stamp": ["pyproject.toml", "src/acme/native/__init__.py"],
    "declare_requirement": ["pyproject.toml"],
}


class _Bodies:
    """The functions each lifecycle entry ran, as the recorders saw them.

    Attributes:
        ran: What the recorders saw since the entry began.
        by_entry: What each entry ran, by the entry's name.
    """

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.by_entry: dict[str, tuple[str, ...]] = {}

    def record(
        self,
        monkeypatch: pytest.MonkeyPatch,
        module: ModuleType,
        name: str,
        answer: object = None,
        *,
        runs: bool = True,
    ) -> None:
        """Record each call of *module*'s *name* by the name of its definition.

        The function runs when *runs*; otherwise the call answers
        *answer* and runs nothing.
        """
        original = getattr(module, name)

        def recorded(*args: object, **kwargs: object) -> object:
            self.ran.append(f"{original.__module__}:{original.__qualname__}")
            return original(*args, **kwargs) if runs else answer

        monkeypatch.setattr(module, name, recorded)

    def entry(self, name: str, call: Callable[[], _T]) -> _T:
        """Call the lifecycle entry *name* through *call*; what it answered."""
        self.ran.clear()
        answered = call()
        self.by_entry[name] = tuple(self.ran)
        return answered


def test_a_nanobind_member_s_lifecycle_runs_these_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.conan import _package as conan_package
    from livery.extensions.conan import _steps as conan_steps
    from livery.extensions.cpp import _language as cpp_language
    from livery.extensions.nanobind import _build as nanobind_build
    from livery.extensions.python import _backend as python_backend
    from livery.workshop._queries import COMPILE_COMMANDS, PUBLIC_MODULES, answer
    from livery.workshop._registries import RegistryTarget

    root = _workspace(tmp_path / "acme")
    members = _born(root, "python-library", "nanobind-module")
    echo, native = members["python-library"], members["nanobind-module"]
    bodies = _Bodies()
    # A function that reads or writes the member's files runs; one that
    # builds, uploads, installs or calls conan answers in its place.
    for module, names in (
        (
            python_backend,
            (
                "current_version",
                "stamp_version",
                "declared_requirements",
                "module_roots",
                "public_modules",
                "references",
                "declare_requirement",
            ),
        ),
        (
            conan_package,
            (
                "current_version",
                "stamp_version",
                "declared_requirements",
                "declare_requirement",
            ),
        ),
        (cpp_language, ("module_roots", "public_modules")),
    ):
        for name in names:
            bodies.record(monkeypatch, module, name)
    stand_ins: dict[str, object] = {
        "publish_artifact": True,
        "isolated_test": {"acme-native": "0.1.0"},
        "verify": 0,
    }
    for name, stand_in in stand_ins.items():
        bodies.record(monkeypatch, python_backend, name, stand_in, runs=False)
    for module, name in (
        (conan_package, "build"),
        (conan_package, "publish_artifact"),
        (python_backend, "build"),
    ):
        bodies.record(monkeypatch, module, name, runs=False)
    bodies.record(
        monkeypatch, nanobind_build, "build_dist", native.directory / "dist", runs=False
    )
    bodies.record(monkeypatch, conan_steps, "profile", runs=False)

    def relative(paths: tuple[Path, ...]) -> tuple[str, ...]:
        return tuple(path.relative_to(native.directory).as_posix() for path in paths)

    # The entries that read the seeds first, then those that write them.
    answers: dict[str, object] = {
        "current_version": bodies.entry(
            "current_version", lambda: _lifecycle.current_version(native)
        ),
        "version_files": relative(
            bodies.entry("version_files", lambda: _lifecycle.version_files(native))
        ),
        "declared_requirements": bodies.entry(
            "declared_requirements", lambda: _lifecycle.declared_requirements(native)
        ),
        "module_roots": bodies.entry(
            "module_roots", lambda: _lifecycle.module_roots(native)
        ),
        "public_modules": bodies.entry(
            "public_modules", lambda: answer(native, PUBLIC_MODULES)
        ),
        "compile_commands": bodies.entry(
            "compile_commands", lambda: answer(native, COMPILE_COMMANDS)
        ),
        "references": tuple(
            sorted(
                source.path
                for source in bodies.entry(
                    "references", lambda: _lifecycle.references(native)
                )
            )
        ),
        "has_isolated_test": bodies.entry(
            "has_isolated_test", lambda: _lifecycle.has_isolated_test(native)
        ),
        # The stamp names the files it wrote with the platform's separator.
        "stamp": [
            PurePath(written).as_posix()
            for written in bodies.entry(
                "stamp", lambda: _lifecycle.stamp(native, root, "0.1.0")
            )
        ],
        "declare_requirement": bodies.entry(
            "declare_requirement",
            lambda: _lifecycle.declare_requirement(native, echo, "0.1.0"),
        ),
    }
    bodies.entry("build", lambda: _lifecycle.build_release(native, root, epoch=0))
    target = RegistryTarget(
        kind="python",
        url="https://idx.example/simple",
        publish_url="https://idx.example/",
        token="t",
    )
    bodies.entry(
        "publish",
        lambda: _lifecycle.publish(native, root, version="0.1.0", target=target),
    )
    bodies.entry(
        "isolated_test",
        lambda: _lifecycle.isolated_test(
            native, root, resolution="head", release_dirs=()
        ),
    )
    bodies.entry(
        "verify",
        lambda: _lifecycle.verify(
            native,
            root,
            tree=root,
            version="0.1.0",
            python="python",
            index="https://idx.example/simple",
            extras="",
        ),
    )
    # The sync phase runs the steps the mount registered.
    _mounted(root)
    bodies.entry("sync", lambda: _lifecycle.sync((native,), root))
    assert bodies.by_entry == NANOBIND_BODIES
    assert answers == NANOBIND_ANSWERS


# The guidance and the categories.


def test_the_compositions_present_guidance_for_the_three_seeds(
    tmp_path: Path,
) -> None:
    from livery.workshop._prose import AGENT, HUMAN, guidance

    root = _workspace(tmp_path / "acme", STACK)
    _born(root, *THREE)
    _mounted(root)
    (fragment,) = [
        prose
        for prose in guidance(root, AGENT)
        if prose.name == "compositions.present.md"
    ]
    rendered = {
        f"compositions.present.{audience}.md": fragment.text(root, audience)
        for audience in (AGENT, HUMAN)
    }
    match(
        rendered, FIXTURES / "three-seeds-guidance", tmp_path / "render", suffix=GOLDEN
    )


#: What each file a nanobind member is born with is to it, and the
#: extension whose rule says so.
NANOBIND_CATEGORIES = {
    ".gitignore": ("configuration", "livery.workshop"),
    "CMakeLists.txt": ("configuration", "livery.workshop"),
    "CMakePresets.json": ("configuration", "livery.workshop"),
    "LICENSE": ("configuration", "livery.workshop"),
    "README.md": ("configuration", "livery.workshop"),
    "conanfile.py": ("configuration", "livery.workshop"),
    "docs/index.md": ("prose", "livery.workshop"),
    "docs/nav.toml": ("nav", "livery.workshop"),
    "pyproject.toml": ("configuration", "livery.workshop"),
    # Both python and cpp claim src/**, and python is later than cpp in
    # the set's composition order, so its rule answers for every file there.
    "src/acme/native/__init__.py": ("source", "python"),
    "src/acme/native/_native.cpp": ("source", "python"),
    "src/acme/native/_native.pyi": ("source", "python"),
    "src/acme/native/py.typed": ("source", "python"),
    # python's test pattern is more specific than cpp's tests/**.
    "tests/test_native_package.py": ("test", "python"),
    "workshop.toml": ("configuration", "livery.workshop"),
}


def test_a_nanobind_member_s_files_take_these_categories(tmp_path: Path) -> None:
    from livery.workshop._categories import category_of

    root = _workspace(tmp_path / "acme")
    native = _born(root, "nanobind-module")["nanobind-module"]
    found: dict[str, tuple[str, str]] = {}
    for path in _files(native.directory):
        category = category_of(native, path)
        found[path] = (category.name, category.supplier)
    assert found == NANOBIND_CATEGORIES
