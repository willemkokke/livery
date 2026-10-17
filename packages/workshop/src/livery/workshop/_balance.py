"""Predict each check platform's wall at one to several shards, and propose the counts.

The reader behind ``fm ci.balance``. It reads the timing rows the
gate job writes ([livery.workshop._metrics][]) per platform: the os
and Python of a check job's display name, ``check (macos-latest,
3.14)``; ``check (macos-latest, 3.14, 2of4)`` is one shard of that
platform, and a GitLab job's bare name reads its platform from the
row's own leg label. Per platform it takes the newest ten rows whose
jobs for it all ran the full gate and succeeded, folds the shards of
one platform into one, and takes the medians of four measures:

- F, the fixed setup: every step the forge timed except the ``Check``
  call, from the first shard, in seconds.
- O, the longest step of the gate that is not a test step, from the
  first shard. The gate's steps are the job's ``check/<phase>.<step>``
  tasks, and the test steps are the test phase's,
  [livery.workshop._balance.TEST_PHASE][].
- S, the summed test time over every package and every shard.
- p, the measured parallelism: a shard's S over its test steps' wall,
  the median over shards.

The wall at n shards is predicted as F + max(O, S / (p n)): the first
shard runs the unsplit steps beside its slice, so no count brings a
platform under F + O. The runner seconds at n are about n F + S / p +
O. The slowest platform, the one with the largest wall at one shard,
takes the largest count within the cap whose every added shard saved
at least F, so a shard that costs more setup than it saves is not
proposed. Every other platform takes the smallest count whose wall is
within [livery.workshop._balance.TOLERANCE][] of the slowest
platform's at its count; one that cannot get there at the cap takes
the cap and is named. The pull request counts apply the same rule
over a pull request's runners when they are fewer than ``[ci]
runners`` names. A platform with fewer than
[livery.workshop._balance.ROWS_NEEDED][] readable rows keeps its count
and takes no part. The verb changes nothing: it prints the table and
the TOML the contract would hold, and a count lands by pull request.
The constants here were chosen before the rows could set them, as the
speed marks' margins were ([livery.workshop._speed][]).

Reach for `render` for what the verb prints, `readings` for the
measures from rows already in hand, `wall` and `runner_seconds` for
the prediction, and `propose` for the counts.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from typing import Any

from livery.workshop._ci_context import state_store
from livery.workshop._contract import load_contract
from livery.workshop._metrics import SERIES
from livery.workshop._points import pull_request_runners

#: The step the forge times for the gate's own call. Every other step
#: the forge times is the job's setup and teardown, F.
CALL_STEP = "Check"

#: The gate's steps among a job's tasks: the task ``check`` and the
#: step's title, ``<phase>.<step>``. A task under ``check/`` spelled
#: otherwise is a step's own command and is not read.
GATE_STEPS = re.compile(r"^check/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

#: The test phase's steps, the ones the shards split, start with this.
#: Every other gate step runs whole on the first shard.
TEST_PHASE = "check/test."

#: The newest full green rows read per platform.
ROWS_READ = 10

#: A platform with fewer readable rows than this keeps its count: a
#: median of two says little. Chosen before the rows could set it.
ROWS_NEEDED = 3

#: Another platform's wall within this fraction of the slowest
#: platform's counts as level with it. Chosen before the rows could
#: set it; a reading of a month of rows may move it.
TOLERANCE = 0.15

#: The largest count proposed when ``--max`` says nothing. Chosen
#: before the rows could set it.
MAX_SHARDS = 4

_DISPLAY = re.compile(
    r"^check \((?P<os>[^,()]+), (?P<version>[^,()]+)(?:, (?P<shard>\d+)of\d+)?\)$"
)
_LABEL = re.compile(
    r"^check-(?P<os>.+?)-(?P<version>\d+(?:\.\d+)*)(?:-(?P<shard>\d+)of\d+)?$"
)


@dataclass(frozen=True)
class Reading:
    """One platform's measures, medians over its readable rows.

    Attributes:
        os: The runner label (``macos-latest``).
        python: The leg's Python (``3.14``).
        seen: The full green rows the platform had among the newest
            [livery.workshop._balance.ROWS_READ][] read.
        rows: Those among them that carry the test steps' wall in
            every shard; the medians are over these.
        fixed: F, the setup and teardown the forge timed around the
            call, in seconds.
        other: O, the longest gate step that is not a test step, in
            seconds; zero when the rows name none.
        tests: S, the summed test time over every package and shard,
            in seconds.
        speed: p, the measured parallelism: a shard's S over its test
            steps' wall, the median over shards.
    """

    os: str
    python: str
    seen: int
    rows: int
    fixed: float
    other: float
    tests: float
    speed: float

    @property
    def platform(self) -> str:
        """The platform as the table names it: ``macos-latest, 3.14``."""
        return f"{self.os}, {self.python}"

    @property
    def enough(self) -> bool:
        """Whether the platform has the rows a proposal needs."""
        return self.rows >= ROWS_NEEDED


@dataclass(frozen=True)
class Proposal:
    """The counts a set of readings proposes.

    Attributes:
        counts: The count per runner label, one entry per runner with
            enough rows; a runner with two Pythons takes the larger.
        slowest: The slowest platform, as the table names it; empty
            when no platform had enough rows.
        wall: The slowest platform's predicted wall at its count, in
            seconds.
        unsharded: Its predicted wall at one shard.
        capped: The platforms that took the cap without reaching the
            tolerance, as the table names them.
    """

    counts: dict[str, int]
    slowest: str = ""
    wall: float = 0.0
    unsharded: float = 0.0
    capped: tuple[str, ...] = ()


def wall(reading: Reading, shards: int) -> float:
    """The predicted wall of the leg at *shards*, in seconds: F + max(O, S / (p n))."""
    return reading.fixed + max(reading.other, reading.tests / (reading.speed * shards))


def runner_seconds(reading: Reading, shards: int) -> float:
    """About what *shards* runners spend together, in seconds: n F + S / p + O."""
    return shards * reading.fixed + reading.tests / reading.speed + reading.other


def _unsharded(reading: Reading) -> float:
    return wall(reading, 1)


def propose(readings: Sequence[Reading], *, cap: int) -> Proposal:
    """The counts for *readings* within *cap*; a platform short of rows takes no part.

    The slowest platform's count stops at the first added shard that
    saves less than its setup. Every other platform takes the smallest
    count within the tolerance of the slowest platform's wall, or the
    cap, named in ``capped``.
    """
    cap = max(1, cap)
    able = [reading for reading in readings if reading.enough]
    if not able:
        return Proposal({})
    slowest = max(able, key=_unsharded)
    count = 1
    while (
        count < cap and wall(slowest, count) - wall(slowest, count + 1) >= slowest.fixed
    ):
        count += 1
    target = wall(slowest, count) * (1 + TOLERANCE)
    counts: dict[str, int] = {}
    capped: list[str] = []
    for reading in able:
        if reading is slowest:
            chosen = count
        else:
            chosen = next(
                (n for n in range(1, cap + 1) if wall(reading, n) <= target), cap
            )
            if wall(reading, chosen) > target:
                capped.append(reading.platform)
        counts[reading.os] = max(counts.get(reading.os, 0), chosen)
    return Proposal(
        counts, slowest.platform, wall(slowest, count), wall(slowest, 1), tuple(capped)
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _platform(name: str, job: Mapping[str, Any]) -> tuple[str, str, int] | None:
    """The os, Python and shard index of a check job; None for another job.

    The display name first (``check (ubuntu-latest, 3.14, 2of4)``),
    then the leg label the job's row carries under ``scope.leg``,
    which is how a GitLab job, displayed as the bare ``check``, names
    its runner. An unsharded job is shard 1.
    """
    match = _DISPLAY.match(name)
    if match is None:
        scope = job.get("scope")
        leg = str(scope.get("leg") or "") if isinstance(scope, Mapping) else ""
        match = _LABEL.match(leg)
    if match is None:
        return None
    return match["os"], match["version"], int(match["shard"] or 1)


def _full_green(job: Mapping[str, Any]) -> bool:
    scope = job.get("scope")
    return (
        job.get("conclusion") == "success"
        and isinstance(scope, Mapping)
        and scope.get("scope") == "full"
    )


def _gate_steps(job: Mapping[str, Any]) -> dict[str, float]:
    """The gate's steps of *job* by task key: its tasks and the steps its tasks ran."""
    found: dict[str, float] = {}
    for field in ("tasks", "task_steps"):
        values = job.get(field)
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():
            ms = _number(value)
            if GATE_STEPS.match(str(key)) and ms is not None:
                found.setdefault(str(key), ms)
    return found


def _fixed_ms(job: Mapping[str, Any]) -> float:
    """The forge's steps of *job* except the call, summed; an untimed step is 0."""
    total = 0.0
    for step in job.get("steps") or []:
        if isinstance(step, Mapping) and step.get("name") != CALL_STEP:
            total += _number(step.get("ms")) or 0.0
    return total


def _tests_ms(job: Mapping[str, Any]) -> float:
    packages = job.get("packages")
    if not isinstance(packages, Mapping):
        return 0.0
    return sum(
        _number(package.get("tests_ms")) or 0.0
        for package in packages.values()
        if isinstance(package, Mapping)
    )


@dataclass(frozen=True)
class _Measures:
    fixed: float
    other: float
    tests: float
    speed: float


def _measure(shards: Sequence[Mapping[str, Any]]) -> _Measures | None:
    """One row's measures for a platform from its shards' jobs, the first shard first.

    None when a shard carries no test step's wall or no test time: p
    cannot be read from it. A shard's test wall is its test steps'
    summed.
    """
    speeds: list[float] = []
    total = 0.0
    for job in shards:
        steps = _gate_steps(job)
        test_ms = sum(ms for key, ms in steps.items() if key.startswith(TEST_PHASE))
        tests = _tests_ms(job)
        if test_ms <= 0 or tests <= 0:
            return None
        speeds.append(tests / test_ms)
        total += tests
    steps = _gate_steps(shards[0])
    other = max(
        (ms for key, ms in steps.items() if not key.startswith(TEST_PHASE)),
        default=0.0,
    )
    return _Measures(
        _fixed_ms(shards[0]) / 1000.0,
        other / 1000.0,
        total / 1000.0,
        statistics.median(speeds),
    )


def readings(rows: Sequence[Mapping[str, Any]]) -> list[Reading]:
    """Each platform's reading from *rows*, newest first; the platforms in name order.

    A row counts for a platform when every job of that platform in it
    ran the full gate and succeeded, and the newest
    [livery.workshop._balance.ROWS_READ][] such rows are read. Among
    them, a row whose shard carries no test step's wall is seen and
    not read. A job that is not a check leg is skipped.
    """
    seen: dict[tuple[str, str], int] = defaultdict(int)
    measures: dict[tuple[str, str], list[_Measures]] = defaultdict(list)
    for row in rows:
        jobs = row.get("jobs")
        if not isinstance(jobs, Mapping):
            continue
        by_platform: dict[tuple[str, str], list[tuple[int, Mapping[str, Any]]]] = (
            defaultdict(list)
        )
        for name, job in jobs.items():
            if not isinstance(job, Mapping):
                continue
            found = _platform(str(name), job)
            if found is not None:
                by_platform[found[:2]].append((found[2], job))
        for key, shards in by_platform.items():
            if seen[key] >= ROWS_READ:
                continue
            shards.sort(key=itemgetter(0))
            if not all(_full_green(job) for _, job in shards):
                continue
            seen[key] += 1
            measured = _measure([job for _, job in shards])
            if measured is not None:
                measures[key].append(measured)
    out: list[Reading] = []
    for key in sorted(seen):
        taken = measures.get(key, [])
        out.append(
            Reading(
                key[0],
                key[1],
                seen[key],
                len(taken),
                fixed=statistics.median([m.fixed for m in taken]) if taken else 0.0,
                other=statistics.median([m.other for m in taken]) if taken else 0.0,
                tests=statistics.median([m.tests for m in taken]) if taken else 0.0,
                speed=statistics.median([m.speed for m in taken]) if taken else 0.0,
            )
        )
    return out


def _platform_lines(reading: Reading, cap: int) -> list[str]:
    """The table of one platform, or the line saying why it keeps its count."""
    if not reading.enough:
        if reading.rows < reading.seen:
            return [
                f"  {reading.platform}: keeps its count: {reading.seen} full green"
                f" row(s), {reading.rows} with the test steps' wall ({TEST_PHASE}*),"
                f" {ROWS_NEEDED} needed"
            ]
        return [
            f"  {reading.platform}: keeps its count: {reading.seen} full green"
            f" row(s), {ROWS_NEEDED} needed"
        ]
    lines = [
        f"  {reading.platform}: {reading.rows} row(s): F {reading.fixed:.0f} s,"
        f" O {reading.other:.0f} s, S {reading.tests:.0f} s, p {reading.speed:.1f}",
        f"    {'shards':>6} {'wall':>8} {'runner s':>10}",
    ]
    lines.extend(
        f"    {n:>6} {wall(reading, n):>6.0f} s {runner_seconds(reading, n):>8.0f} s"
        for n in range(1, cap + 1)
    )
    return lines


def _toml_line(key: str, proposal: Proposal) -> str:
    """The TOML the contract would hold for *key*, or why there is none."""
    if not proposal.counts:
        return (
            f"  nothing proposed: no platform has {ROWS_NEEDED} full green rows"
            " with the test steps' wall"
        )
    sharded = {os: n for os, n in sorted(proposal.counts.items()) if n > 1}
    if not sharded:
        return f"  [ci] {key}: none, every platform runs as one shard"
    inner = ", ".join(f'"{os}" = {n}' for os, n in sharded.items())
    return f"  [ci] {key} = {{ {inner} }}"


def _wall_line(what: str, proposal: Proposal) -> list[str]:
    if not proposal.counts:
        return []
    return [
        f"  predicted {what} wall: {proposal.wall:.0f} s for the slowest leg,"
        f" from {proposal.unsharded:.0f} s"
    ]


def report(
    readings: Sequence[Reading],
    *,
    cap: int,
    runners: Sequence[str],
    pull_request_runners: Sequence[str] | None = None,
) -> list[str]:
    """The lines the verb prints below the window line: the tables, then the proposal.

    Every reading gets its table or its reason; a runner in *runners*
    without a reading keeps its count and says so. The proposal is
    over the readings of *runners*; the pull request proposal over
    *pull_request_runners* when that list is shorter, else the counts
    are the same and say so.
    """
    cap = max(1, cap)
    lines: list[str] = []
    for reading in readings:
        lines.extend(_platform_lines(reading, cap))
    present = {reading.os for reading in readings}
    lines.extend(
        f"  {runner}: keeps its count: 0 full green row(s), {ROWS_NEEDED} needed"
        for runner in runners
        if runner not in present
    )
    full = propose([r for r in readings if r.os in runners], cap=cap)
    lines.extend(_named_capped(readings, full, cap))
    lines.append(_toml_line("shards", full))
    narrower = pull_request_runners is not None and len(pull_request_runners) < len(
        runners
    )
    pulls = (
        propose([r for r in readings if r.os in (pull_request_runners or ())], cap=cap)
        if narrower
        else None
    )
    if pulls is not None:
        lines.extend(_named_capped(readings, pulls, cap))
        lines.append(_toml_line("pull-request-shards", pulls))
    elif full.counts:
        lines.append("  [ci] pull-request-shards: the same")
    lines.extend(_wall_line("run", full))
    if pulls is not None:
        lines.extend(_wall_line("pull request", pulls))
    return lines


def _named_capped(
    readings: Sequence[Reading], proposal: Proposal, cap: int
) -> list[str]:
    """One line per platform that took the cap without reaching the tolerance."""
    by_platform = {reading.platform: reading for reading in readings}
    percent = round(TOLERANCE * 100)
    return [
        f"  {platform} takes the cap, {cap}: {wall(by_platform[platform], cap):.0f} s"
        f" is not within {percent}% of {proposal.wall:.0f} s"
        for platform in proposal.capped
    ]


def _day(entry: Mapping[str, Any]) -> str:
    """The day a row's run was created, else the day the store stamped it."""
    stamp = str(entry.get("created_at") or entry.get("when") or "")
    return stamp[:10] or "an unknown day"


def render(root: Path, *, cap: int = MAX_SHARDS) -> list[str]:
    """What ``fm ci.balance`` prints: the window, the tables, the proposal.

    Reads the metrics series and the root contract's ``[ci] runners``
    and ``[ci] pull-request-runners``. An unreadable series prints its
    reason and proposes nothing; an empty one says so.
    """
    found = SERIES.rows(state_store(root))
    if found.failed:
        return [f"  {found.reason}", "  nothing proposed: the record could not be read"]
    entries = [row.data for row in found.rows]
    if not entries:
        return [
            "  no timing rows yet: the gate job writes one per run",
            "  nothing proposed: the record holds no row",
        ]
    lines = [f"  {line}" for line in found.skipped]
    lines.append(
        f"  {len(entries)} run(s) in the window, {_day(entries[-1])} to"
        f" {_day(entries[0])}; the newest {ROWS_READ} full green row(s) per platform"
        " are read"
    )
    ci = load_contract(root / "workshop.toml").get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    lines.extend(
        report(
            readings(entries),
            cap=cap,
            runners=runners,
            pull_request_runners=pull_request_runners(ci),
        )
    )
    return lines
