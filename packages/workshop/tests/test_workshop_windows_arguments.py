"""Arguments that reach a tool through a Windows `.cmd` launcher intact.

The tool store runs an archive's tool on Windows through a `.cmd`
launcher, and cmd.exe takes `^` as its escape character: a git revision
spelled with a caret reaches git without it. Every revision in the
workshop's sources is spelled without one: `~0` peels to the commit,
`<rev>:` names its tree, and `~1` is its first parent.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

#: A caret after a revision's name: `x^{commit}`, `x^`, `x^2`, `x^~`.
_CARET = re.compile(r"(?<=[\w}])\^(?=\{|\d|~|$)")


def _literals(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Each string the code spells, by line: an f-string's fields as a placeholder.

    A docstring is prose, not an argument, and is left out.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield (
                node.lineno,
                "".join(
                    str(part.value) if isinstance(part, ast.Constant) else "x"
                    for part in node.values
                ),
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


# The pattern first: it finds each spelling the sources once had, and
# leaves the carets that are no revision alone.


@pytest.mark.parametrize(
    "text", ["origin/main^{commit}", "x^{tree}", "point..x^", "x^2"]
)
def test_a_caret_after_a_revision_is_found(text: str) -> None:
    assert _CARET.search(text)


@pytest.mark.parametrize(
    "text", ["origin/main~0", "x:", "point..x~1", "^{}", r"^\d+$", "[^/]+"]
)
def test_a_spelling_without_one_or_a_pattern_is_not(text: str) -> None:
    assert not _CARET.search(text)


def test_no_git_revision_in_the_sources_carries_a_caret() -> None:
    found = [
        f"{path.relative_to(SRC)}:{line}: {text!r}"
        for path in sorted(SRC.rglob("*.py"))
        for line, text in _literals(ast.parse(path.read_text("utf-8")))
        if _CARET.search(text)
    ]
    assert found == []
