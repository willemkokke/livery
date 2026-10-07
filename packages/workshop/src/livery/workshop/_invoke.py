"""How a check's path list reaches its tool: whole or narrowed, and in how many calls.

A check declares its transport and its threshold
([livery.workshop._checks.CheckRecord][]); this module is what the
engine does with them. `runs_whole` decides when a narrowed check
should run over its configured whole instead, `batches` splits a path
list into the fewest calls the platform's command line allows, and
`run_batched` runs each call and merges their failures into one
refusal, so a long list never fails on the operating system's limit
and never stops at the first failing call.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from livery.footman import Failed, fail

#: How a path list reaches the tool: on its command line.
ARGV = "argv"
#: In a file the tool reads, one path per line; named for the tools that
#: take one, and taken by none of the registered checks yet.
FILE = "file"
#: In a configuration file generated for the call; named likewise.
CONFIG = "config"
TRANSPORTS = (ARGV, FILE, CONFIG)

#: The bytes of path arguments one call may carry. Windows caps a whole
#: command line at 32767 characters; POSIX systems allow far more, and
#: the figure stays well under the smallest of them (macOS's ARG_MAX of
#: 1 MiB less the environment).
ARGV_LIMIT = 30_000 if sys.platform == "win32" else 200_000


def runs_whole(chosen: int, total: int, threshold: float) -> bool:
    """Whether a check over *chosen* of *total* units runs its whole instead.

    At or above *threshold*, the share of affected units, narrowing
    saves too little to be worth a list: the check runs over its
    configured whole. A threshold of 1 narrows until every unit is
    affected; nothing affected never runs whole.
    """
    if total <= 0 or chosen <= 0:
        return False
    return chosen / total >= threshold


def batches(
    paths: Sequence[str], *, limit: int = ARGV_LIMIT
) -> tuple[tuple[str, ...], ...]:
    """*paths* in the fewest calls whose arguments stay within *limit* bytes.

    In order: each call takes paths until the next would not fit. A
    path longer than the limit on its own goes in a call of its own,
    since there is no smaller way to pass it.
    """
    calls: list[tuple[str, ...]] = []
    current: list[str] = []
    size = 0
    for path in paths:
        length = len(path.encode("utf-8")) + 1  # the separating space or NUL
        if current and size + length > limit:
            calls.append(tuple(current))
            current, size = [], 0
        current.append(path)
        size += length
    if current:
        calls.append(tuple(current))
    return tuple(calls)


def run_batched(
    paths: Sequence[str],
    call: Callable[[tuple[str, ...]], None],
    *,
    limit: int = ARGV_LIMIT,
) -> None:
    """Run *call* once per batch of *paths*; one refusal naming every failure.

    Every batch runs even when an earlier one fails, so one gate shows
    every finding. An empty *paths* runs nothing.

    Raises:
        Failed: when any batch failed, with each failure's reason.
    """
    reasons: list[str] = []
    for batch in batches(paths, limit=limit):
        try:
            call(batch)
        except Failed as failure:
            reasons.append(str(failure))
    if len(reasons) == 1:
        fail(reasons[0])
    if reasons:
        fail(f"{len(reasons)} calls failed:\n" + "\n".join(reasons))
