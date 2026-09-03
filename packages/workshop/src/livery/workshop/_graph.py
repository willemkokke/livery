"""The affected engine: which packages a change can influence.

The workspace graph is the ``[[depends]]`` edges the contracts
declare. A changed file maps to the package whose directory holds it,
and the affected set is that package plus everything that depends on
it, transitively (the dependents' closure): a change can break its
consumers, never its dependencies. A change outside every package,
the root configuration, the templates, the workspace tests, affects
everything, because the root files configure every gate.

``fm graph.affected`` prints the verdict; the quality family's
``--affected`` flag scopes work to it.
"""

from __future__ import annotations

from pathlib import Path

from footman import group

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


def affected_packages(
    root: Path, git: GitOps, *, base: str = "main"
) -> tuple[Package, ...] | None:
    """The packages this branch's changes can influence.

    None means everything: a touched file outside every package (the
    root configuration, templates, workspace tests) configures every
    gate, so no narrowing is honest. An empty tuple means the branch
    changes nothing at all.
    """
    packages = discover_packages(root)
    seeds: set[str] = set()
    for path in git.changed_paths(base):
        for package in packages:
            if path.startswith(package.path + "/"):
                seeds.add(package.path)
                break
        else:
            return None
    return dependents_closure(packages, seeds)


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
