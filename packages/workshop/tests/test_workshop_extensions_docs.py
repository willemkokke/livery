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
    assert found["livery.workshop"] == "livery.workshop._tasks"
    from livery.footman import registry

    with registry.capture():
        from livery.extensions.docs import _tasks

        assert _tasks.docs_group.name == "docs"
        names = set(_tasks.docs_group.tasks)
    assert {"build", "publish", "serve", "coverage-pages", "task-reference"} <= names


def test_the_base_task_module_does_not_import_the_site() -> None:
    source = (ROOT / "packages/workshop/src/livery/workshop/_tasks.py").read_text()
    assert "_docs" not in source and "extensions.docs" not in source


@pytest.mark.parametrize(
    "name", ["docs_table", "site_reads", "publish_seam", "materialise_module_docs"]
)
def test_the_base_seam_carries_what_the_base_reads(name: str) -> None:
    from livery.workshop import _docs_contract

    assert callable(getattr(_docs_contract, name))
