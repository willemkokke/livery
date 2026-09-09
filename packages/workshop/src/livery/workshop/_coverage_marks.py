"""The coverage marks: a package's high-water mark, and how it moves.

A package whose contract declares ``[qa] coverage-floor = "auto-ratchet"``
is judged against the mark recorded on the CI state store instead of a
committed number. Enforcement passes at the mark minus the package's
epsilon (``[qa] coverage-epsilon``, in percentage points, 0.5 when
absent), and the mark ratchets up only when a run clears it by more than
epsilon, so the scheduling wobble of a parallel suite neither fails the
gate nor moves the mark. The first run records the mark and never
judges; a store that cannot be read falls open with its reason and
never writes, since a write from an unread state would erase the marks
it could not see.

Lowering a mark is an explicit act: ``fm coverage.accept`` writes a
dated, reasoned row, and the gate judges from it. Every row names the
run or the person that wrote it, so the trail of a package's floor is
readable on the store. Reach for [livery.workshop._coverage_marks.marks][]
to read the current marks and [livery.workshop._coverage_marks.judge][]
to decide a run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery.workshop._coverage_store import slug
from livery.workshop._state import Series, put, read

#: The marks: one file per row, the newest kept per package by the window.
SERIES = Series("coverage/marks", window=400, ci_only=False)

#: The row shape; another schema reads as no mark, named.
SCHEMA = 1

#: The floor value that selects the mode.
AUTO_RATCHET = "auto-ratchet"

#: The tolerance when a package declares none, in percentage points.
DEFAULT_EPSILON = 0.5


@dataclass(frozen=True)
class Mark:
    """A package's current mark and the row that set it.

    Attributes:
        package: The package path (``packages/forge``).
        value: The mark, in percent.
        kind: ``ratchet`` (a run raised it), ``first`` (a run recorded it),
            or ``accept`` (a person lowered it).
        by: The run id or the person that wrote the row.
        reason: The reason an accept gave; empty otherwise.
        when: When the row was written, ISO 8601.
    """

    package: str
    value: float
    kind: str
    by: str
    reason: str
    when: str


@dataclass(frozen=True)
class Verdict:
    """One package's answer under auto-ratchet.

    Attributes:
        package: The package path.
        measured: The union's percentage for the package.
        mark: The current mark, or ``None`` when none is recorded.
        epsilon: The tolerance, in percentage points.
        raises: Whether the run clears the mark by more than epsilon.
    """

    package: str
    measured: float
    mark: Mark | None
    epsilon: float

    @property
    def floor(self) -> float | None:
        """The lowest percentage that passes, or ``None`` without a mark."""
        return None if self.mark is None else self.mark.value - self.epsilon

    @property
    def ok(self) -> bool:
        """Whether the package passes: no mark yet, or at least the floor."""
        return self.floor is None or self.measured >= self.floor

    @property
    def raises(self) -> bool:
        """Whether the run sets a new mark: the first, or a clear rise."""
        return self.mark is None or self.measured > self.mark.value + self.epsilon


def _row_name(when: datetime, package: str) -> str:
    return f"{when.strftime('%Y%m%dT%H%M%S.%fZ')}--{slug(package)}"


def marks(root: Path) -> tuple[dict[str, Mark] | None, str]:
    """The current mark per package; ``(None, reason)`` when the store could not answer.

    An empty store is ``({}, "")``: the first run writes. A read the
    transport could not answer returns its reason, and a caller that
    would write refuses on it. A row that does not parse or is of
    another schema is skipped, so one bad row disables nothing.
    """
    found = read(root, SERIES.ref)
    if found.files is None:
        if found.failed:
            return None, f"the marks could not be read: {found.reason}"
        return {}, ""
    current: dict[str, Mark] = {}
    for name in sorted(found.files):
        try:
            row: Any = json.loads(found.files[name])
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            continue
        try:
            mark = Mark(
                package=str(row["package"]),
                value=float(row["value"]),
                kind=str(row.get("kind", "")),
                by=str(row.get("by", "")),
                reason=str(row.get("reason", "")),
                when=str(row.get("when", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
        current[mark.package] = mark
    return current, ""


def write_mark(
    root: Path,
    *,
    package: str,
    value: float,
    kind: str,
    by: str,
    reason: str = "",
    ci_only: bool,
) -> str:
    """Append one row setting *package*'s mark; ``""`` or why it was not written."""
    now = datetime.now(UTC)
    row = {
        "schema": SCHEMA,
        "package": package,
        "value": round(value, 2),
        "kind": kind,
        "by": by,
        "reason": reason,
        "when": now.isoformat(timespec="seconds"),
    }
    return put(
        root,
        SERIES.ref,
        {_row_name(now, package): json.dumps(row, sort_keys=True)},
        message=f"coverage mark: {package} {kind} {value:.2f} by {by}",
        window=SERIES.window,
        ci_only=ci_only,
    )


def judge(
    measured: dict[str, float],
    current: dict[str, Mark],
    epsilons: dict[str, float],
) -> list[Verdict]:
    """Compare the measured packages against their marks; one verdict each."""
    return [
        Verdict(
            package=package,
            measured=percent,
            mark=current.get(package),
            epsilon=epsilons.get(package, DEFAULT_EPSILON),
        )
        for package, percent in sorted(measured.items())
    ]


def render(verdict: Verdict) -> str:
    """One line for *verdict*: the measured value, the mark, the floor, the move."""
    if verdict.mark is None:
        return (
            f"  coverage {verdict.package}: {verdict.measured:.1f}% (no mark yet;"
            " this run records it)"
        )
    state = "" if verdict.ok else " BELOW THE FLOOR"
    line = (
        f"  coverage {verdict.package}: {verdict.measured:.1f}% (mark"
        f" {verdict.mark.value:.1f}% {verdict.mark.kind} by {verdict.mark.by},"
        f" floor {verdict.floor:.1f}%, epsilon {verdict.epsilon}){state}"
    )
    if verdict.mark.reason:
        line += f"\n    accepted: {verdict.mark.reason}"
    if verdict.raises:
        line += f"\n    new mark: {verdict.measured:.1f}%"
    return line
