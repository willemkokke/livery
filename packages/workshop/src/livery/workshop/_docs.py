"""The documentation site: its rendered config, mounts, and verbs.

One rendered site per workspace. The config is emitted the way
``ci.yml`` is: a generated header, drift-checked, the nav owned
between markers. Authors write ``packages/<name>/docs/`` and the
root ``docs/`` tree; every underscore path this module writes is
machine territory, refreshed by the verbs and never edited by a
person.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import toolroom
from footman import fail, group

from livery.workshop._packages import Package, discover_packages

#: The nav block the emitter owns; an edit between these is drift.
NAV_BEGIN = "# docs-nav:begin (generated; the emitter owns this block)"
NAV_END = "# docs-nav:end"

#: Where package docs mount inside the site's tree, per package
#: directory name. Gitignored; rebuilt on every docs verb.
MOUNT = "docs/_generated/packages"


def docs_table(root: Path) -> dict[str, object]:
    """The contract's ``[docs]`` table; empty when undeclared."""
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    table = contract.get("docs") or {}
    return dict(table) if isinstance(table, dict) else {}


def _pages(directory: Path) -> list[str]:
    """The markdown pages under *directory*, index first, then sorted."""
    if not directory.is_dir():
        return []
    found = sorted(
        page.relative_to(directory).as_posix() for page in directory.rglob("*.md")
    )
    return sorted(found, key=lambda page: (page != "index.md", page))


def _project_name(root: Path) -> str:
    """The workspace's stable name for the site.

    The root project's name, never the directory's: a worktree's
    directory name would make the same contract render differently
    per checkout and turn the drift gate against itself.
    """
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text("utf-8")).get("project") or {}
        name = str(project.get("name", ""))
        if name:
            return name
    return root.resolve().name


def _label(page: str) -> str:
    return "Index" if Path(page).name == "index.md" else Path(page).stem


def zensical_config(root: Path) -> str:
    """The rendered ``zensical.toml`` body, generated header excluded.

    Site identity comes from the ``[docs]`` table (title defaults to
    the root project's name); the nav enumerates the root
    ``docs/`` pages and every package's ``docs/`` tree at its mount
    path. The whole file is the emitter's: hand customisation goes
    through the contract, and the drift gate refuses an edit here.
    """
    table = docs_table(root)
    title = str(table.get("title", "")) or _project_name(root)
    site_url = str(table.get("site_url", ""))
    lines = ["[project]", f'site_name = "{title}"']
    if site_url:
        lines.append(f'site_url = "{site_url}"')
    lines.append("nav = [")
    lines.append(f"    {NAV_BEGIN}")
    for page in _pages(root / "docs"):
        if page.startswith("_generated/"):
            continue
        label = "Home" if page == "index.md" else _label(page)
        lines.append(f'    {{ "{label}" = "{page}" }},')
    for package in discover_packages(root):
        pages = _pages(package.directory / "docs")
        if not pages:
            continue
        name = package.directory.name
        lines.append(f'    {{ "{name}" = [')
        for page in pages:
            lines.append(
                f'        {{ "{_label(page)}" = "_generated/packages/{name}/{page}" }},'
            )
        lines.append("    ] },")
    lines.append(f"    {NAV_END}")
    lines.append("]")
    return "\n".join(lines) + "\n"


def mount_package_docs(root: Path) -> list[str]:
    """Mount every package's ``docs/`` into the site tree; the names.

    The mount is rebuilt whole on every call, so a page deleted from
    a package never lingers in the site.
    """
    base = root / MOUNT
    shutil.rmtree(base, ignore_errors=True)
    mounted: list[str] = []
    for package in discover_packages(root):
        docs = package.directory / "docs"
        if not docs.is_dir():
            continue
        shutil.copytree(docs, base / package.directory.name)
        mounted.append(package.directory.name)
    return mounted


def module_docs_dir(package: Package) -> Path | None:
    """Where *package*'s wheel-embedded ``_docs`` lives; None without src.

    The module root is the shallowest ``__init__.py`` under ``src``,
    which is the importable package uv_build ships.
    """
    src = package.directory / "src"
    if not src.is_dir():
        return None
    inits = sorted(src.rglob("__init__.py"), key=lambda p: len(p.parts))
    if not inits:
        return None
    return inits[0].parent / "_docs"


def materialise_module_docs(package: Package) -> Path | None:
    """Refresh the wheel-embedded ``_docs`` from the package's docs.

    Machine territory: the copy is rebuilt whole so the wheel can
    never carry docs older than the tree it was built from, and a
    package without ``docs/`` gets its stale copy removed rather
    than shipped. Returns the materialised path, or None when the
    package has no module to carry it.
    """
    target = module_docs_dir(package)
    if target is None:
        return None
    shutil.rmtree(target, ignore_errors=True)
    docs = package.directory / "docs"
    if not docs.is_dir():
        return None
    shutil.copytree(docs, target)
    return target


docs_group = group("docs", help="The workspace's documentation site")


def _root() -> Path:
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


@docs_group.task(name="build")
def docs_build() -> None:
    """Mount every package's docs and build the site, strict.

    Strict is the point: a broken link or an orphan page fails here,
    on the machine, before CI says the same thing.
    """
    root = _root()
    mounted = mount_package_docs(root)
    if mounted:
        print(f"  mounted docs for {', '.join(mounted)}")
    result = toolroom.zensical.opts(cwd=root, nofail=True).build(
        clean=True, strict=True
    )
    if result.code != 0:
        fail(f"zensical build exited {result.code}:\n{result.stdout}{result.stderr}")
    print(f"  site built at {root / 'site'}")


@docs_group.task(name="serve", infinite=True)
def docs_serve() -> None:
    """Mount every package's docs and serve the site live."""
    root = _root()
    mount_package_docs(root)
    toolroom.zensical.opts(cwd=root).serve()
