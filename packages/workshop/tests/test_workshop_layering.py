"""The layering lint refuses each violation by name, on synthetic trees."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import discover_packages, verify_workspace
from livery.workshop._packages import write_edges

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
        f'kind = "python"\nname = "livery-{name}"\n{contract_extra}'
    )
    deps = ", ".join(f'"{d}"' for d in dependencies)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = [{deps}]\n'
    )


def _forge_stub(root: Path, *, plugin: str = "") -> None:
    # The stdlib rule walks packages/forge/src; give the tree a clean one.
    src = root / "packages" / "forge" / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("import json\n")
    (root / "packages" / "forge" / "workshop.toml").write_text(
        'kind = "python"\nname = "livery-forge"\n'
    )
    # *plugin* is a dotted module declared as a footman task entry
    # point, which is what exempts it from the stdlib-only rule.
    entry = (
        f'\n[project.entry-points."footman.tasks"]\nforge = "{plugin}:tasks"\n'
        if plugin
        else ""
    )
    (root / "packages" / "forge" / "pyproject.toml").write_text(
        f'[project]\nname = "livery-forge"\ndependencies = []\n{entry}'
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


def test_a_module_importing_the_runner_may_not_ask_the_terminal(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "tool")
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "import sys\nimport livery.footman as footman\n\n"
        "def go() -> bool:\n    return sys.stdin.isatty()\n",
    )
    with pytest.raises(ValueError, match=r"attended\(\)") as caught:
        verify_workspace(tmp_path)
    assert "sys.stdin.isatty()" in str(caught.value)
    # The stdout spelling is the same question, and so is a name
    # imported from sys directly.
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "from sys import stdout\nfrom livery import footman\n\n"
        "def go() -> bool:\n    return stdout.isatty()\n",
    )
    with pytest.raises(ValueError, match=r"attended\(\)"):
        verify_workspace(tmp_path)


def test_the_runner_and_plain_application_code_may_ask(tmp_path: Path) -> None:
    # The runner implements the answer, so its own sources ask freely.
    _package(tmp_path, "footman")
    _module(
        tmp_path,
        "footman",
        "context.py",
        "import sys\nfrom livery.footman import task\n\n"
        "def attended() -> bool:\n    return sys.stdin.isatty()\n",
    )
    # A module that never imports the runner is the workspace's own
    # business, whatever it asks.
    _package(tmp_path, "app")
    _module(
        tmp_path,
        "app",
        "ui.py",
        "import sys\n\ndef colour() -> bool:\n    return sys.stdout.isatty()\n",
    )
    verify_workspace(tmp_path)
    # And the runner's own answer is what a task module should call.
    _package(tmp_path, "tool")
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "import livery.footman as footman\n\n"
        "def go() -> bool:\n    return footman.attended()\n",
    )
    verify_workspace(tmp_path)


def test_a_forge_third_party_import_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    src = tmp_path / "packages" / "forge" / "src"
    (src / "bad.py").write_text("import requests\n")
    with pytest.raises(ValueError, match="stdlib-only at import time"):
        verify_workspace(tmp_path)


def test_only_a_declared_plugin_module_may_import_what_the_runner_brings(
    tmp_path: Path,
) -> None:
    """The exemption follows the entry point, never a path spelled twice.

    A module the runner loads as a plugin has the runner present by
    construction, and the mounted layers with it. The package's own
    metadata is where that is declared, so a module at the same path
    with no entry point naming it is not exempt, and renaming the
    module in the metadata moves the exemption with it.
    """
    _forge_stub(tmp_path)
    inside = tmp_path / "packages" / "forge" / "src" / "livery" / "forge"
    plugin_dir = inside / "_dev"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("__init__.py").write_text("from livery import footman\n")
    # The refusals first. Nothing declares this module, so its import of
    # the runner is the ordinary violation however it is spelled.
    with pytest.raises(ValueError, match=r"imports 'livery\.footman'"):
        verify_workspace(tmp_path)
    # A module outside it is refused the same way when one is declared.
    _forge_stub_replace(tmp_path, plugin="livery.forge._dev")
    bad = inside / "_bad.py"
    bad.write_text("from livery import footman\n")
    with pytest.raises(ValueError, match=r"imports 'livery\.footman'"):
        verify_workspace(tmp_path)
    bad.unlink()
    # The exemption covers what the runner brings, not a third party.
    plugin_dir.joinpath("__init__.py").write_text(
        "from livery import footman\nimport requests\n"
    )
    with pytest.raises(ValueError, match="stdlib-only at import time"):
        verify_workspace(tmp_path)
    plugin_dir.joinpath("__init__.py").write_text(
        "from livery import footman\nfrom livery.toolroom import tools\n"
        "from livery.forge import Forge\n"
    )
    verify_workspace(tmp_path)
    # The metadata decides: name another module and this one is refused.
    _forge_stub_replace(tmp_path, plugin="livery.forge._other")
    with pytest.raises(ValueError, match=r"imports 'livery\.footman'"):
        verify_workspace(tmp_path)


def _forge_stub_replace(root: Path, *, plugin: str) -> None:
    """Rewrite forge's manifest to declare *plugin* as its task entry point."""
    entry = f'\n[project.entry-points."footman.tasks"]\nforge = "{plugin}:tasks"\n'
    (root / "packages" / "forge" / "pyproject.toml").write_text(
        f'[project]\nname = "livery-forge"\ndependencies = []\n{entry}'
    )


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
    """A CLI built from this layer runs where pytest is not installed.

    A brand's tool venv carries the layer and its runtime dependencies,
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
        "from livery.toolroom import tools\n\n"
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


def test_a_kind_that_reads_no_sources_contributes_no_reference() -> None:
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._packages import Neighbours, Package

    package = Package(
        directory=Path("/nowhere"),
        path="packages/native",
        name="native",
        kind="cpp-conan",
        depends=(),
    )
    around = Neighbours(owners={}, by_path={})
    assert _cpp_conan.module_roots(package) == ()
    assert _cpp_conan.referenced_siblings(package, around) == {}


def test_a_contract_naming_the_kind_under_type_is_refused_with_the_migration(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "packages" / "old"
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text('type = "python"\nname = "livery-old"\n')
    (directory / "pyproject.toml").write_text('[project]\nname = "livery-old"\n')
    with pytest.raises(
        ValueError,
        match=re.escape(
            "old: workshop.toml names the package kind under `type`;"
            " rename `type` to `kind` in workshop.toml"
        ),
    ):
        discover_packages(tmp_path)


# --- the fix mode: the edge the graph already reaches is written -----------


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


def test_a_test_reference_writes_the_test_edge_and_no_requirement(
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
        " floor 1.2.0 (workshop.toml)"
    ]
    pyproject = (tmp_path / "packages" / "high" / "pyproject.toml").read_text()
    assert "livery-low" not in pyproject
    verify_workspace(tmp_path)


def test_the_python_requirement_joins_a_list_opens_one_or_refuses_without_project(
    tmp_path: Path,
) -> None:
    from livery.workshop._backends import _python
    from livery.workshop._packages import Package

    def member(name: str, pyproject: str) -> Package:
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pyproject.toml").write_text(pyproject)
        return Package(
            directory=directory,
            path=f"packages/{name}",
            name=f"livery-{name}",
            kind="python",
            depends=(),
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


def test_the_conan_requirement_joins_the_tuple_or_refuses_without_one(
    tmp_path: Path,
) -> None:
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._packages import Package

    def recipe(name: str, body: str) -> Package:
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "conanfile.py").write_text(body)
        return Package(
            directory=directory,
            path=f"packages/{name}",
            name=f"acme-{name}",
            kind="cpp-conan",
            depends=(),
        )

    low = recipe("low", 'class Low:\n    name = "acme-low"\n')
    seeded = recipe(
        "seeded",
        'class Seeded:\n    name = "acme-seeded"\n    requires = ("fmt/[>=11.0]",)\n',
    )
    assert _cpp_conan.declare_requirement(seeded, low, "0.3.0") == ["conanfile.py"]
    assert (
        'requires = ("fmt/[>=11.0]", "acme-low/[>=0.3.0]")'
        in (seeded.directory / "conanfile.py").read_text()
    )
    assert _cpp_conan.declare_requirement(seeded, low, "0.4.0") == []
    empty = recipe(
        "empty", 'class Empty:\n    name = "acme-empty"\n    requires = ()\n'
    )
    _cpp_conan.declare_requirement(empty, low, "0.3.0")
    assert (
        'requires = ("acme-low/[>=0.3.0]",)'
        in (empty.directory / "conanfile.py").read_text()
    )
    bare = recipe("bare", 'class Bare:\n    name = "acme-bare"\n')
    with pytest.raises(
        _FAILURES, match=re.escape("declares no `requires = (...)` tuple")
    ):
        _cpp_conan.declare_requirement(bare, low, "0.3.0")
