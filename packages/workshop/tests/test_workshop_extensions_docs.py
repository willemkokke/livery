"""The docs extension inside the wheel: its namespace, entry point and seam."""

from __future__ import annotations

import ast
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

from livery.workshop._ast_rules import ParsedModule
from livery.workshop._packages import extension_imports_in_the_base

ROOT = Path(__file__).resolve().parents[3]


# The refusal first: a base module importing an extension.


def _module(dotted: str, imports: tuple[str, ...]) -> ParsedModule:
    return ParsedModule(
        package=None,
        path=ROOT / "x.py",
        relative=dotted.replace(".", "/") + ".py",
        area="src",
        dotted=dotted,
        tree=ast.parse(""),
        imports=imports,
    )


def test_a_base_module_importing_a_extension_refuses_naming_both() -> None:
    modules = (
        _module("livery.workshop._quality", ("livery.extensions.docs._site",)),
        _module("livery.extensions.docs._site", ("livery.workshop._packages",)),
        _module("livery.workshop._packages", ("livery.footman",)),
    )
    problems = extension_imports_in_the_base(modules)
    assert len(problems) == 1
    assert "livery/workshop/_quality.py: the base imports the extension" in problems[0]
    assert "livery.extensions.docs._site" in problems[0]


def test_an_allowed_base_module_importing_another_extension_refuses() -> None:
    # The allowance names one extension per module: _kinds may import the
    # python extension and nothing else under the namespace.
    modules = (
        _module(
            "livery.workshop._kinds",
            ("livery.extensions.python._backend", "livery.extensions.docs._site"),
        ),
    )
    problems = extension_imports_in_the_base(modules)
    assert len(problems) == 1
    assert "livery.extensions.docs._site" in problems[0]


def test_the_base_extension_import_allowance_is_exact() -> None:
    """Each allowed module still imports its extension; a stale entry refuses.

    The list says what is left of the exception, so an entry whose import
    went comes out of it.
    """
    from livery.workshop._packages import BASE_EXTENSION_IMPORTS

    src = ROOT / "packages" / "workshop" / "src"
    stale: list[str] = []
    for dotted, extension in BASE_EXTENSION_IMPORTS:
        tree = ast.parse((src / f"{dotted.replace('.', '/')}.py").read_text("utf-8"))
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        if not any(
            name == extension or name.startswith(extension + ".") for name in imported
        ):
            stale.append(dotted)
    assert stale == []


# Then the shape: a second tree's extension joins the namespace, and the
# docs extension arrives through its own entry point.


def test_a_extension_from_another_tree_joins_the_namespace(tmp_path: Path) -> None:
    """A tree with livery/extensions/<name>/ and no __init__ above it imports."""
    portion = tmp_path / "livery" / "extensions" / "acme"
    portion.mkdir(parents=True)
    (portion / "__init__.py").write_text('NAME = "acme"\n')
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import livery.extensions.acme as a,"
            " livery.extensions.docs as d; print(a.NAME, d.__name__)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "acme livery.extensions.docs"


def test_the_docs_extension_has_its_own_entry_point_and_the_docs_group() -> None:
    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="footman.tasks")
        if entry.name in ("livery.workshop", "livery.extensions.docs")
    }
    assert found["livery.extensions.docs"] == "livery.extensions.docs._tasks"
    assert found["livery.workshop"] == "livery.workshop._mount"
    from livery.footman import _registry as registry

    with registry.capture():
        from livery.extensions.docs import _tasks

        assert _tasks.docs_group.name == "docs"
        names = set(_tasks.docs_group.tasks)
    assert {"build", "publish", "serve", "coverage-pages", "task-reference"} <= names


def test_the_base_task_module_does_not_import_the_site() -> None:
    source = (ROOT / "packages/workshop/src/livery/workshop/_tasks.py").read_text()
    assert "_docs" not in source and "extensions.docs" not in source


@pytest.mark.parametrize(
    "module", ["_ci_generate", "_workflow_tasks", "_provenance", "_templates"]
)
def test_the_base_modules_read_no_docs_table(module: str) -> None:
    # What a docs job installs, where it deploys and what it reads are
    # declared on the job, and task names link through the extension's
    # own hook, so none of these modules knows the [docs] table.
    path = ROOT / f"packages/workshop/src/livery/workshop/{module}.py"
    assert "_docs_contract" not in path.read_text()


@pytest.mark.parametrize("name", ["declines_api", "materialise_module_docs"])
def test_the_base_seam_carries_what_the_base_reads(name: str) -> None:
    from livery.workshop import _docs_contract

    assert callable(getattr(_docs_contract, name))


def test_the_root_docs_table_is_the_extensions_to_read() -> None:
    from livery.extensions.docs import _contract
    from livery.workshop import _docs_contract

    assert callable(_contract.docs_table)
    assert not hasattr(_docs_contract, "docs_table")


#: The workshop modules whose names the docs extension reads through
#: the workshop's public ones instead.
_REPLACED = frozenset(
    {
        "_checks",
        "_contract",
        "_forge_lane",
        "_influence",
        "_prose",
        "_provenance",
        "_registries",
        "_release_notes",
        "_slots",
        "_state",
    }
)


def test_the_docs_extension_reads_the_workspace_through_public_names() -> None:
    # The reach scan reads the workshop's wheel as one distribution, so
    # it cannot see the extension reading the base; this holds every read
    # a public name replaces until the extension ships apart.
    found: list[str] = []
    docs = ROOT / "packages/workshop/src/livery/extensions/docs"
    for path in sorted(docs.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module == "livery.workshop":
                found += [
                    f"{path.name}:{node.lineno} livery.workshop.{alias.name}"
                    for alias in node.names
                    if alias.name in _REPLACED
                ]
            elif (
                node.module.startswith("livery.workshop.")
                and node.module.split(".")[2] in _REPLACED
            ):
                found.append(f"{path.name}:{node.lineno} {node.module}")
    assert found == []
