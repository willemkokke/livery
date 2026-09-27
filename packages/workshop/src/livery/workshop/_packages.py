"""The package contracts: discovery, the graph, and the layering lint.

A directory under ``packages/`` is a package exactly when it carries a
``workshop.toml``; everything the workshop knows about a package it
learns there. livery.workshop.verify_workspace is the layering lint:
contracts present, declared edges agreeing with the native manifests
in both directions, the graph acyclic, and the one package-specific
invariant (the forge's stdlib rule) kept.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path

from livery.workshop._contract import load_contract


@dataclass(frozen=True)
class Edge:
    """One declared dependency edge of a package.

    Attributes:
        path: The dependency's identity: its directory path from the
            workspace root, as the tags spell it.
        kind: ``runtime``, ``build``, ``test``, or ``tool``. It
            decides one thing: a ``runtime`` or ``build`` edge must
            appear in the native manifest with a constraint carrying
            its floor, and the other two have no native home. Every
            edge counts everywhere else, whatever its kind: the
            acyclicity check, the dependents' closure the gate
            narrows by, the order the release wave publishes in, and
            the floor bump.
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
    under ``packages/`` lacks its contract, or lacks the
    ``pyproject.toml`` its declared kind requires: a half-present
    package is a wrong state, not a lesser one. An unknown type
    passes discovery so the backend refusal can name the vocabulary.
    """
    from livery.workshop._kinds import requires_pyproject

    problems = []
    packages = []
    packages_dir = root / "packages"
    # A workspace born a moment ago has no members yet; zero packages
    # is a legal answer, not a missing directory.
    if not packages_dir.is_dir():
        return ()
    for directory in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        contract_file = directory / "workshop.toml"
        if not contract_file.is_file():
            problems.append(f"{directory.name}: no workshop.toml")
            continue
        contract = load_contract(contract_file)
        type_name = str(contract.get("type", ""))
        if (
            requires_pyproject(type_name)
            and not (directory / "pyproject.toml").is_file()
        ):
            problems.append(f"{directory.name}: no pyproject.toml")
            continue
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
                type=type_name,
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
      exception is the dev plugin under ``_dev``, which may also
      import livery.footman as footman and toolroom: its only loader is footman's
      ``plugin()``, and only a workshop workspace mounts layers, so
      both are present whenever it loads.
    """
    from livery.workshop._kinds import is_python_kind, kind_for, kind_names

    packages = discover_packages(root)
    by_path = {package.path: package for package in packages}
    names_by_path = {package.path: package.name for package in packages}
    problems: list[str] = []

    for package in packages:
        # The kind's own extractor answers what the package declares
        # natively: pyproject dependencies for a python kind, conan
        # references for a conan one, the union for the extension.
        if package.type not in kind_names():
            known = ", ".join(kind_names())
            problems.append(
                f"{package.path}: type {package.type!r} is not a"
                f" registered kind (kinds: {known}); its edges cannot"
                " be checked"
            )
            continue
        record = kind_for(package.type)
        extractor = getattr(record.backend, "declared_requirements", None)
        if extractor is None:
            problems.append(
                f"{package.path}: kind {record.name!r} registers no"
                " declared_requirements extractor, so the lint cannot"
                " compare its edges; add the callable to the backend"
            )
            continue
        native = extractor(package)
        declared = {edge.path: edge for edge in package.depends}
        for edge in package.depends:
            if edge.path not in by_path:
                problems.append(
                    f"{package.path}: declares an edge on {edge.path},"
                    " which is not a package here"
                )
                continue
            if edge.kind not in ("build", "runtime"):
                continue  # test and tool edges have no native home yet
            dep = by_path[edge.path]
            home = (
                "[project.dependencies]"
                if is_python_kind(dep.type)
                else f'conanfile.py (requires "{dep.name}/[>={edge.floor}]")'
            )
            constraint = native.get(dep.name)
            if constraint is None:
                problems.append(
                    f"{package.path}: the build edge on {edge.path} is not"
                    f" declared in {home} ({dep.name} missing)"
                )
            elif edge.floor and f">={edge.floor}" not in constraint:
                problems.append(
                    f"{package.path}: the edge on {edge.path} floors at"
                    f" {edge.floor}, and the declared requirement says"
                    f" {dep.name}{constraint or ''}"
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

    writable, refused = undeclared_references(packages)
    for package, dependency, kind, floor in writable:
        problems.append(
            f"{package.path}: uses {dependency.name} through a sibling that"
            f" brings it in, with no [[depends]] edge on {dependency.path}."
            f" Declare the {kind} edge at floor {floor}, the one the graph"
            " already carries, and the matching requirement"
        )
    for package, dependency in refused:
        problems.append(
            f"{package.path}: uses {dependency.name}, and nothing it"
            f" depends on brings that in. Declare the edge on"
            f" {dependency.path} and its requirement deliberately: this"
            " one is a new dependency, not a fact the graph already has"
        )
    problems.extend(_cycles(packages))
    problems.extend(_forge_is_stdlib_only(root))
    problems.extend(_terminal_is_asked_through_the_runner(root, packages))
    if problems:
        raise ValueError(
            "the workspace breaks its layering:\n  " + "\n  ".join(problems)
        )
    return packages


@dataclass(frozen=True)
class Neighbours:
    """What a kind needs to judge one package's references to its siblings.

    Attributes:
        owners: Each package's own import prefixes, mapped to its
            path. The kinds answer these; the core collects them, so
            a reference is resolved the same way whoever made it.
        by_path: Every package by its identity path.
    """

    owners: dict[str, str]
    by_path: dict[str, Package]

    def owner_of(self, reference: str) -> str:
        """The package path owning *reference*, longest prefix winning.

        Empty when no package owns it: a third-party name, or the
        standard library. Longest prefix, because three distributions
        share the ``livery.toolroom`` namespace and the shortest match
        would attribute two of them to the third.
        """
        best = ""
        for prefix in self.owners:
            matches = reference == prefix or reference.startswith(prefix + ".")
            if matches and len(prefix) > len(best):
                best = prefix
        return self.owners[best] if best else ""


def neighbours(packages: tuple[Package, ...]) -> Neighbours:
    """The reference map for *packages*, each kind answering for its own."""
    from livery.workshop._kinds import kind_for, kind_names

    owners: dict[str, str] = {}
    for package in packages:
        if package.type not in kind_names():
            continue
        roots = getattr(kind_for(package.type).backend, "module_roots", None)
        if roots is None:
            continue
        for prefix in roots(package):
            owners[prefix] = package.path
    return Neighbours(owners=owners, by_path={p.path: p for p in packages})


def _reachable(by_path: dict[str, Package], start: str) -> set[str]:
    """Every package path *start* reaches through declared edges."""
    seen: set[str] = set()
    queue = [edge.path for edge in by_path[start].depends]
    while queue:
        path = queue.pop()
        if path in seen or path not in by_path:
            continue
        seen.add(path)
        queue += [edge.path for edge in by_path[path].depends]
    return seen


def inherited_floor(by_path: dict[str, Package], reach: set[str], dep: str) -> str:
    """The floor *dep* already carries within *reach*; the highest declared.

    Every declaration inside the reachable set is installed, so the
    highest of them is what resolution already guarantees. That floor
    claims nothing new: raising one is a decision, and the release's
    lowest-direct leg is what proves a floor is high enough.
    """
    floors = [
        edge.floor
        for path in reach
        for edge in by_path[path].depends
        if edge.path == dep and edge.floor
    ]
    if not floors:
        return "0.0.0"
    return max(floors, key=_version_key)


def _version_key(version: str) -> tuple[int, ...]:
    """*version* as comparable integers; a non-numeric part sorts as zero."""
    parts = []
    for piece in version.split("."):
        digits = "".join(takewhile(str.isdigit, piece))
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def undeclared_references(
    packages: tuple[Package, ...],
) -> tuple[list[tuple[Package, Package, str, str]], list[tuple[Package, Package]]]:
    """Sibling references no edge declares, split by what the graph reaches.

    Each kind answers which siblings a package's sources reference and
    which of those its own conventions already explain; this walks the
    graph over the answers. A reference the declared graph already
    reaches can be declared without changing what the graph reaches,
    so it can introduce neither a cycle nor an upward edge, and it is
    returned as (package, dependency, edge kind, floor). Anything else
    is a new dependency for a person to decide, returned as
    (package, dependency).
    """
    from livery.workshop._kinds import kind_for, kind_names

    around = neighbours(packages)
    writable: list[tuple[Package, Package, str, str]] = []
    refused: list[tuple[Package, Package]] = []
    for package in packages:
        if package.type not in kind_names():
            continue
        referenced = getattr(
            kind_for(package.type).backend, "referenced_siblings", None
        )
        if referenced is None:
            continue
        declared = {edge.path for edge in package.depends}
        reach = _reachable(around.by_path, package.path)
        for dep_path, area in sorted(referenced(package, around).items()):
            if dep_path in declared or dep_path not in around.by_path:
                continue
            dependency = around.by_path[dep_path]
            if dep_path in reach:
                kind = "test" if area == "tests" else "runtime"
                floor = inherited_floor(around.by_path, reach, dep_path)
                writable.append((package, dependency, kind, floor))
            else:
                refused.append((package, dependency))
    return writable, refused


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

#: The dev plugin's subtree, the one place in forge that may import
#: footman and toolroom: its only loader is footman's own plugin(),
#: and only a workshop workspace mounts layers, so both are present
#: whenever it loads and livery-forge still declares no dependency.
_FORGE_PLUGIN_DIR = "packages/forge/src/livery/forge/_dev"
_FORGE_PLUGIN_IMPORTS = frozenset({"livery.footman", "livery.toolroom"})


#: The runner's distribution name. It implements the terminal
#: questions, so its own sources are the one place that may ask one.
_RUNNER_DIST = "livery-footman"

#: What a module that imports the runner must never call: a terminal
#: says nothing about ``--no-input`` or ``--dry-run``.
_TERMINAL_CALLS = ("sys.stdin.isatty", "sys.stdout.isatty")


def _imports_the_runner(tree: ast.Module) -> bool:
    """Whether the module imports the runner, in any spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "livery.footman"
                or alias.name.startswith("livery.footman.")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "livery.footman" or node.module.startswith(
                "livery.footman."
            ):
                return True
            if node.module == "livery" and any(
                alias.name == "footman" for alias in node.names
            ):
                return True
    return False


def _terminal_calls(tree: ast.Module) -> list[str]:
    """Every call in the module that asks a terminal directly."""
    bare = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sys"
        for alias in node.names
        if alias.name in ("stdin", "stdout")
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spelling = ast.unparse(node.func)
        named = spelling.endswith(".isatty") and spelling.split(".")[0] in bare
        if spelling in _TERMINAL_CALLS or named:
            found.append(spelling)
    return found


def _terminal_is_asked_through_the_runner(
    root: Path, packages: tuple[Package, ...]
) -> list[str]:
    """Violations of the one-answer rule: ask the runner, never the terminal.

    A module that imports the runner has [livery.footman.attended][]
    in reach, which knows ``--no-input`` and ``--dry-run`` as well as
    the terminal, where asking the terminal ignores both in silence.
    The runner's own sources implement that answer and are exempt, and
    a module that never imports it is the workspace's own business.
    """
    problems: list[str] = []
    sources = [root / "tasks.py"]
    for package in packages:
        if package.name == _RUNNER_DIST:
            continue
        sources.extend(sorted((package.directory / "src").rglob("*.py")))
    for source in sources:
        if not source.is_file():
            continue
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        if not _imports_the_runner(tree):
            continue
        problems.extend(
            f"{source.relative_to(root)} asks the terminal with {spelling}():"
            " a module that imports the runner asks"
            " livery.footman.attended() instead, which knows --no-input"
            " and --dry-run as well"
            for spelling in _terminal_calls(tree)
        )
    return problems


def _forge_is_stdlib_only(root: Path) -> list[str]:
    """Violations of the forge's stdlib-at-import-time rule."""
    stdlib = sys.stdlib_module_names
    allowed = set(stdlib) | {"livery"} | _FORGE_LAZY_EXTRAS
    plugin_dir = root / _FORGE_PLUGIN_DIR
    problems = []
    for source in sorted((root / "packages/forge/src").rglob("*.py")):
        # Under the namespace the top-level name says nothing: the
        # dotted path is judged, livery.forge everywhere and the dev
        # plugin's two tools in its own subtree only.
        livery_ok = {"livery.forge"}
        if source.is_relative_to(plugin_dir):
            livery_ok |= _FORGE_PLUGIN_IMPORTS
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "livery" or node.module.startswith("livery."):
                    names = [f"{node.module}.{alias.name}" for alias in node.names]
                else:
                    names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top == "livery":
                    fine = any(
                        name == ok or name.startswith(ok + ".") for ok in livery_ok
                    )
                else:
                    fine = top in allowed
                if not fine:
                    problems.append(
                        f"{source.relative_to(root)} imports {name!r}:"
                        " livery.forge is stdlib-only at import time"
                    )
    return problems
