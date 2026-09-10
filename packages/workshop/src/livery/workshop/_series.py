"""Every series the workshop keeps, declared in one place.

The janitor walks this tuple to bound them
([livery.workshop._state.sweep][]) and a reader lists them. Each
series is declared where its rows mean something; this module only
gathers the declarations, so a new kind of row is one entry here and
nothing else.
"""

from __future__ import annotations

from livery.workshop import (
    _coverage_marks,
    _coverage_store,
    _diagnostics,
    _gate_record,
    _metrics,
    _verified,
)
from livery.workshop._state import Keyed, Series

#: Every series and family, remote and local.
DECLARED: tuple[Series | Keyed, ...] = (
    _metrics.SERIES,
    _metrics.RUNS,
    _verified.SERIES,
    _coverage_marks.SERIES,
    _coverage_store.COVERAGE,
    _gate_record.SERIES,
    _diagnostics.SERIES,
)
