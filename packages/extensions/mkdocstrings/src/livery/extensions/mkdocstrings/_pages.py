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

The pages and the block are data the docs extension reads: the
generated tree is ``docs/_generated/`` in the package, and the block is
the file ``nav.api.toml`` in it, a ``nav`` list of one-key tables, each
a label to a page path inside the package's ``docs/``. This extension
writes both itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, cast

from livery.workshop import (
    extension_set,
    read_contract,
    step_extensions,
    step_option,
)

if TYPE_CHECKING:
    from livery.workshop import Package, PhaseContext

#: The extension's step of the docs phase, whose ``paths`` option a
#: package sets in its contract's ``[phases.docs.mkdocstrings]`` table.
STEP = "docs.mkdocstrings"

#: The package's generated tree under its ``docs/``, which the docs
#: extension merges into the package's section on the site, stripping
#: the tree's name from every link into it.
GENERATED_DIR = "_generated"

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


def documented(package: Package) -> bool:
    """Whether the reference documents *package*, by the extensions its set holds.

    A package whose set holds an extension the step names, python, is
    documented; any other is never asked. The step is read from the
    installed declaration in a process that mounted nothing, as the
    site's config may be.
    """
    return bool(set(step_extensions(STEP)) & set(extension_set(package)))


def declines_api(package: Package) -> bool:
    """Whether *package* turns its reference off with ``[docs] api = false``.

    The key is the docs extension's, read here from the package's
    contract as data.
    """
    table = read_contract(package.directory).get("docs") or {}
    return isinstance(table, dict) and table.get("api") is False


def _owned_root(package: Package, src: Path) -> Path | None:
    """The directory under *src* of the first module *package* owns; None for none.

    Its module roots, as its extensions answer them, in their order:
    the first whose directory exists. A root
    with no ``__init__.py`` starts there, at the module its wheel ships.
    """
    from livery.workshop import MODULE_ROOTS, answer

    for module in answer(package, MODULE_ROOTS):
        directory = src.joinpath(*module.split("."))
        if directory.is_dir():
            return directory
    return None


def roots(src: Path) -> list[Path]:
    """The importable roots under *src*: each topmost directory with an ``__init__.py``.

    Shallowest first, then by path. A directory with no ``__init__.py``
    is a namespace and marks nothing, so a package under one is a root
    of its own, and a package under a root is part of that root.
    """
    found: list[Path] = []
    marks = sorted(src.rglob("__init__.py"), key=lambda path: (len(path.parts), path))
    for mark in marks:
        if not any(mark.parent.is_relative_to(root) for root in found):
            found.append(mark.parent)
    return found


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
    if not src.is_dir():
        return []
    marked = roots(src)
    if not marked:
        # A root with no __init__.py: the first module the package owns.
        owned = _owned_root(package, src)
        marked = [] if owned is None else [owned]
    found: list[tuple[str, str]] = []
    for index, root in enumerate(marked):
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
    return list(cast("list[str]", step_option(STEP, package, "paths")))


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
    block = generated / f"nav.{NAV_BLOCK}.toml"
    shutil.rmtree(base, ignore_errors=True)
    modules = pages(package)
    if not modules:
        block.unlink(missing_ok=True)
        return []
    for page, dotted in modules:
        target = base / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# `{dotted}`\n\n::: {dotted}\n", encoding="utf-8")
    entries = ['{ "API" = [']
    entries += [
        f'    {{ "{dotted}" = "{GENERATED_DIR}/{API_DIR}/{page}" }},'
        for page, dotted in modules
    ]
    entries.append("] },")
    body = "\n".join(entries)
    block.write_text(
        f"# The {NAV_BLOCK!r} nav block, emitted by its generator; the authored"
        f" nav.toml places it.\nnav = [\n{body}\n]\n",
        encoding="utf-8",
    )
    return [page for page, _dotted in modules]


def pages_run(ctx: PhaseContext) -> None:
    """Write the API reference of the package the run names: the docs phase's step.

    The pages and the nav block of `write_reference`, the count said.
    """
    package = ctx.member
    written = write_reference(package)
    if written:
        print(f"  API pages for {package.member}: {len(written)}")
