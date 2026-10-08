"""The site's Packages section: where each package sits, in what order, and its page.

Every package sits under one entry, Packages, in a tree its contract
decides. A python package sits at its import path, less the namespace
every python package shares: ``livery.toolroom.bench`` sits at
toolroom, then bench. A package of another kind sits at its folder
under ``packages/``. A package's ``[docs] name``, a dotted path whose
last part is its label, places it anywhere, inside another package's
entry included. Siblings come in the order the release wave publishes
in, every dependency before its dependents, and a group sorts with its
earliest member. The landing page lists the same tree, each package
with its contract's description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from livery.footman import fail
from livery.workshop import Package, discover_packages, read_contract
from livery.workshop._docs_contract import MOUNT, module_root
from livery.workshop._graph import order_topologically

#: The landing page's name under the mount: ``packages/index.md`` on the site.
LANDING = "index.md"

#: A ``[docs] name``: dotted parts of letters, digits, ``_`` and ``-``.
_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")


@dataclass
class Node:
    """One place in the Packages tree: a package's, a group's, or both.

    Attributes:
        label: The place's last part, the nav's label for it.
        package: The package at this place, or None for a group.
        children: The places below it, by label.
        rank: Where the place sorts among its siblings: its earliest
            package's position in the dependency order.
    """

    label: str
    package: Package | None = None
    children: dict[str, Node] = field(default_factory=dict[str, "Node"])
    rank: int = 0

    def ordered(self) -> list[Node]:
        """The places below, dependencies first, then by label."""
        return sorted(
            self.children.values(), key=lambda child: (child.rank, child.label)
        )


def declared_place(package: Package) -> tuple[str, ...] | None:
    """The place *package*'s ``[docs] name`` gives it, or None without one.

    Raises:
        Failed: when the name is not a dotted path, naming the file.
    """
    table = read_contract(package.directory).get("docs") or {}
    name = table.get("name") if isinstance(table, dict) else None
    if name is None:
        return None
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        fail(
            f"{package.directory / 'workshop.toml'}: [docs] name {name!r} is not a"
            " dotted path, such as 'workshop.extensions.ruff'"
        )
    return tuple(name.split("."))


def places(packages: tuple[Package, ...]) -> dict[str, tuple[str, ...]]:
    """Each package's place in the Packages tree, by its path.

    Its ``[docs] name`` when it declares one; else its import path, less
    the first part when every python package's import path shares it;
    else its folder under ``packages/``.
    """
    declared = {package.path: declared_place(package) for package in packages}
    imported: dict[str, tuple[str, ...]] = {}
    for package in packages:
        root = module_root(package)
        if declared[package.path] is None and root is not None:
            imported[package.path] = root.relative_to(package.directory / "src").parts
    heads = {parts[0] for parts in imported.values()}
    shared = (
        heads.pop()
        if len(heads) == 1 and all(len(parts) > 1 for parts in imported.values())
        else ""
    )
    found: dict[str, tuple[str, ...]] = {}
    for package in packages:
        place = declared[package.path]
        if place is None and package.path in imported:
            parts = imported[package.path]
            place = parts[1:] if shared else parts
        found[package.path] = place or tuple(package.member.split("/"))
    return found


def package_tree(packages: tuple[Package, ...]) -> Node:
    """The Packages tree of *packages*; read it through `Node.ordered`.

    Raises:
        Failed: when two packages sit at one place, naming both.
    """
    order = order_topologically(tuple(sorted(packages, key=lambda p: p.path)))
    found = places(order)
    root = Node("Packages")
    taken: dict[tuple[str, ...], Package] = {}
    for rank, package in enumerate(order):
        place = found[package.path]
        if place in taken:
            fail(
                f"{taken[place].path} and {package.path} both sit at"
                f" {'.'.join(place)} in the Packages tree: give one a [docs] name"
                " of its own"
            )
        taken[place] = package
        node = root
        for part in place:
            # The dependency order visits the earliest member first, so
            # a group keeps the rank its first package gave it.
            node = node.children.setdefault(part, Node(part, rank=rank))
        node.package = package
    return root


def _landing_link(base: Path, package: Package) -> str:
    """The landing page's link to *package*: its index, else its first page."""
    section = base / package.member
    if not section.is_dir():
        return ""
    if (section / "index.md").is_file():
        return f"{package.member}/index.md"
    pages = sorted(page.relative_to(base).as_posix() for page in section.rglob("*.md"))
    return pages[0] if pages else ""


def write_packages_page(root: Path) -> Path | None:
    """Write the Packages landing page into the mount; its path, None without packages.

    Runs after the mount, so each package links to the page its
    section opens on. Rebuilt on every docs verb, as the mount is.
    """
    packages = discover_packages(root)
    if not packages:
        return None
    base = root / MOUNT
    lines = [
        "# Packages",
        "",
        "Every package of the workspace, each after the packages it depends on.",
        "",
    ]

    def walk(node: Node, depth: int) -> None:
        for child in node.ordered():
            pad = "    " * depth
            package = child.package
            if package is None:
                lines.append(f"{pad}- {child.label}")
            else:
                link = _landing_link(base, package)
                label = f"[{child.label}]({link})" if link else child.label
                description = str(
                    read_contract(package.directory).get("description", "")
                )
                lines.append(
                    f"{pad}- {label}" + (f": {description}" if description else "")
                )
            walk(child, depth + 1)

    walk(package_tree(packages), 0)
    path = base / LANDING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
