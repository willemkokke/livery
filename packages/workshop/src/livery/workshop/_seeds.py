"""Write the files a birth starts with, which are the workspace's own afterwards.

A workspace's birth writes the seed tree ``project``. A package's birth
writes the seed tree ``package-base``, then the files of the template
the package is born from. A seed is a file an extension ships under
``content/seeds/<tree>/``, and every extension of the workspace's stack
adds the seeds it ships for the tree: the base's ``package-base`` holds
the docs page and its nav, and a listed extension such as the
changelog's adds its own.

A template is declared under ``[templates.<name>]`` by an installed
extension's ``extension.toml``, or by the base's ``contract.toml``: its
``doc``, the ``extensions`` a package born from it lists, and
``extends``, the template whose files the birth writes first. Its files
live under ``content/templates/<name>/`` beside the file that declares
it. A package can be born from any installed template, whether the
workspace lists its declarer or not, and a template is read at a birth
alone: nothing records which one a package came from.

`create` writes a ``.jinja`` file rendered with minijinja, its suffix
dropped, and any other file byte for byte; path names render too
(``src/{{ source_path }}/...``). A file that exists is never touched,
and nothing is receipted: once written, a file belongs to the
workspace, and no step judges it.

For one path, a template's file wins over the seeds and over the files
of the template it extends. Two extensions seeding one path of a tree
follow the shipped files' rule instead: the one later in composition
order, which requires the other, declares the other's under
``[replaces]`` or ``[deletes]`` in its ``extension.toml``, or the birth
refuses naming both. The fragment
engine keeps the composed files current; this module writes these once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.footman import fail
from livery.workshop._extensions import extension_content, stack_names
from livery.workshop._fragment_engine import Fragment

if TYPE_CHECKING:
    from livery.workshop._declaration import DeclaredTemplate

SEEDS = "seeds"
"""The `content/` subdirectory an extension's seeds live under."""

TEMPLATES = "templates"
"""The `content/` subdirectory a template's files live under, by its name."""

TEMPLATE = ".jinja"
"""The suffix of a file rendered with minijinja; any other is copied."""

PROJECT = "project"
"""The tree a workspace's birth writes."""

PACKAGE_BASE = "package-base"
"""The tree every package's birth writes before its template's files.

The base's own holds the docs page and its nav, and a listed extension
may ship seeds of its own under the same tree.
"""

#: The base's project seeds, which `fm explain` names as seeds.
PROJECT_SEEDS = ("docs/index.md", "README.md", "LICENSE")


def derived(facts: Mapping[str, Any]) -> dict[str, str]:
    """The names a package's seeds derive from its name and its namespace.

    The distribution name decides the import path, as it does in this
    repository: each hyphen after the namespace's own prefix is one
    namespace level (`acme-tools-store` in `acme` imports as
    `acme.tools.store`). The directory decides nothing, so a package
    in a group directory (`extensions/widgets`, named
    `acme-extensions-widgets`) imports as `acme.extensions.widgets`.
    `project_slug` is the last level, `source_path` the directory
    under `src/` and `tests/`, and `import_path` the dotted import.
    """
    namespace = str(facts.get("namespace_package", ""))
    name = str(facts.get("package_name", ""))
    prefix = namespace.replace(".", "-") + "-" if namespace else ""
    levels = name.removeprefix(prefix).split("-")
    if not namespace:
        # A workspace without a namespace keeps one top-level module.
        levels = ["_".join(levels)]
    module = [*namespace.split("."), *levels] if namespace else levels
    return {
        "project_slug": levels[-1],
        "source_path": "/".join(module),
        "import_path": ".".join(module),
    }


def template_files(template: DeclaredTemplate) -> Path:
    """The directory *template*'s files live in, beside the file declaring it."""
    return template.path.resolve().parent / "content" / TEMPLATES / template.name


def installed_templates() -> dict[str, DeclaredTemplate]:
    """Every installed template, by name: the base's and each installed extension's.

    Read from the declaration files alone, without judging the rest of
    an extension's file or importing anything, and whether the
    workspace lists the extension or not.

    Raises:
        Failed: for a template two extensions declare, naming both, and
            for a template off its shape, naming the file and the key.
    """
    import tomllib

    from livery.workshop._contract_keys import BASE_FILE
    from livery.workshop._declaration import (
        DeclarationError,
        template_declarations,
        template_specs_of,
    )
    from livery.workshop._extensions import (
        SELF,
        extension_package,
        installed_extensions,
    )

    try:
        base = tomllib.loads(BASE_FILE.read_text("utf-8"))
        found = list(template_specs_of(base, BASE_FILE, extension=SELF).values())
        for extension in installed_extensions():
            found += template_declarations(extension, extension_package(extension))
    except DeclarationError as error:
        fail(str(error))
    by_name: dict[str, list[DeclaredTemplate]] = {}
    for template in found:
        by_name.setdefault(template.name, []).append(template)
    for name, held in sorted(by_name.items()):
        if len(held) > 1:
            owners = sorted(template.extension for template in held)
            named = (
                f"both {owners[0]} and {owners[1]}"
                if len(owners) == 2
                else f"{', '.join(owners[:-1])} and {owners[-1]}"
            )
            fail(
                f"the template {name} is declared by {named}; one extension"
                " declares a template, so one of them renames it"
            )
    return {name: held[0] for name, held in sorted(by_name.items())}


def described(templates: Mapping[str, DeclaredTemplate]) -> str:
    """Each of *templates* on a line of its own, its name then its doc."""
    return "\n".join(
        f"  {name}: {template.doc}" for name, template in sorted(templates.items())
    )


def template_chain(
    name: str, templates: Mapping[str, DeclaredTemplate] | None = None
) -> tuple[DeclaredTemplate, ...]:
    """The templates a birth from *name* writes, in order: the one extended first.

    *name* is last, after the template it extends and that template's
    own chain. *templates* is the installed ones, read when None.

    Raises:
        Failed: for a name no installed extension declares, an
            ``extends`` that names one, and an ``extends`` that leads
            back to a template of the chain; each refusal names the
            installed templates or the cycle.
    """
    known = installed_templates() if templates is None else templates
    if name not in known:
        fail(
            f"no installed template is named {name}; the installed templates"
            f" are:\n{described(known)}"
        )
    chain: list[DeclaredTemplate] = []
    current = name
    while current:
        names = [template.name for template in chain]
        if current in names:
            loop = [*names[names.index(current) :], current]
            fail(
                f"the template {name} reaches an extends cycle:"
                f" {' extends '.join(loop)}; each template extends one that does"
                " not lead back to it"
            )
        template = known.get(current)
        if template is None:
            fail(
                f"the template {chain[-1].name} extends {current}, which no"
                " installed extension declares; the installed templates are:"
                f"\n{described(known)}"
            )
        chain.append(template)
        current = template.extends
    return tuple(reversed(chain))


def _judged(chain: Sequence[DeclaredTemplate]) -> tuple[str, ...]:
    """The canonical list of the born template, the last of *chain*, once judged.

    Raises:
        Failed: for a template of the chain whose directory of files is
            missing, and for a born template listing an extension no
            installed distribution declares, or a list whose
            requirements order no start.
    """
    from livery.workshop._composition import OrderCycle, canonical
    from livery.workshop._declaration import DeclarationError
    from livery.workshop._extensions import GROUP, installed_declaration

    for template in chain:
        files = template_files(template)
        if not files.is_dir():
            fail(
                f"the template {template.name} has no files: {files} is missing"
                f" beside {template.path.name}, which declares it"
            )
    born = chain[-1]
    try:
        for name in born.extensions:
            if installed_declaration(name) is None:
                fail(
                    f"the template {born.name} lists {name}, which no installed"
                    f" distribution declares in {GROUP}; install it, or birth the"
                    " package from another template"
                )
        return canonical(born.extensions, installed_declaration)
    except (DeclarationError, OrderCycle) as error:
        fail(f"the template {born.name}: {error}")


def _shipped(order: Sequence[str], tree: str) -> list[Fragment]:
    """Each listed extension's seeds in *tree*, one fragment each."""
    found: list[Fragment] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        found += _fragments(extension, content / SEEDS / tree, f"{SEEDS}/{tree}")
    return found


def _fragments(owner: str, base: Path, named: str) -> list[Fragment]:
    """Each file under *base*, a fragment of *owner* named under *named*."""
    if not base.is_dir():
        return []
    found: list[Fragment] = []
    for source in sorted(base.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(base).as_posix().removesuffix(TEMPLATE)
        found.append(Fragment(owner, f"{named}/{relative}", relative, source=source))
    return found


def plan(
    root: Path,
    tree: str,
    data: Mapping[str, Any],
    *,
    templates: Sequence[DeclaredTemplate] = (),
) -> dict[str, Fragment]:
    """Each file's path, rendered, to the fragment that writes it.

    The seeds of *tree* that the stack ships come first, then each of
    *templates* in order; a template's file takes a path from the seeds
    and from the templates before it.

    Raises:
        Failed: for two extensions seeding one path with no declaration
            between them, a declaration naming a seed no listed extension
            ships, and one naming a seed of an extension the declaring
            one does not require.
    """
    from livery.workshop._fragment_engine import (
        _render,  # pyright: ignore[reportPrivateUsage]
        _resolve,  # pyright: ignore[reportPrivateUsage]
    )
    from livery.workshop._shipped_files import declared

    def rendered(fragment: Fragment) -> str:
        return _render(
            Fragment(fragment.owner, f"{fragment.name} (path)", "", fragment.target),
            data,
        )

    order = list(stack_names(root))
    rank = {name: index for index, name in enumerate(order)}
    chosen: dict[str, Fragment] = {}
    seeds = _shipped(order, tree)
    for fragment in _resolve(declared(seeds, order, seeds=True), order):
        path = rendered(fragment)
        held = chosen.get(path)
        if held is not None and held.owner != fragment.owner:
            low, high = sorted((held, fragment), key=lambda f: rank[f.owner])
            fail(
                f"{path} is seeded by both {low.ref} and {high.ref}: {high.owner}"
                f' declares [replaces] "{low.ref}" = "<reason>" in its'
                " extension.toml to take it, or [deletes] to drop it"
            )
        chosen[path] = fragment
    for template in templates:
        named = f"{TEMPLATES}/{template.name}"
        for fragment in _fragments(template.extension, template_files(template), named):
            chosen[rendered(fragment)] = fragment
    return chosen


def create(
    root: Path,
    destination: Path,
    tree: str,
    data: Mapping[str, Any],
    *,
    templates: Sequence[DeclaredTemplate] = (),
) -> list[str]:
    """Write the seeds of *tree*, then *templates*' files, into *destination*.

    Args:
        root: The workspace whose stack ships the seeds.
        destination: Where the files land: the root for a project, the
            member's directory for a package.
        tree: The seed tree, ``project`` or ``package-base``.
        data: What the files read when they render.
        templates: The templates whose files come after the seeds, in
            order: a later one wins for a path.

    Returns:
        The paths written, relative to *destination*. A path that
        already exists is left alone and is not listed.
    """
    from livery.workshop._fragment_engine import (
        _render,  # pyright: ignore[reportPrivateUsage]
    )

    written: list[str] = []
    for path, fragment in sorted(plan(root, tree, data, templates=templates).items()):
        target = destination / path
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = fragment.source
        if source is not None and not source.name.endswith(TEMPLATE):
            target.write_bytes(source.read_bytes())
        else:
            target.write_bytes(_render(fragment, data).encode("utf-8"))
        written.append(path)
    return written


def create_package(
    root: Path, destination: Path, template: str, data: Mapping[str, Any]
) -> list[str]:
    """Write a package born from *template* into *destination*; the paths written.

    The ``package-base`` seeds come first, then the files of each
    template of the chain, the one extended first and *template* last
    (`template_chain`). The files read the born template's
    ``extensions``, in their canonical form, as ``package_extensions``,
    spelled as a contract spells the list: the contract writes it.
    Nothing is written until every template of the chain is judged.

    Raises:
        Failed: for a template no installed extension declares, a chain
            that does not resolve, a template whose files are missing,
            and a born template listing an extension a package cannot
            list; each refusal names what to change.
    """
    from livery.workshop._contract import spelled_list

    chain = template_chain(template)
    listed = _judged(chain)
    full = {**data, "package_extensions": spelled_list(listed)}
    return create(root, destination, PACKAGE_BASE, full, templates=chain)
