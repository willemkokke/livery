"""Count the processes a block starts, for the tests that pin a process budget.

A store read or write must cost a fixed handful of git processes
whatever the series holds; a test that pins the count keeps a per-row
process from creeping back in. This is not a conftest, for the reason
`workshop_seeds` gives: footman's suite already has one of that name.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from os.path import basename
from typing import Any


@contextmanager
def counting_spawns() -> Iterator[Counter[str]]:
    """Count every process started inside the block, by the program's base name."""
    counts: Counter[str] = Counter()
    original = subprocess.Popen.__init__

    def counting(
        self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any
    ) -> None:
        head = args[0] if isinstance(args, (list, tuple)) and args else str(args)
        counts[basename(str(head)).removesuffix(".exe")] += 1
        original(self, args, *rest, **kwargs)

    subprocess.Popen.__init__ = counting  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]
