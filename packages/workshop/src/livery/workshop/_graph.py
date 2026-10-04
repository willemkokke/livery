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

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from livery.footman.api import group
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
        if package.kind not in kind_names():
            print(
                f"  {package.path}: kind {package.kind!r} is not a"
                " registered kind; failing open to everything"
            )
            return None
    scope = affected_from_paths(
        root,
        packages,
        git.changed_paths(base),
        git=git,
        # Asked only for a change outside every package.
        before=lambda: git.merge_base(base),
    )
    return None if scope is None else scope.packages


@dataclass(frozen=True)
class Scope:
    """What a change reaches: the packages, and the tests that stand for some.

    Attributes:
        packages: The packages to gate, in discovery order, the
            workspace's own tests unit last when its files changed.
        tests: Per package, the test files that stand for its suite;
            the walk names none until a better narrowing exists, so
            every package runs its suite whole.
        examples: The package paths whose changed files are examples
            and nothing else: their examples run and no suite. A
            package reached through its source runs both.
    """

    packages: tuple[Package, ...]
    tests: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    examples: tuple[str, ...] = ()


def docs_page(packages: tuple[Package, ...], path: str) -> Package | None:
    """The package whose authored docs page *path* is, or None for any other path."""
    if not path.endswith(".md") or "/_generated/" in path:
        return None
    for package in packages:
        if path.startswith(package.path + "/docs/"):
            return package
    return None


def affected_from_paths(
    root: Path,
    packages: tuple[Package, ...],
    paths: Iterable[str],
    *,
    git: GitOps | None = None,
    before: str | Callable[[], str] = "",
) -> Scope | None:
    """What a change to *paths* reaches: the packages, and the tests that stand alone.

    The classification `affected_packages` applies to a branch's
    changes, for any set of paths: the reflex hands it the paths
    between a proved tree and the working tree. Each path is
    classified by its package's kind: a test file reaches its own
    package alone, since nothing imports a test, and that package
    runs its whole suite; source, test support, and configuration
    reach the package's dependents and run the suites. A change under
    the workspace's own tests runs that unit whole. A docs page
    reaches nothing: the site build reads it. An example file reaches
    its package's examples check
    and no suite, unless source of the package changed too. A file
    outside every package reaches everything, unless *before* (the
    commit or tree the paths were taken from) shows the change is
    about the packages added or removed, or, for ``uv.lock``, about
    the members whose resolution it moves
    ([livery.workshop._root_attribution][]): then it reaches those.
    *before* may be a function answering it, asked only when a path
    outside every package needs it.
    ``None`` means everything, an empty scope nothing a gate reads.
    """
    from livery.workshop._categories import EXAMPLE, TEST, category_of
    from livery.workshop._coverage_store import WORKSPACE_TESTS, workspace_suite

    seeds: set[str] = set()
    # A package whose test files changed runs its whole suite; nothing
    # imports a test, so its dependents' suites do not run. The suite
    # is not narrowed to the changed files until a better narrowing
    # exists: the tests a change reaches are more than the ones it edits.
    suites: set[str] = set()
    examples: set[str] = set()
    tests_changed = False
    outside: list[str] = []
    for path in paths:
        if docs_page(packages, path) is not None:
            continue
        if is_prose(path) or is_site(path):
            continue
        if path.startswith(WORKSPACE_TESTS + "/"):
            tests_changed = True
            continue
        for package in packages:
            if path.startswith(package.path + "/"):
                relative = path[len(package.path) + 1 :]
                category = category_of(package, relative).name
                if category == EXAMPLE:
                    examples.add(package.path)
                elif category == TEST:
                    suites.add(package.path)
                else:
                    seeds.add(package.path)
                break
        else:
            outside.append(path)
    if outside:
        attributed = _attribute(root, packages, outside, git=git, before=before)
        if attributed is None:
            return None
        seeds |= attributed
    reached = {package.path for package in dependents_closure(packages, seeds)}
    alone = tuple(
        sorted(path for path in examples if path not in reached and path not in suites)
    )
    members = tuple(
        package
        for package in packages
        if package.path in reached or package.path in suites or package.path in alone
    )
    if not tests_changed:
        return Scope(members, {}, alone)
    unit = workspace_suite(root)
    if unit is None:
        print(f"  {WORKSPACE_TESTS}/: changed and gone; everything runs")
        return None
    return Scope((*members, unit), {}, alone)


def _attribute(
    root: Path,
    packages: tuple[Package, ...],
    paths: list[str],
    *,
    git: GitOps | None,
    before: str | Callable[[], str],
) -> set[str] | None:
    """The packages the changes to *paths*, all outside every package, are about.

    None means everything, and the first path that forces it is
    named: without *before* there is nothing to compare with, and a
    change the package set does not explain configures every gate.
    """
    from livery.workshop._fragment_engine import RENDERED_MANIFEST
    from livery.workshop._root_attribution import (
        LOCK,
        explained,
        lock_affected,
        package_delta,
    )

    # The render's receipt records each composed file's digest. It
    # configures no gate: every file it names is judged on its own,
    # and the drift check that reads it runs on every gate.
    paths = [path for path in paths if path != RENDERED_MANIFEST]
    if not paths:
        return set()

    point = before if isinstance(before, str) else before()
    if git is None or not point:
        print(f"  {paths[0]}: outside the packages; everything runs")
        return None
    delta = package_delta(git, point, packages)
    seeds = {package.path for package in delta.added}
    for path in paths:
        if delta.holds(path):
            continue  # a removed package's own files: nothing depends on it now
        old = git.file_at(point, path)
        target = root / path
        new = target.read_text("utf-8") if target.is_file() else ""
        if path == LOCK:
            moved = lock_affected(old, new, delta)
            if moved is None:
                print(
                    f"  {path}: a resolution beyond the members moved; everything runs"
                )
                return None
            seeds |= moved
            continue
        if not explained(old, new, delta):
            print(f"  {path}: outside the packages; everything runs")
            return None
        print(f"  {path}: names only the packages added or removed")
    return seeds


@graph.task(name="affected")
def graph_affected(base: str = "main") -> None:
    """Print the affected packages for this branch, one per line.

    Args:
        base: the branch the change will merge into
    """
    from livery.workshop._extensions import workspace_root

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
