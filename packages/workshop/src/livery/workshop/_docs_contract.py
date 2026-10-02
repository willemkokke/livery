"""What the base knows of a package's docs: its table, its layout, the site's reads.

The site's assembly is a layer's (``livery.extensions.docs``);
these are the facts the base reads for its own reasons: the contract's
``[docs]`` table and the generators it declares, which the CI render
installs requirements for; the layout of a package's ``docs/`` tree
the mount and the wheel build copy; the publish seam the workflow
configures; and the categories the site reads, which the affected
walk and the provenance lines answer with.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from livery.footman.api import fail
from livery.workshop._contract import load_contract
from livery.workshop._contract_keys import Declared
from livery.workshop._packages import Package, discover_packages

#: Where package docs mount inside the site's tree, per package
#: directory name. Gitignored; rebuilt on every docs verb.
MOUNT = "docs/packages"


#: A package's own generated tree, under its ``docs/``: a name on disk,
#: never a published path. The mount merges it into the package's root
#: and strips the prefix from every link into it.
GENERATED_DIR = "_generated"


GENERATED = GENERATED_DIR + "/"


#: Where the site may publish.
SEAMS = ("pages", "container", "ssh", "none")

#: The ``[docs] publish`` key, declared beside its reader.
DECLARED: tuple[Declared, ...] = (Declared("root", "docs.publish", ("str",), SEAMS),)


def docs_table(root: Path) -> dict[str, object]:
    """The contract's ``[docs]`` table; empty when undeclared."""
    contract = load_contract(root / "workshop.toml")
    table = contract.get("docs") or {}
    return dict(table) if isinstance(table, dict) else {}


def package_generators(package: Package) -> list[tuple[str, tuple[str, ...]]]:
    """The docs generators *package* declares: (verb, requirements) each.

    A package's ``workshop.toml`` ``[docs]`` table lists them under
    ``generators``: a verb name (a footman task the package ships),
    or a table naming the verb and the system tools the generator
    needs on a docs machine (``{ verb = "...", requires = [...] }``).
    Anything else refuses naming the file and the entry. Empty
    without a declaration.
    """
    contract_path = package.directory / "workshop.toml"
    contract = load_contract(contract_path)
    table = contract.get("docs") or {}
    declared = table.get("generators") if isinstance(table, dict) else None
    if declared is None:
        return []
    if not isinstance(declared, list):
        fail(f"{contract_path}: [docs] generators must be a list")
    generators: list[tuple[str, tuple[str, ...]]] = []
    for entry in declared:
        if isinstance(entry, str) and entry:
            generators.append((entry, ()))
            continue
        if isinstance(entry, dict) and isinstance(entry.get("verb"), str):
            requires = entry.get("requires", [])
            if isinstance(requires, list) and all(
                isinstance(tool, str) for tool in requires
            ):
                generators.append((entry["verb"], tuple(requires)))
                continue
        fail(
            f"{contract_path}: [docs] generators entry {entry!r} is not a"
            ' verb name or a { verb = "...", requires = [...] } table'
        )
    return generators


def docs_requirements(root: Path) -> tuple[str, ...]:
    """The union of every declared generator's system requirements.

    What the emitted docs CI jobs install before building the site;
    sorted, so the rendered workflow is deterministic.
    """
    union: set[str] = set()
    for package in discover_packages(root):
        for _verb, requires in package_generators(package):
            union.update(requires)
    return tuple(sorted(union))


#: The package-owned nav file inside ``docs/``.
NAV_TOML = "nav.toml"


def declines_api(package: Package) -> bool:
    """Whether *package* turns its reference off with ``[docs] api = false``."""
    table = load_contract(package.directory / "workshop.toml").get("docs") or {}
    return isinstance(table, dict) and table.get("api") is False


def module_root(package: Package) -> Path | None:
    """The importable module's root: the shallowest ``__init__.py``."""
    src = package.directory / "src"
    if not src.is_dir():
        return None
    inits = sorted(src.rglob("__init__.py"), key=lambda p: len(p.parts))
    return inits[0].parent if inits else None


def module_docs_dir(package: Package) -> Path | None:
    """Where *package*'s wheel-embedded ``_docs`` lives; None without src.

    The module root is the shallowest ``__init__.py`` under ``src``,
    which is the importable package uv_build ships.
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


#: The publish seam each forge kind defaults to.
DEFAULT_SEAMS = {"github": "pages", "gitlab": "pages", "gitea": "container"}


def publish_seam(root: Path) -> str:
    """The declared publish seam: pages, container, ssh, or none.

    The contract's ``[docs] publish`` wins, one of `SEAMS`, which the
    contract's judge holds it to; without it the forge kind picks its
    default.
    """
    table = docs_table(root)
    declared = str(table.get("publish", ""))
    if declared:
        return declared
    contract = load_contract(root / "workshop.toml")
    kind = str((contract.get("forge") or {}).get("kind", ""))
    return DEFAULT_SEAMS.get(kind, "none")


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
    from the category registry, so a layer that adds a category the
    site reads names it here.
    """
    from livery.workshop._categories import category_of
    from livery.workshop._provenance import unit_of

    unit, inside = unit_of(root, packages, path)
    if unit is None:
        return False
    return category_of(unit, inside).name in SITE_CATEGORIES
