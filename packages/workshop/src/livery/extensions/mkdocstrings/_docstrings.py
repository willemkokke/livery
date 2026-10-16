"""Every name a python package exports has a docstring, which the reference publishes.

`lint.docstrings` imports each root of each python package, every
topmost package with an ``__init__.py`` as the API reference documents
them, in a child process and refuses a name in a root's ``__all__``
whose object has no docstring, nor a string literal under its
assignment: a type alias carries no runtime ``__doc__``, and the API
pages read the source. The packages are the ones a run reaches whose
set holds python, less those that turn the step off in their contract.
"""

from __future__ import annotations

import ast
import itertools
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from livery.extensions.mkdocstrings._pages import roots
from livery.footman import fail
from livery.workshop import scoped_packages

if TYPE_CHECKING:
    from livery.workshop import PhaseContext

#: The step, as the gate names it.
STEP = "lint.docstrings"


def assignment_docstrings(src: Path) -> set[str]:
    """The names under *src* documented by a string literal under their assignment."""
    documented: set[str] = set()
    for module in sorted(src.rglob("*.py")):
        body = ast.parse(module.read_text("utf-8")).body
        for first, second in itertools.pairwise(body):
            if not (
                isinstance(second, ast.Expr)
                and isinstance(second.value, ast.Constant)
                and isinstance(second.value.value, str)
                and second.value.value.strip()
            ):
                continue
            if isinstance(first, ast.AnnAssign) and isinstance(first.target, ast.Name):
                documented.add(first.target.id)
            elif isinstance(first, ast.Assign):
                documented.update(
                    target.id
                    for target in first.targets
                    if isinstance(target, ast.Name)
                )
    return documented


#: Run in the workspace's interpreter: imports each named package and
#: prints, as JSON, the exports with neither a docstring nor a
#: documented assignment, and the packages that are not installed.
_PROBE = """
import importlib, json, sys
missing = []
absent = []
for dotted, documented in json.loads(sys.stdin.read()):
    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError as error:
        if error.name is None or not dotted.startswith(error.name):
            raise
        absent.append(dotted)
        continue
    for name in getattr(module, "__all__", ()):
        if name in documented:
            continue
        if not (getattr(getattr(module, name), "__doc__", None) or "").strip():
            missing.append(f"{dotted}.{name}")
print(json.dumps({"missing": missing, "absent": absent}))
"""


def undocumented_exports(root: Path, members: list[Path]) -> list[str]:
    """The exports of *members* that carry no docstring, as dotted names.

    Each root of each member (`roots`) is imported in one child process
    of the interpreter running this one, which is the workspace's own,
    so a package's import-time effects stay out of the gate's process.
    """
    import livery.footman as footman

    targets: list[tuple[str, list[str]]] = []
    for member in members:
        src = member / "src"
        if not src.is_dir():
            continue
        documented = sorted(assignment_docstrings(src))
        targets += [
            (".".join(root.relative_to(src).parts), documented)
            for root in roots(src)
            if root != src
        ]
    if not targets:
        return []
    result = footman.run(
        [sys.executable, "-c", _PROBE],
        cwd=root,
        input=json.dumps(targets),
        nofail=True,
    )
    if result.code != 0:
        fail(
            f"the docstring probe exited {result.code} importing the members:\n"
            f"{result.stderr.rstrip()}"
        )
    found = json.loads(result.stdout)
    for dotted in found["absent"]:
        # Not installed is the environment's fault, not the docs': the
        # sync installs every member.
        print(
            f"  {STEP}: {dotted} skips (not installed;"
            f" `{footman.prog()} sync` installs it)"
        )
    return [str(name) for name in found["missing"]]


def docstrings_run(ctx: PhaseContext) -> None:
    """Refuse an export without a docstring: the lint phase's step.

    Judged over the python packages the run reaches
    (`livery.workshop.scoped_packages`), each skip said.
    """
    members = [package.directory for package in scoped_packages(ctx, STEP)]
    missing = undocumented_exports(ctx.root, members)
    if missing:
        fail(
            "exports without a docstring: "
            + ", ".join(missing)
            + "; the published API page shows each one"
        )
