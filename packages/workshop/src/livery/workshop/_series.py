"""Every series the workshop keeps, declared in one place.

The janitor walks them to bound them ([livery.git.sweep][]) and a
reader lists them. Each series is declared where its rows mean
something; this module only gathers the declarations: the base's own,
`BASE`, and the ones the listed extensions keep, their answers to
``store.series`` ([livery.workshop._series.declared][]).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery import git
from livery.workshop import (
    _diagnostics,
    _gate_record,
    _metrics,
    _shard,
    _speed,
    _traces,
    _verified,
)

if TYPE_CHECKING:
    from pathlib import Path

#: The local series a loop pass writes its rows to: one file per
#: pass on the driver's checkout, never pushed, so passes compare
#: across days on one machine. Declared here rather than beside the
#: loop, since the loop's module loads the CI verbs and this module
#: must import without them; `fm store.show loop` reads it.
LOOP = git.Series("loop", window=200, ci_only=False, local=True)

#: The base's own series and families, remote and local.
BASE: tuple[git.Series | git.Keyed, ...] = (
    _metrics.SERIES,
    _metrics.RUNS,
    _shard.DURATIONS,
    _verified.SERIES,
    _speed.SERIES,
    _gate_record.SERIES,
    _diagnostics.SERIES,
    _traces.TRACES,
    _traces.ABOUT,
    LOOP,
    git.FETCHED,
)


def declared(root: Path) -> tuple[git.Series | git.Keyed, ...]:
    """Every series and family *root* keeps: the base's, then the listed extensions'.

    An extension keeps a series by answering ``store.series`` with it.
    """
    from livery.workshop._queries import STORE_SERIES, answer

    kept = answer(root, STORE_SERIES)
    return BASE + tuple(item for item in kept if item not in BASE)
