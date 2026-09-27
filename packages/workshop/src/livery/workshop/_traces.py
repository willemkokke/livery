"""The legs' traces: what a run cost, kept where no sync brings it.

A profiled leg pushes the trace it wrote to a namespace of its own, outside
the one refspec a sync mirrors. A checkout pays nothing for traces nobody
asked for, and the gate reads none of them. The file is a Chrome trace of
that leg: every task, step and test it ran, and the wall-clock origin of its
own clock, so one leg's timeline can be laid beside another's.

The workspace contract decides, under ``[ci]``: ``profile-legs`` whether a
leg pushes at all, ``profile-window`` how many runs are kept, and
``profile-into`` where an assembled file lands.

The push is observational. A contract that says no, a trace that is not
there, and a push origin refuses are each one printed line, and none of them
decides a job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.workshop._state import (
    LEG_VARIABLE,
    TRACE_NAMESPACE,
    Keyed,
    RunContext,
    put,
    run_context,
)

#: The one file a leg puts on its trace ref: the trace as its writer
#: wrote it, byte for byte, so nothing re-serialises a megabyte to
#: store it and two legs of one run delta against each other in the
#: pack.
TRACE_FILE = "trace.json"

#: The contract's keys, under ``[ci]``.
LEGS_KEY = "profile-legs"
WINDOW_KEY = "profile-window"
INTO_KEY = "profile-into"

#: What the contract answers when it says nothing. Twenty runs is a
#: guess made before any run had pushed one; the window is measured
#: and tuned once real runs have.
LEGS_DEFAULT = True
WINDOW_DEFAULT = 20
INTO_DEFAULT = ".fm/profiles"


@dataclass(frozen=True)
class Policy:
    """What the contract says about the legs' traces.

    Attributes:
        legs: Whether a leg pushes its trace.
        window: How many runs' traces are kept.
        into: Where an assembled file lands, relative to the
            workspace root unless it is absolute.
    """

    legs: bool
    window: int
    into: str


def policy(root: Path) -> tuple[Policy, str]:
    """The contract's ``[ci] profile-*`` for *root*, and what it got wrong.

    A key of the wrong type is named with what it must be, and the
    default stands for it: this decides whether a timeline is kept, so
    a typo must be visible and must not stop a job.
    """
    from livery.workshop._contract import load_contract

    contract = load_contract(root / "workshop.toml")
    found: dict[str, Any] = contract.get("ci") or {}
    legs, why_legs = _flag(found, LEGS_KEY, LEGS_DEFAULT)
    window, why_window = _count(found, WINDOW_KEY, WINDOW_DEFAULT)
    into, why_into = _text(found, INTO_KEY, INTO_DEFAULT)
    return Policy(legs, window, into), "; ".join(
        why for why in (why_legs, why_window, why_into) if why
    )


def _flag(found: dict[str, Any], key: str, fallback: bool) -> tuple[bool, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    if isinstance(value, bool):
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is true or false"


def _count(found: dict[str, Any], key: str, fallback: int) -> tuple[int, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    # A bool is an int in Python and not a count here.
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is a whole number of runs"


def _text(found: dict[str, Any], key: str, fallback: str) -> tuple[str, str]:
    value = found.get(key)
    if value is None:
        return fallback, ""
    if isinstance(value, str) and value:
        return value, ""
    return fallback, f"[ci] {key} is {value!r}; it is a path"


def _kept_runs(root: Path) -> int | None:
    """How many runs of traces the contract keeps, for the janitor."""
    kept, _why = policy(root)
    return kept.window


#: One ref per leg of a run, keyed as the per-run metrics are, so the
#: two halves of one leg answer to the same key. The files are not
#: rows, so the count of runs is the whole bound the janitor applies.
TRACES = Keyed(
    # Under the traces' own namespace, so the ref reads
    # ``refs/workshop-trace/run/<run>/<leg>``, the per-run metrics'
    # shape one namespace over.
    "run",
    ("run", "leg"),
    namespace=TRACE_NAMESPACE,
    holds_rows=False,
    keep=_kept_runs,
)


def this_leg() -> str:
    """The leg this process belongs to, as the job runner named it."""
    return os.environ.get(LEG_VARIABLE, "")


def push(root: Path, trace: Path, *, run: RunContext | None = None) -> str:
    """Put *trace* on this leg's ref of the run; the line to print.

    Empty when the contract asks for no traces, so a workspace that
    wants none neither pushes nor says anything. Every other answer is
    a line: a trace that was never written, a leg the runner did not
    name, a run this is not, or origin's own refusal.
    """
    kept, why = policy(root)
    if not kept.legs:
        return ""
    if why:
        return f"profile: {why}"
    found = run or run_context()
    if found is None:
        return "profile: not a CI run; the trace stays on this machine"
    leg = this_leg()
    if not leg:
        return f"profile: {LEG_VARIABLE} names no leg; the trace is not pushed"
    if not trace.is_file():
        return f"profile: no trace at {trace}; nothing to push"
    series = TRACES.series(found.run_id, leg)
    refused = put(
        root,
        series.ref,
        {TRACE_FILE: trace.read_text(encoding="utf-8")},
        message=f"trace: {leg} of run {found.run_id}",
        ci_only=True,
    )
    if refused:
        return f"profile: the trace was not pushed ({refused})"
    size = trace.stat().st_size
    return f"profile: {size // 1024} KiB pushed to {series.ref}"
