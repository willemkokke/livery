"""The python API reference: a page per module, and the nav block that lists them.

The ``mkdocstrings`` step of the docs phase runs once per package whose
set holds python and writes the package's reference into its generated
tree: a page per module of each of its roots under ``api/``, each a
single ``:::`` directive that the python handler renders with the
module's members, and the ``api`` nav block that lists the pages,
``nav.api.toml``, which the site places in the package's section. Both
are rebuilt whole, so a module that went takes its page with it, and a
package that declines its reference with ``[docs] api = false`` keeps
neither.

The handler reads a package's modules from its sources and from the
directories the step's ``paths`` option names, and its options and
inventories are the ones the site's config writes
(the ``docs.config`` answer in this extension's ``_config`` module).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, cast

from livery.extensions.docs._navblocks import nav_block_file, write_nav_block
from livery.footman import fail
from livery.workshop._docs_contract import (
    GENERATED,
    GENERATED_DIR,
    declines_api,
    module_root,
)
from livery.workshop._packages import root_marks

if TYPE_CHECKING:
    from livery.workshop import Package, PhaseContext
    from livery.workshop._steps import StepRecord

#: The extension's name, as a workspace lists it.
EXTENSION = "mkdocstrings"

#: The extension's step of the docs phase, whose ``paths`` option a
#: package sets in its contract's ``[phases.docs.mkdocstrings]`` table.
STEP = "docs.mkdocstrings"

#: Where a package's API pages live in its generated tree, and so in its
#: section on the site: ``packages/<member>/api/``.
API_DIR = "api"

#: The nav block that lists a package's API pages, emitted beside them.
NAV_BLOCK = "api"

#: The inventories cross-ecosystem references resolve against. Pinned
#: here so one workshop release moves every project.
INVENTORIES = ("https://docs.python.org/3/objects.inv",)

#: The python handler's options. Google style is the house convention;
#: a docstring is published the moment it is written, empty ones
#: included.
OPTIONS: dict[str, object] = {
    "docstring_style": "google",
    "show_if_no_docstring": True,
    "show_root_heading": True,
    "show_root_full_path": True,
    "separate_signature": True,
    "show_signature_annotations": True,
    "signature_crossrefs": True,
    "members_order": "source",
    "merge_init_into_class": True,
    "summary": True,
    "heading_level": 2,
}


def step() -> StepRecord:
    """The extension's step of the docs phase, as its declaration reads.

    Read from the installed declaration rather than the registered
    steps, since the site's config asks for a package's paths in a
    process that may have mounted nothing.

    Raises:
        Failed: when no installed distribution declares the step,
            naming it.
    """
    from livery.workshop._extensions import installed_declaration

    found = installed_declaration(EXTENSION)
    for record in found.additions.steps if found is not None else ():
        if record.name == STEP:
            return record
    fail(
        f"no installed {EXTENSION} extension declares the {STEP} step; install"
        " the workshop release that ships it"
    )


def documented(package: Package) -> bool:
    """Whether the reference documents *package*, by the extensions its set holds.

    A package whose set holds an extension the step names, python, is
    documented; any other is never asked.
    """
    from livery.workshop._kinds import extension_set

    return bool(set(step().extensions) & set(extension_set(package)))


def pages(package: Package) -> list[tuple[str, str]]:
    """(page path, dotted import path) per module of each root, public first.

    Every module gets a page, underscore-private included: a docstring
    is published the moment it is written. Public sorts before private
    at every level of the tree, and a package's ``__init__`` is its
    index page. A package that ships more than one root, as a wheel
    shipping two does, documents each: the shallowest root's pages sit
    at the reference's top, and each further root's under a directory
    named by its dotted path, after them. A package may decline the
    whole reference with ``[docs] api = false``, the shape a forwarding
    shim takes: its surface is another package's, and documenting the
    forwarders would document the real thing twice.
    """
    if declines_api(package):
        return []
    src = package.directory / "src"
    roots: list[Path] = []
    if src.is_dir():
        # Each topmost mark is a root; a mark under one is its package.
        for mark in root_marks(src):
            if not any(mark.parent.is_relative_to(root) for root in roots):
                roots.append(mark.parent)
    if not roots:
        # A root with no __init__.py: the first module the package owns.
        owned = module_root(package)
        roots = [] if owned is None else [owned]
    found: list[tuple[str, str]] = []
    for index, root in enumerate(roots):
        prefix = "" if index == 0 else ".".join(root.relative_to(src).parts) + "/"
        found += [(prefix + page, dotted) for page, dotted in _root_pages(src, root)]
    return found


def _root_pages(src: Path, root: Path) -> list[tuple[str, str]]:
    """(page path, dotted import path) per module under *root*, public first."""
    entries: list[tuple[tuple[tuple[bool, str], ...], str, str]] = []
    for path in root.rglob("*.py"):
        if path.name == "__main__.py" or "_docs" in path.parts:
            continue
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if parts and parts[-1] == "__init__":
            parts = (*parts[:-1], "")
        key = tuple((part.startswith("_"), part) for part in parts)
        page = (
            "index.md"
            if relative.as_posix() == "__init__"
            else relative.with_suffix(".md")
            .as_posix()
            .replace("/__init__.md", "/index.md")
        )
        dotted = ".".join(path.relative_to(src).with_suffix("").parts)
        dotted = dotted.removesuffix(".__init__")
        entries.append((key, page, dotted))
    entries.sort()
    return [(page, dotted) for _key, page, dotted in entries]


def declared_paths(package: Package) -> list[str]:
    """The directories *package*'s ``paths`` option names, relative to the package.

    Directories holding modules the pages reference beyond the
    package's sources: rendered stubs, for one. A value that is not a
    list of strings refuses naming the contract.
    """
    from livery.workshop._steps import option_value

    return list(cast("list[str]", option_value(step(), package, "paths")))


def sources(package: Package) -> list[str]:
    """The handler's search paths for *package*, relative to the workspace root.

    Its sources while it has pages, then the directories its ``paths``
    option names.
    """
    name = package.member
    found = [f"packages/{name}/src"] if pages(package) else []
    found += [f"packages/{name}/{extra}" for extra in declared_paths(package)]
    return found


def write_reference(package: Package) -> list[str]:
    """Write *package*'s API pages and its nav block; the pages written.

    One page per module, each a single directive, under ``api/`` in
    the generated tree the site's mount merges, so the pages publish
    under the package's own path, and the block listing them beside
    them. Both are rebuilt whole: a package with no page now keeps
    neither.
    """
    generated = package.directory / "docs" / GENERATED_DIR
    base = generated / API_DIR
    shutil.rmtree(base, ignore_errors=True)
    modules = pages(package)
    if not modules:
        nav_block_file(generated, NAV_BLOCK).unlink(missing_ok=True)
        return []
    for page, dotted in modules:
        target = base / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# `{dotted}`\n\n::: {dotted}\n", encoding="utf-8")
    entries = ['{ "API" = [']
    entries += [
        f'    {{ "{dotted}" = "{GENERATED}{API_DIR}/{page}" }},'
        for page, dotted in modules
    ]
    entries.append("] },")
    write_nav_block(generated, NAV_BLOCK, entries)
    return [page for page, _dotted in modules]


def pages_run(ctx: PhaseContext) -> None:
    """Write the API reference of the package the run names: the docs phase's step.

    The pages and the nav block of `write_reference`, the count said.
    """
    package = ctx.member
    written = write_reference(package)
    if written:
        print(f"  API pages for {package.member}: {len(written)}")
