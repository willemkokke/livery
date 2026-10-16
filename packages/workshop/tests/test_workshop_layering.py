"""The layering lint refuses each violation by name, on synthetic trees."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from livery.workshop import discover_packages, verify_workspace
from livery.workshop._packages import RULES, Package, write_edges
from workshop_installed import skip_unless_installed

_FAILURES = (BaseException,)


def _package(
    root: Path,
    name: str,
    *,
    contract_extra: str = "",
    dependencies: tuple[str, ...] = (),
) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n{contract_extra}'
    )
    deps = ", ".join(f'"{d}"' for d in dependencies)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = [{deps}]\n'
    )


def _forge_stub(root: Path) -> None:
    # A member the graph's edges point at, with one clean source.
    src = root / "packages" / "forge" / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("import json\n")
    (root / "packages" / "forge" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-forge"\n'
    )
    (root / "packages" / "forge" / "pyproject.toml").write_text(
        '[project]\nname = "livery-forge"\ndependencies = []\n'
    )


def test_a_contractless_package_is_refused(tmp_path: Path) -> None:
    (tmp_path / "packages" / "stray").mkdir(parents=True)
    with pytest.raises(ValueError, match=re.escape("stray: no workshop.toml")):
        discover_packages(tmp_path)


def test_an_undeclared_native_dependency_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "tool", dependencies=("livery-forge>=0.1.0",))
    with pytest.raises(ValueError, match="no \\[\\[depends\\]\\] edge"):
        verify_workspace(tmp_path)


def test_a_declared_edge_missing_natively_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.1.0"\n'
        ),
    )
    with pytest.raises(ValueError, match="is not declared in"):
        verify_workspace(tmp_path)


def test_a_floor_disagreement_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.2.0"\n'
        ),
        dependencies=("livery-forge>=0.1.0",),
    )
    with pytest.raises(ValueError, match=re.escape("floors at 0.2.0")):
        verify_workspace(tmp_path)


def test_a_cycle_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "a",
        contract_extra='[[depends]]\npath = "packages/b"\nkind = "build"\n',
        dependencies=("livery-b",),
    )
    _package(
        tmp_path,
        "b",
        contract_extra='[[depends]]\npath = "packages/a"\nkind = "build"\n',
        dependencies=("livery-a",),
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        verify_workspace(tmp_path)


def _module(root: Path, package: str, name: str, body: str) -> Path:
    src = root / "packages" / package / "src"
    src.mkdir(parents=True, exist_ok=True)
    path = src / name
    path.write_text(body)
    return path


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.1.0"\n'
        ),
        dependencies=("livery-forge>=0.1.0",),
    )
    packages = verify_workspace(tmp_path)
    assert [p.name for p in packages] == ["livery-forge", "livery-tool"]


def test_the_task_tree_imports_no_pytest() -> None:
    """A CLI built from this extension runs where pytest is not installed.

    A brand's tool venv carries the extension and its runtime dependencies,
    never the test toolchain. One import of a pytest plugin module on
    the path a verb takes turns every command on that CLI into a
    ModuleNotFoundError, which is how it was found: the descendant
    chain's brand tool refused `new.project` on a pre-task that
    reached the points module.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "class Refuse:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'pytest' or name.startswith('pytest.'):\n"
        "            raise ImportError('a brand tool has no pytest')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Refuse())\n"
        "import livery.workshop._tasks\n"
        "import livery.workshop._points\n"
        "import livery.workshop._ci_tasks\n"
        "print('imported')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "imported" in done.stdout


def test_the_verbs_import_no_optional_plugin() -> None:
    """An optional plugin is imported where it is used, never on the way in.

    The profile plugin is mounted by a workspace that wants traces and is
    inert otherwise. A module-level import of it anywhere a verb's own path
    reaches makes every run of every verb pay for it, and makes a run that
    mounted nothing carry its global option too. The submit reaches the
    traces module for one function, which is how this was found.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import livery.workshop._submit\n"
        "import livery.workshop._traces\n"
        "import livery.workshop._ci_tasks\n"
        "rode = [n for n in sys.modules if n.endswith('footman.profile')]\n"
        "print('rode:', rode)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "rode: []" in done.stdout, done.stdout


def _spawned_by_name(source: Path) -> list[str]:
    """Every `run([...])` under *source* whose program is a literal."""
    import ast

    offenders = []
    for path in sorted(source.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else ""
            if name != "run" and getattr(called, "id", "") != "run":
                continue
            argv = node.args[0]
            if not isinstance(argv, ast.List) or not argv.elts:
                continue
            head = argv.elts[0]
            if isinstance(head, ast.Constant):
                offenders.append(
                    f"{path.relative_to(source)}:{node.lineno}: {head.value!r}"
                )
    return offenders


def test_a_literal_program_name_is_caught(tmp_path: Path) -> None:
    """The walk sees a planted spawn, and passes what a handle writes.

    The refusal first: a check that cannot fail proves nothing, and
    this one only ever runs against a tree that already passes.
    """
    (tmp_path / "bad.py").write_text(
        "import livery.footman as footman\n\n"
        "def go() -> None:\n"
        '    footman.run(["git", "status"], nofail=True)\n'
    )
    assert _spawned_by_name(tmp_path) == ["bad.py:4: 'git'"]
    # A handle call, a variable program, and a shell string all pass:
    # none of them names a program this tree could have resolved.
    (tmp_path / "bad.py").write_text(
        "import sys\nimport livery.footman as footman\n"
        "import livery.toolroom.tools as tools\n\n"
        "def go() -> None:\n"
        '    tools.git.opts(nofail=True)("status")\n'
        '    footman.run([sys.executable, "-c", "pass"])\n'
        '    footman.run("tar -cf - . | ssh host tar -xf -", shell=True)\n'
    )
    assert _spawned_by_name(tmp_path) == []


def test_no_tool_is_spawned_under_a_literal_program_name() -> None:
    """Every tool the workshop runs reaches it through a toolroom handle.

    An argv list whose first element is a string literal names a
    program on PATH and steps around the store: the version is
    whatever the machine happens to hold, the run is unrecorded, and
    the flags check against nothing. A handle answers all three, and
    `tools.<name>` reaches a tool with no generated stub just as
    well, so a missing stub is never the reason to spawn by name.

    Two spellings stay and neither is a literal. The runner is
    `footman.prog()`, since a branded instance is not called `fm`.
    The interpreter of a venv under test is `sys.executable` or a
    path, since that interpreter is the subject, not a tool.
    """
    source = Path(__file__).resolve().parents[1] / "src" / "livery" / "workshop"
    offenders = _spawned_by_name(source)
    assert not offenders, "spawned by name instead of by handle:\n" + "\n".join(
        offenders
    )


# --- references the sources make, and what accounts for each ----------------


def _sourced(root: Path, package: str, relative: str, body: str) -> None:
    """Write *body* at *relative* under a package's src, tree and all."""
    path = root / "packages" / package / "src" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _tested(root: Path, package: str, relative: str, body: str) -> None:
    """Write *body* at *relative* under a package's tests."""
    path = root / "packages" / package / "tests" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _edge(path: str, kind: str, floor: str) -> str:
    return f'[[depends]]\npath = "{path}"\nkind = "{kind}"\nfloor = "{floor}"\n'


def test_a_reference_nothing_reaches_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    with pytest.raises(ValueError, match="this one is a new dependency"):
        verify_workspace(tmp_path)


def test_a_reference_the_graph_reaches_names_the_edge_and_the_floor(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(
        tmp_path,
        "mid",
        contract_extra=_edge("packages/low", "runtime", "1.2.0"),
        dependencies=("livery-low>=1.2.0",),
    )
    _sourced(tmp_path, "mid", "livery/mid/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/mid", "runtime", "0.1.0"),
        dependencies=("livery-mid>=0.1.0",),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    with pytest.raises(
        ValueError, match=re.escape("Declare the runtime edge at floor 1.2.0")
    ):
        verify_workspace(tmp_path)


def test_the_inherited_floor_is_the_highest_the_reachable_set_carries(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    for name, floor in (("mid", "1.2.0"), ("other", "2.5.1")):
        _package(
            tmp_path,
            name,
            contract_extra=_edge("packages/low", "runtime", floor),
            dependencies=(f"livery-low>={floor}",),
        )
        _sourced(tmp_path, name, f"livery/{name}/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=(
            _edge("packages/mid", "runtime", "0.1.0")
            + _edge("packages/other", "runtime", "0.1.0")
        ),
        dependencies=("livery-mid>=0.1.0", "livery-other>=0.1.0"),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    with pytest.raises(ValueError, match=re.escape("at floor 2.5.1")):
        verify_workspace(tmp_path)


def test_a_reference_from_the_tests_alone_asks_for_a_test_edge(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(
        tmp_path,
        "mid",
        contract_extra=_edge("packages/low", "runtime", "1.2.0"),
        dependencies=("livery-low>=1.2.0",),
    )
    _sourced(tmp_path, "mid", "livery/mid/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/mid", "runtime", "0.1.0"),
        dependencies=("livery-mid>=0.1.0",),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "")
    _tested(tmp_path, "high", "test_it.py", "import livery.low\n")
    with pytest.raises(ValueError, match=re.escape("Declare the test edge")):
        verify_workspace(tmp_path)


def test_a_reference_inside_a_declared_plugin_module_is_accounted_for(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    (tmp_path / "packages" / "high" / "pyproject.toml").write_text(
        '[project]\nname = "livery-high"\ndependencies = []\n\n'
        '[project.entry-points."footman.tasks"]\n'
        '"livery.high" = "livery.high._dev"\n'
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "")
    _sourced(tmp_path, "high", "livery/high/_dev.py", "import livery.low\n")
    verify_workspace(tmp_path)


def test_a_sibling_named_in_an_optional_extra_is_accounted_for(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    (tmp_path / "packages" / "high" / "pyproject.toml").write_text(
        '[project]\nname = "livery-high"\ndependencies = []\n\n'
        "[project.optional-dependencies]\n"
        'test = ["livery-low>=1.0"]\n'
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    verify_workspace(tmp_path)


def test_a_reference_guarded_by_try_except_is_accounted_for(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    _sourced(
        tmp_path,
        "high",
        "livery/high/__init__.py",
        "try:\n    import livery.low\nexcept ImportError:\n    pass\n",
    )
    verify_workspace(tmp_path)


def test_the_longest_prefix_decides_which_package_owns_a_module(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "space")
    _sourced(tmp_path, "space", "livery/space/__init__.py", "")
    _package(tmp_path, "inner")
    # A PEP 420 namespace: this distribution owns livery.space.inner,
    # and the shorter prefix belongs to its neighbour.
    _sourced(tmp_path, "inner", "livery/space/inner/__init__.py", "")
    _package(tmp_path, "high")
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.space.inner\n")
    with pytest.raises(ValueError, match="livery-inner"):
        verify_workspace(tmp_path)


def test_a_runtime_edges_floor_must_match_its_requirement(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/low", "runtime", "1.0.0"),
        dependencies=("livery-low>=2.0.0",),
    )
    with pytest.raises(ValueError, match=re.escape("floors at 1.0.0")):
        verify_workspace(tmp_path)


def test_a_contract_naming_the_kind_under_type_is_refused_with_the_migration(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "packages" / "old"
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text('type = "python"\nname = "livery-old"\n')
    (directory / "pyproject.toml").write_text('[project]\nname = "livery-old"\n')
    with pytest.raises(
        BaseException, match="the top level has no key 'type': it takes categories"
    ):
        discover_packages(tmp_path)


# --- the fix mode: the edge the graph already reaches is written -----------


def _recipe(name: str) -> str:
    """A conan package's recipe, which names its reference, for ``acme-<name>``."""
    return (
        "from conan import ConanFile\n\n\n"
        f"class {name.title()}(ConanFile):\n"
        f'    """The {name} library."""\n\n'
        f'    name = "acme-{name}"\n'
        '    version = "0.1.0"\n\n'
        "    def build(self):\n"
        "        pass\n"
    )


#: A consumer's recipe: requirements alone, and no reference of its own.
CONSUMER_RECIPE = (
    "from conan import ConanFile\n\n\n"
    "class Consumer(ConanFile):\n"
    '    generators = "CMakeDeps"\n'
    '    requires = ("fmt/[>=11.0]",)\n'
)


def _member(
    root: Path,
    name: str,
    files: dict[str, str],
    extensions: tuple[str, ...] = (),
) -> Package:
    """The member ``acme-<name>`` at ``packages/<name>``, holding *files*."""
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    held = f"extensions = [{listed}]\n" if extensions else ""
    (directory / "workshop.toml").write_text(f'name = "acme-{name}"\n{held}')
    for relative, text in files.items():
        (directory / relative).write_text(text)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        extensions=extensions,
    )


def _three_ecosystems(root: Path) -> tuple[Package, Package, Package]:
    """A python package, a C++ library with conan, and a nanobind module.

    Each lists its extensions; the nanobind module's set holds python
    and conan beside nanobind, conan through a consumer's recipe.
    """
    echo = _member(
        root,
        "echo",
        {"pyproject.toml": '[project]\nname = "acme-echo"\n'},
        ("python",),
    )
    lib = _member(root, "lib", {"conanfile.py": _recipe("lib")}, ("cmake", "conan"))
    native = _member(
        root,
        "native",
        {
            "pyproject.toml": '[project]\nname = "acme-native"\n',
            "conanfile.py": CONSUMER_RECIPE,
        },
        ("nanobind",),
    )
    return echo, lib, native


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root*'s packages, by its path there, to its text."""
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in sorted((root / "packages").rglob("*"))
        if path.is_file()
    }


def test_a_sibling_no_writer_of_the_set_takes_refuses_naming_the_sibling_s_set(
    tmp_path: Path,
) -> None:
    skip_unless_installed("cmake", "conan", "nanobind")
    from livery.footman import Failed
    from livery.workshop import _lifecycle
    from livery.workshop._packages import declare_edge

    echo, lib, native = _three_ecosystems(tmp_path)
    bare = _member(tmp_path, "bare", {"conanfile.py": _recipe("bare")})
    before = _tree(tmp_path)
    # The package's set holds python's writer alone, and the library
    # holds no python.
    with pytest.raises(Failed) as refused:
        _lifecycle.declare_requirement(echo, lib, "0.1.0")
    assert str(refused.value) == (
        "packages/echo holds python, and no writer of its set takes a requirement"
        " on packages/lib, which holds cmake, cpp, conan: a writer takes a sibling"
        " of its own ecosystem alone. List an extension whose manifest takes"
        " packages/lib, or drop the dependency"
    )
    # The edge refuses before its contract changes.
    with pytest.raises(Failed, match=re.escape("which holds cmake, cpp, conan")):
        declare_edge(echo, lib, kind="runtime", floor="0.1.0")
    # A package that lists nothing holds no writer at all.
    with pytest.raises(
        Failed,
        match=re.escape(
            "packages/bare holds no extension, and no writer of its set takes a"
            " requirement on packages/native, which holds cpp, conan, python,"
            " nanobind:"
        ),
    ):
        _lifecycle.declare_requirement(bare, native, "0.1.0")
    assert _tree(tmp_path) == before


def test_python_s_writer_leaves_a_sibling_outside_its_ecosystem_alone(
    tmp_path: Path,
) -> None:
    # conan's own suite judges conan's writer the same way.
    skip_unless_installed("conan", "nanobind")
    from livery.extensions.python import _backend as python

    echo, lib, native = _three_ecosystems(tmp_path)
    before = _tree(tmp_path)
    # Python's writer takes a sibling that holds python alone.
    assert python.declare_requirement(echo, lib, "0.1.0") is None
    assert _tree(tmp_path) == before
    # It takes a sibling of its own ecosystem.
    assert python.declare_requirement(native, echo, "0.1.0") == ["pyproject.toml"]


def test_a_reference_nothing_reaches_is_not_written(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    assert write_edges(tmp_path) == []
    assert (
        "[[depends]]"
        not in (tmp_path / "packages" / "high" / "workshop.toml").read_text()
    )
    with pytest.raises(ValueError, match="this one is a new dependency"):
        verify_workspace(tmp_path)


def test_a_reference_the_graph_reaches_is_written_at_the_inherited_floor(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(
        tmp_path,
        "mid",
        contract_extra=_edge("packages/low", "runtime", "1.2.0"),
        dependencies=("livery-low>=1.2.0",),
    )
    _sourced(tmp_path, "mid", "livery/mid/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/mid", "runtime", "0.1.0"),
        dependencies=("livery-mid>=0.1.0",),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    with pytest.raises(ValueError, match=re.escape("the gate's --fix writes both")):
        verify_workspace(tmp_path)
    written = write_edges(tmp_path)
    assert written == [
        "  layering: packages/high declares the runtime edge on packages/low at"
        " floor 1.2.0 (workshop.toml, pyproject.toml)"
    ]
    contract = (tmp_path / "packages" / "high" / "workshop.toml").read_text()
    assert _edge("packages/low", "runtime", "1.2.0") in contract
    pyproject = (tmp_path / "packages" / "high" / "pyproject.toml").read_text()
    assert '"livery-low>=1.2.0"' in pyproject
    # The lint is satisfied, and a second fix writes nothing.
    verify_workspace(tmp_path)
    assert write_edges(tmp_path) == []


def test_a_test_reference_writes_the_test_edge_and_its_test_requirement(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(
        tmp_path,
        "mid",
        contract_extra=_edge("packages/low", "runtime", "1.2.0"),
        dependencies=("livery-low>=1.2.0",),
    )
    _sourced(tmp_path, "mid", "livery/mid/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/mid", "runtime", "0.1.0"),
        dependencies=("livery-mid>=0.1.0",),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "")
    _tested(tmp_path, "high", "test_low.py", "import livery.low\n")
    written = write_edges(tmp_path)
    assert written == [
        "  layering: packages/high declares the test edge on packages/low at"
        " floor 1.2.0 (workshop.toml, pyproject.toml)"
    ]
    # The requirement lands in the test extra, not the dependencies.
    pyproject = (tmp_path / "packages" / "high" / "pyproject.toml").read_text()
    assert tomllib.loads(pyproject)["project"] == {
        "name": "livery-high",
        "dependencies": ["livery-mid>=0.1.0"],
        "optional-dependencies": {"test": ["livery-low>=1.2.0"]},
    }
    verify_workspace(tmp_path)


# A test edge: its requirement is a line of the package's test
# requirements, the test extra for python.


def test_a_test_edge_whose_requirement_is_missing_is_named_and_the_fix_writes_it(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import write_test_requirements

    _forge_stub(tmp_path)
    _package(tmp_path, "tool", contract_extra=_edge("packages/forge", "test", "0.1.0"))
    with pytest.raises(ValueError) as refused:
        verify_workspace(tmp_path)
    assert (
        "packages/tool: the test edge on packages/forge is not declared in the"
        " package's test requirements (livery-forge missing; packages/forge holds"
        " python); the gate's --fix writes it"
    ) in str(refused.value)
    assert write_test_requirements(tmp_path) == [
        "  layering: packages/tool requires livery-forge at floor 0.1.0 for its"
        " tests (pyproject.toml)"
    ]
    pyproject = (tmp_path / "packages" / "tool" / "pyproject.toml").read_text()
    assert tomllib.loads(pyproject)["project"]["optional-dependencies"] == {
        "test": ["livery-forge>=0.1.0"]
    }
    verify_workspace(tmp_path)
    assert write_test_requirements(tmp_path) == []


def test_a_test_requirement_at_another_floor_is_named_and_the_fix_writes_the_floor(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import write_test_requirements

    _forge_stub(tmp_path)
    _package(tmp_path, "tool", contract_extra=_edge("packages/forge", "test", "0.2.0"))
    pyproject = tmp_path / "packages" / "tool" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + "\n[project.optional-dependencies]\n"
        'test = ["livery-forge>=0.1.0", "pyyaml"]\n'
    )
    with pytest.raises(
        ValueError,
        match=re.escape(
            "packages/tool: the test edge on packages/forge floors at 0.2.0, and the"
            " declared test requirement says livery-forge>=0.1.0; the gate's --fix"
            " writes the floor"
        ),
    ):
        verify_workspace(tmp_path)
    assert len(write_test_requirements(tmp_path)) == 1
    assert '\ntest = ["livery-forge>=0.2.0", "pyyaml"]\n' in pyproject.read_text()
    verify_workspace(tmp_path)


def test_a_test_edge_with_no_home_and_a_tool_edge_are_not_judged(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import write_test_requirements

    _forge_stub(tmp_path)
    # A set that answers no test requirements has no home for a test
    # edge, and a tool edge has none anywhere.
    bare = tmp_path / "packages" / "bare"
    bare.mkdir(parents=True)
    (bare / "workshop.toml").write_text(
        'extensions = []\nname = "livery-bare"\n'
        + _edge("packages/forge", "test", "0.1.0")
    )
    _package(tmp_path, "tool", contract_extra=_edge("packages/forge", "tool", "0.1.0"))
    verify_workspace(tmp_path)
    assert write_test_requirements(tmp_path) == []
    assert not (bare / "pyproject.toml").exists()
    assert (
        "optional-dependencies"
        not in (tmp_path / "packages" / "tool" / "pyproject.toml").read_text()
    )


def test_a_test_edge_no_writer_takes_refuses_naming_both_sets(tmp_path: Path) -> None:
    skip_unless_installed("cmake", "conan")
    from livery.footman import Failed
    from livery.workshop import _lifecycle

    echo, lib, _native = _three_ecosystems(tmp_path)
    before = _tree(tmp_path)
    with pytest.raises(Failed) as refused:
        _lifecycle.declare_requirement(echo, lib, "0.1.0", kind="test")
    assert str(refused.value).startswith(
        "packages/echo holds python, and no writer of its set takes a test"
        " requirement on packages/lib, which holds cmake, cpp, conan:"
    )
    assert _tree(tmp_path) == before


def test_the_graph_fix_writes_the_test_requirements_then_locks_then_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _extensions, _packages
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import step_for

    order: list[str] = []

    def extensions(root: Path) -> list[str]:
        order.append(f"extensions at {root.name}")
        return []

    def requirements(root: Path) -> list[str]:
        order.append(f"test requirements at {root.name}")
        return [
            "  layering: packages/tool requires livery-forge at floor 1 for its"
            " tests (pyproject.toml)"
        ]

    def judged(root: Path) -> tuple[Package, ...]:
        order.append(f"judge at {root.name}")
        return ()

    def locked(*args: str, root: Path) -> None:
        order.append(f"uv {' '.join(args)} at {root.name}")

    monkeypatch.setattr(_extensions, "write_extensions", extensions)
    monkeypatch.setattr(_packages, "write_test_requirements", requirements)
    monkeypatch.setattr(_packages, "verify_graph", judged)
    monkeypatch.setattr("livery.workshop._uv.run_uv", locked)
    record = step_for("layering.graph")
    assert record.fix is not None
    record.fix(PhaseContext(root=tmp_path, packages=(), fix=True))
    name = tmp_path.name
    assert order == [
        f"extensions at {name}",
        f"test requirements at {name}",
        f"uv lock at {name}",
        f"judge at {name}",
    ]


def _python_member(root: Path, name: str, pyproject: str, *listed: str) -> Package:
    """The package ``livery-<name>`` holding *listed*, its manifest *pyproject*."""
    directory = root / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(pyproject)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        depends=(),
        extensions=listed,
    )


#: The comment a test extra the writer opens starts with.
OPENED = (
    "# The members this package's tests need beyond its dependencies, one\n"
    "# for each test edge of its workshop.toml, at the edge's floor.\n"
)


def test_the_python_test_requirement_refuses_without_project_and_skips_a_stranger(
    tmp_path: Path,
) -> None:
    from livery.extensions.python import _backend as _python

    low = _python_member(tmp_path, "low", '[project]\nname = "livery-low"\n', "python")
    native = _python_member(tmp_path, "native", "", "cmake")
    orphan = _python_member(tmp_path, "orphan", "[tool.uv]\npackage = false\n")
    with pytest.raises(_FAILURES, match="no \\[project\\] table"):
        _python.declare_test_requirement(orphan, low, "1.2.0")
    # A sibling that holds no python is another writer's.
    assert _python.declare_test_requirement(low, native, "1.2.0") is None
    assert (low.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-low"\n'
    )
    # An extra in a form the edit does not take refuses; nothing is written.
    odd = (
        '[project]\nname = "livery-odd"\n\n[project.optional-dependencies]\n'
        'test = ["pyyaml",\n    "livery-low"]\n'
    )
    taken = _python_member(tmp_path, "odd", odd, "python")
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "spells its test extra in a form this edit does not take; write"
            ' "livery-low>=1.2.0" into it by hand'
        ),
    ):
        _python.declare_test_requirement(taken, low, "1.2.0")
    assert (taken.directory / "pyproject.toml").read_text() == odd


def test_the_python_test_requirement_opens_the_extra_joins_it_and_writes_the_floor(
    tmp_path: Path,
) -> None:
    from livery.extensions.python import _backend as _python

    low = _python_member(tmp_path, "low", '[project]\nname = "livery-low"\n', "python")
    urls = '\n[project.urls]\nRepository = "https://example.test"\n'
    # No extra at all: the table opens after [project], before the next.
    bare = _python_member(
        tmp_path, "bare", f'[project]\nname = "livery-bare"\n{urls}', "python"
    )
    assert _python.declare_test_requirement(bare, low, "1.2.0") == ["pyproject.toml"]
    assert (bare.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-bare"\n\n[project.optional-dependencies]\n'
        f'{OPENED}test = ["livery-low>=1.2.0"]\n{urls}'
    )
    # Carried already: nothing to write.
    assert _python.declare_test_requirement(bare, low, "1.2.0") == []
    # A table without the extra: it gains the extra at its end.
    other = _python_member(
        tmp_path,
        "other",
        '[project]\nname = "livery-other"\n\n[project.optional-dependencies]\n'
        f'# Why.\ncli = ["click"]\n{urls}',
        "python",
    )
    _python.declare_test_requirement(other, low, "1.2.0")
    assert (other.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-other"\n\n[project.optional-dependencies]\n'
        f'# Why.\ncli = ["click"]\n{OPENED}test = ["livery-low>=1.2.0"]\n{urls}'
    )
    # A line at another floor is replaced where it stands, and a new one
    # joins the end while the extra fits on its line.
    inline = _python_member(
        tmp_path,
        "inline",
        '[project]\nname = "livery-inline"\n\n[project.optional-dependencies]\n'
        'test = ["livery-low>=1.0.0", "pyyaml"]  # kept\n',
        "python",
    )
    _python.declare_test_requirement(inline, low, "1.2.0")
    deep = _python_member(tmp_path, "deep", '[project]\nname = "x"\n', "python")
    _python.declare_test_requirement(inline, deep, "10.20.30")
    assert (
        (inline.directory / "pyproject.toml")
        .read_text()
        .endswith(
            'test = ["livery-low>=1.2.0", "pyyaml", "livery-deep>=10.20.30"]  # kept\n'
        )
    )
    # Past 88 columns the extra spreads to a line each, comments kept.
    longer = _python_member(
        tmp_path,
        "another-sibling-with-a-long-name",
        '[project]\nname = "y"\n',
        "python",
    )
    _python.declare_test_requirement(inline, longer, "1.0.0")
    spread = (
        "test = [  # kept\n"
        '    "livery-low>=1.2.0",\n'
        '    "pyyaml",\n'
        '    "livery-deep>=10.20.30",\n'
        '    "livery-another-sibling-with-a-long-name>=1.0.0",\n'
        "]\n"
    )
    assert (inline.directory / "pyproject.toml").read_text().endswith(spread)
    # A spread extra: a line at another floor is replaced, its comments kept.
    _python.declare_test_requirement(inline, low, "2.0.0")
    assert (
        (inline.directory / "pyproject.toml")
        .read_text()
        .endswith(spread.replace("livery-low>=1.2.0", "livery-low>=2.0.0"))
    )
    # No floor: the name alone.
    plain = _python_member(
        tmp_path, "plain", '[project]\nname = "livery-plain"\n', "python"
    )
    _python.declare_test_requirement(plain, low, "")
    assert 'test = ["livery-low"]' in (plain.directory / "pyproject.toml").read_text()
    assert _python.declare_test_requirement(plain, low, "") == []


def test_the_python_requirement_joins_a_list_opens_one_or_refuses_without_project(
    tmp_path: Path,
) -> None:
    from livery.extensions.python import _backend as _python
    from livery.workshop._packages import Package

    def member(name: str, pyproject: str) -> Package:
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pyproject.toml").write_text(pyproject)
        return Package(
            directory=directory,
            path=f"packages/{name}",
            name=f"livery-{name}",
            depends=(),
            extensions=("python",),
        )

    low = member("low", '[project]\nname = "livery-low"\n')
    listed = member(
        "listed",
        '[project]\nname = "livery-listed"\ndependencies = [\n    "pyyaml",\n]\n',
    )
    assert _python.declare_requirement(listed, low, "1.2.0") == ["pyproject.toml"]
    assert (listed.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-listed"\ndependencies = [\n'
        '    "pyyaml",\n    "livery-low>=1.2.0",\n]\n'
    )
    inline = member("inline", '[project]\nname = "livery-inline"\ndependencies = []\n')
    _python.declare_requirement(inline, low, "1.2.0")
    assert (inline.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-inline"\ndependencies = [\n'
        '    "livery-low>=1.2.0",\n]\n'
    )
    bare = member("bare", '[project]\nname = "livery-bare"\n')
    _python.declare_requirement(bare, low, "1.2.0")
    assert '"livery-low>=1.2.0"' in (bare.directory / "pyproject.toml").read_text()
    # Already declared, at any constraint: nothing to write.
    assert _python.declare_requirement(bare, low, "9.9.9") == []
    orphan = member("orphan", "[tool.uv]\npackage = false\n")
    with pytest.raises(_FAILURES, match="no \\[project\\] table"):
        _python.declare_requirement(orphan, low, "1.2.0")


# The one parse, and the rules an extension registers into it.


def test_the_layering_phase_parses_each_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python import _declared, _parse, _terminal
    from livery.extensions.python._reaches import scan
    from livery.workshop import _lifecycle
    from livery.workshop._packages import RuleContext

    _package(tmp_path, "tool")
    _module(tmp_path, "tool", "a.py", "import json\n")
    _module(tmp_path, "tool", "b.py", "import os\n")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    _forge_stub(tmp_path)
    counted: list[Path] = []
    real = _parse._parse

    def counting(path: Path) -> _parse.Parsed | None:
        counted.append(path)
        return real(path)

    def gate() -> None:
        # The base's rules, the python extension's three steps, and each
        # package's references answer.
        packages = verify_workspace(tmp_path)
        modules = _parse.parsed_modules(tmp_path, packages)
        assert scan(modules, packages) == {}
        assert _terminal.problems(modules) == []
        assert _declared.problems(RuleContext(tmp_path, packages)) == []
        for package in packages:
            assert _lifecycle.references(package)

    monkeypatch.setattr(_parse, "_parse", counting)
    _parse._PARSED.clear()
    gate()
    assert sorted(p.name for p in counted) == ["a.py", "b.py", "ok.py", "tasks.py"]
    # The memo: a second gate over the same bytes parses nothing.
    gate()
    assert len(counted) == 4
    # An edited file parses again, once.
    _module(tmp_path, "tool", "a.py", "import json\nimport re\n")
    gate()
    assert [p.name for p in counted[4:]] == ["a.py"]


def test_each_base_rule_is_a_step_of_the_layering_phase_over_the_sources() -> None:
    from livery.workshop._steps import BASE_EXTENSION, step_for

    for step in RULES:
        record = step_for(f"layering.{step}")
        assert record.extension == BASE_EXTENSION
        assert record.inputs is not None
        assert record.inputs.reads == ("tasks.py", "packages/**/*.py")
        # The sibling step's fix is the one rewrite among them.
        assert (record.fix is not None) == (step == "sibling-references")


def test_a_base_step_refuses_with_its_rules_findings_and_the_others_hold(
    tmp_path: Path,
) -> None:
    from livery.footman import Failed
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import step_for

    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(tmp_path, "high")
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    ctx = PhaseContext(root=tmp_path, packages=discover_packages(tmp_path))
    with pytest.raises(Failed) as raised:
        step_for("layering.sibling-references").run(ctx)
    assert str(raised.value).startswith("the workspace breaks its layering:\n  ")
    assert "packages/high: uses livery-low, and nothing it depends on" in str(
        raised.value
    )
    assert set(RULES) == {"base-imports-no-extension", "sibling-references"}
    step_for("layering.base-imports-no-extension").run(ctx)


def test_the_sibling_fix_rewrites_then_locks_a_moved_manifest_then_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _packages
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import step_for

    order: list[str] = []

    def written(root: Path) -> list[str]:
        order.append(f"fix at {root.name}")
        return [
            "  layering: packages/tool declares the build edge on packages/forge"
            " at floor 1 (pyproject.toml)"
        ]

    def judged(
        step: str, root: Path, packages: object, files: object = None
    ) -> list[str]:
        del root, packages, files
        order.append(f"judge {step}")
        return []

    def locked(*args: str, root: Path) -> None:
        order.append(f"uv {' '.join(args)} at {root.name}")

    monkeypatch.setattr(_packages, "write_edges", written)
    monkeypatch.setattr(_packages, "rule_problems", judged)
    monkeypatch.setattr("livery.workshop._uv.run_uv", locked)
    record = step_for("layering.sibling-references")
    assert record.fix is not None
    record.fix(PhaseContext(root=tmp_path, packages=(), fix=True))
    # The rewrite, the lock the moved manifest asks for, then the judge.
    assert order == [
        f"fix at {tmp_path.name}",
        f"uv lock at {tmp_path.name}",
        "judge sibling-references",
    ]


def test_the_sibling_fix_judges_the_contracts_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._phases import PhaseContext
    from livery.workshop._steps import step_for

    _forge_stub(tmp_path)
    _package(tmp_path, "low")
    _sourced(tmp_path, "low", "livery/low/__init__.py", "")
    _package(
        tmp_path,
        "mid",
        contract_extra=_edge("packages/low", "runtime", "1.2.0"),
        dependencies=("livery-low>=1.2.0",),
    )
    _sourced(tmp_path, "mid", "livery/mid/__init__.py", "")
    _package(
        tmp_path,
        "high",
        contract_extra=_edge("packages/mid", "runtime", "0.1.0"),
        dependencies=("livery-mid>=0.1.0",),
    )
    _sourced(tmp_path, "high", "livery/high/__init__.py", "import livery.low\n")
    locked: list[str] = []

    def lock(*args: str, root: Path) -> None:
        locked.append(f"uv {' '.join(args)} at {root.name}")

    monkeypatch.setattr("livery.workshop._uv.run_uv", lock)
    record = step_for("layering.sibling-references")
    assert record.fix is not None
    # The run's packages are read before the fix writes: the judge that
    # follows the fix takes the edge it wrote.
    record.fix(
        PhaseContext(root=tmp_path, packages=discover_packages(tmp_path), fix=True)
    )
    contract = (tmp_path / "packages" / "high" / "workshop.toml").read_text()
    assert _edge("packages/low", "runtime", "1.2.0") in contract
    assert locked == [f"uv lock at {tmp_path.name}"]


def test_a_src_at_the_workspace_root_refuses_naming_the_move(tmp_path: Path) -> None:
    _package(tmp_path, "tool")
    _forge_stub(tmp_path)
    (tmp_path / "src").mkdir()
    with pytest.raises(ValueError, match="src/ at the workspace root is not a member"):
        verify_workspace(tmp_path)
