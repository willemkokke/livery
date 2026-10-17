"""The runner footprint: the runner-minutes the metrics window holds, by runner label.

What ``fm ci.footprint`` prints. Each row of the ``metrics`` series
([livery.workshop._metrics.SERIES][]) is one run with the forge's wall
time of every job. A job whose display name lists its matrix, ``check
(macos-latest, 3.14)`` or ``check (windows-latest, 3.14, 2of4)``, ran
on the runner the first item names; every other job (the gate job, the
docs build, the release jobs, the matrix picker) ran on the first
runner ``[ci] runners`` names, where the workflow places them. The
folded row of a platform's shards is left out: the forge never ran it,
and the shards' own rows carry the minutes. A job without a wall time,
still running when the gate job collected or never started, is counted
apart and priced as nothing.

The price is GitHub's, for the standard runners of a private
repository; `fm ci.footprint` decides when to print it. A public
repository on GitHub is not billed for them, so the price says what
the same minutes would cost a private one.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from livery.workshop._contract import load_contract

#: GitHub's rates in USD per minute for the standard runners of a
#: private repository, by the class a runner label names: Linux
#: 2-core, Windows 2-core, macOS 4-core (M1), as the page
#: https://docs.github.com/en/billing/reference/actions-runner-pricing
#: lists them. Larger runners cost more per minute and are not priced
#: here; a public repository is not billed for standard runners at all.
RATES: Mapping[str, float] = {"linux": 0.006, "windows": 0.010, "macos": 0.062}

#: The page the rates come from, printed under the price.
SOURCE = "https://docs.github.com/en/billing/reference/actions-runner-pricing"

#: The two kinds a line sums: the check matrix's legs, and every other job.
LEGS = "check legs"
OTHER = "other jobs"

#: A matrix job's display name: the job, then its runner first inside
#: the parentheses, then at least one more item (the Python, a shard).
_MATRIX = re.compile(r"^[^ (]+ \((?P<runner>[^,)]+), [^)]+\)$")


@dataclass(frozen=True)
class Line:
    """The jobs and minutes of one kind on one runner label.

    Attributes:
        label: The runner label, ``macos-latest``.
        kind: `LEGS` for the check matrix's legs, `OTHER` for every
            other job, counted on the first runner ``[ci] runners``
            names.
        jobs: How many jobs with a wall time the line sums.
        minutes: Their summed wall time in minutes.
    """

    label: str
    kind: str
    jobs: int
    minutes: float

    @property
    def runner_class(self) -> str:
        """The class the label names, a key of `RATES`; empty for none."""
        return runner_class(self.label)

    @property
    def usd(self) -> float | None:
        """The line's minutes at the class's rate; None for a label of no class."""
        rate = RATES.get(self.runner_class)
        return None if rate is None else self.minutes * rate


@dataclass(frozen=True)
class Footprint:
    """The runner-minutes of a window of runs, by runner label and kind.

    Attributes:
        runs: How many runs the window holds.
        first_day: The day of the oldest run, ISO; empty when no run
            says when it was created.
        last_day: The day of the newest run, ISO; empty likewise.
        span_days: The days from the oldest run to the newest, at
            least one: the rate per 30 days divides by it.
        lines: One per runner label and kind, by label then kind.
        untimed: The jobs without a wall time, counted apart.
    """

    runs: int
    first_day: str
    last_day: str
    span_days: float
    lines: tuple[Line, ...]
    untimed: int

    @property
    def jobs(self) -> int:
        """Every job the window holds, the ones without a wall time included."""
        return sum(line.jobs for line in self.lines) + self.untimed

    @property
    def minutes(self) -> float:
        """The summed runner-minutes."""
        return sum(line.minutes for line in self.lines)

    @property
    def usd(self) -> float:
        """The priced lines' USD; a label of no class adds nothing."""
        return sum(line.usd or 0.0 for line in self.lines)

    @property
    def unpriced(self) -> tuple[Line, ...]:
        """The lines whose label names no class."""
        return tuple(line for line in self.lines if line.usd is None)

    def per_30_days(self, value: float) -> float:
        """*value*, a total over the window, as a rate per 30 days."""
        return value * 30.0 / self.span_days


def runner_class(label: str) -> str:
    """The class *label* names: ``linux``, ``windows``, ``macos``, or empty.

    By substring of the lowercased label: ``ubuntu`` or ``linux`` is
    linux, ``windows`` is windows, ``macos`` is macos. A label naming
    none, a self-hosted runner's say, is empty, and prices as nothing.
    """
    lowered = label.lower()
    if "ubuntu" in lowered or "linux" in lowered:
        return "linux"
    if "windows" in lowered:
        return "windows"
    if "macos" in lowered:
        return "macos"
    return ""


def runner_of(display: str, *, default: str) -> tuple[str, str]:
    """The runner label the job *display* ran on, and the kind of line it counts in.

    A matrix job, ``check (macos-latest, 3.14)`` or ``check
    (windows-latest, 3.14, 2of4)``, ran on the first item in its
    parentheses and counts as `LEGS`. Every other display name ran on
    *default* and counts as `OTHER`.
    """
    matched = _MATRIX.match(display)
    if matched is None:
        return default, OTHER
    return matched["runner"].strip(), LEGS


def _folded(row: Mapping[str, Any]) -> bool:
    """Whether *row* is a fold's own row: its ``shard`` lists the shards."""
    shard = row.get("shard")
    return isinstance(shard, Mapping) and "shards" in shard


def _moment(entry: Mapping[str, Any]) -> datetime | None:
    stamp = str(entry.get("created_at") or entry.get("when") or "")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def footprint(
    entries: Sequence[Mapping[str, Any]], *, default_runner: str
) -> Footprint:
    """Sum the runner-minutes of *entries*, the rows of a window of runs.

    Every job of every row is counted on one runner label, as
    `runner_of` attributes it with *default_runner*; a fold's own row
    is left out, since the shards' rows carry its minutes. A job whose
    ``wall_ms`` is no number is counted apart. The span is from the
    oldest row's creation to the newest, at least one day; a row that
    says no moment is left out of it.
    """
    jobs: dict[tuple[str, str], int] = defaultdict(int)
    minutes: dict[tuple[str, str], float] = defaultdict(float)
    untimed = 0
    for entry in entries:
        rows = entry.get("jobs")
        if not isinstance(rows, Mapping):
            continue
        for display, row in rows.items():
            if not isinstance(row, Mapping) or _folded(row):
                continue
            wall = row.get("wall_ms")
            if not isinstance(wall, int | float):
                untimed += 1
                continue
            key = runner_of(str(display), default=default_runner)
            jobs[key] += 1
            minutes[key] += float(wall) / 60_000.0
    moments = sorted(m for m in (_moment(entry) for entry in entries) if m is not None)
    first, last = (moments[0], moments[-1]) if moments else (None, None)
    span = (last - first).total_seconds() / 86_400.0 if first and last else 0.0
    return Footprint(
        runs=len(entries),
        first_day=first.date().isoformat() if first else "",
        last_day=last.date().isoformat() if last else "",
        span_days=max(1.0, span),
        lines=tuple(
            Line(label, kind, jobs[label, kind], minutes[label, kind])
            for label, kind in sorted(jobs)
        ),
        untimed=untimed,
    )


def default_runner(root: Path) -> str:
    """The first runner the root contract's ``[ci] runners`` names.

    ``ubuntu-latest`` when the contract names none, as the workflow
    renders it.
    """
    ci = load_contract(root / "workshop.toml").get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    return runners[0]


def render(summed: Footprint, *, default_runner: str) -> list[str]:
    """The footprint as ``fm ci.footprint`` prints it: the window, a table, the rate.

    The window line gives the runs, the days and the span. One table
    line per runner label and kind, the total, and a line for the
    jobs without a wall time when there are any. Then the minutes per
    30 days from the span.
    """
    days = (
        f", {summed.first_day} to {summed.last_day}"
        if summed.first_day and summed.last_day
        else ""
    )
    lines = [
        f"  {summed.runs} run(s) in the window{days}, {summed.span_days:.1f} day(s);"
        f" {summed.jobs} job(s)",
        f"  a job outside the check matrix is counted on {default_runner}, the first"
        " of [ci] runners, where the workflow places it",
        f"  {'label':<24} {'kind':<12} {'jobs':>6} {'minutes':>9}",
    ]
    lines.extend(
        f"  {line.label:<24} {line.kind:<12} {line.jobs:>6} {line.minutes:>9.1f}"
        for line in summed.lines
    )
    timed = summed.jobs - summed.untimed
    lines.append(f"  {'total':<24} {'':<12} {timed:>6} {summed.minutes:>9.1f}")
    if summed.untimed:
        lines.append(
            f"  {'no wall time':<24} {'':<12} {summed.untimed:>6} {'-':>9}   still"
            " running when the gate job collected, or never started"
        )
    lines.append(
        f"  rate: {summed.per_30_days(summed.minutes):.0f} minutes per 30 days"
    )
    return lines


def price(summed: Footprint) -> list[str]:
    """The footprint priced at GitHub's rates for a private repository, as printed.

    Only for a public repository on GitHub, which is not billed for
    standard runners: the table says what the same minutes would cost
    a private one, one line per runner label and kind at its class's
    rate, a label of no class named as unpriced, then the total, the
    USD per 30 days, and the source page.
    """
    lines = [
        "  a public repository on GitHub is not billed for standard runners; the"
        " same minutes on a private one, at GitHub's rates:",
        f"  {'label':<24} {'kind':<12} {'class':<8} {'USD/min':>8} {'USD':>9}",
    ]
    for line in summed.lines:
        usd = line.usd
        if usd is None:
            lines.append(
                f"  {line.label:<24} {line.kind:<12} unpriced: the label names no"
                " class (ubuntu or linux, windows, macos)"
            )
            continue
        lines.append(
            f"  {line.label:<24} {line.kind:<12} {line.runner_class:<8}"
            f" {RATES[line.runner_class]:>8.3f} {usd:>9.2f}"
        )
    lines.append(f"  {'total':<24} {'':<12} {'':<8} {'':>8} {summed.usd:>9.2f}")
    lines.append(
        f"  rate: {summed.per_30_days(summed.usd):.2f} USD per 30 days; larger"
        " runners cost more per minute"
    )
    lines.append(f"  source: {SOURCE}")
    return lines
