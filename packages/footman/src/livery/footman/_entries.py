"""The installed entry points, every group, scanned once per process.

Reading entry points re-reads every installed distribution's
``entry_points.txt`` on each call, whatever the group asked for, so a
process that asks for several groups pays for several scans. This
module scans once, on first use, and answers every group from that
scan: footman's plugin loader, its built-in rungs and ``--plugins``,
and anything built on footman, the workshop's extensions among them.

``importlib.metadata`` is imported on first use alone: the completion
hot path never asks, and never pays for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

_SCAN: tuple[EntryPoint, ...] | None = None
_OFFERED_BY: dict[tuple[str, str, str], str] = {}


def installed_entry_points(group: str | None = None) -> tuple[EntryPoint, ...]:
    """The installed entry points of *group*, or of every group when None.

    The first call scans every installed distribution; every later call
    in the process answers from that scan. A process that installs a
    distribution and must see it calls
    [livery.footman.rescan_entry_points][] first.
    """
    global _SCAN
    if _SCAN is None:
        _SCAN = _scan()
    if group is None:
        return _SCAN
    return tuple(entry for entry in _SCAN if entry.group == group)


def _scan() -> tuple[EntryPoint, ...]:
    """Every installed distribution's entry points, the first of each name on the path.

    Walked through the distributions rather than ``entry_points()``,
    whose no-argument form on Python 3.11 returns groups by name: the
    same entry points on every supported Python. A distribution
    installed twice is read once, the copy earlier on the path, as the
    standard library reads it.
    """
    import importlib.metadata
    import re

    seen: set[str] = set()
    found: list[EntryPoint] = []
    _OFFERED_BY.clear()
    for dist in importlib.metadata.distributions():
        given = dist.metadata["Name"] or ""
        name = re.sub(r"[-_.]+", "-", given).lower()
        if name in seen:
            continue
        seen.add(name)
        for entry in dist.entry_points:
            found.append(entry)
            _OFFERED_BY.setdefault((entry.group, entry.name, entry.value), given)
    return tuple(found)


def distribution_of(entry: EntryPoint) -> str:
    """The name of the distribution that offers *entry*; empty when none is known.

    The scan reads each name when it reads the entry points. An entry
    point reads its distribution's metadata later, from the directory
    the scan found it in. A sync in another process that re-installs
    an editable distribution at a new version moves that directory,
    and the later read then finds no name. An entry point the scan did
    not make answers from its own distribution.
    """
    found = _OFFERED_BY.get((entry.group, entry.name, entry.value))
    if found is not None:
        return found
    dist = getattr(entry, "dist", None)
    return str(getattr(dist, "name", "") or "") if dist is not None else ""


def rescan_entry_points() -> None:
    """Forget the scan, so the next question reads the installed metadata again."""
    global _SCAN
    _SCAN = None
