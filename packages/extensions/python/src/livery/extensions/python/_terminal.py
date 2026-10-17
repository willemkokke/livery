"""The runner-terminal rule: a module that imports the runner never asks the terminal.

Whether a person is there has one answer, [livery.footman.attended][],
which knows ``--no-input`` and ``--dry-run`` as well as the terminal. A
module that imports the runner has that answer in reach, and asking
the terminal itself (``sys.stdin.isatty()`` and its relatives) ignores
both flags in silence. The runner's own sources implement the answer
and are exempt, and a module that never imports the runner is the
workspace's own business.

The rule is a step of the layering phase,
``[phases.layering.runner-terminal]``, over the one parse of the python
sources: the root's ``tasks.py`` and each package's ``src``, the files
a narrowed run reaches or every one. It runs while the python
extension is mounted, which a package whose set holds python mounts.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from livery.footman import fail
from livery.workshop import selected_files

if TYPE_CHECKING:
    from livery.extensions.python._parse import ParsedModule
    from livery.workshop import PhaseContext

#: What a module that imports the runner must never call: a terminal
#: says nothing about ``--no-input`` or ``--dry-run``.
TERMINAL_CALLS = (
    "sys.stdin.isatty",
    "sys.stdout.isatty",
    "sys.stderr.isatty",
    "os.isatty",
)


def runner_terminal(ctx: PhaseContext) -> None:
    """The step: refuse each terminal question in a module that imports the runner.

    The sources the run reaches ([livery.workshop.selected_files][])
    are judged over the one parse; every source when the run knows
    nothing of what changed.
    """
    from livery.extensions.python._parse import parsed_modules

    found = problems(parsed_modules(ctx.root, ctx.packages, selected_files(ctx)))
    if found:
        fail("the sources break the runner-terminal rule:\n  " + "\n  ".join(found))


def problems(modules: tuple[ParsedModule, ...]) -> list[str]:
    """Each terminal question asked by a module of *modules* that imports the runner.

    The root's ``tasks.py`` and the sources under each package's
    ``src`` are judged, apart from the runner's own.
    """
    from livery.extensions.python._backend import RUNNER_DISTRIBUTION

    found: list[str] = []
    for module in modules:
        if module.area not in ("root", "src"):
            continue
        if module.package is not None and module.package.name == RUNNER_DISTRIBUTION:
            continue
        if not imports_the_runner(module.tree):
            continue
        found.extend(
            f"{module.relative} asks the terminal with {spelling}(): a module"
            " that imports the runner asks livery.footman.attended() instead,"
            " which knows --no-input and --dry-run as well"
            for spelling in terminal_calls(module.tree)
        )
    return found


def imports_the_runner(tree: ast.Module) -> bool:
    """Whether the module *tree* parses imports the runner, in any spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "livery.footman"
                or alias.name.startswith("livery.footman.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "livery.footman" or node.module.startswith(
                "livery.footman."
            ):
                return True
            if node.module == "livery" and any(
                alias.name == "footman" for alias in node.names
            ):
                return True
    return False


def terminal_calls(tree: ast.Module) -> list[str]:
    """Every call in the module *tree* parses that asks a terminal directly."""
    bare = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sys"
        for alias in node.names
        if alias.name in ("stdin", "stdout", "stderr")
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spelling = ast.unparse(node.func)
        named = spelling.endswith(".isatty") and spelling.split(".")[0] in bare
        if spelling in TERMINAL_CALLS or named:
            found.append(spelling)
    return found
