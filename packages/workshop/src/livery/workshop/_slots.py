"""Slots: a hole one layer cuts in a rendered file, and the values others fill it with.

A line the base template writes that a layer needs different is never
overridden whole and never deleted by a fragment; the template splits
until that line is a slot, and the records fill it. The owning layer
declares the slot with its composition rule,
[livery.workshop._slots.register_slot][]; any layer or record
contributes, [livery.workshop._slots.contribute][]; the render reads
the composed value, [livery.workshop._slots.composed][]. A list slot
composes as the union in contribution order. A scalar slot takes the
nearest contribution, the last one made, and two claims from one
layer refuse naming both. A contribution to a slot nobody declared
refuses naming the layer, which the dependency closure makes rare:
the owner is listed before whoever contributes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: The layer the builtin declarations and contributions belong to.
_BASE = "livery.workshop"

#: The composition rules a slot may name.
UNION = "union"
NEAREST = "nearest"
Compose = Callable[[list[object]], object]


class SlotError(ValueError):
    """A slot cannot compose: an undeclared slot, or two claims at one level."""


@dataclass(frozen=True)
class Contribution:
    """One value a layer or a record put into a slot.

    Attributes:
        value: The contribution.
        layer: The layer that made it.
        by: What made it, a check's name or a layer's, for a refusal.
    """

    value: object
    layer: str
    by: str


@dataclass
class Slot:
    """A declared slot and its contributions, in the order made.

    Attributes:
        name: The slot's name, ``python.dev-group``.
        compose: ``union`` for a list, ``nearest`` for a scalar, or a
            callable over the contributed values.
        default: What the slot composes to with no contribution.
        layer: The layer that declared it.
        contributions: Every contribution so far.
    """

    name: str
    compose: str | Compose
    default: object
    layer: str
    contributions: list[Contribution] = field(default_factory=list)


_SLOTS: dict[str, Slot] = {}


def register_slot(
    name: str,
    *,
    compose: str | Compose = UNION,
    default: object = None,
    layer: str = _BASE,
) -> None:
    """Declare *name* with its composition rule; a declared name is replaced.

    A list slot's default is an empty list; a scalar's is None unless
    given. Replacing a declaration keeps its contributions.
    """
    if compose not in (UNION, NEAREST) and not callable(compose):
        raise SlotError(
            f"slot {name!r}: compose is {UNION!r}, {NEAREST!r}, or a callable"
        )
    if default is None and compose == UNION:
        default = []
    kept = _SLOTS[name].contributions if name in _SLOTS else []
    _SLOTS[name] = Slot(name, compose, default, layer, kept)


def unregister_slot(name: str) -> None:
    """Withdraw *name* with its contributions; nothing when there is none."""
    _SLOTS.pop(name, None)


def contribute(name: str, value: object, *, layer: str = _BASE, by: str = "") -> None:
    """Add *value* to the slot *name*.

    Raises:
        SlotError: when no layer declared *name*.
    """
    slot = _SLOTS.get(name)
    if slot is None:
        raise SlotError(
            f"{by or layer} contributes to slot {name!r}, which no layer declares;"
            f" the slots are {', '.join(sorted(_SLOTS)) or 'none'}"
        )
    slot.contributions.append(Contribution(value, layer, by or layer))


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
            claims from one layer.
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
        for contribution in slot.contributions:
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
        c for c in slot.contributions if c.layer == nearest.layer and c is not nearest
    ]
    if rivals:
        rival = rivals[-1]
        raise SlotError(
            f"slot {name!r}: two claims at one level, {rival.by} ({rival.value!r})"
            f" and {nearest.by} ({nearest.value!r}), both from {nearest.layer};"
            " one of them yields"
        )
    return nearest.value


def all_composed() -> dict[str, object]:
    """Every declared slot's composed value, by name, for the render."""
    return {name: composed(name) for name in slots()}
