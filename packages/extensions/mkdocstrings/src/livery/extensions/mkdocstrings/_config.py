"""The python handler's tables in the site's config, the members policy applied.

The extension answers the docs extension's ``docs.config`` query with
`handler_tables`, which writes the handler's two tables for the
packages a config covers: their search paths and the inventories, then
the handler's options. The site's own config covers every package; a
package's preview covers that package alone, reads the paths from its
own directory, and resolves a reference it does not carry through the
published site's inventory. The ``docs.members`` answer ``all`` adds an
empty filter list, so every member is documented; ``public`` leaves the
handler's own default filter standing. A config that covers no package
with a path gets no table at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from livery.extensions.docs import ALL_MEMBERS, MEMBERS, Scope
from livery.extensions.mkdocstrings._pages import (
    INVENTORIES,
    OPTIONS,
    documented,
    sources,
)
from livery.workshop import answer

if TYPE_CHECKING:
    from livery.extensions.docs import Writer

#: The mkdocstrings handler the pages' directives are rendered with.
HANDLER = "python"

#: The inventory a site publishes beside its pages, which a preview's
#: references to the rest of the site resolve against.
SITE_INVENTORY = "objects.inv"


def writer(root: Path) -> Writer:
    """Answer ``docs.config`` with the function that writes the handler's tables.

    The docs extension asks with the workspace root, which the answer
    does not depend on, and calls the answer with each config's scope.
    """
    del root
    return handler_tables


def options(root: Path) -> dict[str, object]:
    """The handler's options as the site's config writes them, by name.

    The extension's options, then the members policy the listed
    extensions answer: ``all`` adds ``filters``, an empty list.
    """
    found = dict(OPTIONS)
    if answer(root, MEMBERS) == ALL_MEMBERS:
        found["filters"] = []
    return found


def toml_value(value: object) -> str:
    """*value* as a TOML literal: a bool, a number, a string, or a list of those."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        items: list[object] = list(value)
        return "[" + ", ".join(toml_value(item) for item in items) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def handler_tables(scope: Scope) -> str:
    """The python handler's tables for the config *scope* describes, as TOML.

    The search paths of each package the scope covers that the
    reference documents, in the scope's order, each reached from the
    config's directory; the inventories, the published site's own
    after the pinned ones for a preview; then the options table. Empty
    when no package gives a path.
    """
    paths = [
        scope.anchor + path
        for package in scope.packages
        if documented(package)
        for path in sources(package)
    ]
    if not paths:
        return ""
    inventories = [*INVENTORIES]
    if scope.published:
        inventories.append(f"{scope.published.rstrip('/')}/{SITE_INVENTORY}")
    table = f"project.plugins.mkdocstrings.handlers.{HANDLER}"
    lines = [
        f"[{table}]",
        f"paths = {toml_value(paths)}",
        f"inventories = {toml_value(inventories)}",
        "",
        f"[{table}.options]",
    ]
    lines += [
        f"{key} = {toml_value(value)}" for key, value in options(scope.root).items()
    ]
    return "\n".join(lines) + "\n"
