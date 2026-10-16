"""The docs extension's own distribution: its namespace, entry points and plugin.

Then the kit on the extension, what a workspace without it installs,
and the words its sources may use.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from livery.workshop.testing import CLAUSES, Clause

ROOT = Path(__file__).resolve().parents[4]


# A second tree's extension joins the namespace, and the docs extension
# arrives through its own distribution's entry points.


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
    assert {"build", "publish", "serve"} <= names
    # Steps of the docs phase draw the task reference and the coverage
    # pages; no verb does.
    assert not {"coverage-pages", "task-reference"} & names


def test_the_entry_module_serves_no_nav_block_helper() -> None:
    # A generator writes its nav block as data, the file the extension
    # reads; the extension serves no code for it.
    import livery.extensions.docs as docs_module

    for name in ("write_nav_block", "nav_block_file", "GENERATED_DIR"):
        with pytest.raises(
            AttributeError,
            match=f"module 'livery.extensions.docs' has no attribute '{name}'",
        ):
            getattr(docs_module, name)


def test_its_own_distribution_ships_docs_and_the_workshop_wheel_none() -> None:
    from importlib.metadata import distribution

    from livery.workshop._extensions import shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in distribution("livery-extensions-docs").entry_points
    }
    assert ours == {
        ("workshop.extensions", "docs", "livery.extensions.docs"),
        ("footman.tasks", "livery.extensions.docs", "livery.extensions.docs._tasks"),
    }
    theirs = distribution("livery-workshop").entry_points
    assert [
        entry.name
        for entry in theirs
        if entry.value.startswith("livery.extensions.docs")
    ] == []
    shipped = shipping_distribution("livery.extensions.docs")
    assert shipped is not None and shipped.name == "livery-extensions-docs"


def test_the_plugin_loaded_alone_registers_the_docs_group_alone() -> None:
    # The task reference renders each provider's tree with nothing else
    # mounted. A base module that registers the base's verbs when it is
    # imported is read inside the function that needs it, so the
    # plugin's tree holds the docs group and no group of the base's.
    probe = (
        "import json\n"
        "import livery.footman as footman\n"
        "import livery.extensions.docs._tasks\n"
        "tree = footman.task_tree()\n"
        "print(json.dumps([sorted(tree['groups']), sorted(tree['tasks'])]))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [["docs"], []]


def _workspace(tmp_path: Path, listed: str) -> Path:
    """A workspace listing *listed*, with the python member core."""
    root = tmp_path / "ws"
    member = root / "packages" / "core"
    (member / "src" / "acme" / "core").mkdir(parents=True)
    (member / "src" / "acme" / "core" / "__init__.py").write_text("")
    (member / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-core"\n'
    )
    (member / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    (root / "workshop.toml").write_text(
        f'[workspace]\nname = "acme"\nextensions = {listed}\n'
    )
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    return root


def _mount(root: Path) -> None:
    """Mount what *root* lists, so its phases' steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


def test_a_workspace_without_the_docs_extension_installs_none(tmp_path: Path) -> None:
    # Unlisted, neither the extension's distribution nor the site's
    # builder reaches the venv: no line of any group the project file
    # composes names either, and the workshop wheel requires neither.
    from importlib.metadata import requires

    from extensions_docs_workspace import composed_groups

    root = _workspace(tmp_path, "[]")
    groups = composed_groups(root)
    lines = [line for group in groups.values() for line in group]
    assert groups["docs"] == []
    assert [
        line for line in lines if "livery-extensions-docs" in line or "zensical" in line
    ] == []
    required = requires("livery-workshop") or []
    assert [
        line for line in required if "extensions-docs" in line or "zensical" in line
    ] == []
    # Listed, the dev group installs the extension's distribution, and
    # the docs group the builder.
    root = _workspace(tmp_path / "listed", '["docs"]')
    groups = composed_groups(root)
    assert "livery-extensions-docs" in groups["dev"]
    assert groups["docs"] == ["zensical>=0.0.50"]


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_docs_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    _mount(_workspace(tmp_path, '["docs"]'))
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "docs"
    )
    assert {record.name for record in steps} == {"docs.generators", "lint.doclinks"}
    subject = Subject("livery.extensions.docs", sets=(("python",),), steps=steps)
    assert clause.judge(subject) == []


def _language_words() -> set[str]:
    """The words of a language the docs extension never names outside prose.

    The language the API reference renders and the tools that read it,
    and the name of every installed extension that composes something in
    a package.
    """
    from livery.workshop._extensions import composes_per_package, installed_extensions

    words = {"python", "griffe", "mkdocstrings"}
    words.update(name for name in installed_extensions() if composes_per_package(name))
    return words


def _words(text: str) -> list[str]:
    """The words of *text*, lower case: a name split at its underscores and dots."""
    return [word for word in re.split(r"[^a-z0-9]+", text.lower()) if word]


def _code_words(source: str) -> list[tuple[int, str]]:
    """Each word of *source*'s code with its line: its names and strings.

    Docstrings are prose and left out, and so are comments, which
    never reach the syntax tree.
    """
    tree = ast.parse(source)
    prose: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                prose.add(id(first.value))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in prose:
                texts.append(node.value)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.arg):
            texts.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)
        elif isinstance(node, ast.alias):
            texts += [node.name, node.asname or ""]
        elif isinstance(node, ast.ImportFrom):
            texts.append(node.module or "")
        line = int(getattr(node, "lineno", 0))
        found += [(line, word) for text in texts for word in _words(text)]
    return found


def _declared_words(data: object, path: str = "") -> list[tuple[str, str]]:
    """Each word of a declaration's keys and values, by key path; prose left out.

    A key's ``doc`` and a job's ``note`` are prose, and so are the
    file's comments, which the parse drops.
    """
    found: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in cast("dict[str, object]", data).items():
            where = f"{path}.{key}" if path else key
            found += [(where, word) for word in _words(key)]
            if key not in ("doc", "note"):
                found += _declared_words(value, where)
    elif isinstance(data, list):
        for item in cast("list[object]", data):
            found += _declared_words(item, path)
    elif isinstance(data, str):
        found += [(path, word) for word in _words(data)]
    return found


def test_a_language_word_in_a_source_s_code_is_found_and_its_prose_is_not() -> None:
    source = (
        '"""A module docstring naming python is prose."""\n'
        "# A comment naming griffe is prose.\n"
        "HANDLER = 'python'\n"
        "from livery.extensions.mkdocstrings import _pages\n"
    )
    languages = {"python", "griffe", "mkdocstrings"}
    found = sorted(
        (line, word) for line, word in _code_words(source) if word in languages
    )
    assert found == [(3, "python"), (4, "mkdocstrings")]
    declared = {
        "phases": {"lint": {"refs": {"doc": "the python names", "main": "griffe"}}}
    }
    assert _declared_words(declared)[-1] == ("phases.lint.refs.main", "griffe")
    assert ("phases.lint.refs.doc", "python") not in _declared_words(declared)


#: The one declared key that names a language: the docs extension's
#: answer to the query whose lines the workspace's environment installs.
#: The site's builder is a python program the docs verbs run from that
#: environment, so its line rides the environment's docs group; the key
#: says where the builder installs, not what the site documents.
_BUILDER_GROUP = "queries.python.docs-group"


def test_the_docs_extension_names_no_language() -> None:
    """Docs' sources and declaration name no language outside their prose.

    No python, griffe or mkdocstrings word and no name of an extension
    a package lists, in a name, a string or a declared key or value:
    what renders a language's API reference is an extension of its own,
    which answers the docs extension's queries and adds its steps. The
    key of the docs extension's answer to the query that installs the
    site's builder names where the builder installs, and is left out.
    """
    import tomllib

    docs = ROOT / "packages/extensions/docs/src/livery/extensions/docs"
    words = _language_words()
    assert {"python", "cpp", "conan", "nanobind"} <= words
    found: list[str] = []
    for path in sorted(docs.glob("*.py")):
        source = path.read_text("utf-8")
        found += [
            f"{path.name}:{line} {word}"
            for line, word in _code_words(source)
            if word in words
        ]
    declaration = tomllib.loads((docs / "extension.toml").read_text("utf-8"))
    declared = _declared_words(declaration)
    # The key left out is one the declaration holds, so it never outlives
    # the answer.
    assert _BUILDER_GROUP in {where for where, _word in declared}
    found += [
        f"extension.toml {where} {word}"
        for where, word in declared
        if word in words and where != _BUILDER_GROUP
    ]
    assert found == []
