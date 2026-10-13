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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path
from typing import Any

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
        step_options: The options the package sets on its steps, by
            the table that sets them: ``phases.<phase>`` (every step of
            the phase) and ``phases.<phase>.<step>`` (one step), each to
            its ``(option, value)`` pairs; the step registry validates
            them against what the steps they reach declare.
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
    step_options: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = ()
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
    from livery import git

    result = git.call(root, "ls-files", "-z", *args, "--", relative)
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


#: The workspace's own test directory, a unit of the gate like a
#: package's suite. Its tests reach any package, so every run of a test
#: step runs them, and a change under it affects that unit alone.
WORKSPACE_TESTS = "tests"


def workspace_suite(root: Path) -> Package | None:
    """The workspace's own tests, the ``tests`` directory at its root, as a unit.

    ``None`` when the workspace has none. The unit's kind,
    ``workspace``, is no registered kind, so no step judges it by
    kind; a test step runs it beside the members it judges.
    """
    directory = root / WORKSPACE_TESTS
    if not directory.is_dir():
        return None
    return Package(
        directory=directory,
        path=WORKSPACE_TESTS,
        name="workspace-tests",
        kind="workspace",
        depends=(),
    )


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


def removed_option_tables(contract: dict[str, Any]) -> list[str]:
    """Each ``[checks]`` or ``[roles]`` table of a package *contract*, one line each.

    The check tables' spelling of a step's options: each line names
    the table and the step spelling that says it, ``[phases.<phase>]``
    for every step of a phase and ``[phases.<phase>.<step>]`` for one.
    Read before the contract's judge, which would refuse the table as
    unknown without naming what replaced it.
    """
    problems: list[str] = []
    checks = contract.get("checks")
    if "checks" in contract and not isinstance(checks, dict):
        problems.append(
            "[checks] is the check tables' spelling; a step's options are"
            " [phases.<phase>.<step>]"
        )
    steps: dict[str, Any] = checks if isinstance(checks, dict) else {}
    for step, table in steps.items():
        # [checks.<step>.<phase>] is one step's table; a scalar or an
        # empty table under [checks.<step>] is the step's in every phase.
        nested = (
            [str(phase) for phase, value in table.items() if isinstance(value, dict)]
            if isinstance(table, dict)
            else []
        )
        problems += [
            f"[checks.{step}.{phase}] is the check tables' spelling; the step's"
            f" options are [phases.{phase}.{step}]"
            for phase in nested
        ]
        if not isinstance(table, dict) or len(nested) < max(len(table), 1):
            problems.append(
                f"[checks.{step}] is the check tables' spelling; a step's options"
                f" are [phases.<phase>.{step}], and [phases.<phase>] reaches"
                " every step of the phase"
            )
    phases = contract.get("roles")
    if "roles" in contract and not isinstance(phases, dict):
        problems.append(
            "[roles] is the check tables' spelling; every step of a phase takes"
            " [phases.<phase>]"
        )
    named: dict[str, Any] = phases if isinstance(phases, dict) else {}
    problems += [
        f"[roles.{phase}] is the check tables' spelling; every step of the phase"
        f" takes [phases.{phase}]"
        for phase in named
    ]
    return problems


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
        for phase, table in contract.get("phases", {}).items():
            # Scalars are the phase's own options, for every step of
            # the phase; a table is one step's.
            own = tuple(
                (str(key), value)
                for key, value in table.items()
                if not isinstance(value, dict)
            )
            if own or not table:
                options.append((f"phases.{phase}", own))
            options.extend(
                (
                    f"phases.{phase}.{step}",
                    tuple((str(key), value) for key, value in nested.items()),
                )
                for step, nested in table.items()
                if isinstance(nested, dict)
            )
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
                step_options=tuple(options),
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
    the base's rules over every python source
    ([livery.workshop._packages.import_problems][]) together, in one refusal.
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
    contract's step options name what exists, and the root is no
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


@dataclass(frozen=True)
class RuleContext:
    """What a rule of the layering phase judges: the workspace and its sources.

    Attributes:
        root: The workspace root.
        packages: Every package the workspace carries.
        files: The root-relative sources the run judges; None for every
            one. A rule over the sources keeps to these, and a rule over
            a package's references judges each package holding one
            (`packages_in_scope`).
    """

    root: Path
    packages: tuple[Package, ...]
    files: frozenset[str] | None = None

    def in_scope(self, path: Path) -> bool:
        """Whether *path*, absolute, is a source this run judges."""
        if self.files is None:
            return True
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        return relative in self.files

    def packages_in_scope(self) -> tuple[Package, ...]:
        """The packages holding a source this run judges, every one without a scope."""
        if self.files is None:
            return self.packages
        files = self.files
        return tuple(
            package
            for package in self.packages
            if any(path.startswith(f"{package.path}/") for path in files)
        )

    def sources(self) -> tuple[SourceReferences, ...]:
        """What each source this run judges references, one record per source.

        Read from the ``references`` answer of each package in scope,
        in package order; a package whose kind is not registered is
        left out, since ``layering.graph`` names it.
        """
        from livery.workshop import _lifecycle
        from livery.workshop._kinds import kind_names

        known = kind_names()
        return tuple(
            source
            for package in self.packages_in_scope()
            if package.kind in known
            for source in _lifecycle.references(package)
            if self.files is None or source.path in self.files
        )


#: A rule's judge: the context, to the problems, empty when the rule
#: holds.
Judge = Callable[[RuleContext], list[str]]


def import_problems(
    root: Path, packages: tuple[Package, ...], files: frozenset[str] | None = None
) -> list[str]:
    """Every base rule's findings over the sources in scope, each by its step's name."""
    return [
        f"{step}: {line}"
        for step in RULES
        for line in rule_problems(step, root, packages, files)
    ]


def rule_problems(
    step: str,
    root: Path,
    packages: tuple[Package, ...],
    files: frozenset[str] | None = None,
) -> list[str]:
    """What the base's layering step *step* finds over the sources in scope.

    *files* names the root-relative sources a narrowed run judges;
    None judges every one. A rule over the sources judges the named
    ones, and a rule over a package's references judges each package
    holding one.
    """
    return RULES[step](RuleContext(root=root, packages=packages, files=files))


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
    from livery.workshop._steps import option_problems

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
class ReferencedName:
    """One name a source references, and how the source makes the reference.

    Attributes:
        name: The dotted name, as the source spells it.
        lazy: Whether only a function or a method body makes the
            reference, so importing the source does not.
        type_checking: Whether only a type checker makes it, so no run
            of the source does.
        optional: Whether the source handles the name being absent, so
            it runs without it.
    """

    name: str
    lazy: bool = False
    type_checking: bool = False
    optional: bool = False


@dataclass(frozen=True)
class SourceReferences:
    """What one source references: one record of a package's ``references`` answer.

    Attributes:
        path: The source, relative to the workspace root, posix: a file
            inside the package the answer is about.
        module: The name other code references the source by; empty for
            a source nothing references by name, such as a test.
        references: Every name the source references, in source order,
            once for each way the source makes the reference.
        accounted: The names among them that the conventions of the
            source's language account for, so they need no dependency
            edge.
    """

    path: str
    module: str = ""
    references: tuple[ReferencedName, ...] = ()
    accounted: tuple[str, ...] = ()


@dataclass(frozen=True)
class Neighbours:
    """What resolving one package's references to its siblings needs.

    Attributes:
        owners: Each package's own import prefixes, mapped to its
            path. The packages' extensions or kinds answer these; the
            core collects them, so a reference is resolved the same way
            whoever made it.
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

    Each package's ``references`` answer says what its sources
    reference and which names its language's conventions account for;
    this resolves each other name to the sibling that owns it and walks
    the graph over them. A reference the declared graph already
    reaches can be declared without changing what the graph reaches,
    so it can introduce neither a cycle nor an upward edge, and it is
    returned as (package, dependency, edge kind, floor). Anything else
    is a new dependency for a person to decide, returned as
    (package, dependency). *only* judges the packages at those paths
    and walks the graph over every one.
    """
    from livery.workshop._kinds import kind_names

    around = neighbours(packages)
    writable: list[tuple[Package, Package, str, str]] = []
    refused: list[tuple[Package, Package]] = []
    for package in packages:
        if package.kind not in kind_names():
            continue
        if only is not None and package.path not in only:
            continue
        referenced = _referenced_siblings(package, around)
        declared = {edge.path for edge in package.depends}
        reach = _reachable(around.by_path, package.path)
        for dep_path, kind in sorted(referenced.items()):
            if dep_path in declared or dep_path not in around.by_path:
                continue
            dependency = around.by_path[dep_path]
            if dep_path in reach:
                floor = inherited_floor(around.by_path, reach, dep_path)
                writable.append((package, dependency, kind, floor))
            else:
                refused.append((package, dependency))
    return writable, refused


def _referenced_siblings(package: Package, around: Neighbours) -> dict[str, str]:
    """The siblings *package*'s sources reference that nothing accounts for.

    Each to the edge it asks for. A name the source runs without, or
    one the conventions of its language account for, needs no edge. A
    sibling that only test and test-support files reference, by their
    category, asks for a ``test`` edge; one that any other file
    references asks for a ``runtime`` edge.
    """
    from livery.workshop import _lifecycle
    from livery.workshop._categories import TEST, TEST_SUPPORT, category_of

    found: dict[str, str] = {}
    for source in _lifecycle.references(package):
        accounted = set(source.accounted)
        owners = {
            owner
            for reference in source.references
            if not reference.optional and reference.name not in accounted
            if (owner := around.owner_of(reference.name)) not in ("", package.path)
        }
        if not owners:
            continue
        relative = source.path.removeprefix(f"{package.path}/")
        tested = category_of(package, relative).name in (TEST, TEST_SUPPORT)
        for owner in owners:
            if found.get(owner) != "runtime":
                found[owner] = "test" if tested else "runtime"
    return found


def declare_edge(
    package: Package, dependency: Package, *, kind: str, floor: str
) -> list[str]:
    """Declare *package*'s *kind* edge on *dependency* at *floor*; the files written.

    The ``[[depends]]`` edge joins the package's contract, and for a
    build or runtime edge the package's kind writes the native
    requirement at the same floor, so ``layering.graph`` finds the
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

    The fix of the layering phase's sibling-references step. For each reference
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
    from livery.extensions.python._parse import parsed_source

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
    from livery.extensions.python._parse import parsed_source
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


def _runner_terminal(context: RuleContext) -> list[str]:
    """The one-answer rule as a registered rule, over the python parse."""
    return _terminal_is_asked_through_the_runner(
        context.root, context.packages, context
    )


def _forge_stdlib(context: RuleContext) -> list[str]:
    """The forge's stdlib rule as a registered rule, over the python parse."""
    return _forge_is_stdlib_only(context.root, context.packages, context)


def _sibling_references(context: RuleContext) -> list[str]:
    """The undeclared sibling references, by what the graph reaches.

    A package's references come from all of its sources, so a package
    holding a source in scope is judged whole, and one holding none
    keeps its verdict.
    """
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


#: The base's own import path, and the namespace extensions live under.
BASE_MODULE = "livery.workshop"
EXTENSIONS_NAMESPACE = "livery.extensions"


#: The base modules that import an extension: each module and the
#: extension it imports, to why. The package kinds' code is the
#: extensions', and the base takes it as the kinds' backends while the
#: kinds live in the base; the runner's one-answer rule and the forge's
#: stdlib rule walk the python extension's parse while the base holds
#: them. A test keeps the list exact, and it is empty once the package
#: kinds and those two rules leave the base.
BASE_EXTENSION_IMPORTS: dict[tuple[str, str], str] = {
    ("livery.workshop._kinds", "livery.extensions.python"): (
        "registers the python kind with the extension's code as its backend"
    ),
    ("livery.workshop._packages", "livery.extensions.python"): (
        "the runner's one-answer rule and the forge's stdlib rule walk the"
        " syntax trees of the python extension's parse"
    ),
    ("livery.workshop._backends._python_nanobind", "livery.extensions.python"): (
        "the nanobind kind builds on the python kind's functions"
    ),
    ("livery.workshop._backends._cpp_conan", "livery.extensions.cmake"): (
        "the cpp-conan kind's gate build and tests are cmake's code"
    ),
    ("livery.workshop._backends._cpp_conan", "livery.extensions.conan"): (
        "the cpp-conan kind's recipe and release are conan's code"
    ),
    ("livery.workshop._backends._cpp_conan", "livery.extensions.cpp"): (
        "the cpp-conan kind's file conventions are cpp's code"
    ),
}


def allowed_extension_import(module: str, imported: str) -> bool:
    """Whether the base module *module* may import *imported*, an extension's module.

    True when [livery.workshop._packages.BASE_EXTENSION_IMPORTS][] names
    the pair: the extension itself, or a module under it.
    """
    return any(
        imported == extension or imported.startswith(extension + ".")
        for base, extension in BASE_EXTENSION_IMPORTS
        if base == module
    )


def extension_imports_in_the_base(sources: Iterable[SourceReferences]) -> list[str]:
    """Each base module that imports an extension, with the extension named.

    The base (``livery.workshop``) imports no extension under
    ``livery.extensions``: what an extension needs of the base it takes through
    the base's seams, and the base reaches an extension only through the
    registries the extension fills at mount. The one exception is
    [livery.workshop._packages.BASE_EXTENSION_IMPORTS][]: a module it names may
    import the extensions it names beside it, and nothing else under the
    namespace. *sources* are records of the ``references`` answers; a
    module that runs without the name it imports is left alone, and
    one importing it lazily or for a type checker is judged.
    """
    problems: list[str] = []
    for source in sources:
        if not source.module.startswith(BASE_MODULE + "."):
            continue
        for reference in source.references:
            imported = reference.name
            if reference.optional or allowed_extension_import(source.module, imported):
                continue
            if imported == EXTENSIONS_NAMESPACE or imported.startswith(
                EXTENSIONS_NAMESPACE + "."
            ):
                problems.append(
                    f"{source.path}: the base imports the extension {imported}; the"
                    " base reaches an extension through a registry it fills at mount,"
                    " never by import"
                )
    return problems


def _base_imports_no_extension(context: RuleContext) -> list[str]:
    """The base-imports-no-extension rule, over the ``references`` answers in scope."""
    return extension_imports_in_the_base(context.sources())


#: The base's rules over the sources, each the layering step of its
#: name, which [livery.workshop._steps][] registers beside the base's
#: other steps: the runner's one-answer rule and the forge's stdlib
#: rule over the python extension's parse, and the base importing no
#: extension and the sibling references over the ``references``
#: answers. The sibling step's fix writes the edges
#: ([livery.workshop._packages.write_edges][]).
RULES: dict[str, Judge] = {
    "runner-terminal": _runner_terminal,
    "base-imports-no-extension": _base_imports_no_extension,
    "forge-stdlib-only": _forge_stdlib,
    "sibling-references": _sibling_references,
}
