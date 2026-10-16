"""Compose extensions on one package: its set, order, list and name.

A package lists the extensions it is made of in its own
``workshop.toml``. Its set is that list and every extension the list
requires, transitively, whatever those extensions declare: requires
alone composes a package. Two extensions that cannot share one are
refused by the rules of the phase they both add steps to, two
providers of one key or two owners of one executable, never by a rule
over the pair.

Composition order is the order a set mounts in and is named in: an
extension comes after the extensions it requires, and ties go
alphabetically. A package writes its set as the canonical list, the
minimal one: no extension another member requires, in composition
order. A combination is a set, named by its canonical list joined with
``+``.

Every function here but `extension_set` reads declarations through a
lookup and imports nothing: [livery.workshop._extensions][] answers the
lookup from what is installed, and a test from what it declares.
[livery.workshop.extension_set][] is a package's set as the installed
declarations compose it, which the base's readers and the extensions
ask.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from livery.workshop._declaration import Declaration
    from livery.workshop._packages import Package

#: How a name's declaration is found: None for an extension that no
#: installed distribution declares.
Lookup = Callable[[str], "Declaration | None"]


class OrderCycle(ValueError):
    """A declared order with no first member: each one comes after another.

    Attributes:
        members: The extensions on the cycle, each before the next and
            the last before the first.
    """

    def __init__(self, members: tuple[str, ...]) -> None:
        self.members = members
        super().__init__(
            "the extensions' declared order has no start: "
            + " before ".join((*members, members[0]))
            + "; remove one requires among them"
        )


def requirements(name: str, lookup: Lookup) -> tuple[str, ...]:
    """The extensions *name* declares it requires; none when nothing declares it."""
    declared = lookup(name)
    return declared.requires if declared is not None else ()


def closure(names: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """*names* and every extension they require, transitively, first met first.

    A list's set. A name the lookup finds no declaration for stays in
    it and requires nothing: a sync installs it, and the mount names it
    until then.
    """
    found: dict[str, None] = {}
    pending = list(names)
    while pending:
        name = pending.pop(0)
        if name not in found:
            found[name] = None
            pending.extend(requirements(name, lookup))
    return tuple(found)


#: Each list a package names, to the scan of the installed extensions it
#: was composed against and the set it composed to.
_SETS: dict[tuple[str, ...], tuple[object, tuple[str, ...]]] = {}


def extension_set(package: Package) -> tuple[str, ...]:
    """The extensions *package* holds: its list and every extension the list requires.

    In composition order, as the installed declarations declare it:
    each extension after the ones it requires, ties alphabetical, and
    alphabetical while the declared order has no start. The list's own
    order means nothing. Empty for a package that lists none. The
    workspace's own tests ([livery.workshop.workspace_suite][]) hold the
    root's set: the extensions ``[workspace] extensions`` lists and
    every one they require, read from the root's contract on each call.
    A step that names extensions judges a package whose set holds one
    of them.

    A list is composed once for each scan of the installed extensions,
    so a scan taken after an install composes it again.
    """
    from livery.workshop._packages import WORKSPACE_TESTS

    if package.path == WORKSPACE_TESTS:
        from livery.workshop._extensions import root_set
        from livery.workshop._packages import root_of

        return root_set(root_of(package))
    listed = package.extensions
    if not listed:
        return ()
    from livery.footman import installed_entry_points
    from livery.workshop._extensions import installed_declaration

    scan = installed_entry_points()
    held = _SETS.get(listed)
    if held is not None and held[0] is scan:
        return held[1]
    found = order_or_sorted(
        closure(listed, installed_declaration), installed_declaration
    )
    _SETS[listed] = (scan, found)
    return found


def implied(names: Iterable[str], lookup: Lookup) -> frozenset[str]:
    """Of the extensions *names* require, transitively, every one."""
    found: set[str] = set()
    for name in names:
        found.update(closure(requirements(name, lookup), lookup))
    return frozenset(found)


def nearest_extensions(names: Iterable[str], lookup: object) -> tuple[str, ...]:
    """Of the extensions *names*, the ones no other of them requires: the nearest.

    The rule a query's ``nearest`` combine applies, for a combine
    function that decides between answers by what each answering
    extension requires, transitively. *lookup* is the one such a
    function is called with, passed on as it came. The nearest come in
    the order of *names*; two of them means neither requires the other.
    Answers whose requires close a cycle leave none nearest, and
    ``layering.graph`` names the cycle.
    """
    named = tuple(dict.fromkeys(names))
    required = implied(named, cast("Lookup", lookup))
    return tuple(name for name in named if name not in required)


def order(
    names: Iterable[str],
    lookup: Lookup,
    *,
    requires: bool = True,
    edges: Iterable[tuple[str, str]] = (),
) -> tuple[str, ...]:
    """*names* in order: after what each requires and the *edges*, ties alphabetical.

    A name outside *names* orders nothing.

    Args:
        names: The extensions to order.
        lookup: Where each one's declaration is found.
        requires: Whether a requirement comes first. A phase orders by
            its context keys alone.
        edges: Further ``(earlier, later)`` pairs, such as a phase's
            context keys from provider to reader.

    Raises:
        OrderCycle: when the declared order has no start, naming the
            members of one cycle.
    """
    members = sorted(set(names))
    present = set(members)
    waiting: dict[str, set[str]] = {name: set() for name in members}
    if requires:
        for name in members:
            waiting[name].update(
                other for other in requirements(name, lookup) if other in present
            )
    for earlier_name, later_name in edges:
        if earlier_name in present and later_name in present:
            waiting[later_name].add(earlier_name)
    for name in members:
        waiting[name].discard(name)
    ready = [name for name in members if not waiting[name]]
    heapq.heapify(ready)
    done: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        done.append(name)
        for other in members:
            if name in waiting[other]:
                waiting[other].discard(name)
                if not waiting[other]:
                    heapq.heappush(ready, other)
    if len(done) < len(members):
        left = {name: waiting[name] for name in members if name not in done}
        raise OrderCycle(cycle_of(left))
    return tuple(done)


def order_or_sorted(names: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """*names* in composition order, alphabetical when the order has no start.

    For a reader that answers while a cycle in the declared order
    stands: the sync that lets a person remove the cycle still runs, and
    ``layering.graph`` names it.
    """
    members = tuple(dict.fromkeys(names))
    try:
        return order(members, lookup)
    except OrderCycle:
        return tuple(sorted(members))


def cycle_of(waiting: dict[str, set[str]]) -> tuple[str, ...]:
    """One cycle among *waiting*, each member before the next, the first alphabetical.

    Every member left waits on another member left, so walking from
    each to one it waits on returns to a name already walked.
    """
    walked: list[str] = []
    name = min(waiting)
    while name not in walked:
        walked.append(name)
        name = min(waiting[name])
    cycle = walked[walked.index(name) :]
    # The walk went from each member to one it comes after.
    cycle.reverse()
    start = cycle.index(min(cycle))
    return tuple(cycle[start:] + cycle[:start])


def canonical(listed: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """The minimal list of *listed*'s set, in composition order.

    Raises:
        OrderCycle: when the set's declared order has no start.
    """
    members = closure(listed, lookup)
    required = implied(members, lookup)
    return tuple(name for name in order(members, lookup) if name not in required)


def combination(listed: Iterable[str], lookup: Lookup) -> str:
    """The name of *listed*'s set: its canonical list joined with ``+``.

    Raises:
        OrderCycle: when the set's declared order has no start.
    """
    return "+".join(canonical(listed, lookup))
