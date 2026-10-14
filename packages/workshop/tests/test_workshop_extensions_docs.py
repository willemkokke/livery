"""The docs extension inside the wheel: its namespace, entry point and seam."""

from __future__ import annotations

import ast
import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from livery.workshop._packages import (
    ReferencedName,
    SourceReferences,
    extension_imports_in_the_base,
)

ROOT = Path(__file__).resolve().parents[3]


# The refusal first: a base module importing an extension.


def _module(dotted: str, imports: tuple[str, ...]) -> SourceReferences:
    return SourceReferences(
        path=dotted.replace(".", "/") + ".py",
        module=dotted,
        references=tuple(ReferencedName(name) for name in imports),
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


def test_a_base_module_s_optional_import_is_left_alone_and_any_other_refuses() -> None:
    # An import the module runs without is a declaration of its own; one
    # made lazily or for a type checker still ties the base to the
    # extension.
    source = SourceReferences(
        path="livery/workshop/_quality.py",
        module="livery.workshop._quality",
        references=(
            ReferencedName("livery.extensions.docs._site", optional=True),
            ReferencedName("livery.extensions.docs._refs", lazy=True),
            ReferencedName("livery.extensions.docs._llms", type_checking=True),
        ),
    )
    problems = extension_imports_in_the_base((source,))
    assert [problem.split(";")[0] for problem in problems] == [
        "livery/workshop/_quality.py: the base imports the extension"
        " livery.extensions.docs._refs",
        "livery/workshop/_quality.py: the base imports the extension"
        " livery.extensions.docs._llms",
    ]


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
    assert {"build", "publish", "serve"} <= names
    # Steps of the docs phase draw the task reference and the coverage
    # pages; no verb does.
    assert not {"coverage-pages", "task-reference"} & names


def test_the_base_task_module_does_not_import_the_site() -> None:
    source = (ROOT / "packages/workshop/src/livery/workshop/_tasks.py").read_text()
    assert "_docs" not in source and "extensions.docs" not in source


def _docs_table_reads(source: str) -> list[int]:
    """The lines of *source* that read a ``[docs]`` table, by subscript or ``get``."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript):
            key = node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            key = node.args[0]
        else:
            continue
        if isinstance(key, ast.Constant) and key.value == "docs":
            found.append(node.lineno)
    return sorted(found)


def test_a_docs_table_read_is_found_by_its_subscript_and_its_get() -> None:
    source = (
        'table = contract.get("docs") or {}\n'
        'site = contract["docs"]\n'
        'kinds = ("feat", "docs")\n'
        'listed = "docs" in names\n'
    )
    assert _docs_table_reads(source) == [1, 2]


def test_the_base_modules_read_no_docs_table() -> None:
    """No base module reads a ``[docs]`` table, the root's or a package's.

    The table and its keys are the docs extension's. What a docs job
    installs, where it deploys and what it reads are declared on the
    job, task names link through the extension's own hook, the
    extension that writes the API reference reads ``[docs] api``, and
    the docs a python wheel embeds are the python extension's.
    """
    base = ROOT / "packages/workshop/src/livery/workshop"
    found = [
        f"{path.relative_to(base).as_posix()}:{line}"
        for path in sorted(base.rglob("*.py"))
        for line in _docs_table_reads(path.read_text("utf-8"))
    ]
    assert found == []


#: The workshop modules whose names the docs extension reads through
#: the workshop's public ones instead.
_REPLACED = frozenset(
    {
        "_steps",
        "_contract",
        "_forge_lane",
        "_influence",
        "_prose",
        "_provenance",
        "_registries",
        "_release_notes",
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


def _language_words() -> set[str]:
    """The words of a language the docs extension never names outside prose.

    The language the API reference renders and the tools that read it,
    and each part of every concrete kind's name.
    """
    from livery.workshop._kinds import all_kinds

    words = {"python", "griffe", "mkdocstrings"}
    for record in all_kinds():
        if not record.abstract:
            words.update(record.name.split("-"))
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

    No python, griffe or mkdocstrings word and no part of a kind's name
    in a name, a string or a declared key or value, and no import of
    the kind registry: what renders a language's API reference is an
    extension of its own, which answers the docs extension's queries
    and adds its steps. The key of the docs extension's answer to the
    query that installs the site's builder names where the builder
    installs, and is left out.
    """
    import tomllib

    docs = ROOT / "packages/workshop/src/livery/extensions/docs"
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
        found += [
            f"{path.name}:{node.lineno} imports {node.module}"
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("livery.workshop._kinds")
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
