"""The docs extension's steps: generators run, and links resolve.

`docs.generators`, a step of the docs phase, runs the verbs each
package names under ``[docs] generators`` before the site builds.
`lint.doclinks` reads the authored markdown of the workspace's `docs/`
and of every member's `docs/`, and refuses a link to a file that does
not exist or an anchor no heading of the target makes.

The strict site build judges what they leave out: the links into the
pages generated at build time, the pages a package's own generators
write, and the nav.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery.extensions.docs._navblocks import GENERATED_DIR
from livery.footman import fail
from livery.workshop import (
    Changes,
    Package,
    PhaseContext,
    discover_packages,
    selected_files,
)

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


def _docs_trees(root: Path, packages: tuple[Package, ...]) -> list[Path]:
    trees = [root / "docs"]
    trees += [package.directory / "docs" for package in packages]
    return [tree for tree in trees if tree.is_dir()]


def _member_depth(packages: tuple[Package, ...], relative: Path) -> int:
    """How many leading parts of *relative* name a package's directory.

    2 for ``packages/forge/...``, 3 for ``packages/<group>/<name>/...``,
    0 for a path outside every package, or one that is the package
    directory itself or above it.
    """
    parts = relative.parts
    for package in packages:
        depth = len(Path(package.path).parts)
        if len(parts) > depth and parts[:depth] == Path(package.path).parts:
            return depth
    return 0


def _pages(root: Path, packages: tuple[Package, ...]) -> list[Path]:
    pages: list[Path] = []
    for tree in _docs_trees(root, packages):
        pages += [
            page
            for page in sorted(tree.rglob("*.md"))
            if GENERATED_DIR not in page.relative_to(tree).parts
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
    packages = discover_packages(root)
    for page in _pages(root, packages):
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
                depth = _member_depth(packages, Path(path_part))
                if depth:
                    parts = Path(path_part).parts
                    resolved = (
                        root.joinpath(*parts[:depth]) / "docs" / Path(*parts[depth:])
                    ).resolve()
                elif GENERATED_DIR in path_part:
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


def headings_removed(changes: Changes) -> bool:
    """Whether a changed markdown file lost a heading, an anchor a link may name.

    A link in an unchanged page may point at it, so ``lint.doclinks``
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


def run_generators(root: Path) -> list[str]:
    """Run every package's declared docs generators; the verbs run.

    Each verb runs as its own runner invocation at the workspace
    root, in the packages' order and each verb once, so a generator is
    the task a person would type. A failing generator turns the build
    red with the verb and its package named; its own output has already
    streamed. Generators must be idempotent: a second run on unchanged
    input is a no-op.
    """
    import shutil

    import livery.footman as footman
    from livery.extensions.docs._contract import package_generators
    from livery.workshop import discover_packages

    ran: list[str] = []
    runner = ""
    for package in discover_packages(root):
        for generator in package_generators(package):
            verb = generator.verb
            if verb in ran:
                continue  # two declarations of one shared verb run it once
            if not runner:
                runner = shutil.which(footman.prog()) or ""
                if not runner:
                    fail(
                        f"{footman.prog()} is not on PATH, so the declared"
                        f" docs generators cannot run; enter the"
                        " environment (source setup.sh) first"
                    )
            code = footman.run([runner, verb], cwd=root, nofail=True)
            if int(code) != 0:
                fail(
                    f"docs generator `{footman.prog()} {verb}`"
                    f" ({package.name}) exited {int(code)}"
                )
            ran.append(verb)
    return ran


def generators_run(ctx: PhaseContext) -> None:
    """Run the verbs the packages name under ``[docs] generators``: a docs step.

    `run_generators` over the workspace, the verbs it ran said.
    """
    ran = run_generators(ctx.root)
    if ran:
        print(f"  generators: {', '.join(ran)}")


def doclinks_run(ctx: PhaseContext) -> None:
    problems = link_problems(ctx.root, selected_files(ctx))
    if problems:
        fail("links that resolve nothing:\n  " + "\n  ".join(problems))
