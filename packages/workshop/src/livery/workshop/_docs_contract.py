"""What the base knows of a package's docs: its table, its layout, the site's reads.

The site's assembly is an extension's (``livery.extensions.docs``);
these are the facts the base reads for its own reasons: the contract's
``[docs]`` table; the layout of a package's ``docs/`` tree the mount
and the wheel build copy; and the categories the site reads, which
the affected walk and the provenance lines answer with.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from livery.workshop._contract import load_contract
from livery.workshop._packages import Package, root_marks

#: Where package docs mount inside the site's tree, per package
#: directory name. Gitignored; rebuilt on every docs verb.
MOUNT = "docs/packages"


#: A package's own generated tree, under its ``docs/``: a name on disk,
#: never a published path. The mount merges it into the package's root
#: and strips the prefix from every link into it.
GENERATED_DIR = "_generated"


GENERATED = GENERATED_DIR + "/"


def docs_table(root: Path) -> dict[str, object]:
    """The contract's ``[docs]`` table; empty when undeclared."""
    contract = load_contract(root / "workshop.toml")
    table = contract.get("docs") or {}
    return dict(table) if isinstance(table, dict) else {}


#: The package-owned nav file inside ``docs/``.
NAV_TOML = "nav.toml"


def declines_api(package: Package) -> bool:
    """Whether *package* turns its reference off with ``[docs] api = false``."""
    table = load_contract(package.directory / "workshop.toml").get("docs") or {}
    return isinstance(table, dict) and table.get("api") is False


def module_root(package: Package) -> Path | None:
    """The importable module's root: the shallowest ``__init__.py``.

    A root with neither, one with no public names, is the first module
    the package's kind says it owns, the directory its wheel ships.
    """
    src = package.directory / "src"
    if not src.is_dir():
        return None
    marks = root_marks(src)
    if marks:
        return marks[0].parent
    from livery.workshop._kinds import kind_for, kind_names

    if package.kind not in kind_names():
        return None
    owned = getattr(kind_for(package.kind).backend, "module_roots", None)
    for module in owned(package) if owned is not None else ():
        directory = src.joinpath(*module.split("."))
        if directory.is_dir():
            return directory
    return None


def module_docs_dir(package: Package) -> Path | None:
    """Where *package*'s wheel-embedded ``_docs`` lives; None without src.

    The module root is the shallowest root mark under ``src``
    ([livery.workshop._packages.root_marks][]), the importable package
    uv_build ships.
    """
    found = module_root(package)
    return None if found is None else found / "_docs"


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


@contextmanager
def module_docs(package: Package) -> Generator[Path | None]:
    """The wheel-embedded ``_docs``, in the source tree for one build alone.

    Materialised whole on entry, as `materialise_module_docs` does, and
    removed on exit, after a failed build too. A copy left behind puts
    the docs' example files under the package's source, where every
    checker that walks the tree judges them as the package's own code.
    """
    target = materialise_module_docs(package)
    try:
        yield target
    finally:
        if target is not None:
            shutil.rmtree(target, ignore_errors=True)


#: The categories the site reads: a change to any other category
#: leaves the site as it was, so the docs job has nothing to build.
SITE_CATEGORIES = frozenset(
    {"prose", "nav", "asset", "example", "generated", "site", "readme"}
)


def site_reads(root: Path, packages: tuple[Package, ...], path: str) -> bool:
    """Whether the site build reads *path*: the docs check's claim.

    A package's docs pages, nav, assets and examples, and the root's
    site files and README, are what the build reads; a note under
    ``notes/``, a source file or a contract is not. The answer comes
    from the category registry, so an extension that adds a category the
    site reads names it here.
    """
    from livery.workshop._categories import category_of
    from livery.workshop._provenance import unit_of

    unit, inside = unit_of(root, packages, path)
    if unit is None:
        return False
    return category_of(unit, inside).name in SITE_CATEGORIES
