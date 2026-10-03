"""Slots: a hole one extension cuts in a rendered file, and the values filling it.

A line the base template writes that an extension needs different is never
overridden whole and never deleted by a fragment; the template splits
until that line is a slot, and the records fill it. The owning extension
declares the slot with its composition rule,
[livery.workshop._slots.register_slot][]; any extension or record
contributes, [livery.workshop._slots.contribute][]; the render reads
the composed value, [livery.workshop._slots.composed][]. A list slot
composes as the union in contribution order. A scalar slot takes the
nearest contribution, the last one made, and two claims from one
extension refuse naming both. A slot declared with its values refuses any
other contribution, naming the contributor and the values. A
contribution to a slot nobody declared refuses naming the extension, which
the dependency closure makes rare: the owner is listed before whoever
contributes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: The extension the builtin declarations and contributions belong to.
_BASE = "livery.workshop"

#: The composition rules a slot may name.
UNION = "union"
NEAREST = "nearest"
Compose = Callable[[list[object]], object]


class SlotError(ValueError):
    """A slot cannot compose: an undeclared slot, or two claims at one level."""


@dataclass(frozen=True)
class Contribution:
    """One value an extension or a record put into a slot.

    Attributes:
        value: The contribution.
        extension: The extension that made it.
        by: What made it, a check's name or an extension's, for a refusal.
    """

    value: object
    extension: str
    by: str


@dataclass
class Slot:
    """A declared slot and its contributions, in the order made.

    Attributes:
        name: The slot's name, ``python.dev-group``.
        compose: ``union`` for a list, ``nearest`` for a scalar, or a
            callable over the contributed values.
        default: What the slot composes to with no contribution.
        extension: The extension that declared it.
        contributions: Every contribution so far.
        values: The only values a contribution may carry, or None
            for any value.
    """

    name: str
    compose: str | Compose
    default: object
    extension: str
    contributions: list[Contribution] = field(default_factory=list)
    values: tuple[object, ...] | None = None


_SLOTS: dict[str, Slot] = {}


def register_slot(
    name: str,
    *,
    compose: str | Compose = UNION,
    default: object = None,
    extension: str = _BASE,
    values: tuple[object, ...] | None = None,
) -> None:
    """Declare *name* with its composition rule; a declared name is replaced.

    A list slot's default is an empty list; a scalar's is None unless
    given. A slot declared with *values* accepts no other contribution.
    Replacing a declaration keeps its contributions.
    """
    if compose not in (UNION, NEAREST) and not callable(compose):
        raise SlotError(
            f"slot {name!r}: compose is {UNION!r}, {NEAREST!r}, or a callable"
        )
    if default is None and compose == UNION:
        default = []
    kept = _SLOTS[name].contributions if name in _SLOTS else []
    _SLOTS[name] = Slot(name, compose, default, extension, kept, values=values)


def unregister_slot(name: str) -> None:
    """Withdraw *name* with its contributions; nothing when there is none."""
    _SLOTS.pop(name, None)


def contribute(
    name: str, value: object, *, extension: str = _BASE, by: str = ""
) -> None:
    """Add *value* to the slot *name*.

    Raises:
        SlotError: when no extension declared *name*, or the slot declares
            its values and *value* is not one of them.
    """
    slot = _SLOTS.get(name)
    if slot is None:
        raise SlotError(
            f"{by or extension} contributes to slot {name!r},"
            " which no extension declares;"
            f" the slots are {', '.join(sorted(_SLOTS)) or 'none'}"
        )
    if slot.values is not None and value not in slot.values:
        raise SlotError(
            f"{by or extension} contributes {value!r} to slot {name!r}, whose values"
            f" are {', '.join(repr(known) for known in slot.values)}"
        )
    slot.contributions.append(Contribution(value, extension, by or extension))


def withdraw(name: str, *, by: str) -> None:
    """Remove every contribution *by* made to *name*; nothing when there is none."""
    slot = _SLOTS.get(name)
    if slot is not None:
        slot.contributions[:] = [c for c in slot.contributions if c.by != by]


def slots() -> tuple[str, ...]:
    """Every declared slot's name, sorted."""
    return tuple(sorted(_SLOTS))


def composed(name: str) -> object:
    """The value the slot *name* composes to.

    Raises:
        SlotError: when *name* is undeclared, or a scalar slot has two
            claims from one extension.
    """
    slot = _SLOTS.get(name)
    if slot is None:
        raise SlotError(
            f"slot {name!r} is not declared; the slots are"
            f" {', '.join(sorted(_SLOTS)) or 'none'}"
        )
    values = [c.value for c in slot.contributions]
    if not isinstance(slot.compose, str):
        return slot.compose(values)
    if slot.compose == UNION:
        found: list[object] = []
        # Extensions in the order they first contributed, the mount
        # order; within one extension, by contributor name rather than by
        # when each registered, so a check registered again (a test's
        # restore, a record replaced) leaves the render where it was.
        rank: dict[str, int] = {}
        for contribution in slot.contributions:
            rank.setdefault(contribution.extension, len(rank))
        ordered = sorted(slot.contributions, key=lambda c: (rank[c.extension], c.by))
        for contribution in ordered:
            items: list[object] = (
                list(contribution.value)
                if isinstance(contribution.value, (list, tuple))
                else [contribution.value]
            )
            for item in items:
                if item not in found:
                    found.append(item)
        return found
    if not slot.contributions:
        return slot.default
    nearest = slot.contributions[-1]
    rivals = [
        c
        for c in slot.contributions
        if c.extension == nearest.extension and c is not nearest
    ]
    if rivals:
        rival = rivals[-1]
        raise SlotError(
            f"slot {name!r}: two claims at one level, {rival.by} ({rival.value!r})"
            f" and {nearest.by} ({nearest.value!r}), both from {nearest.extension};"
            " one of them yields"
        )
    return nearest.value


def all_composed() -> dict[str, object]:
    """Every declared slot's composed value, by name, for the render."""
    return {name: composed(name) for name in slots()}
