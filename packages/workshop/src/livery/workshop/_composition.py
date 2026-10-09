"""Compose package-level extensions on one package: its set, order, list and name.

A package lists the package-level extensions it is made of in its own
``workshop.toml``. Its set is that list and every package-level
extension the list requires, transitively. The set is valid when every
pair in it is connected: one requires the other, one contributes to the
other through a ``[for.<target>]`` table, or one names the other in
``[extension] compatible``.

Composition order is the order a set mounts in and is named in: an
extension comes after the extensions it requires and the ones it names
in ``after``, and before the ones it names in ``before``; ties go
alphabetically. A package writes its set as the canonical list, the
minimal one: no extension another member requires, in composition
order. A combination is a valid set, named by its canonical list joined
with ``+``.

Every function here reads declarations through a lookup and imports
nothing: [livery.workshop._extensions][] answers the lookup from what
is installed, and a test from what it declares.
"""

from __future__ import annotations

import contextlib
import heapq
import itertools
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.workshop._declaration import Declaration

#: How a name's declaration is found: None for an extension that no
#: installed distribution declares.
Lookup = Callable[[str], "Declaration | None"]

#: The level a package's own list names.
PACKAGE = "package"


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
            + "; remove one requires, before or after among them"
        )


def requirements(name: str, lookup: Lookup) -> tuple[str, ...]:
    """The extensions *name* declares it requires; none when nothing declares it."""
    declared = lookup(name)
    return declared.requires if declared is not None else ()


def closure(names: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """*names* and every extension they require, transitively, first met first."""
    found: dict[str, None] = {}
    pending = list(names)
    while pending:
        name = pending.pop(0)
        if name not in found:
            found[name] = None
            pending.extend(requirements(name, lookup))
    return tuple(found)


def package_level(name: str, lookup: Lookup) -> bool:
    """Whether *name* is an installed extension a package may list."""
    declared = lookup(name)
    return declared is not None and PACKAGE in declared.levels


def package_set(listed: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """The package-level extensions *listed* stands for: the list and what it requires.

    A requirement listed at the workspace alone is the workspace's, and
    an extension nothing installed declares belongs to no level: both
    stay out of the set.
    """
    return tuple(
        name for name in closure(listed, lookup) if package_level(name, lookup)
    )


def implied(names: Iterable[str], lookup: Lookup) -> frozenset[str]:
    """Of the extensions *names* require, transitively, every one."""
    found: set[str] = set()
    for name in names:
        found.update(closure(requirements(name, lookup), lookup))
    return frozenset(found)


def connected(first: str, second: str, lookup: Lookup) -> bool:
    """Whether *first* and *second* know each other, so one package may hold both.

    One requires the other, transitively; one contributes to the other
    through a ``[for.<target>]`` table; or one names the other in
    ``compatible``.
    """
    for one, other in ((first, second), (second, first)):
        declared = lookup(one)
        if declared is None:
            continue
        if (
            other in closure(declared.requires, lookup)
            or other in declared.compatible
            or other in declared.target_tables
        ):
            return True
    return False


def order(
    names: Iterable[str],
    lookup: Lookup,
    *,
    requires: bool = True,
    edges: Iterable[tuple[str, str]] = (),
) -> tuple[str, ...]:
    """*names* in order: each after what it requires and names in ``after``.

    An extension also comes before what it names in ``before``. A name
    outside *names* orders nothing, and ties go alphabetically.

    Args:
        names: The extensions to order.
        lookup: Where each one's declaration is found.
        requires: Whether a requirement comes first. A phase orders by
            its context keys, ``before`` and ``after`` alone.
        edges: Further ``(earlier, later)`` pairs, such as a phase's
            context keys from provider to reader.

    Raises:
        OrderCycle: when the declared order has no start, naming the
            members of one cycle.
    """
    members = sorted(set(names))
    present = set(members)
    waiting: dict[str, set[str]] = {name: set() for name in members}
    for name in members:
        declared = lookup(name)
        if declared is None:
            continue
        earlier = (*declared.requires, *declared.after) if requires else declared.after
        waiting[name].update(other for other in earlier if other in present)
        for later in declared.before:
            if later in present:
                waiting[later].add(name)
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
        raise OrderCycle(_cycle(left))
    return tuple(done)


def _cycle(waiting: dict[str, set[str]]) -> tuple[str, ...]:
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
    members = package_set(listed, lookup)
    required = implied(members, lookup)
    return tuple(name for name in order(members, lookup) if name not in required)


def combination(listed: Iterable[str], lookup: Lookup) -> str:
    """The name of *listed*'s set: its canonical list joined with ``+``.

    Raises:
        OrderCycle: when the set's declared order has no start.
    """
    return "+".join(canonical(listed, lookup))


def unconnected(members: Iterable[str], lookup: Lookup) -> list[tuple[str, str]]:
    """Each pair of *members* that do not know each other, alphabetical."""
    return [
        (first, second)
        for first, second in itertools.combinations(sorted(set(members)), 2)
        if not connected(first, second, lookup)
    ]


def combinations(names: Iterable[str], lookup: Lookup) -> tuple[str, ...]:
    """Every valid set of the package-level extensions among *names*, by name, sorted.

    A set is valid when every pair in it is connected, it holds every
    package-level extension its members require, each of those is
    installed, and its declared order has a start. Each set is named
    once, by its canonical list.
    """
    candidates = sorted(name for name in set(names) if package_level(name, lookup))
    found: set[str] = set()

    def grow(chosen: tuple[str, ...], rest: list[str]) -> None:
        if chosen and _complete(chosen, lookup):
            with contextlib.suppress(OrderCycle):
                found.add(combination(chosen, lookup))
        for index, name in enumerate(rest):
            if all(connected(name, other, lookup) for other in chosen):
                grow((*chosen, name), rest[index + 1 :])

    grow((), candidates)
    return tuple(sorted(found))


def _complete(chosen: tuple[str, ...], lookup: Lookup) -> bool:
    """Whether *chosen* holds every installed package-level extension it requires."""
    for name in closure(chosen, lookup):
        if lookup(name) is None:
            return False
        if package_level(name, lookup) and name not in chosen:
            return False
    return True
