"""The docs gate: links resolve and every export carries a docstring.

Nav orphans need no test here: the nav is emitted from the tree and
the template drift gate refuses a stale render, so a page missing
from the nav is drift before it is anything else. What remains is
what the emitter cannot know: whether the prose's links point at
real files and anchors, and whether the public surface says what it
is.
"""

from __future__ import annotations

import ast
import importlib
import itertools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^#{1,6} +(.*)$", re.M)


def _slug(heading: str) -> str:
    """The anchor python-markdown derives from a heading."""
    text = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return re.sub(r"[ ]", "-", text)


def _docs_trees(root: Path) -> list[Path]:
    trees = [root / "docs"]
    packages = root / "packages"
    if packages.is_dir():
        trees += sorted(
            member / "docs"
            for member in packages.iterdir()
            if (member / "docs").is_dir()
        )
    return [tree for tree in trees if tree.is_dir()]


def _pages(root: Path) -> list[Path]:
    pages: list[Path] = []
    for tree in _docs_trees(root):
        pages += [page for page in tree.rglob("*.md") if "_generated" not in page.parts]
    return pages


def _link_problems(root: Path) -> list[str]:
    """Every unresolvable link or anchor in the authored docs.

    Links into ``_generated/`` map back to their package sources;
    the strict site build owns everything else generated.
    """
    problems: list[str] = []
    for page in _pages(root):
        text = page.read_text("utf-8")
        for target in _LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, anchor = target.partition("#")
            if not path_part:
                resolved = page
            else:
                resolved = (page.parent / path_part).resolve()
                mounted = re.fullmatch(r"_generated/packages/([^/]+)/(.+)", path_part)
                if mounted:
                    resolved = (
                        root / "packages" / mounted.group(1) / "docs" / mounted.group(2)
                    ).resolve()
                elif "_generated" in path_part:
                    continue  # built at docs time; the strict build owns it
            if not resolved.is_file():
                problems.append(f"{page.relative_to(root)}: {target} does not exist")
                continue
            if anchor and resolved.suffix == ".md":
                slugs = {
                    _slug(heading)
                    for heading in _HEADING_RE.findall(resolved.read_text("utf-8"))
                }
                if anchor not in slugs:
                    problems.append(
                        f"{page.relative_to(root)}: {target} anchors nothing"
                    )
    return problems


def test_a_broken_link_and_a_dead_anchor_are_named(tmp_path: Path) -> None:
    # The forcing case first: the checker goes red for both failure
    # shapes before the real tree gets its verdict.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Home\n\n[gone](missing.md)\n[bad](other.md#nowhere)\n"
    )
    (docs / "other.md").write_text("# Other\n\n## A real heading\n")
    problems = _link_problems(tmp_path)
    assert any("missing.md does not exist" in item for item in problems)
    assert any("anchors nothing" in item for item in problems)
    (docs / "index.md").write_text("# Home\n\n[good](other.md#a-real-heading)\n")
    assert _link_problems(tmp_path) == []


def test_internal_links_and_anchors_resolve() -> None:
    problems = _link_problems(ROOT)
    assert problems == [], "\n".join(problems)


def _assignment_docstrings(src: Path) -> set[str]:
    """Names documented by a string literal under their assignment.

    A ``TypeAlias`` cannot carry a runtime ``__doc__``; the renderer
    reads the source-level string instead, so the gate must too.
    """
    documented: set[str] = set()
    for module in src.rglob("*.py"):
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


def test_an_undocumented_assignment_is_seen_as_such(tmp_path: Path) -> None:
    # The forcing case: the source-level reader distinguishes a
    # documented alias from a bare one.
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        'A: int = 1\n"""Documented."""\n\nB = 2\n"""Also documented."""\n\nC = 3\n'
    )
    assert _assignment_docstrings(src) == {"A", "B"}


def test_every_export_carries_a_docstring() -> None:
    # The published API page shows every export; an empty hover is a
    # hole a reader falls into. The __all__ pins already force the
    # surface; this forces its words.
    missing: list[str] = []
    for member in sorted((ROOT / "packages").iterdir()):
        contract = member / "workshop.toml"
        src = member / "src"
        if not contract.is_file() or not src.is_dir():
            continue
        inits = sorted(src.rglob("__init__.py"), key=lambda p: len(p.parts))
        if not inits:
            continue
        dotted = ".".join(inits[0].parent.relative_to(src).parts)
        module = importlib.import_module(dotted)
        documented = _assignment_docstrings(src)
        for name in getattr(module, "__all__", ()):
            exported = getattr(module, name)
            if name in documented:
                continue
            if not (getattr(exported, "__doc__", None) or "").strip():
                missing.append(f"{dotted}.{name}")
    assert missing == [], "exports without a docstring: " + ", ".join(missing)
