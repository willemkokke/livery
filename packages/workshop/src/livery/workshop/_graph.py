"""The affected engine: which packages a change can influence.

The workspace graph is the ``[[depends]]`` edges the contracts
declare. A changed file maps to the package whose directory holds it,
and the affected set is that package plus everything that depends on
it, transitively (the dependents' closure): a change can break its
consumers, never its dependencies. A change outside every package,
the root configuration, the templates, the workspace tests, affects
everything, because the root files configure every gate.

``fm graph.affected`` prints the verdict; the quality family's
the reflex and the CI legs scope work to it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from livery.footman import group
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package, discover_packages

graph = group("graph", help="The workspace dependency graph")


def dependents_closure(
    packages: tuple[Package, ...], seeds: set[str]
) -> tuple[Package, ...]:
    """*seeds* (package paths) plus everything depending on them.

    Follows the reversed ``[[depends]]`` edges to a fixed point, and
    answers in discovery order so output stays deterministic.
    """
    dependents: dict[str, set[str]] = {package.path: set() for package in packages}
    for package in packages:
        for edge in package.depends:
            dependents.setdefault(edge.path, set()).add(package.path)
    affected = set(seeds)
    frontier = list(seeds)
    while frontier:
        for consumer in dependents.get(frontier.pop(), ()):
            if consumer not in affected:
                affected.add(consumer)
                frontier.append(consumer)
    return tuple(package for package in packages if package.path in affected)


#: The prose directory: nothing under it reaches a gate.
NOTES = "notes/"

#: The site's own files at the root: its configuration and its pages.
SITE_CONFIG = "zensical.toml"
SITE_DOCS = "docs/"


def is_site(path: str) -> bool:
    """Whether *path* is the site's own: the root ``zensical.toml`` or ``docs/`` tree.

    Only the site build reads them, and it runs on every run, so
    they reach no format, lint, type, or test gate. A package's
    ``docs/`` directory is the package's, not the site's.
    """
    return path == SITE_CONFIG or path.startswith(SITE_DOCS)


def is_prose(path: str) -> bool:
    """Whether *path* is prose: under ``notes/``, or a markdown file anywhere.

    Prose reaches no format, lint, type, or test gate. The site build
    is where markdown is judged, and it runs on every run.
    """
    return path.startswith(NOTES) or path.endswith(".md")


def affected_packages(
    root: Path, git: GitOps, *, base: str = "main"
) -> tuple[Package, ...] | None:
    """The packages this branch's changes can influence, and the workspace's own tests.

    None means everything: a touched file outside every package (the
    root configuration, templates) configures every gate, so no
    narrowing is honest, and the file is named. Prose (`is_prose`)
    and the site's own files (`is_site`) affect no package wherever
    they live, so a diff confined to them affects nothing. A change
    under the workspace's own ``tests/`` affects that unit alone,
    which the answer then carries as a package of its own
    ([livery.workshop._coverage_store.workspace_suite][]): its tests
    reach every package, and every leg that runs a suite runs them
    anyway, so no package's suite has to run for it. An empty tuple
    means the branch changes nothing a gate reads.
    """
    from livery.workshop._kinds import kind_names

    packages = discover_packages(root)
    # The fail-open guard: a package of an unregistered kind has an
    # edge story the registry cannot vouch for, so no narrowing is
    # honest. Everything runs, and the reason is printed rather than
    # silently widening the gate.
    for package in packages:
        if package.type not in kind_names():
            print(
                f"  {package.path}: type {package.type!r} is not a"
                " registered kind; failing open to everything"
            )
            return None
    scope = affected_from_paths(root, packages, git.changed_paths(base))
    return None if scope is None else scope.packages


@dataclass(frozen=True)
class Scope:
    """What a change reaches: the packages, and the tests that stand for some.

    Attributes:
        packages: The packages to gate, in discovery order, the
            workspace's own tests unit last when its files changed.
        tests: For a package whose changed files are tests and
            nothing else, those files, repo-relative; a package absent
            here runs its suite.
    """

    packages: tuple[Package, ...]
    tests: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def affected_from_paths(
    root: Path, packages: tuple[Package, ...], paths: Iterable[str]
) -> Scope | None:
    """What a change to *paths* reaches: the packages, and the tests that stand alone.

    The classification `affected_packages` applies to a branch's
    changes, for any set of paths: the reflex hands it the paths
    between a proved tree and the working tree. Each path is
    classified by its package's kind: a test file reaches its own
    package alone, since nothing imports a test, and that package
    runs those files when they are all that changed in it; source,
    test support, and configuration reach the package's dependents
    and run the suites. ``None`` means everything, an empty scope
    nothing a gate reads.
    """
    from livery.workshop._backends import _python
    from livery.workshop._coverage_store import WORKSPACE_TESTS, workspace_suite
    from livery.workshop._kinds import TEST, backend_for

    seeds: set[str] = set()
    picked: dict[str, list[str]] = {}
    tests_changed = False
    unit_suite = False
    for path in paths:
        if is_prose(path) or is_site(path):
            continue
        if path.startswith(WORKSPACE_TESTS + "/"):
            tests_changed = True
            if _python.classify(workspace_suite(root) or packages[0], path) == TEST:
                picked.setdefault(WORKSPACE_TESTS, []).append(path)
            else:
                unit_suite = True
            continue
        for package in packages:
            if path.startswith(package.path + "/"):
                relative = path[len(package.path) + 1 :]
                if backend_for(package).classify(package, relative) == TEST:
                    picked.setdefault(package.path, []).append(path)
                else:
                    seeds.add(package.path)
                break
        else:
            print(f"  {path}: outside the packages; everything runs")
            return None
    reached = {package.path for package in dependents_closure(packages, seeds)}
    tests = {
        path: tuple(files)
        for path, files in picked.items()
        if path != WORKSPACE_TESTS and path not in reached
    }
    members = tuple(
        package
        for package in packages
        if package.path in reached or package.path in tests
    )
    if not tests_changed:
        return Scope(members, tests)
    unit = workspace_suite(root)
    if unit is None:
        print(f"  {WORKSPACE_TESTS}/: changed and gone; everything runs")
        return None
    if not unit_suite and WORKSPACE_TESTS in picked:
        tests[WORKSPACE_TESTS] = tuple(picked[WORKSPACE_TESTS])
    return Scope((*members, unit), tests)


@graph.task(name="affected")
def graph_affected(base: str = "main") -> None:
    """Print the affected packages for this branch, one per line.

    Args:
        base: the branch the change will merge into
    """
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        print("  no workspace: no workshop.toml above the working directory")
        return
    git = GitOps(root)
    git.fetch()
    affected = affected_packages(root, git, base=base)
    if affected is None:
        print("  everything: a change outside the packages configures every gate")
        return
    if not affected:
        print("  nothing: the branch changes no files")
        return
    for package in affected:
        print(f"  {package.path} ({package.name})")


def order_topologically(packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """*packages* with every dependency before its dependents.

    Kahn's walk over the ``[[depends]]`` edges restricted to the given
    set; ties keep the input order so the result is deterministic. A
    cycle cannot arise, the layering lint refuses one long before a
    release asks.
    """
    chosen = {p.path for p in packages}
    remaining = list(packages)
    ordered: list[Package] = []
    placed: set[str] = set()
    while remaining:
        for index, package in enumerate(remaining):
            needs = {
                edge.path
                for edge in package.depends
                if edge.path in chosen and edge.path not in placed
            }
            if not needs:
                ordered.append(package)
                placed.add(package.path)
                del remaining[index]
                break
        else:  # pragma: no cover - guarded by the layering lint
            ordered.extend(remaining)
            break
    return tuple(ordered)
