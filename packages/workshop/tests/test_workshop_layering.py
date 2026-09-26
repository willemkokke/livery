"""The layering lint refuses each violation by name, on synthetic trees."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import discover_packages, verify_workspace


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
        f'type = "python"\nname = "livery-{name}"\n{contract_extra}'
    )
    deps = ", ".join(f'"{d}"' for d in dependencies)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = [{deps}]\n'
    )


def _forge_stub(root: Path) -> None:
    # The stdlib rule walks packages/forge/src; give the tree a clean one.
    src = root / "packages" / "forge" / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("import json\n")
    (root / "packages" / "forge" / "workshop.toml").write_text(
        'type = "python"\nname = "livery-forge"\n'
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


def test_the_dev_plugin_may_import_its_two_tools_and_nothing_else(
    tmp_path: Path,
) -> None:
    _forge_stub(tmp_path)
    plugin_dir = tmp_path / "packages" / "forge" / "src" / "livery" / "forge" / "_dev"
    plugin_dir.mkdir(parents=True)
    # The refusals first: a forge module outside the plugin importing
    # the runner, and the plugin importing a third-party package.
    bad = tmp_path / "packages" / "forge" / "src" / "livery" / "forge" / "_bad.py"
    bad.write_text("from livery import footman\n")
    with pytest.raises(ValueError, match=r"imports 'livery\.footman'"):
        verify_workspace(tmp_path)
    bad.unlink()
    (plugin_dir / "__init__.py").write_text(
        "from livery import footman\nimport requests\n"
    )
    with pytest.raises(ValueError, match="stdlib-only at import time"):
        verify_workspace(tmp_path)
    (plugin_dir / "__init__.py").write_text(
        "from livery import footman\nfrom livery.toolroom import tools\n"
        "from livery.forge import Forge\n"
    )
    verify_workspace(tmp_path)


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
