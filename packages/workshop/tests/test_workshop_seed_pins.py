"""What each package seed births, composes and runs, pinned as data.

Four seed trees birth a member: python's, nanobind's (a python package
whose extension module is C++), the C++ library's with conan, and the
extension package's. Each test states one thing the workshop does with
such a member: the files its birth writes, the package-level extensions
it holds, the files a sync composes for a workspace of one python, one
nanobind and one C++ member, the tools a workspace requires, the members
each step judges, where a requirement on a sibling lands, what the
release wave and the lifecycle run, the guidance the workspace reads,
and what each file of a nanobind member is to it. A change that moves
one of these on purpose restates it here in the same change, so the
diff says what moved.

The goldens live under ``fixtures/seeds/<tree>/``,
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
from workshop_members import mounted_now

_T = TypeVar("_T")

#: The suffix every golden here carries, which no tool reads.
GOLDEN = ".golden"

#: The seed trees a member is born from, each to its member's directory
#: here: the three package seeds, then the extension package's.
TREES = {
    "package-python": "echo",
    "package-python-nanobind": "native",
    "package-cpp-conan": "lib",
    "package-extension": "plugin",
}

#: The three package seeds, whose members one workspace composes.
THREE = ("package-python", "package-python-nanobind", "package-cpp-conan")

#: What the composing workspace lists: the python formatter, linter, two
#: type checkers and test runner, and the C and C++ formatter and linter.
STACK = ("ruff", "basedpyright", "mypy", "pytest", "clang-format", "clang-tidy")

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
    from livery.workshop import _extensions, _points, _site_files, _steps

    monkeypatch.setattr(footman, "prog", lambda: "fm")
    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))
    # An extension registers the site files it renders when its module is
    # imported, and no test puts that registry back. No workspace here
    # lists such an extension, so a process started in one renders none.
    monkeypatch.setattr(_site_files, "_FILES", {})


def _workspace(root: Path, listed: tuple[str, ...] = ()) -> Path:
    """A workspace named acme at *root* that lists *listed* and has no member."""
    root.mkdir(parents=True)
    spelled = ", ".join(f'"{name}"' for name in listed)
    (root / "workshop.toml").write_text(CONTRACT.format(listed=spelled), "utf-8")
    return root


def _born(root: Path, *trees: str) -> dict[str, Package]:
    """Birth one member of each of *trees* in *root*, as ``fm new.package`` does.

    The members discovered, each by its tree.
    """
    for tree in trees:
        render_member(root, TREES[tree], kind=tree)
    found = {package.member: package for package in discover_packages(root)}
    return {tree: found[TREES[tree]] for tree in trees}


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

#: The package-level extensions each tree's member holds, in composition
#: order. A member that lists none holds what its kind stands for.
HELD = {
    "package-python": ("python",),
    "package-python-nanobind": ("cpp", "conan", "nanobind", "python"),
    "package-cpp-conan": ("cmake", "cpp", "conan"),
    "package-extension": ("python",),
}


def test_each_seed_s_member_holds_its_set(tmp_path: Path) -> None:
    from livery.workshop._composition import order
    from livery.workshop._extensions import installed_declaration, package_extensions
    from livery.workshop._kinds import extension_set

    root = _workspace(tmp_path / "acme")
    members = _born(root, *TREES)
    held = {
        tree: order(extension_set(package), installed_declaration)
        for tree, package in members.items()
    }
    assert held == HELD
    # The mount takes the union of the members' sets, in composition order.
    assert package_extensions(root) == ("cmake", "cpp", "conan", "nanobind", "python")


#: The lines of each tree's contract that say what its member is, by key.
#: The golden holds ``<key> = ...`` in their place.
IDENTIFYING = {
    "package-python": {"kind": '"python"', "extensions": '["python"]'},
    "package-python-nanobind": {"kind": '"python-nanobind"'},
    "package-cpp-conan": {"kind": '"cpp-conan"', "extensions": '["cmake", "conan"]'},
    "package-extension": {
        "kind": '"python"',
        "extensions": '["python"]',
        "template": '"package-extension"',
    },
}

_IDENTIFYING_LINE = re.compile(r"^(kind|extensions|template) = (.*)$", re.MULTILINE)


def test_each_seed_births_these_files(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "acme")
    _born(root, *TREES)
    born: dict[str, str] = {}
    identifying: dict[str, dict[str, str]] = {}
    for tree, member in TREES.items():
        files = _files(root / "packages" / member)
        contract = files["workshop.toml"]
        identifying[tree] = dict(_IDENTIFYING_LINE.findall(contract))
        files["workshop.toml"] = _IDENTIFYING_LINE.sub(r"\1 = ...", contract)
        born.update({f"{tree}/{path}": text for path, text in files.items()})
    assert identifying == IDENTIFYING
    match(born, FIXTURES / "seeds", tmp_path / "render", suffix=GOLDEN)


# The workspace of the three package seeds.


def _three_seeds(root: Path) -> tuple[dict[str, Package], tuple[str, ...]]:
    """A workspace listing `STACK` with a member of each of `THREE`, synced.

    The members by their trees, and the extensions the mount took, in
    its order. The members are born first, and a process started after
    them mounts and composes, as ``fm new.package`` hands the wiring to
    a runner started after the birth.
    """
    _workspace(root, STACK)
    members = _born(root, *THREE)
    mounted = _mounted(root)
    apply_project(root)
    return members, mounted


#: What a process started in the three seeds' workspace mounts, in order:
#: the package-level extensions the members hold, then the list.
MOUNTED = (
    "cmake",
    "cpp",
    "conan",
    "nanobind",
    "python",
    "ruff",
    "basedpyright",
    "mypy",
    "pytest",
    "clang-format",
    "clang-tidy",
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
    " ruff:ruff recommendations, basedpyright:basedpyright recommendations",
    ".vscode/settings.json: livery.workshop:.vscode/settings.json,"
    " ruff:step format.ruff settings,"
    " basedpyright:step typecheck.basedpyright settings",
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
#: first required.
REQUIRED = {
    "no member": (("uv", "kind python"),),
    "package-python": (("uv", "kind python"),),
    "package-python-nanobind": (
        ("uv", "kind python"),
        ("cmake", "kind python-nanobind"),
        ("ninja", "kind python-nanobind"),
        ("conan", "kind python-nanobind"),
        ("cmake_conan", "kind python-nanobind"),
    ),
    "package-cpp-conan": (
        ("cmake", "kind cpp-conan"),
        ("conan", "kind cpp-conan"),
        ("ninja", "kind cpp-conan"),
        ("uv", "kind python"),
        ("cmake", "kind python-nanobind"),
        ("ninja", "kind python-nanobind"),
        ("conan", "kind python-nanobind"),
        ("cmake_conan", "kind python-nanobind"),
    ),
}

#: The tools the host must provide that the same workspaces name, on a
#: host that provides none of them.
HOST_TOOLS = {
    "no member": (),
    "package-python": (),
    "package-python-nanobind": ("c++", "cc"),
    "package-cpp-conan": ("c++", "cc"),
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


#: What each python step reads when a change touches the workspace's own
#: tests alone, in a workspace whose one member is a C++ library.
ROOT_TESTS_READ = {
    "format.ruff": ("tests",),
    "lint.ruff": ("tests",),
    "typecheck.basedpyright": (".",),
    "typecheck.mypy": (".",),
}

#: The tools the steps require in that workspace, each with its site.
ROOT_TESTS_TOOLS = (
    ("ruff", "step format.ruff"),
    ("ruff", "step lint.ruff"),
    ("basedpyright", "step typecheck.basedpyright"),
    ("mypy", "step typecheck.mypy"),
    ("pytest", "step test.pytest"),
    ("pytest", "step examples.pytest"),
    ("clang_format", "step format.clang-format"),
    ("clang_tidy", "step lint.clang-tidy"),
)

#: The per-file ignores the claims render for the root's own files there.
ROOT_IGNORES = (
    ("tests/**", ("D1",)),
    ("tests/**/*_test.py", ("D1",)),
    ("tests/**/test_*.py", ("D1",)),
)


def test_the_workspace_s_own_tests_are_judged_as_python_without_a_python_member(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import workspace_suite
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import (
        PATHS,
        per_file_ignores,
        scoped_paths,
        steps_by_name,
        tested,
    )
    from livery.workshop._tools import requirements

    root = _workspace(tmp_path / "acme", STACK)
    lib = _born(root, "package-cpp-conan")["package-cpp-conan"]
    (root / "tests").mkdir()
    (root / "tests" / "test_workspace.py").write_text(
        "def test_the_workspace() -> None:\n    pass\n", "utf-8"
    )
    _mounted(root)
    unit = workspace_suite(root)
    assert unit is not None
    # The test phase runs the root's suite beside the library's.
    assert [package.path for package in tested((lib, unit))] == [
        "packages/lib",
        "tests",
    ]
    # A change to the root's tests alone is a scoped run over that unit.
    ctx = PhaseContext(root=root, packages=(lib,), subset=(unit,))
    read = {
        name: scoped_paths(ctx, name)
        for name, record in steps_by_name().items()
        if record.narrowing == PATHS and "python" in record.extensions
    }
    assert read == ROOT_TESTS_READ
    assert (
        tuple(
            dict.fromkeys(
                (found.name, found.site)
                for found in requirements(root)
                if found.site.startswith("step ")
            )
        )
        == ROOT_TESTS_TOOLS
    )
    root_ignores = tuple(
        (pattern, codes)
        for pattern, codes in per_file_ignores(("cpp-conan",))
        if not pattern.startswith("packages/")
    )
    assert root_ignores == ROOT_IGNORES


#: Each step a process registers in the three seeds' workspace, in
#: registration order, with the extension that registered it and the
#: members it judges.
JUDGED = (
    "drift.check (livery.workshop): echo, native, lib",
    "provenance.check (livery.workshop): echo, native, lib",
    "layering.graph (livery.workshop): echo, native, lib",
    "layering.base-imports-no-extension (livery.workshop): echo, native, lib",
    "layering.sibling-references (livery.workshop): echo, native, lib",
    "build.configure (livery.workshop): lib",
    "build.compile (livery.workshop): lib",
    "test.ctest (livery.workshop): lib",
    "layering.private-reaches (python): echo, native, lib",
    "layering.runner-terminal (python): echo, native, lib",
    "layering.declared-imports (python): echo, native, lib",
    "format.ruff (ruff): echo, native, lib",
    "lint.ruff (ruff): echo, native, lib",
    "typecheck.basedpyright (basedpyright): echo, native",
    "typecheck.mypy (mypy): echo, native",
    "test.pytest (pytest): echo, native",
    "examples.pytest (pytest): echo, native",
    "format.clang-format (clang-format): native, lib",
    "lint.clang-tidy (clang-tidy): native, lib",
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
    # The library's seeded recipe declares no requires tuple to join.
    ("lib", "geometry"): (
        "refused: packages/lib/conanfile.py declares no `requires = (...)` tuple"
        ' to add "acme-geometry/[>=0.1.0]" to; add the requirement by hand',
    ),
}


def test_the_sibling_requirement_lands_in_its_ecosystem_s_manifest(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path / "acme")
    for name, tree in (
        ("echo", "package-python"),
        ("core", "package-python"),
        ("native", "package-python-nanobind"),
        ("lib", "package-cpp-conan"),
        ("geometry", "package-cpp-conan"),
    ):
        render_member(root, name, kind=tree)
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
    for tree in ("package-python", "package-python-nanobind"):
        (members[tree].directory / "dist").mkdir()
    assert [path.relative_to(root).as_posix() for path in _wheel_dists(plans)] == [
        "packages/echo/dist",
        "packages/native/dist",
    ]


#: Where python's backend and the nanobind kind's are defined.
PYTHON = "livery.extensions.python._backend"
NANOBIND = "livery.workshop._backends._python_nanobind"

#: The function each lifecycle entry ran for a nanobind member, which lists
#: no extension, by the entry's name: the module and name that define it.
NANOBIND_BODIES = {
    "current_version": (f"{PYTHON}:current_version",),
    "version_files": (f"{PYTHON}:stamp_version",),
    "declared_requirements": (f"{NANOBIND}:declared_requirements",),
    "module_roots": (f"{PYTHON}:module_roots",),
    "public_modules": (f"{PYTHON}:public_modules",),
    "compile_commands": (f"{PYTHON}:compile_commands",),
    "references": (f"{PYTHON}:references",),
    # The answer is whether the backend has an isolated test; none runs.
    "has_isolated_test": (),
    "stamp": (f"{PYTHON}:stamp_version",),
    "declare_requirement": (f"{NANOBIND}:declare_requirement",),
    "build": (f"{NANOBIND}:build",),
    "publish": (f"{PYTHON}:publish_artifact",),
    "isolated_test": (f"{PYTHON}:isolated_test",),
    "verify": (f"{PYTHON}:verify",),
    # The sync phase runs over what the kind stands for: conan's step.
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
    from livery.extensions.conan import _steps as conan_steps
    from livery.workshop._backends import _python_nanobind
    from livery.workshop._registries import RegistryTarget

    root = _workspace(tmp_path / "acme")
    members = _born(root, "package-python", "package-python-nanobind")
    echo, native = members["package-python"], members["package-python-nanobind"]
    bodies = _Bodies()
    # A function that reads or writes the member's files runs; one that
    # builds, uploads, installs or calls conan answers in its place.
    for name in (
        "current_version",
        "stamp_version",
        "declared_requirements",
        "declare_requirement",
        "module_roots",
        "public_modules",
        "compile_commands",
        "references",
    ):
        bodies.record(monkeypatch, _python_nanobind, name)
    stand_ins: dict[str, object] = {
        "build": native.directory / "dist",
        "publish_artifact": True,
        "isolated_test": {"acme-native": "0.1.0"},
        "verify": 0,
    }
    for name, answer in stand_ins.items():
        bodies.record(monkeypatch, _python_nanobind, name, answer, runs=False)
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
            "public_modules", lambda: _lifecycle.public_modules(native)
        ),
        "compile_commands": bodies.entry(
            "compile_commands", lambda: _lifecycle.compile_commands(native)
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
    bodies.entry("build", lambda: _lifecycle.build(native, root, epoch=0))
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
    bodies.entry("sync", lambda: _lifecycle.sync((native,), root))
    assert bodies.by_entry == NANOBIND_BODIES
    assert answers == NANOBIND_ANSWERS


# The guidance and the categories.


def test_the_kinds_present_guidance_for_the_three_seeds(tmp_path: Path) -> None:
    from livery.workshop._prose import AGENT, HUMAN, guidance

    root = _workspace(tmp_path / "acme", STACK)
    _born(root, *THREE)
    _mounted(root)
    (fragment,) = [
        prose for prose in guidance(root, AGENT) if prose.name == "kinds.present.md"
    ]
    rendered = {
        f"kinds.present.{audience}.md": fragment.text(root, audience)
        for audience in (AGENT, HUMAN)
    }
    match(
        rendered, FIXTURES / "three-seeds-guidance", tmp_path / "render", suffix=GOLDEN
    )


#: What each file a nanobind member is born with is to it, and the
#: extension or kind whose rule says so.
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
    # cpp's table is nearer than python's, and both claim src/**.
    "src/acme/native/__init__.py": ("source", "cpp"),
    "src/acme/native/_native.cpp": ("source", "cpp"),
    "src/acme/native/_native.pyi": ("source", "cpp"),
    "src/acme/native/py.typed": ("source", "cpp"),
    # python's test pattern is more specific than cpp's tests/**.
    "tests/test_native_package.py": ("test", "python"),
    "workshop.toml": ("configuration", "livery.workshop"),
}


def test_a_nanobind_member_s_files_take_these_categories(tmp_path: Path) -> None:
    from livery.workshop._categories import category_of

    root = _workspace(tmp_path / "acme")
    native = _born(root, "package-python-nanobind")["package-python-nanobind"]
    found: dict[str, tuple[str, str]] = {}
    for path in _files(native.directory):
        category = category_of(native, path)
        found[path] = (category.name, category.supplier)
    assert found == NANOBIND_CATEGORIES
