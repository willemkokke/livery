"""Every git call the workshop makes goes through livery.git, never the handle.

The library carries the guards a raw call skips, the one identity, the
shared ssh connection, the environment a task marks for itself, so a
call of the tool store's git handle from the workshop's source is a
call that lost them. The scan refuses one with no allowance: a job no
function names takes `git.run` or `git.call`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import livery.workshop

# The workshop distribution's own trees: the base and the extensions it
# ships. Its grandparent is the whole livery namespace, which in an
# installed environment holds the git library beside it, and that one
# calls the handle by design.
_LIVERY = Path(livery.workshop.__file__).resolve().parents[1]
SRC = (_LIVERY / "workshop", _LIVERY / "extensions")


def handle_calls(*roots: Path) -> list[str]:
    """Every attribute access of a git handle on the tools module under *roots*."""
    found: list[str] = []
    for src in roots:
        found.extend(_handle_calls(src))
    return found


def _handle_calls(src: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        aliases = {"tools", "toolroom"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "livery.toolroom.tools":
                        aliases.add(alias.asname or "tools")
            elif isinstance(node, ast.ImportFrom) and node.module == "livery.toolroom":
                for alias in node.names:
                    if alias.name == "tools":
                        aliases.add(alias.asname or "tools")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "git"
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            ):
                found.append(f"{path.relative_to(src)}:{node.lineno}")
    return found


def test_no_workshop_module_calls_the_git_handle() -> None:
    assert handle_calls(*SRC) == []


def test_the_scan_sees_a_call_of_the_handle(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "import livery.toolroom.tools as tools\n"
        "def f(root):\n"
        "    return tools.git.opts(cwd=root)('status')\n"
    )
    (tmp_path / "n.py").write_text(
        "from livery.toolroom import tools as t\n"
        "def g(root):\n"
        "    return t.git_lfs.opts(cwd=root)('status'), t.git(root)\n"
    )
    assert handle_calls(tmp_path) == ["m.py:3", "n.py:3"]
