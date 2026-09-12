"""The test speed marks: a package's suite time per check leg, and how it moves.

The gate job judges each check leg's summed test time per package, the
``packages`` field of the timing rows ([livery.workshop._metrics][]),
against a mark on the ``speed/marks`` series. The mark is the median of
the leg's last five green runs for the package, recorded once five are
seen; it ratchets down when that median beats it by more than five per
cent, so a suite cannot regress slowly, and a person raises it with
``fm speed.accept``, since a new heavy test is a cost someone chose.

A run over the mark by more than fifteen per cent and twenty seconds
warns, naming the leg's slowest tests and the ones that grew most
against their own medians; the second such run in a row on a reference
leg (ubuntu) is red, and the other legs warn only until their variance
is measured. A store that cannot be read falls open with its reason and
writes nothing. The margins are constants here, chosen before the rows
could set them; a reading of a month of rows may move them.

Reach for `judge_run` to judge the run the gate job is in, `accept` for
the person's raise, and `render_marks` for what ``fm ci.timings`` adds.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery.footman import fail
from livery.workshop._state import RunContext, Series, slug

#: The marks: one row per write, the newest per package and leg the
#: mark; anyone may write, since an accept is a person's act, and the
#: gate job's writer guards its own rule that only a run records or
#: ratchets.
SERIES = Series("speed/marks", window=400, ci_only=False)

#: The green runs behind a mark: the median of this many is the mark.
SAMPLE = 5

#: Over the mark by more than this fraction ...
MARGIN = 0.15

#: ... and by more than this many seconds is over.
FLOOR_SECONDS = 20.0

#: The sample's median under the mark by more than this fraction
#: moves the mark down to it.
RATCHET = 0.05

#: The runner whose legs are red on the second run over; every other
#: leg warns only.
REFERENCE = "ubuntu"

#: The contract key that turns the speed marks on.
ENABLED_KEY = "speed-marks"


def enabled(root: Path) -> bool:
    """The contract's ``[ci] speed-marks``; false when undeclared.

    Off by default: a hosted runner's test time varies by tens of
    seconds between two runs of one tree, and a mark taken from such
    runs says nothing about the suite. A workspace whose runners keep
    a steady clock declares the key. Refuses a value that is not a
    boolean, naming the key.
    """
    from livery.workshop._contract import load_contract

    ci = load_contract(root / "workshop.toml").get("ci") or {}
    declared = ci.get(ENABLED_KEY, False) if isinstance(ci, dict) else False
    if not isinstance(declared, bool):
        fail(f"[ci] {ENABLED_KEY} must be true or false, not {declared!r}")
    return declared


#: The slowest tests a leg's row names, and a warning prints.
SLOWEST = 10

#: The tests that grew most a warning prints.
GROWN = 5

_DISPLAY = re.compile(r"^(?P<job>[^ (]+) \((?P<os>[^,]+), (?P<python>[^)]+)\)$")


def leg_label(display: str) -> str:
    """The leg label of a matrix job's display name; a plain name stands.

    ``check (ubuntu-latest, 3.14)`` is the leg ``check-ubuntu-latest-3.14``,
    the spelling the job runner gives every entry it spawns.
    """
    match = _DISPLAY.match(display)
    if match is None:
        return display
    return f"{match['job']}-{match['os']}-{match['python']}"


def is_reference(leg: str) -> bool:
    """Whether *leg* runs on the reference runner, the one whose red counts."""
    return REFERENCE in leg


@dataclass(frozen=True)
class Mark:
    """A package's mark on one leg and the row that set it.

    Attributes:
        package: The package path (``packages/forge``).
        leg: The check leg (``check-ubuntu-latest-3.14``).
        seconds: The mark: the suite's summed test time.
        kind: ``first`` (a run recorded it), ``ratchet`` (a run moved
            it down), or ``accept`` (a person raised it).
        by: The run or the person that wrote the row.
        reason: The reason an accept gave; empty otherwise.
        when: When the row was written, ISO 8601.
    """

    package: str
    leg: str
    seconds: float
    kind: str
    by: str
    reason: str
    when: str


@dataclass(frozen=True)
class Sample:
    """One package's time on one leg in this run, with the leg's history.

    Attributes:
        package: The package path.
        leg: The check leg.
        seconds: This run's summed test time for the package.
        tests: How many tests ran.
        previous: The leg's earlier green runs' times for the package,
            newest first, at most one fewer than `SAMPLE`.
        slowest: This run's slowest tests, name and seconds, slowest first.
        grown: The tests that grew most against their median over the
            earlier runs: name, the median, this run's seconds.
    """

    package: str
    leg: str
    seconds: float
    tests: int
    previous: tuple[float, ...]
    slowest: tuple[tuple[str, float], ...] = ()
    grown: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True)
class Verdict:
    """One package on one leg, judged against its mark.

    Attributes:
        sample: The run's time and the leg's history.
        mark: The current mark, or ``None`` when none is recorded.
    """

    sample: Sample
    mark: Mark | None

    @property
    def limit(self) -> float | None:
        """The seconds above which a run is over; ``None`` without a mark."""
        if self.mark is None:
            return None
        return round(
            max(self.mark.seconds * (1 + MARGIN), self.mark.seconds + FLOOR_SECONDS), 1
        )

    @property
    def over(self) -> bool:
        """Whether this run is over the mark by the margin."""
        return self.limit is not None and self.sample.seconds > self.limit

    @property
    def previous_over(self) -> bool:
        """Whether the leg's newest earlier green run was over too."""
        return (
            self.limit is not None
            and bool(self.sample.previous)
            and self.sample.previous[0] > self.limit
        )

    @property
    def red(self) -> bool:
        """The second run over in a row, on a reference leg."""
        return self.over and self.previous_over and is_reference(self.sample.leg)

    @property
    def median(self) -> float | None:
        """The median of the sample, this run included; ``None`` short of one."""
        values = (self.sample.seconds, *self.sample.previous)
        if len(values) < SAMPLE:
            return None
        return round(statistics.median(values[:SAMPLE]), 1)

    @property
    def records(self) -> bool:
        """Whether this run records the first mark: no mark, a full sample."""
        return self.mark is None and self.median is not None

    @property
    def ratchets(self) -> bool:
        """Whether the sample's median beats the mark by more than `RATCHET`."""
        return (
            self.mark is not None
            and self.median is not None
            and self.median < self.mark.seconds * (1 - RATCHET)
        )


def _row_name(when: datetime, package: str, leg: str) -> str:
    return f"{when.strftime('%Y%m%dT%H%M%S.%fZ')}--{slug(package)}--{slug(leg)}"


def marks(root: Path) -> tuple[dict[tuple[str, str], Mark] | None, str]:
    """The current mark per package and leg; ``(None, reason)`` when unreadable.

    An empty store is ``({}, "")``. A row that does not parse, is of
    another schema, or lacks a mark's fields is skipped, so one bad
    row disables nothing. The newest row per package and leg is its
    mark.
    """
    found = SERIES.rows(root)
    if found.failed:
        return None, found.reason
    current: dict[tuple[str, str], Mark] = {}
    for row in found.rows:
        mark = _mark(row.data)
        if mark is not None:
            current.setdefault((mark.package, mark.leg), mark)
    return current, ""


def _mark(data: dict[str, Any]) -> Mark | None:
    try:
        return Mark(
            package=str(data["package"]),
            leg=str(data["leg"]),
            seconds=float(data["seconds"]),
            kind=str(data.get("kind", "")),
            by=str(data.get("by", "")),
            reason=str(data.get("reason", "")),
            when=str(data.get("when", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_mark(
    root: Path,
    *,
    package: str,
    leg: str,
    seconds: float,
    kind: str,
    by: str,
    reason: str = "",
) -> str:
    """Append one row setting *package*'s mark on *leg*; ``""`` or why not."""
    row = {
        "package": package,
        "leg": leg,
        "seconds": round(seconds, 1),
        "kind": kind,
        "by": by,
        "reason": reason,
    }
    return SERIES.put(
        root,
        {_row_name(datetime.now(UTC), package, leg): row},
        message=f"speed mark: {package} on {leg} {kind} {seconds:.1f}s by {by}",
    )


def _package_times(row: dict[str, Any]) -> dict[str, tuple[float, int]]:
    """Each package's summed seconds and test count in a leg's row; ran ones only."""
    out: dict[str, tuple[float, int]] = {}
    packages = row.get("packages")
    if not isinstance(packages, dict):
        return out
    for name, data in packages.items():
        if not isinstance(data, dict):
            continue
        tests = data.get("tests", 0)
        ms = data.get("tests_ms", 0.0)
        if isinstance(tests, int) and tests > 0 and isinstance(ms, int | float):
            out[f"packages/{name}"] = (round(float(ms) / 1000.0, 1), tests)
    return out


def _slowest(row: dict[str, Any]) -> dict[str, float]:
    """The slowest tests a leg's row names, seconds by node id."""
    out: dict[str, float] = {}
    for item in row.get("slowest", []) or []:
        if isinstance(item, dict) and isinstance(item.get("s"), int | float):
            out[str(item.get("test", ""))] = float(item["s"])
    return out


def _package_of(nodeid: str) -> str:
    parts = nodeid.split("::", 1)[0].split("/")
    return f"packages/{parts[1]}" if len(parts) >= 2 and parts[0] == "packages" else ""


def _grown(
    now: dict[str, float], earlier: list[dict[str, float]], package: str
) -> tuple[tuple[str, float, float], ...]:
    """The tests of *package* that grew most against their median in *earlier*."""
    history: dict[str, list[float]] = defaultdict(list)
    for row in earlier:
        for test, seconds in row.items():
            history[test].append(seconds)
    grown: list[tuple[float, str, float, float]] = []
    for test, seconds in now.items():
        if _package_of(test) != package or test not in history:
            continue
        before = statistics.median(history[test])
        if seconds > before * (1 + MARGIN) and seconds > before + 1.0:
            grown.append((seconds - before, test, round(before, 2), seconds))
    grown.sort(reverse=True)
    return tuple((test, before, now_s) for _, test, before, now_s in grown[:GROWN])


def samples(entries: list[dict[str, Any]], run_id: str) -> list[Sample]:
    """This run's package times per check leg, with each leg's green history.

    *entries* are the timing rows newest first. The run's own entry
    is the one whose ``run`` is *run_id*; a check leg of it yields one
    sample per package whose tests ran. The history is the same leg's
    earlier entries that concluded green and ran the package's tests,
    newest first, up to one fewer than `SAMPLE`. Empty when the run
    has no entry.
    """
    current = next((e for e in entries if str(e.get("run", "")) == run_id), None)
    if current is None:
        return []
    earlier = [
        e for e in entries if e is not current and str(e.get("run", "")) != run_id
    ]
    out: list[Sample] = []
    for job, row in sorted((current.get("jobs") or {}).items()):
        if not job.startswith("check") or not isinstance(row, dict):
            continue
        leg = leg_label(job)
        now_slowest = _slowest(row)
        for package, (seconds, tests) in sorted(_package_times(row).items()):
            previous: list[float] = []
            slow_rows: list[dict[str, float]] = []
            for entry in earlier:
                past = (entry.get("jobs") or {}).get(job)
                if not isinstance(past, dict) or past.get("conclusion") != "success":
                    continue
                times = _package_times(past)
                if package not in times:
                    continue
                previous.append(times[package][0])
                slow_rows.append(_slowest(past))
                if len(previous) >= SAMPLE - 1:
                    break
            slowest = tuple(
                sorted(
                    (
                        (t, s)
                        for t, s in now_slowest.items()
                        if _package_of(t) == package
                    ),
                    key=lambda item: -item[1],
                )[:SLOWEST]
            )
            out.append(
                Sample(
                    package=package,
                    leg=leg,
                    seconds=seconds,
                    tests=tests,
                    previous=tuple(previous),
                    slowest=slowest,
                    grown=_grown(now_slowest, slow_rows, package),
                )
            )
    return out


def judge(found: list[Sample], current: dict[tuple[str, str], Mark]) -> list[Verdict]:
    """Each sample against its mark; one verdict each."""
    return [
        Verdict(sample=sample, mark=current.get((sample.package, sample.leg)))
        for sample in found
    ]


def render(verdict: Verdict) -> list[str]:
    """The lines for *verdict*: the time, the mark, the state, the culprits."""
    sample, mark = verdict.sample, verdict.mark
    head = f"  speed {sample.package} on {sample.leg}: {sample.seconds:.1f}s"
    if mark is None:
        seen = len(sample.previous) + 1
        if verdict.records:
            return [f"{head}, no mark yet; this run records {verdict.median:.1f}s"]
        return [f"{head}, no mark yet ({seen}/{SAMPLE} green runs)"]
    limit = verdict.limit
    assert limit is not None
    state = ""
    if verdict.red:
        state = " OVER for the second run in a row: red"
    elif verdict.over and not is_reference(sample.leg):
        state = " OVER (this leg warns only)"
    elif verdict.over:
        state = " OVER (a second run over in a row is red)"
    lines = [
        f"{head} (mark {mark.seconds:.1f}s {mark.kind} by {mark.by},"
        f" limit {limit:.1f}s){state}"
    ]
    if mark.reason:
        lines.append(f"    accepted: {mark.reason}")
    if verdict.over:
        if sample.slowest:
            named = ", ".join(f"{test} {s:.1f}s" for test, s in sample.slowest)
            lines.append(f"    slowest: {named}")
        if sample.grown:
            named = ", ".join(
                f"{test} {before:.1f}s -> {now:.1f}s"
                for test, before, now in sample.grown
            )
            lines.append(f"    grew: {named}")
    if verdict.ratchets:
        lines.append(
            f"    new mark: {verdict.median:.1f}s (the median of {SAMPLE} beats"
            f" {mark.seconds:.1f}s by more than {RATCHET:.0%})"
        )
    return lines


def judge_run(root: Path, run: RunContext) -> tuple[list[Verdict], list[str]]:
    """Judge the run's check legs; the verdicts and the lines for what could not be.

    Reads the timing rows and the marks; either unreadable falls open
    with its reason and no verdict, so a store fault never reddens a
    run and never writes.
    """
    from livery.workshop._metrics import SERIES as METRICS

    rows = METRICS.rows(root)
    if rows.failed:
        return [], [f"  speed: not judged ({rows.reason})"]
    current, why = marks(root)
    if current is None:
        return [], [f"  speed: not judged ({why})"]
    found = samples([row.data for row in rows.rows], run.run_id)
    if not found:
        return [], [
            f"  speed: run {run.run_id} left no check leg with tests; nothing to judge"
        ]
    return judge(found, current), []


def apply(root: Path, verdicts: list[Verdict], *, by: str) -> list[str]:
    """Write the first marks and the ratchets among *verdicts*; the lines."""
    lines: list[str] = []
    for verdict in verdicts:
        if not (verdict.records or verdict.ratchets):
            continue
        median = verdict.median
        assert median is not None
        kind = "first" if verdict.records else "ratchet"
        why = write_mark(
            root,
            package=verdict.sample.package,
            leg=verdict.sample.leg,
            seconds=median,
            kind=kind,
            by=by,
        )
        lines.append(
            f"    {'recorded' if not why else 'not recorded: ' + why}"
            f" ({verdict.sample.package} on {verdict.sample.leg}, {kind} {median:.1f}s)"
        )
    return lines


def accept(
    root: Path,
    *,
    package: str,
    leg: str,
    seconds: float,
    reason: str,
    by: str,
    packages: tuple[str, ...],
) -> list[str]:
    """Raise *package*'s mark on *leg* to *seconds* deliberately; the lines.

    Refuses without a reason, for a path not among *packages*, a time
    that is not positive, an unreadable store, and a value at or below
    the current mark: lowering is the ratchet's own move.
    """
    if not reason.strip():
        fail(
            "a reason is required: --reason=<why the suite may take longer> goes"
            " on the record beside the new mark"
        )
    if package not in packages:
        fail(f"no package at {package!r}; the packages are {', '.join(packages)}")
    if seconds <= 0:
        fail(f"the mark is a time in seconds; {seconds!r} is not")
    current, why = marks(root)
    if current is None:
        fail(f"refusing: {why}; a write from an unread store would erase its rows")
    mark = current.get((package, leg))
    if mark is not None and seconds <= mark.seconds:
        fail(
            f"{package}'s mark on {leg} is {mark.seconds:.1f}s; {seconds:.1f}s does"
            " not raise it. Lowering is the ratchet's own move, when the runs beat"
            " the mark."
        )
    written = write_mark(
        root,
        package=package,
        leg=leg,
        seconds=seconds,
        kind="accept",
        by=by,
        reason=reason.strip(),
    )
    if written:
        fail(f"the mark was not written: {written}")
    was = f"{mark.seconds:.1f}s" if mark is not None else "none"
    return [
        f"  speed {package} on {leg}: mark {was} -> {seconds:.1f}s accepted by {by}",
        f"    reason: {reason.strip()}",
    ]


def render_marks(root: Path) -> list[str]:
    """The marks beside the newest run's times, for ``fm ci.timings``."""
    from livery.workshop._metrics import SERIES as METRICS

    current, why = marks(root)
    if current is None:
        return [f"  speed marks: not read ({why})"]
    if not current:
        return ["  speed marks: none yet; the gate job records one per package and leg"]
    latest: dict[tuple[str, str], float] = {}
    rows = METRICS.rows(root)
    if not rows.failed and rows.rows:
        newest = rows.rows[0].data
        for job, row in (newest.get("jobs") or {}).items():
            if isinstance(row, dict):
                for package, (seconds, _tests) in _package_times(row).items():
                    latest[(package, leg_label(job))] = seconds
    lines = ["  speed marks", f"    {'package on leg':<52} {'mark':>8} {'latest':>8}"]
    for (package, leg), mark in sorted(current.items()):
        now = latest.get((package, leg))
        shown = f"{now:.1f}s" if now is not None else "-"
        lines.append(
            f"    {f'{package} on {leg}'[:52]:<52} {mark.seconds:7.1f}s {shown:>8}"
            f"  {mark.kind} by {mark.by}"
        )
    return lines
