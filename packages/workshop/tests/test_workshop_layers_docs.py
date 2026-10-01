"""The docs layer inside the wheel: the namespace, the entry point, the base's seam."""

from __future__ import annotations

import ast
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

from livery.workshop._ast_rules import ParsedModule
from livery.workshop._packages import layer_imports_in_the_base, layers_namespace_inits

ROOT = Path(__file__).resolve().parents[3]


# The refusals first: a base module importing a layer, an __init__ in the namespace.


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


def test_a_base_module_importing_a_layer_refuses_naming_both() -> None:
    modules = (
        _module("livery.workshop._quality", ("livery.workshop.layers.docs._site",)),
        _module("livery.workshop.layers.docs._site", ("livery.workshop._packages",)),
        _module("livery.workshop._packages", ("livery.footman",)),
    )
    problems = layer_imports_in_the_base(modules)
    assert len(problems) == 1
    assert "livery/workshop/_quality.py: the base imports the layer" in problems[0]
    assert "livery.workshop.layers.docs._site" in problems[0]


BARE = (
    '"""The layers."""\n\nfrom pkgutil import extend_path\n\n'
    "__path__ = extend_path(__path__, __name__)\n"
)


def test_a_layers_init_carrying_more_than_the_path_extension_refuses(
    tmp_path: Path,
) -> None:
    assert layers_namespace_inits(tmp_path) == []
    init = tmp_path / "packages/workshop/src/livery/workshop/layers/__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(BARE + "\nNAME = 1\n")
    problems = layers_namespace_inits(tmp_path)
    assert len(problems) == 1 and "carries the path extension alone" in problems[0]
    init.write_text("")
    assert len(layers_namespace_inits(tmp_path)) == 1
    init.write_text(BARE)
    assert layers_namespace_inits(tmp_path) == []


def test_this_repository_keeps_the_base_free_of_layer_imports() -> None:
    assert layers_namespace_inits(ROOT) == []
    init = ROOT / "packages/workshop/src/livery/workshop/layers/__init__.py"
    assert "extend_path(__path__, __name__)" in init.read_text()


# Then the shape: a second tree's layer joins the namespace, and the
# docs layer arrives through its own entry point.


def test_a_layer_from_another_tree_joins_the_namespace(tmp_path: Path) -> None:
    """A tree with livery/workshop/layers/<name>/ and no __init__ above it imports."""
    portion = tmp_path / "livery" / "workshop" / "layers" / "acme"
    portion.mkdir(parents=True)
    (portion / "__init__.py").write_text('NAME = "acme"\n')
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import livery.workshop, livery.workshop.layers.acme as a,"
            " livery.workshop.layers.docs as d; print(a.NAME, d.__name__)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "acme livery.workshop.layers.docs"


def test_the_docs_layer_has_its_own_entry_point_and_the_docs_group() -> None:
    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="footman.tasks")
        if entry.name.startswith("livery.workshop")
    }
    assert found["livery.workshop.layers.docs"] == "livery.workshop.layers.docs._tasks"
    assert found["livery.workshop"] == "livery.workshop._tasks"
    from livery.footman import registry

    with registry.capture():
        from livery.workshop.layers.docs import _tasks

        assert _tasks.docs_group.name == "docs"
        names = set(_tasks.docs_group.tasks)
    assert {"build", "publish", "serve", "coverage-pages", "task-reference"} <= names


def test_the_base_task_module_does_not_import_the_site() -> None:
    source = (ROOT / "packages/workshop/src/livery/workshop/_tasks.py").read_text()
    assert "_docs" not in source and "layers.docs" not in source


@pytest.mark.parametrize(
    "name", ["docs_table", "site_reads", "publish_seam", "materialise_module_docs"]
)
def test_the_base_seam_carries_what_the_base_reads(name: str) -> None:
    from livery.workshop import _docs_contract

    assert callable(getattr(_docs_contract, name))
