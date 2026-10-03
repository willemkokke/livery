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


def installed_entry_points(group: str | None = None) -> tuple[EntryPoint, ...]:
    """The installed entry points of *group*, or of every group when None.

    The first call scans every installed distribution; every later call
    in the process answers from that scan. A process that installs a
    distribution and must see it calls
    [livery.footman.api.rescan_entry_points][] first.
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
    for dist in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", dist.metadata["Name"] or "").lower()
        if name in seen:
            continue
        seen.add(name)
        found.extend(dist.entry_points)
    return tuple(found)


def rescan_entry_points() -> None:
    """Forget the scan, so the next question reads the installed metadata again."""
    global _SCAN
    _SCAN = None
