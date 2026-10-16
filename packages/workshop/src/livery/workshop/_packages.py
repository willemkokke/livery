"""The package contracts: discovery, the graph, and the layering lint.

A directory under ``packages/`` is a package exactly when it carries a
``workshop.toml``; everything the workshop knows about a package it
learns there. livery.workshop.verify_workspace is the layering lint:
contracts present, declared edges agreeing with the native manifests
in both directions, the graph acyclic, the base importing no
extension, and each reference to a sibling declared as an edge.

`holds_runtime_language` says whether a package holds the language the
workshop runs on, `RUNTIME_LANGUAGE`; a member that does is a
distribution of that language, and discovery requires its
``pyproject.toml``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import takewhile
from pathlib import Path, PurePosixPath

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
            axis alone, winning over every extension's rule.
        publish: Whether the release train uploads the package's
            artifact to its registry. ``[release] publish = false``
            keeps an internal tool versioned, tagged and built by the
            train and never uploaded; the default is true.
        extensions: The extensions the package lists, in its contract's
            order: its canonical list, which the extensions it requires
            complete into its set.
    """

    directory: Path
    path: str
    name: str
    depends: tuple[Edge, ...]
    publish: bool = True
    categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Left out of the hash, so a package stays a set member and a key:
    # an option's value may be a list, a strs option's, or anything a
    # contract holds before the registry judges it.
    step_options: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = field(
        default=(), hash=False
    )
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


def root_of(package: Package) -> Path:
    """The root of the workspace *package* is a member of.

    The package's directory with its path's parts taken off: the root
    is two directories above ``packages/forge``, and three above
    ``packages/extensions/widgets``.
    """
    return package.directory.parents[len(PurePosixPath(package.path).parts) - 1]


#: The workspace's own test directory, a unit of the gate like a
#: package's suite. It holds the root's set. Its tests reach any
#: package, so every run of a test step that judges them runs them, and
#: a change under it affects that unit alone.
WORKSPACE_TESTS = "tests"


def workspace_suite(root: Path) -> Package | None:
    """The workspace's own tests, the ``tests`` directory at its root, as a unit.

    ``None`` when the workspace has none. The unit lists no extension
    and holds the root's set. A test step whose extensions that set
    holds runs it beside the members it judges
    ([livery.workshop.scoped_suite][]). It is no member, so no package
    step judges it, and the workspace's category table answers for its
    files (`is_workspace_unit`).
    """
    directory = root / WORKSPACE_TESTS
    if not directory.is_dir():
        return None
    return Package(
        directory=directory,
        path=WORKSPACE_TESTS,
        name="workspace-tests",
        depends=(),
    )


def is_workspace_unit(package: Package) -> bool:
    """Whether *package* is the workspace's own unit rather than a member.

    The workspace's tests (`workspace_suite`) are its unit, and a root
    without them is its own unit, for the files under no package. A
    member's path is under ``packages/``, and a unit's is not.
    """
    return not package.path.startswith(f"{PACKAGES_DIR}/")


#: The extension of the language the workshop runs on. A member
#: holding it is a distribution of that language: discovery requires its
#: project file, and it joins the uv workspace the venv is made from.
RUNTIME_LANGUAGE = "python"


def holds_runtime_language(package: Package) -> bool:
    """Say whether *package* holds the extension of the workshop's own language.

    That extension is ``python``. A member holds it when it lists it,
    or when an extension it lists requires it. The workspace's own
    tests hold it when the root's set does. Discovery asks this of
    every member, so a member that lists it is answered without
    reading any extension's declaration.
    """
    if RUNTIME_LANGUAGE in package.extensions:
        return True
    from livery.workshop._composition import extension_set

    return RUNTIME_LANGUAGE in extension_set(package)


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


def discover_packages(root: Path) -> tuple[Package, ...]:
    """Every package the workspace carries, by its contract, sorted by path.

    Raises ValueError, with every finding listed, when a directory
    under ``packages/`` lacks its contract, or holds the runtime's
    language (`holds_runtime_language`) and lacks the
    ``pyproject.toml`` a distribution of that language carries: a
    half-present package is a wrong state, not a lesser one. A
    directory holding only what a removed package left behind is
    named as such, with the ``fm sync`` that removes it. A package
    that lists no extension holds none, and needs no project file.
    """
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
        package = Package(
            directory=directory,
            path=f"{PACKAGES_DIR}/{member}",
            name=str(contract.get("name", "")),
            depends=depends,
            publish=publish,
            categories=tuple(reassigned),
            step_options=tuple(options),
            extensions=tuple(str(name) for name in contract.get("extensions", ())),
        )
        if (
            holds_runtime_language(package)
            and not (directory / "pyproject.toml").is_file()
        ):
            problems.append(
                f"{member}: holds {RUNTIME_LANGUAGE} and has no pyproject.toml"
            )
            continue
        packages.append(package)
    if problems:
        raise ValueError(
            "packages missing a file they need:\n  " + "\n  ".join(problems)
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
    - the base imports no extension, and each reference a package's
      sources make to a sibling is a declared edge.
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
        in package order.
        """
        from livery.workshop import _lifecycle

        return tuple(
            source
            for package in self.packages_in_scope()
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
    from livery.workshop._composition import extension_set

    by_path = {package.path: package for package in packages}
    names_by_path = {package.path: package.name for package in packages}
    problems: list[str] = []

    for package in packages:
        # The set's answers say what the package declares natively:
        # python's the pyproject's dependencies, conan's the recipe's
        # references, combined.
        native = _lifecycle.declared_requirements(package)
        tested = _lifecycle.declared_test_requirements(package)
        declared = {edge.path: edge for edge in package.depends}
        for edge in package.depends:
            if edge.path not in by_path:
                problems.append(
                    f"{package.path}: declares an edge on {edge.path},"
                    " which is not a package here"
                )
                continue
            if edge.kind == "test":
                if tested is not None:
                    problems.extend(_test_problems(package, edge, by_path, tested))
                continue
            if edge.kind not in ("build", "runtime"):
                continue  # a tool edge has no native home
            dep = by_path[edge.path]
            constraint = native.get(dep.name)
            if constraint is None:
                # The sibling's set says which ecosystem's manifest the
                # requirement belongs in: a writer takes its own alone.
                held = ", ".join(extension_set(dep)) or "no extension"
                problems.append(
                    f"{package.path}: the {edge.kind} edge on {edge.path} is not"
                    f" declared in the package's manifests ({dep.name} missing;"
                    f" {edge.path} holds {held})"
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
            path. The packages' extensions answer these; the core
            collects them, so a reference is resolved the same way
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
    """The reference map for *packages*, each package's set answering for its own."""
    from livery.workshop import _lifecycle

    owners: dict[str, str] = {}
    for package in packages:
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
    around = neighbours(packages)
    writable: list[tuple[Package, Package, str, str]] = []
    refused: list[tuple[Package, Package]] = []
    for package in packages:
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

    For a build, runtime or test edge, the writers of the package's set
    first write the native requirement at the same floor
    (``_lifecycle.declare_requirement``), so ``layering.graph`` finds
    the two in agreement; then the ``[[depends]]`` edge joins the
    package's contract. A sibling no writer takes refuses before the
    contract changes. A test edge in a set with no writer of a test
    requirement, and a tool edge, write no requirement. The files are
    relative to the package, the contract first.
    """
    from livery.workshop import _lifecycle

    required = (
        _lifecycle.declare_requirement(package, dependency, floor, kind=kind)
        if kind != "tool"
        else []
    )
    contract = package.directory / "workshop.toml"
    text = contract.read_text("utf-8")
    edge = (
        f'\n[[depends]]\npath = "{dependency.path}"\nkind = "{kind}"\n'
        f'floor = "{floor}"\n'
    )
    contract.write_text(text.rstrip("\n") + "\n" + edge, encoding="utf-8")
    return ["workshop.toml", *required]


def write_edges(root: Path) -> list[str]:
    """Declare every sibling reference the graph already reaches; the lines written.

    The fix of the layering phase's sibling-references step. For each reference
    [livery.workshop._packages.undeclared_references][] can write, the
    writers of the package's set write the native requirement and the
    ``[[depends]]`` edge joins the package's contract, both at the
    floor the graph carries. A reference the graph does not reach is
    left for the judge that follows: it is a new dependency, and a
    decision. Idempotent: a second call writes nothing.
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


def _test_problems(
    package: Package,
    edge: Edge,
    by_path: Mapping[str, Package],
    tested: Mapping[str, str],
) -> list[str]:
    """How *package*'s test *edge* disagrees with its test requirements *tested*."""
    from livery.workshop._composition import extension_set

    dependency = by_path[edge.path]
    constraint = tested.get(dependency.name)
    if constraint is None:
        held = ", ".join(extension_set(dependency)) or "no extension"
        return [
            f"{package.path}: the test edge on {edge.path} is not declared in the"
            f" package's test requirements ({dependency.name} missing;"
            f" {edge.path} holds {held}); the gate's --fix writes it"
        ]
    if edge.floor and f">={edge.floor}" not in constraint:
        return [
            f"{package.path}: the test edge on {edge.path} floors at {edge.floor},"
            " and the declared test requirement says"
            f" {dependency.name}{constraint}; the gate's --fix writes the floor"
        ]
    return []


def write_test_requirements(root: Path) -> list[str]:
    """Write each test requirement a package's manifests lack; the lines written.

    The fix of ``layering.graph`` for the test edges it judges: in each
    package whose set answers ``test-requirements``, a test edge whose
    requirement is missing, or carries another floor, is written at the
    edge's floor by the writers of the set
    (``_lifecycle.declare_requirement``). Idempotent: a second call
    writes nothing.

    Raises:
        Failed: when no writer of a package's set takes a test edge's
            sibling, naming both sets.
    """
    from livery.workshop import _lifecycle

    packages = discover_packages(root)
    by_path = {package.path: package for package in packages}
    written: list[str] = []
    for package in packages:
        tested = _lifecycle.declared_test_requirements(package)
        if tested is None:
            continue
        for edge in package.depends:
            if edge.kind != "test" or edge.path not in by_path:
                continue
            if not _test_problems(package, edge, by_path, tested):
                continue
            dependency = by_path[edge.path]
            files = _lifecycle.declare_requirement(
                package, dependency, edge.floor, kind="test"
            )
            if files:
                written.append(
                    f"  layering: {package.path} requires {dependency.name}"
                    f" at floor {edge.floor or 'none'} for its tests"
                    f" ({', '.join(files)})"
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


def extension_imports_in_the_base(sources: Iterable[SourceReferences]) -> list[str]:
    """Each base module that imports an extension, with the extension named.

    The base (``livery.workshop``) imports no extension under
    ``livery.extensions``: what an extension needs of the base it takes through
    the base's seams, and the base reaches an extension only through the
    registries the extension fills at mount. *sources* are records of the
    ``references`` answers; a module that runs without the name it imports
    is left alone, and one importing it lazily or for a type checker is
    judged.
    """
    problems: list[str] = []
    for source in sources:
        if not source.module.startswith(BASE_MODULE + "."):
            continue
        for reference in source.references:
            imported = reference.name
            if reference.optional:
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
#: name, which the base registers beside its other steps: the base
#: importing no extension and the sibling references, both over the
#: ``references`` answers. The sibling step's fix writes the edges
#: (``write_edges``).
RULES: dict[str, Judge] = {
    "base-imports-no-extension": _base_imports_no_extension,
    "sibling-references": _sibling_references,
}
