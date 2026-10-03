"""Write the files a birth starts with, which are the workspace's own afterwards.

A seed is a file an extension ships under `content/seeds/<tree>/`, where
the tree names what is being born: `project` for a workspace,
`package-base` and a kind's own tree (`package-python`, ...) for a
member. [livery.workshop._seeds.create][] writes the seeds of every
listed extension into a destination: a `.jinja` file rendered with
minijinja and its suffix dropped, any other file copied byte for byte,
and path names rendered too (`src/{{ source_path }}/...`). A file that
exists is never touched, and nothing is receipted: once written, a seed
belongs to the workspace, and no check judges it.

Within one extension, a later tree of the chain wins over an earlier
one for the same path (a kind's tree over `package-base`). Two
extensions seeding one path follow the shipped files' rule: the later
one declares `REPLACES` or `DELETES` in its declaration module, or the
birth refuses naming both. The fragment engine
([livery.workshop._fragment_engine.plan][]) keeps files current;
this module only writes them once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from livery.footman.api import fail
from livery.workshop._extensions import extension_content, stack_names
from livery.workshop._fragment_engine import Fragment

SEEDS = "seeds"
"""The `content/` subdirectory an extension's seeds live under."""

TEMPLATE = ".jinja"
"""The suffix of a seed rendered with minijinja; any other is copied."""

PROJECT = "project"
"""The tree a workspace's birth writes."""

#: The base's project seeds, which `fm explain` names as seeds.
PROJECT_SEEDS = ("docs/index.md", "README.md", "LICENSE")


def derived(facts: Mapping[str, Any]) -> dict[str, str]:
    """The names a package's seeds derive from its name and its namespace.

    `project_slug` is the module name inside the namespace (`acme-tools`
    in `acme` is `tools`), `source_path` the directory under `src/` and
    `tests/`, and `import_path` the dotted import.
    """
    namespace = str(facts.get("namespace_package", ""))
    name = str(facts.get("package_name", ""))
    prefix = namespace.replace(".", "-") + "-" if namespace else ""
    slug = name.removeprefix(prefix).replace("-", "_")
    if not namespace:
        return {"project_slug": slug, "source_path": slug, "import_path": slug}
    return {
        "project_slug": slug,
        "source_path": f"{namespace.replace('.', '/')}/{slug}",
        "import_path": f"{namespace}.{slug}",
    }


def _shipped(order: Sequence[str], trees: Sequence[str]) -> list[Fragment]:
    """Each listed extension's seeds in *trees*, one fragment each."""
    found: list[Fragment] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        for tree in trees:
            base = content / SEEDS / tree
            if not base.is_dir():
                continue
            for source in sorted(base.rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(base).as_posix().removesuffix(TEMPLATE)
                found.append(
                    Fragment(
                        extension, f"{SEEDS}/{tree}/{relative}", relative, source=source
                    )
                )
    return found


def _tree(fragment: Fragment) -> str:
    return fragment.name.split("/")[1]


def plan(
    root: Path, trees: Sequence[str], data: Mapping[str, Any]
) -> dict[str, Fragment]:
    """Each seed's path, rendered, to the fragment that writes it.

    Raises:
        Failed: for two extensions seeding one path with no declaration
            between them, a declaration naming a seed no earlier listed
            extension ships, and a tree no listed extension seeds.
    """
    from livery.workshop._fragment_engine import (
        _render,  # pyright: ignore[reportPrivateUsage]
        _resolve,  # pyright: ignore[reportPrivateUsage]
    )
    from livery.workshop._shipped_files import declared

    order = list(stack_names(root))
    shipped = _shipped(order, trees)
    unseeded = [t for t in trees if not any(_tree(f) == t for f in shipped)]
    if unseeded:
        fail(
            f"no listed extension seeds {', '.join(unseeded)}: the extensions are"
            f" {', '.join(order)}; the one that ships it goes in [workspace]"
            " extensions"
        )
    rank = {name: index for index, name in enumerate(order)}
    tree_rank = {tree: index for index, tree in enumerate(trees)}
    chosen: dict[str, Fragment] = {}
    for fragment in _resolve(declared(shipped, order, seeds=True), order):
        path = _render(
            Fragment(fragment.owner, f"{fragment.name} (path)", "", fragment.target),
            data,
        )
        held = chosen.get(path)
        if held is not None and held.owner != fragment.owner:
            low, high = sorted((held, fragment), key=lambda f: rank[f.owner])
            fail(
                f"{path} is seeded by both {low.ref} and {high.ref}: {high.owner}"
                f" declares REPLACES = {{{low.ref!r}: <reason>}} to take it,"
                " or DELETES to drop it"
            )
        if held is None or tree_rank[_tree(fragment)] > tree_rank[_tree(held)]:
            chosen[path] = fragment
    return chosen


def create(
    root: Path, destination: Path, trees: Sequence[str], data: Mapping[str, Any]
) -> list[str]:
    """Write the seeds of *trees* into *destination*; the paths written.

    Args:
        root: The workspace whose listed extensions ship the seeds.
        destination: Where the seeds land: the root for a project, the
            member's directory for a package.
        trees: The seed trees in chain order: a later one wins for a
            path within one extension.
        data: What the templates read.

    Returns:
        The paths written, relative to *destination*. A path that
        already exists is left alone and is not listed.
    """
    from livery.workshop._fragment_engine import (
        _render,  # pyright: ignore[reportPrivateUsage]
    )

    written: list[str] = []
    for path, fragment in sorted(plan(root, trees, data).items()):
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
