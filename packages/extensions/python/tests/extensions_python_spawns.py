"""Count the processes a block starts, for the tests that pin a process budget.

A machine's run of the speed lines reads no mark, so it starts no git
process; a test that pins the count keeps a store read from creeping
into that path.
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
    """Count every process started inside the block, by program and git subcommand.

    ``counts["git"]`` is every git; ``counts["git fetch"]`` the fetches,
    past git's own ``-c`` pairs and flags, for a pin on round trips.
    """
    counts: Counter[str] = Counter()
    original = subprocess.Popen.__init__

    def counting(
        self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any
    ) -> None:
        argv = (
            [str(item) for item in args]
            if isinstance(args, (list, tuple))
            else [str(args)]
        )
        program = basename(argv[0]).removesuffix(".exe") if argv else ""
        counts[program] += 1
        words = argv[1:]
        while words and words[0].startswith("-"):
            words = words[2:] if words[0] == "-c" else words[1:]
        if words:
            counts[f"{program} {words[0]}"] += 1
        original(self, args, *rest, **kwargs)

    subprocess.Popen.__init__ = counting  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]
