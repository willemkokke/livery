"""The base without the docs extension: it imports no extension and reads no docs table.

The refusals first: a base module importing an extension, and a read of
a ``[docs]`` table. Then this workspace's base, and the task links the
base makes from the workspace's own template.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from livery.footman import Invocation
from livery.workshop._packages import (
    ReferencedName,
    SourceReferences,
    extension_imports_in_the_base,
)
from livery.workshop._tasks import link_task_names

ROOT = Path(__file__).resolve().parents[3]


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


def test_a_base_module_importing_an_extension_refuses_naming_it() -> None:
    # No base module is excepted: one that runs a package step's code
    # by importing the extension refuses as any other does, for each
    # extension it imports.
    modules = (
        _module(
            "livery.workshop._steps",
            (
                "livery.extensions.cmake._build",
                "livery.extensions.conan._package",
                "livery.extensions.cpp",
            ),
        ),
    )
    assert extension_imports_in_the_base(modules) == [
        f"livery/workshop/_steps.py: the base imports the extension {name}; the"
        " base reaches an extension through a registry it fills at mount, never"
        " by import"
        for name in (
            "livery.extensions.cmake._build",
            "livery.extensions.conan._package",
            "livery.extensions.cpp",
        )
    ]


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


def test_the_base_task_module_does_not_import_the_site() -> None:
    source = (ROOT / "packages/workshop/src/livery/workshop/_tasks.py").read_text()
    assert "_docs" not in source and "extensions.docs" not in source


def test_the_workspace_template_links_without_the_docs_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nextensions = []\n'
        'docs-url = "https://acme.example/reference/#{slug}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    inv = Invocation()
    link_task_names(inv)
    assert inv.docs_url == "https://acme.example/reference/#{slug}"
