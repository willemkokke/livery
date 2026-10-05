"""The docs extension's checks: links resolve, and every export has a docstring.

`lint.doclinks` reads the authored markdown of the workspace's `docs/`
and of every member's `docs/`, and refuses a link to a file that does
not exist or an anchor no heading of the target makes. `lint.docrefs`
resolves the cross-references in the changed sources' docstrings
([livery.extensions.docs._refs][]). `lint.docstrings`
imports each python member's top package in a child process and refuses
a name in its `__all__` whose object has no docstring, nor a string
literal under its assignment (a type alias carries no runtime
`__doc__`, and the API pages read the source).

The strict site build judges what both leave out: the pages generated
at build time and the nav.
"""

from __future__ import annotations

import ast
import itertools
import json
import re
import sys
from pathlib import Path

from livery.footman.api import fail
from livery.workshop._checks import (
    NONE,
    PACKAGES,
    CheckRecord,
    GateContext,
    check_for,
    selected_files,
)
from livery.workshop._influence import Changes, Inputs
from livery.workshop._packages import member_depth, package_directories

EXTENSION = "livery.extensions.docs"

#: The trees the site build writes under the workspace's `docs/`, which
#: are not authored pages.
SITE_TREES = ("packages", "releases", "tasks", "tools", "_extensions")

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^#{1,6} +(.*)$", re.M)


def _slug(heading: str) -> str:
    """The anchor python-markdown derives from a heading."""
    text = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    # Runs collapse to one hyphen, as python-markdown's toc does: a
    # heading like "without a run: --describe" anchors at
    # ...-run-describe, never ...-run---describe.
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _docs_trees(root: Path) -> list[Path]:
    trees = [root / "docs"]
    trees += [
        member / "docs"
        for member in package_directories(root)
        if (member / "docs").is_dir()
    ]
    return [tree for tree in trees if tree.is_dir()]


def _pages(root: Path) -> list[Path]:
    pages: list[Path] = []
    for tree in _docs_trees(root):
        pages += [
            page
            for page in sorted(tree.rglob("*.md"))
            if "_generated" not in page.relative_to(tree).parts
            and not (
                tree == root / "docs" and page.relative_to(tree).parts[0] in SITE_TREES
            )
        ]
    return pages


def link_problems(root: Path, pages: frozenset[str] | None = None) -> list[str]:
    """Every unresolvable link or anchor in the authored docs under *root*.

    A link into `packages/<name>/`, where the site mounts each member's
    docs, maps back to that package's `docs/`; a generated target and a
    package's changelog page are written at build time, and the strict
    build judges them. *pages* keeps the judgment to those
    root-relative pages; every link still resolves against the whole
    tree.
    """
    problems: list[str] = []
    for page in _pages(root):
        if pages is not None and page.relative_to(root).as_posix() not in pages:
            continue
        text = page.read_text("utf-8")
        for target in _LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, anchor = target.partition("#")
            if not path_part:
                resolved = page
            else:
                resolved = (page.parent / path_part).resolve()
                if path_part == "changelog.md" and "packages" in page.parts:
                    continue
                depth = member_depth(root, Path(path_part))
                if depth:
                    parts = Path(path_part).parts
                    resolved = (
                        root.joinpath(*parts[:depth]) / "docs" / Path(*parts[depth:])
                    ).resolve()
                elif "_generated" in path_part:
                    continue
            if not resolved.is_file():
                problems.append(
                    f"{page.relative_to(root).as_posix()}: {target} does not exist"
                )
                continue
            if anchor and resolved.suffix == ".md":
                slugs = {
                    _slug(heading)
                    for heading in _HEADING_RE.findall(resolved.read_text("utf-8"))
                }
                if anchor not in slugs:
                    problems.append(
                        f"{page.relative_to(root).as_posix()}: {target} anchors nothing"
                    )
    return problems


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


def _top_package(src: Path) -> str:
    """The dotted name of the shallowest package under *src*, or empty."""
    inits = sorted(src.rglob("__init__.py"), key=lambda path: len(path.parts))
    return ".".join(inits[0].parent.relative_to(src).parts) if inits else ""


def undocumented_exports(root: Path, members: list[Path]) -> list[str]:
    """The exports of *members* that carry no docstring, as dotted names.

    Each member's top package is imported in one child process of the
    interpreter running this one, which is the workspace's own, so a
    package's import-time effects stay out of the gate's process.
    """
    import livery.footman.api as footman

    targets: list[tuple[str, list[str]]] = []
    for member in members:
        src = member / "src"
        dotted = _top_package(src) if src.is_dir() else ""
        if dotted:
            targets.append((dotted, sorted(assignment_docstrings(src))))
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
            f"  lint.docstrings: {dotted} skips (not installed;"
            f" `{footman.prog()} sync` installs it)"
        )
    return [str(name) for name in found["missing"]]


def headings_removed(changes: Changes) -> bool:
    """Whether a changed markdown file lost a heading, an anchor a link may name.

    A link in an unchanged page may point at it, so the link check
    then judges every page.
    """
    for path in changes.paths:
        if not path.endswith(".md"):
            continue
        before = changes.text_before(path)
        if not before:
            continue
        now = changes.root / path
        after = now.read_text("utf-8") if now.is_file() else ""
        had = {_slug(heading) for heading in _HEADING_RE.findall(before)}
        has = {_slug(heading) for heading in _HEADING_RE.findall(after)}
        if had - has:
            return True
    return False


def names_removed(changes: Changes) -> bool:
    """Whether a changed python source lost a name a cross-reference may point at.

    A reference in an unchanged docstring may name it, so the
    reference check then reads every source.
    """
    from livery.extensions.docs._refs import defined_names

    for path in changes.paths:
        if not path.endswith(".py"):
            continue
        before = changes.text_before(path)
        if not before:
            continue
        now = changes.root / path
        after = now.read_text("utf-8") if now.is_file() else ""
        if defined_names(before) - defined_names(after):
            return True
    return False


#: The python sources a cross-reference lives in and points at.
SOURCES = ("packages/**/src/**/*.py",)


def docrefs_run(ctx: GateContext) -> None:
    from livery.extensions.docs._refs import reference_problems
    from livery.workshop._packages import package_directories

    sources = [
        directory / "src"
        for directory in package_directories(ctx.root)
        if (directory / "src").is_dir()
    ]
    files = selected_files(check_for("lint.docrefs"), ctx)
    if files is None:
        judged = sorted(path for src in sources for path in src.rglob("*.py"))
    else:
        judged = sorted(
            ctx.root / path for path in files if (ctx.root / path).is_file()
        )
    problems = reference_problems(ctx.root, judged, sources)
    if problems:
        fail("cross-references that name nothing:\n  " + "\n  ".join(problems))


def doclinks_run(ctx: GateContext) -> None:
    problems = link_problems(ctx.root, selected_files(check_for("lint.doclinks"), ctx))
    if problems:
        fail("links that resolve nothing:\n  " + "\n  ".join(problems))


def docstrings_run(ctx: GateContext) -> None:
    from livery.workshop._kinds import kind_chain, kind_names

    members = [
        package.directory
        for package in (ctx.subset if ctx.subset is not None else ctx.packages)
        # The workspace unit is a package of no registered kind.
        if package.kind in kind_names()
        and "python" in {record.name for record in kind_chain(package.kind)}
    ]
    missing = undocumented_exports(ctx.root, members)
    if missing:
        fail(
            "exports without a docstring: "
            + ", ".join(missing)
            + "; the published API page shows each one"
        )


CHECKS = (
    CheckRecord(
        "docrefs",
        "lint",
        docrefs_run,
        narrowing=NONE,
        extension=EXTENSION,
        inputs=Inputs(reads=SOURCES, widen=names_removed),
    ),
    CheckRecord(
        "doclinks",
        "lint",
        doclinks_run,
        narrowing=NONE,
        extension=EXTENSION,
        inputs=Inputs(
            reads=("docs/**/*.md", "packages/**/docs/**/*.md"),
            on_removal=True,
            widen=headings_removed,
        ),
    ),
    CheckRecord(
        "docstrings",
        "lint",
        docstrings_run,
        narrowing=PACKAGES,
        extension=EXTENSION,
    ),
)
"""The checks the docs extension registers as it mounts."""
