"""The package contracts: discovery, the graph, and the layering lint.

A directory under ``packages/`` is a package exactly when it carries a
``livery.toml``; everything the workshop knows about a package it
learns there. livery.workshop.verify_workspace is the layering lint:
contracts present, declared edges agreeing with the native manifests
in both directions, the graph acyclic, and the one package-specific
invariant (the forge's stdlib rule) kept.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Edge:
    """One declared dependency edge of a package.

    Attributes:
        path: The dependency's identity: its directory path from the
            workspace root, as the tags spell it.
        kind: ``build``, ``test``, or ``tool``; only ``build`` edges
            order publishing, and only they must appear in the native
            manifest.
        floor: The released version the native manifest must require.
    """

    path: str
    kind: str
    floor: str


@dataclass(frozen=True)
class Package:
    """One package, as its contract declares it.

    Attributes:
        directory: The package directory.
        path: The identity path from the workspace root
            (``packages/forge``).
        name: The distribution name (``livery-forge``).
        type: The backend selector (``python`` today).
        depends: The declared edges, in contract order.
    """

    directory: Path
    path: str
    name: str
    type: str
    depends: tuple[Edge, ...]


def discover_packages(root: Path) -> tuple[Package, ...]:
    """Every package the workspace carries, by its contract, sorted by path.

    Raises ValueError, with every finding listed, when a directory
    under ``packages/`` lacks its contract or its ``pyproject.toml``:
    a half-present package is a wrong state, not a lesser one.
    """
    problems = []
    packages = []
    packages_dir = root / "packages"
    for directory in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        contract_file = directory / "livery.toml"
        if not contract_file.is_file():
            problems.append(f"{directory.name}: no livery.toml")
            continue
        if not (directory / "pyproject.toml").is_file():
            problems.append(f"{directory.name}: no pyproject.toml")
            continue
        contract = tomllib.loads(contract_file.read_text("utf-8"))
        depends = tuple(
            Edge(
                path=str(edge.get("path", "")),
                kind=str(edge.get("kind", "build")),
                floor=str(edge.get("floor", "")),
            )
            for edge in contract.get("depends", [])
        )
        packages.append(
            Package(
                directory=directory,
                path=f"packages/{directory.name}",
                name=str(contract.get("name", "")),
                type=str(contract.get("type", "")),
                depends=depends,
            )
        )
    if problems:
        raise ValueError(
            "packages missing their contracts:\n  " + "\n  ".join(problems)
        )
    return tuple(packages)


def verify_workspace(root: Path) -> tuple[Package, ...]:
    """The layering lint: check every workspace invariant, or raise.

    Returns the discovered packages when everything holds. Raises
    ValueError listing every violation verbatim otherwise:

    - every ``build`` edge appears in the native manifest with a
      constraint carrying its floor, and every workspace-internal
      native dependency is a declared edge (agreement both ways);
    - the dependency graph is acyclic, so dependencies only point
      downward;
    - ``livery.forge`` imports only the standard library at module
      import time, plus its one declared lazy extra (PyNaCl), because
      the whole ecosystem stands on it being dependency-free. The one
      exception is the dev-container plugin under ``_dev``, which may
      also import footman: its only loader is footman's ``plugin()``,
      so footman is present whenever it loads.
    """
    packages = discover_packages(root)
    by_path = {package.path: package for package in packages}
    names_by_path = {package.path: package.name for package in packages}
    problems: list[str] = []

    for package in packages:
        native = _native_dependencies(package.directory / "pyproject.toml")
        declared = {edge.path: edge for edge in package.depends}
        for edge in package.depends:
            if edge.path not in by_path:
                problems.append(
                    f"{package.path}: declares an edge on {edge.path},"
                    " which is not a package here"
                )
                continue
            if edge.kind != "build":
                continue  # test and tool edges have no native home yet
            dep_name = names_by_path[edge.path]
            constraint = native.get(dep_name)
            if constraint is None:
                problems.append(
                    f"{package.path}: the build edge on {edge.path} is not in"
                    f" [project.dependencies] ({dep_name} missing)"
                )
            elif edge.floor and f">={edge.floor}" not in constraint:
                problems.append(
                    f"{package.path}: the edge on {edge.path} floors at"
                    f" {edge.floor}, and [project.dependencies] says"
                    f" {dep_name}{constraint or ''}"
                )
        internal_names = set(names_by_path.values())
        for name in native:
            if name in internal_names and name != package.name:
                dep_path = next(path for path, n in names_by_path.items() if n == name)
                if dep_path not in declared:
                    problems.append(
                        f"{package.path}: depends on {name} natively, with no"
                        f" [[depends]] edge on {dep_path}"
                    )

    problems.extend(_cycles(packages))
    problems.extend(_forge_is_stdlib_only(root))
    if problems:
        raise ValueError(
            "the workspace breaks its layering:\n  " + "\n  ".join(problems)
        )
    return packages


def _native_dependencies(pyproject: Path) -> dict[str, str]:
    """The [project.dependencies] entries, name to constraint text."""
    data = tomllib.loads(pyproject.read_text("utf-8"))
    entries = {}
    for requirement in data.get("project", {}).get("dependencies", []):
        text = str(requirement)
        name = text
        for cut in "[>=<!~; ":
            head, _, _ = name.partition(cut)
            name = head
        entries[name] = text[len(name) :].split(";")[0].replace("]", "")
    return entries


def _cycles(packages: tuple[Package, ...]) -> list[str]:
    """A cycle report, empty when dependencies only point downward."""
    edges = {
        package.path: [edge.path for edge in package.depends] for package in packages
    }
    seen: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> list[str]:
        if node in stack:
            loop = [*stack[stack.index(node) :], node]
            return [" -> ".join(loop)]
        if node in seen:
            return []
        seen.add(node)
        stack.append(node)
        found = [cycle for child in edges.get(node, []) for cycle in walk(child)]
        stack.pop()
        return found

    return [
        f"dependency cycle: {cycle}"
        for package in packages
        for cycle in walk(package.path)
    ]


#: The optional extras livery.forge may import lazily; growing this
#: set is a plan decision, not an edit.
_FORGE_LAZY_EXTRAS = frozenset({"nacl"})

#: The dev-container plugin's subtree, the one place in forge that may
#: import footman: its only loader is footman's own plugin(), so
#: footman is present whenever it loads, and livery-forge still
#: declares no dependency.
_FORGE_PLUGIN_DIR = "packages/forge/src/livery/forge/_dev"


def _forge_is_stdlib_only(root: Path) -> list[str]:
    """Violations of the forge's stdlib-at-import-time rule."""
    stdlib = sys.stdlib_module_names
    allowed = set(stdlib) | {"livery"} | _FORGE_LAZY_EXTRAS
    plugin_dir = root / _FORGE_PLUGIN_DIR
    problems = []
    for source in sorted((root / "packages/forge/src").rglob("*.py")):
        allowed_here = allowed
        if source.is_relative_to(plugin_dir):
            allowed_here = allowed | {"footman"}
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top not in allowed_here:
                    problems.append(
                        f"{source.relative_to(root)} imports {name!r}:"
                        " livery.forge is stdlib-only at import time"
                    )
    return problems
