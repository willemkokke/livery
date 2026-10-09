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
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path

from livery.workshop._ast_rules import (
    AstRule,
    ParsedModule,
    RuleContext,
    ast_rules,
    parsed_modules,
    parsed_source,
    register_ast_rule,
)
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
            (``packages/forge``, or ``packages/extensions/widgets`` for a
            package in a group directory).
        name: The distribution name (``livery-forge``).
        kind: The package kind, as the contract's ``kind`` declares
            it: ``python``, ``python-nanobind`` or ``cpp-conan``.
        depends: The declared edges, in contract order.
        checks: The options the package sets on its checks, by the
            table that sets them: ``checks.<tool>`` (every check of the
            tool), ``checks.<tool>.<role>`` (one check) and
            ``roles.<role>`` (every check of the role), each to its
            ``(option, value)`` pairs; the check registry validates
            them against what the checks they reach declare.
        categories: The package's own reassignments of its paths
            among the categories, ``[categories] vendored =
            ["docs/assets/vendor/**"]``: a fact about the package in
            the narrow shape a coverage floor has, on the category
            axis alone, winning over every kind's rule.
        publish: Whether the release train uploads the package's
            artifact to its registry. ``[release] publish = false``
            keeps an internal tool versioned, tagged and built by the
            train and never uploaded; the default is true.
        extensions: The package-level extensions the package lists, in
            its contract's order: its canonical list, which the
            extensions it requires complete into its set.
    """

    directory: Path
    path: str
    name: str
    kind: str
    depends: tuple[Edge, ...]
    publish: bool = True
    categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    checks: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = ()
    extensions: tuple[str, ...] = ()

    @property
    def member(self) -> str:
        """The package's path under ``packages/``: ``forge``, ``extensions/widgets``.

        It names the package in a release tag
        (``packages/<member>/v<version>``) and everywhere else a
        package is addressed by its place rather than its
        distribution name.
        """
        return self.path.removeprefix(f"{PACKAGES_DIR}/")


#: The directory every package lives under, relative to the root.
PACKAGES_DIR = "packages"


#: How git sees a directory under ``packages/`` that has no contract.
RESIDUE = "residue"
TRACKED = "tracked"
UNTRACKED = "untracked"
SECRET = "secret"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Leftover:
    """A directory under ``packages/`` with no ``workshop.toml``, as git sees it.

    Git removes a package's tracked files when a checkout moves past
    the package's removal and keeps the files it ignores (bytecode,
    built wheels, coverage pages), so the directory stays behind
    without its contract.

    Attributes:
        directory: The directory.
        state: ``residue`` when git tracks nothing under it and every
            file it holds is one git ignores, none of them a machine
            secret: what a removed package leaves behind, which can go.
            ``tracked`` when git tracks a file there: a package
            missing its contract. ``untracked`` when it holds a file
            git neither tracks nor ignores: work nobody committed yet.
            ``secret`` when an ignored file is a machine secret, which
            no checkout can restore. ``unknown`` when git could not
            answer.
        paths: The files that decide the state, relative to the root:
            the ignored files of residue, else the tracked, untracked
            or secret ones.
        reason: git's own words, when it could not answer.
    """

    directory: Path
    state: str
    paths: tuple[str, ...] = ()
    reason: str = ""


def _ls_files(root: Path, relative: str, *args: str) -> tuple[tuple[str, ...], str]:
    """The files ``git ls-files`` lists under *relative*; git's words if it fails."""
    import livery.toolroom.tools as tools

    result = tools.git.opts(cwd=root, nofail=True, recorded=False)(
        "ls-files", "-z", *args, "--", relative
    )
    if result.code != 0:
        said = (result.stderr or result.stdout).strip()
        return (), said or f"git ls-files exited {result.code}"
    return tuple(sorted(entry for entry in result.stdout.split("\0") if entry)), ""


def leftover(root: Path, directory: Path) -> Leftover:
    """How git sees *directory*, a directory under ``packages/`` with no contract."""
    from livery.workshop._clean import protected_within

    # A pathspec in posix form: git reads a backslash as an escape.
    relative = directory.relative_to(root).as_posix()
    tracked, failed = _ls_files(root, relative)
    if failed:
        return Leftover(directory, UNKNOWN, reason=failed)
    if tracked:
        return Leftover(directory, TRACKED, tracked)
    untracked, failed = _ls_files(root, relative, "--others", "--exclude-standard")
    if failed:
        return Leftover(directory, UNKNOWN, reason=failed)
    if untracked:
        return Leftover(directory, UNTRACKED, untracked)
    secrets = protected_within(root, relative)
    if secrets:
        return Leftover(directory, SECRET, secrets)
    ignored, failed = _ls_files(
        root, relative, "--others", "--ignored", "--exclude-standard"
    )
    if failed:
        return Leftover(directory, UNKNOWN, reason=failed)
    return Leftover(directory, RESIDUE, ignored)


def _no_contract(root: Path, directory: Path) -> str:
    """Discovery's problem for *directory*, which has no contract.

    The classification asks git, which costs a process or three, and
    runs only here, on the way to a refusal.
    """
    member = directory.relative_to(root / PACKAGES_DIR).as_posix()
    if leftover(root, directory).state != RESIDUE:
        return f"{member}: no workshop.toml"
    import livery.footman as footman

    return (
        f"{member}: no workshop.toml, and git tracks nothing under"
        f" {PACKAGES_DIR}/{member}: it holds only ignored files, what a removed"
        f" package leaves behind. `{footman.prog()} sync` removes the directory"
    )


def is_group(directory: Path) -> bool:
    """Whether *directory* under ``packages/`` groups packages rather than being one.

    A group has no ``workshop.toml`` of its own and at least one
    subdirectory that has one. Groups do not nest.
    """
    if (directory / "workshop.toml").is_file():
        return False
    return any((child / "workshop.toml").is_file() for child in directory.iterdir())


def package_directories(root: Path) -> tuple[Path, ...]:
    """Every directory under ``packages/`` that should hold a package, sorted by path.

    A package lives at ``packages/<name>/`` or, inside a group
    directory, at ``packages/<group>/<name>/``. The directories are
    returned whether or not they carry a contract;
    [livery.workshop.discover_packages][] judges them. Reach for
    this where a glob would assume one level.
    """
    packages_dir = root / PACKAGES_DIR
    if not packages_dir.is_dir():
        return ()
    found: list[Path] = []
    for directory in (p for p in packages_dir.iterdir() if p.is_dir()):
        if is_group(directory):
            found.extend(p for p in directory.iterdir() if p.is_dir())
        else:
            found.append(directory)
    return tuple(sorted(found, key=lambda path: path.as_posix()))


_RELEASE_TAG = re.compile(rf"({PACKAGES_DIR}(?:/[^/]+)+)/v(\d+\.\d+\.\d+)")


def release_tag(tag: str) -> tuple[str, str] | None:
    """The package path and version a release *tag* names; None for another tag.

    A release tag is ``packages/<member>/v<major>.<minor>.<patch>``, and
    the member may sit in a group directory:
    ``packages/extensions/widgets/v1.2.0`` names
    ``("packages/extensions/widgets", "1.2.0")``. Every reader of
    release tags parses them here, so a package at any depth under
    ``packages/`` releases like any other.
    """
    match = _RELEASE_TAG.fullmatch(tag)
    return (match.group(1), match.group(2)) if match else None


def receipt_member(tag: str) -> str:
    """The member a release receipt *tag* names, or ``""`` for another tag.

    ``packages/extensions/widgets/v1.2.0`` names ``extensions/widgets``.
    """
    found = release_tag(tag)
    return found[0].removeprefix(f"{PACKAGES_DIR}/") if found else ""


def member_depth(root: Path, relative: Path) -> int:
    """How many leading parts of *relative* name a package's directory.

    2 for ``packages/forge/...``, 3 for ``packages/<group>/<name>/...``
    in a group directory, 0 for a path outside ``packages/`` or one
    that is the package directory itself or above it.
    """
    parts = relative.parts
    if len(parts) <= 2 or parts[0] != PACKAGES_DIR:
        return 0
    group = root / PACKAGES_DIR / parts[1]
    if group.is_dir() and is_group(group):
        return 3 if len(parts) > 3 else 0
    return 2


def member_of(relative: str, members: Iterable[str]) -> str:
    """The member of *members* whose directory holds *relative*, or ``""``.

    *relative* is a posix path from the workspace root
    (``packages/extensions/widgets/src/x.py``); *members* are package
    members as [livery.workshop.Package][] names them
    (``extensions/widgets``). The longest match wins.
    """
    found = ""
    for member in members:
        if relative.startswith(f"{PACKAGES_DIR}/{member}/") and len(member) > len(
            found
        ):
            found = member
    return found


def root_marks(src: Path) -> list[Path]:
    """The files that mark an importable root under *src*, shallowest first.

    A root carries an ``__init__.py``; the topmost on a branch is that
    branch's root. A namespace, one with no ``__init__.py``, marks
    nothing.
    """
    return sorted(src.rglob("__init__.py"), key=lambda path: (len(path.parts), path))


def package_paths(packages: tuple[Package, ...]) -> tuple[str, ...]:
    """The src and tests directories the *packages* own, as they exist.

    The workspace's own tests, a unit whose directory is the tests
    themselves, contribute that directory.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    paths = []
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            if package.directory.is_dir():
                paths.append(package.path)
            continue
        for name in ("src", "tests"):
            directory = package.directory / name
            if directory.is_dir():
                paths.append(f"{package.path}/{name}")
    return tuple(paths)


def discover_packages(root: Path) -> tuple[Package, ...]:
    """Every package the workspace carries, by its contract, sorted by path.

    Raises ValueError, with every finding listed, when a directory
    under ``packages/`` lacks its contract, or lacks the
    ``pyproject.toml`` its declared kind requires: a half-present
    package is a wrong state, not a lesser one. A directory holding
    only what a removed package left behind is named as such, with
    the ``fm sync`` that removes it. An unknown kind
    passes discovery so the backend refusal can name the vocabulary.
    """
    from livery.workshop._kinds import requires_pyproject

    problems = []
    packages = []
    # A workspace born a moment ago has no members yet; zero packages
    # is a legal answer, not a missing directory.
    for directory in package_directories(root):
        member = directory.relative_to(root / PACKAGES_DIR).as_posix()
        contract_file = directory / "workshop.toml"
        if not contract_file.is_file():
            problems.append(_no_contract(root, directory))
            continue
        contract = load_contract(contract_file)
        kind_name = str(contract.get("kind", ""))
        if (
            requires_pyproject(kind_name)
            and not (directory / "pyproject.toml").is_file()
        ):
            problems.append(f"{member}: no pyproject.toml")
            continue
        depends = tuple(
            Edge(
                path=str(edge.get("path", "")),
                kind=str(edge.get("kind", "build")),
                floor=str(edge.get("floor", "")),
            )
            for edge in contract.get("depends", [])
        )
        release = contract.get("release") or {}
        # The contract's judge has held every table below to its shape.
        reassigned = [
            (str(category), tuple(patterns))
            for category, patterns in contract.get("categories", {}).items()
        ]
        options: list[tuple[str, tuple[tuple[str, object], ...]]] = []
        for tool, table in contract.get("checks", {}).items():
            # Scalars are the tool's own options; a table is one of its
            # checks, by role.
            own = tuple(
                (str(key), value)
                for key, value in table.items()
                if not isinstance(value, dict)
            )
            if own or not table:
                options.append((f"checks.{tool}", own))
            options.extend(
                (
                    f"checks.{tool}.{role}",
                    tuple((str(key), value) for key, value in nested.items()),
                )
                for role, nested in table.items()
                if isinstance(nested, dict)
            )
        options.extend(
            (f"roles.{role}", tuple((str(key), value) for key, value in table.items()))
            for role, table in contract.get("roles", {}).items()
        )
        options_by_check = tuple(options)
        publish = bool(release.get("publish", True))
        packages.append(
            Package(
                directory=directory,
                path=f"{PACKAGES_DIR}/{member}",
                name=str(contract.get("name", "")),
                kind=kind_name,
                depends=depends,
                publish=publish,
                categories=tuple(reassigned),
                checks=options_by_check,
                extensions=tuple(str(name) for name in contract.get("extensions", ())),
            )
        )
    if problems:
        raise ValueError(
            "packages missing their contracts:\n  " + "\n  ".join(problems)
        )
    return tuple(packages)


def verify_workspace(root: Path) -> tuple[Package, ...]:
    """The layering lint: check every workspace invariant, or raise.

    The graph's invariants ([livery.workshop._packages.verify_graph][]) and
    the rules over every python source
    ([livery.workshop._packages.verify_imports][]) together, in one refusal.
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
      ``plugin()``, and only a workshop workspace mounts extensions, so
      both are present whenever it loads.
    """
    packages = discover_packages(root)
    problems = graph_problems(root, packages) + import_problems(root, packages)
    if problems:
        raise ValueError(
            "the workspace breaks its layering:\n  " + "\n  ".join(problems)
        )
    return packages


def verify_graph(root: Path) -> tuple[Package, ...]:
    """The graph half of the layering lint: the contracts and the native manifests.

    Each edge agrees with its native manifest, the graph is acyclic,
    the extensions a contract lists close over what they require, each
    contract's check options name what exists, and the root is no
    package. What it reads is the contracts and the manifests, so a run
    whose change touched none of them skips it. Returns the discovered
    packages; raises ValueError listing every violation otherwise.
    """
    packages = discover_packages(root)
    problems = graph_problems(root, packages)
    if problems:
        raise ValueError(
            "the workspace breaks its layering:\n  " + "\n  ".join(problems)
        )
    return packages


def verify_imports(
    root: Path, files: frozenset[str] | None = None
) -> tuple[Package, ...]:
    """The rules over the python sources: each module's imports and references.

    *files* names the root-relative sources in scope; None judges
    every one. A rule over a module judges the named ones, and a rule
    over a package's references judges each package holding one.
    Returns the discovered packages; raises ValueError listing every
    violation otherwise.
    """
    packages = discover_packages(root)
    problems = import_problems(root, packages, files)
    if problems:
        raise ValueError(
            "the workspace breaks its layering:\n  " + "\n  ".join(problems)
        )
    return packages


def import_problems(
    root: Path, packages: tuple[Package, ...], files: frozenset[str] | None = None
) -> list[str]:
    """Every registered rule's findings over the sources in scope, by rule name."""
    # The rules over the sources, builtin and registered alike, read
    # the one parse; each problem carries its rule's name.
    context = RuleContext(root=root, packages=packages, files=files)
    modules = parsed_modules(root, packages, files)
    problems: list[str] = []
    for rule in ast_rules():
        problems.extend(f"{rule.name}: {line}" for line in rule.judge(modules, context))
    return problems


def graph_problems(root: Path, packages: tuple[Package, ...]) -> list[str]:
    """Every violation of the graph's invariants, verbatim; empty when they hold."""
    from livery.workshop import _lifecycle
    from livery.workshop._kinds import is_python_kind, kind_names

    by_path = {package.path: package for package in packages}
    names_by_path = {package.path: package.name for package in packages}
    problems: list[str] = []

    for package in packages:
        # The kind's own extractor answers what the package declares
        # natively: pyproject dependencies for a python kind, conan
        # references for a conan one, the union for the extension.
        if package.kind not in kind_names():
            known = ", ".join(kind_names())
            problems.append(
                f"{package.path}: kind {package.kind!r} is not a"
                f" registered kind (kinds: {known}); its edges cannot"
                " be checked"
            )
            continue
        native = _lifecycle.declared_requirements(package)
        if native is None:
            problems.append(
                f"{package.path}: kind {package.kind!r} registers no"
                " declared_requirements extractor, so the lint cannot"
                " compare its edges; add the callable to the backend"
            )
            continue
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
                if is_python_kind(dep.kind)
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

    problems.extend(_cycles(packages))
    from livery.workshop._extensions import closure_problems

    problems.extend(closure_problems(root))
    from livery.workshop._checks import option_problems

    problems.extend(option_problems(packages))
    if (root / "src").is_dir():
        problems.append(
            "src/ at the workspace root is not a member: the root is never a"
            " package, so nothing claims, formats, lints or tests it; a"
            " package lives under packages/<name>/, one directory per"
            " package, and a project shipping one package has one"
        )
    return problems


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
    from livery.workshop import _lifecycle
    from livery.workshop._kinds import kind_names

    owners: dict[str, str] = {}
    for package in packages:
        if package.kind not in kind_names():
            continue
        for prefix in _lifecycle.module_roots(package):
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
    packages: tuple[Package, ...], *, only: set[str] | None = None
) -> tuple[list[tuple[Package, Package, str, str]], list[tuple[Package, Package]]]:
    """Sibling references no edge declares, split by what the graph reaches.

    Each kind answers which siblings a package's sources reference and
    which of those its own conventions already explain; this walks the
    graph over the answers. A reference the declared graph already
    reaches can be declared without changing what the graph reaches,
    so it can introduce neither a cycle nor an upward edge, and it is
    returned as (package, dependency, edge kind, floor). Anything else
    is a new dependency for a person to decide, returned as
    (package, dependency). *only* judges the packages at those paths
    and walks the graph over every one.
    """
    from livery.workshop import _lifecycle
    from livery.workshop._kinds import kind_names

    around = neighbours(packages)
    writable: list[tuple[Package, Package, str, str]] = []
    refused: list[tuple[Package, Package]] = []
    for package in packages:
        if package.kind not in kind_names():
            continue
        if only is not None and package.path not in only:
            continue
        referenced = _lifecycle.referenced_siblings(package, around)
        if referenced is None:
            continue
        declared = {edge.path for edge in package.depends}
        reach = _reachable(around.by_path, package.path)
        for dep_path, area in sorted(referenced.items()):
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


def declare_edge(
    package: Package, dependency: Package, *, kind: str, floor: str
) -> list[str]:
    """Declare *package*'s *kind* edge on *dependency* at *floor*; the files written.

    The ``[[depends]]`` edge joins the package's contract, and for a
    build or runtime edge the package's kind writes the native
    requirement at the same floor, so the layering check finds the
    two in agreement. The files are relative to the package.
    """
    from livery.workshop import _lifecycle

    contract = package.directory / "workshop.toml"
    text = contract.read_text("utf-8")
    edge = (
        f'\n[[depends]]\npath = "{dependency.path}"\nkind = "{kind}"\n'
        f'floor = "{floor}"\n'
    )
    contract.write_text(text.rstrip("\n") + "\n" + edge, encoding="utf-8")
    files = ["workshop.toml"]
    if kind in ("build", "runtime"):
        files += _lifecycle.declare_requirement(package, dependency, floor)
    return files


def write_edges(root: Path) -> list[str]:
    """Declare every sibling reference the graph already reaches; the lines written.

    The layering check's fix mode. For each reference
    [livery.workshop._packages.undeclared_references][] can write, the
    ``[[depends]]`` edge joins the package's contract and the kind
    writes the native requirement, both at the floor the graph
    carries. A reference the graph does not reach is left for the
    judge that follows: it is a new dependency, and a decision.
    Idempotent: a second call writes nothing.
    """
    packages = discover_packages(root)
    written: list[str] = []
    for package, dependency, kind, floor in undeclared_references(packages)[0]:
        files = declare_edge(package, dependency, kind=kind, floor=floor)
        written.append(
            f"  layering: {package.path} declares the {kind} edge on"
            f" {dependency.path} at floor {floor} ({', '.join(files)})"
        )
    return written


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

#: The distribution the stdlib-at-import-time rule is about. The whole
#: ecosystem stands on it being installable with nothing behind it.
_FORGE_DIST = "livery-forge"


#: The runner's distribution name. It implements the terminal
#: questions, so its own sources are the one place that may ask one.
_RUNNER_DIST = "livery-footman"

#: What a module that imports the runner must never call: a terminal
#: says nothing about ``--no-input`` or ``--dry-run``.
_TERMINAL_CALLS = (
    "sys.stdin.isatty",
    "sys.stdout.isatty",
    "sys.stderr.isatty",
    "os.isatty",
)


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
        if alias.name in ("stdin", "stdout", "stderr")
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
    root: Path, packages: tuple[Package, ...], context: RuleContext | None = None
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
        if context is not None and not context.in_scope(source):
            continue
        parsed = parsed_source(source)
        if parsed is None or not _imports_the_runner(parsed.tree):
            continue
        problems.extend(
            f"{source.relative_to(root)} asks the terminal with {spelling}():"
            " a module that imports the runner asks"
            " livery.footman.attended() instead, which knows --no-input"
            " and --dry-run as well"
            for spelling in _terminal_calls(parsed.tree)
        )
    return problems


def _forge_is_stdlib_only(
    root: Path, packages: tuple[Package, ...], context: RuleContext | None = None
) -> list[str]:
    """Violations of the forge's stdlib-at-import-time rule.

    A module the package declares as a footman task entry point is
    exempt: its only loader is footman's own ``plugin()``, so the
    runner is present by construction and the mounted extensions with it,
    and the distribution still declares no dependency on either. The
    entry point is where that fact already lives, so nothing here
    repeats the module's path.
    """
    stdlib = sys.stdlib_module_names
    allowed = set(stdlib) | {"livery"} | _FORGE_LAZY_EXTRAS
    from livery.workshop import _lifecycle
    from livery.workshop._kinds import kind_names

    forge = next((p for p in packages if p.name == _FORGE_DIST), None)
    if forge is None or forge.kind not in kind_names():
        return []
    # The same reader the kind's own reference check uses, so one
    # answer about what a package declares serves both.
    plugins = _lifecycle.plugin_modules(forge)
    base = forge.directory / "src"
    problems = []
    for source in sorted(base.rglob("*.py")):
        # Under the namespace the top-level name says nothing: the
        # dotted path is judged, livery.forge everywhere and anything
        # the runner brings inside a declared plugin module.
        dotted = ".".join(source.relative_to(base).parts)
        dotted = dotted.removesuffix(".py").removesuffix(".__init__")
        if context is not None and not context.in_scope(source):
            continue
        livery_ok = {"livery.forge"}
        if any(
            dotted == module or dotted.startswith(module + ".") for module in plugins
        ):
            livery_ok = {"livery"}
        parsed = parsed_source(source)
        if parsed is None:
            continue
        for node in ast.walk(parsed.tree):
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


def _runner_terminal(
    modules: tuple[ParsedModule, ...], context: RuleContext
) -> list[str]:
    """The one-answer rule as a registered rule; its own walk reads the shared parse."""
    del modules
    return _terminal_is_asked_through_the_runner(
        context.root, context.packages, context
    )


def _forge_stdlib(modules: tuple[ParsedModule, ...], context: RuleContext) -> list[str]:
    """The forge's stdlib rule as a registered rule."""
    del modules
    return _forge_is_stdlib_only(context.root, context.packages, context)


def _sibling_references(
    modules: tuple[ParsedModule, ...], context: RuleContext
) -> list[str]:
    """The undeclared sibling references, by what the graph reaches.

    A package's references come from all of its sources, so a package
    holding a source in scope is judged whole, and one holding none
    keeps its verdict.
    """
    del modules
    problems: list[str] = []
    judged = {package.path for package in context.packages_in_scope()}
    writable, refused = undeclared_references(context.packages, only=judged)
    for package, dependency, kind, floor in writable:
        problems.append(
            f"{package.path}: uses {dependency.name} through a sibling that"
            f" brings it in, with no [[depends]] edge on {dependency.path}."
            f" Declare the {kind} edge at floor {floor}, the one the graph"
            " already carries, and the matching requirement; the gate's"
            " --fix writes both"
        )
    for package, dependency in refused:
        problems.append(
            f"{package.path}: uses {dependency.name}, and nothing it"
            f" depends on brings that in. Declare the edge on"
            f" {dependency.path} and its requirement deliberately: this"
            " one is a new dependency, not a fact the graph already has"
        )
    return problems


def _write_edges_fix(
    modules: tuple[ParsedModule, ...], context: RuleContext
) -> list[str]:
    """The sibling rule's fix: declare what the graph already reaches."""
    del modules
    return write_edges(context.root)


#: The base's own import path, and the namespace extensions live under.
BASE_MODULE = "livery.workshop"
EXTENSIONS_NAMESPACE = "livery.extensions"


def extension_imports_in_the_base(modules: tuple[ParsedModule, ...]) -> list[str]:
    """Each base module that imports an extension, with the extension named.

    The base (``livery.workshop``) imports no extension under
    ``livery.extensions``: what an extension needs of the base it takes through
    the base's seams, and the base reaches an extension only through the
    registries the extension fills at mount.
    """
    problems: list[str] = []
    for module in modules:
        if not module.dotted.startswith(BASE_MODULE + "."):
            continue
        for imported in module.imports:
            if imported == EXTENSIONS_NAMESPACE or imported.startswith(
                EXTENSIONS_NAMESPACE + "."
            ):
                problems.append(
                    f"{module.relative}: the base imports the extension {imported}; the"
                    " base reaches an extension through a registry it fills at mount,"
                    " never by import"
                )
    return problems


def _base_imports_no_extension(
    modules: tuple[ParsedModule, ...], context: RuleContext
) -> list[str]:
    """The base-imports-no-extension rule."""
    del context
    return extension_imports_in_the_base(modules)


# The builtin rules, registered at import the way the builtin checks
# and kinds are; an extension registers its own beside them.
register_ast_rule(AstRule("runner-terminal", _runner_terminal))
register_ast_rule(AstRule("base-imports-no-extension", _base_imports_no_extension))
register_ast_rule(AstRule("forge-stdlib-only", _forge_stdlib))
register_ast_rule(
    AstRule("sibling-references", _sibling_references, fix=_write_edges_fix)
)
