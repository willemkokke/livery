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
- O, the summed wall of the gate's steps that are not test steps,
  from the first shard. The gate's steps are the job's
  ``check/<phase>.<step>`` tasks, and the test steps are the test
  phase's, [livery.workshop._metrics.TEST_PHASE][].
- S, the summed test time over every package and every shard.
- p, the measured parallelism: a shard's S over its test steps' wall,
  the median over shards.

The wall at n shards is predicted as F + max(O, (S / p + O) / n). The
first shard runs the unsplit steps, and its tests wait about their
summed wall, so the partition starts the first shard's slice O p item
seconds in ([livery.workshop._metrics.first_shard_head][]): the S + O p
item seconds split evenly over the n shards, and no count brings a
platform under F + O, the first shard with no tests left. The runner
seconds at n are about n F + S / p + O. The slowest platform, the one
with the largest wall at one shard, takes the largest count within
the cap whose every added shard saved at least F, so a shard that
costs more setup than it saves is not proposed. Every other platform
takes the smallest count whose wall is within
[livery.workshop._balance.TOLERANCE][] of the slowest
platform's at its count; one that cannot get there at the cap takes
the cap and is named. The pull request counts apply the same rule
over a pull request's runners when they are fewer than ``[ci]
runners`` names. A platform with fewer than
[livery.workshop._balance.ROWS_NEEDED][] readable rows keeps its count
and takes no part. The cap is ``[ci] shards-max``, which the verb
reads and never writes.

The verb prints the table and the TOML the contract would hold. With
``--write`` it rewrites ``[ci] shards``, and ``[ci]
pull-request-shards`` when the pull request runners are fewer, in the
root ``workshop.toml``, keeping every comment and every other key:
when a count differs from the contract's and the predicted wall at
the proposed counts differs from the wall at the contract's by more
than [livery.workshop._balance.HYSTERESIS][], so a count does not move
back and forth between two readings that predict about the same
wall. With ``--submit`` the change lands on the day's branch,
``chore/balance-<date>``, rendered, committed and submitted unarmed
for a person to merge; the nightly runs that once a day. The
constants here were chosen before the rows could set them, as the
speed marks' margins were ([livery.workshop._speed][]).

Reach for `render` for what the verb prints, `read` for the same with
the counts a write judges, `judge` for the write's decision, `write`
and `submit` for the two acts, `readings` for the measures from rows
already in hand, `wall` and `runner_seconds` for the prediction, and
`propose` for the counts.
"""

from __future__ import annotations

import re
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from operator import itemgetter
from pathlib import Path
from typing import Any

from livery import git
from livery.footman import fail
from livery.workshop._ci_context import state_store
from livery.workshop._contract import load_contract
from livery.workshop._metrics import SERIES, TEST_PHASE, gate_steps, tests_ms
from livery.workshop._points import pull_request_runners, shard_counts, shards_max

#: The step the forge times for the gate's own call. Every other step
#: the forge times is the job's setup and teardown, F.
CALL_STEP = "Check"

#: The newest full green rows read per platform.
ROWS_READ = 10

#: A platform with fewer readable rows than this keeps its count: a
#: median of two says little. Chosen before the rows could set it.
ROWS_NEEDED = 3

#: Another platform's wall within this fraction of the slowest
#: platform's counts as level with it. Chosen before the rows could
#: set it; a reading of a month of rows may move it.
TOLERANCE = 0.15

#: The write happens only when the predicted wall at the proposed
#: counts differs from the wall at the contract's counts by more than
#: this fraction of the latter, so two readings that predict about the
#: same wall do not move a count back and forth. Chosen before the rows
#: could set it.
HYSTERESIS = 0.10

#: The comment written above a key the balancer adds to the contract.
COMMENTS = {
    "shards": (
        "# The shards each runner's check leg runs as; fm ci.balance --write sets them."
    ),
    "pull-request-shards": (
        "# The shards a pull request's check legs run as; fm ci.balance --write"
        " sets them."
    ),
}

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
        other: O, the summed wall of the gate steps that are not test
            steps, in seconds; zero when the rows name none.
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
    """The leg's predicted wall at *shards*, in seconds: F + max(O, (S / p + O) / n)."""
    spread = (reading.tests / reading.speed + reading.other) / shards
    return reading.fixed + max(reading.other, spread)


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


def _fixed_ms(job: Mapping[str, Any]) -> float:
    """The forge's steps of *job* except the call, summed; an untimed step is 0."""
    total = 0.0
    for step in job.get("steps") or []:
        if isinstance(step, Mapping) and step.get("name") != CALL_STEP:
            total += _number(step.get("ms")) or 0.0
    return total


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
        steps = gate_steps(job)
        test_ms = sum(ms for key, ms in steps.items() if key.startswith(TEST_PHASE))
        tests = tests_ms(job.get("packages"))
        if test_ms <= 0 or tests <= 0:
            return None
        speeds.append(tests / test_ms)
        total += tests
    steps = gate_steps(shards[0])
    other = sum(ms for key, ms in steps.items() if not key.startswith(TEST_PHASE))
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


def _toml_line(key: str, proposal: Proposal, runners: Sequence[str]) -> str:
    """The TOML the contract would hold for *key*, or why there is none."""
    if not proposal.counts:
        return (
            f"  nothing proposed: no platform has {ROWS_NEEDED} full green rows"
            " with the test steps' wall"
        )
    sharded = _ordered(proposal.counts, runners)
    if not sharded:
        return f"  [ci] {key}: none, every platform runs as one shard"
    return f"  [ci] {key} = {spell(sharded)}"


def _ordered(counts: Mapping[str, int], runners: Sequence[str]) -> dict[str, int]:
    """The counts above one, in the order of *runners*, as the contract holds them."""
    return {runner: counts[runner] for runner in runners if counts.get(runner, 1) > 1}


def spell(table: Mapping[str, int]) -> str:
    """*table* as the contract spells an inline table; ``none`` when empty."""
    if not table:
        return "none"
    return "{ " + ", ".join(f'"{runner}" = {n}' for runner, n in table.items()) + " }"


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
    lines.append(_toml_line("shards", full, runners))
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
        lines.append(
            _toml_line("pull-request-shards", pulls, pull_request_runners or ())
        )
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


@dataclass(frozen=True)
class Counts:
    """One key's count per runner: the contract's, the proposal's, and both walls.

    Attributes:
        key: ``shards`` or ``pull-request-shards``.
        runners: The runners the key covers, in the contract's order.
        current: The count per runner the contract gives, one where it
            is silent.
        proposed: The count per runner the proposal gives; a runner
            whose platform keeps its count has its current one.
        before: The predicted wall of the slowest leg at the current
            counts, in seconds, over the platforms with enough rows.
        after: The same at the proposed counts.
    """

    key: str
    runners: tuple[str, ...]
    current: dict[str, int]
    proposed: dict[str, int]
    before: float
    after: float

    @property
    def changed(self) -> bool:
        """Whether a count differs between the proposal and the contract."""
        return self.proposed != self.current

    @property
    def moved(self) -> bool:
        """Whether the wall moves by more than the hysteresis between the two."""
        return abs(self.after - self.before) > HYSTERESIS * self.before

    @property
    def table(self) -> dict[str, int]:
        """The proposed counts as the contract holds them: those above one, in order."""
        return _ordered(self.proposed, self.runners)

    @property
    def held(self) -> dict[str, int]:
        """The current counts as the contract holds them."""
        return _ordered(self.current, self.runners)


@dataclass(frozen=True)
class Balance:
    """One reading of the record: the verb's lines and the counts a write judges.

    Attributes:
        lines: The window line, the tables and the proposal, as `render`
            prints them.
        shards: The counts of ``[ci] shards`` over ``[ci] runners``;
            None when the record proposed nothing, and ``reason`` says
            why.
        pull_request: The counts of ``[ci] pull-request-shards`` over
            ``[ci] pull-request-runners`` when that list is shorter than
            ``[ci] runners``; None otherwise, and a write leaves the key
            as it is.
        reason: Why the record proposed nothing; empty when it did.
    """

    lines: list[str]
    shards: Counts | None = None
    pull_request: Counts | None = None
    reason: str = ""


@dataclass(frozen=True)
class Verdict:
    """A write's decision on a `Balance`: the tables for the contract, or why none.

    Attributes:
        tables: The table per key the write puts in the contract, an
            empty one removing the key; None when nothing is written.
        why: Why nothing is written; empty when something is.
        what: The wall the decision read, ``run`` or ``pull request``.
        before: That wall at the contract's counts, in seconds.
        after: That wall at the proposed counts.
    """

    tables: dict[str, dict[str, int]] | None
    why: str = ""
    what: str = ""
    before: float = 0.0
    after: float = 0.0


def counts(
    readings: Sequence[Reading],
    *,
    key: str,
    runners: Sequence[str],
    current: Mapping[str, int],
    cap: int,
) -> Counts:
    """One key's counts over *runners*: *current*, the proposal, and both walls.

    A runner whose platform has too few rows keeps its current count.
    The walls are the slowest leg's among the platforms with enough
    rows, at the current counts and at the proposed ones.
    """
    proposal = propose([r for r in readings if r.os in runners], cap=cap)
    proposed = {
        runner: proposal.counts.get(runner, current[runner]) for runner in runners
    }
    able = [r for r in readings if r.os in runners and r.enough]
    return Counts(
        key,
        tuple(runners),
        dict(current),
        proposed,
        max((wall(r, current[r.os]) for r in able), default=0.0),
        max((wall(r, proposed[r.os]) for r in able), default=0.0),
    )


def _cap(ci: dict[str, Any], cap: int) -> int:
    """The cap the proposal stays within: ``[ci] shards-max``, or *cap* when under it.

    Zero reads the contract's. A *cap* above the contract's refuses by
    name: a count above ``[ci] shards-max`` is one the contract refuses
    at its next load.
    """
    most = shards_max(ci)
    if cap <= 0:
        return most
    if cap > most:
        fail(
            f"--max={cap} is above [ci] shards-max, {most}: raise the cap in the"
            " contract, or lower --max"
        )
    return cap


def read(root: Path, *, cap: int = 0) -> Balance:
    """Read the record and the contract: what ``fm ci.balance`` prints, and the counts.

    Reads the metrics series and the root contract's ``[ci] runners``,
    ``[ci] pull-request-runners``, ``[ci] shards``, ``[ci]
    pull-request-shards`` and ``[ci] shards-max``. *cap* is ``--max``,
    zero for the contract's cap. An unreadable series prints its reason
    and proposes nothing; an empty one says so.
    """
    found = SERIES.rows(state_store(root))
    if found.failed:
        return Balance(
            [f"  {found.reason}", "  nothing proposed: the record could not be read"],
            reason="the record could not be read",
        )
    entries = [row.data for row in found.rows]
    if not entries:
        return Balance(
            [
                "  no timing rows yet: the gate job writes one per run",
                "  nothing proposed: the record holds no row",
            ],
            reason="the record holds no row",
        )
    lines = [f"  {line}" for line in found.skipped]
    lines.append(
        f"  {len(entries)} run(s) in the window, {_day(entries[-1])} to"
        f" {_day(entries[0])}; the newest {ROWS_READ} full green row(s) per platform"
        " are read"
    )
    ci = load_contract(root / "workshop.toml").get("ci") or {}
    runners = [str(runner) for runner in ci.get("runners") or ["ubuntu-latest"]]
    pulls = pull_request_runners(ci)
    cap = _cap(ci, cap)
    measured = readings(entries)
    lines.extend(report(measured, cap=cap, runners=runners, pull_request_runners=pulls))
    if not any(r.enough and r.os in runners for r in measured):
        return Balance(
            lines,
            reason=(
                f"no platform has {ROWS_NEEDED} full green rows with the test"
                " steps' wall"
            ),
        )
    current = shard_counts(ci, key="shards", runners=runners, listed="[ci] runners")
    shards = counts(measured, key="shards", runners=runners, current=current, cap=cap)
    pull_request = None
    if len(pulls) < len(runners):
        if ci.get("pull-request-shards") is None:
            held = {runner: current[runner] for runner in pulls}
        else:
            held = shard_counts(
                ci,
                key="pull-request-shards",
                runners=pulls,
                listed="[ci] pull-request-runners",
            )
        pull_request = counts(
            measured, key="pull-request-shards", runners=pulls, current=held, cap=cap
        )
    return Balance(lines, shards, pull_request)


def render(root: Path, *, cap: int = 0) -> list[str]:
    """What ``fm ci.balance`` prints: the window, the tables, the proposal.

    `read` with the counts left out; *cap* means what it means there.
    """
    return read(root, cap=cap).lines


def judge(balance: Balance) -> Verdict:
    """Decide what a write puts in the contract for *balance*, or why nothing.

    Nothing is written when the record proposed nothing, when every
    count is the contract's, and when the predicted wall at the
    proposed counts is within the hysteresis of the wall at the
    contract's counts. The run wall decides, over ``[ci] runners``;
    when the run's counts are the contract's and only the pull
    request's differ, the pull request wall decides. Otherwise the
    tables are ``[ci] shards`` and, when the pull request runners are
    fewer, ``[ci] pull-request-shards``, both at the proposal.
    """
    shards = balance.shards
    if shards is None:
        return Verdict(None, f"nothing written: {balance.reason}")
    pulls = balance.pull_request
    if shards.changed:
        judged, what = shards, "run"
    elif pulls is not None and pulls.changed:
        judged, what = pulls, "pull request"
    else:
        return Verdict(None, "nothing written: every count is the contract's")
    if not judged.moved:
        return Verdict(
            None,
            f"nothing written: the predicted {what} wall moves from"
            f" {judged.before:.0f} s to {judged.after:.0f} s, within"
            f" {round(HYSTERESIS * 100)}%",
        )
    tables = {"shards": shards.table}
    if pulls is not None:
        tables["pull-request-shards"] = pulls.table
    return Verdict(tables, "", what, judged.before, judged.after)


#: The ``[ci]`` header, on a line of its own, a comment after it allowed.
_CI_HEADER = re.compile(r"^[ \t]*\[[ \t]*ci[ \t]*\][ \t]*(?:#.*)?$")

#: Any table header: the line's first character after its indent is ``[``.
_HEADER = re.compile(r"^[ \t]*\[")

#: A key's line, bare or quoted, up to its ``=``.
_KEY_LINE = re.compile(r'^([ \t]*)(?:([A-Za-z0-9_-]+)|"([^"]*)")[ \t]*=')


def _value_end(
    lines: Sequence[str], first: int, end: int, column: int
) -> tuple[int, str]:
    """Where the value at *column* of line *first* ends, and its trailing comment.

    The value ends on the first line that closes every bracket it
    opened, brackets inside strings and comments not counted; the
    comment is the one after the value on that line, empty without
    one.
    """
    depth = 0
    quote = ""
    line = first
    at = column
    while line < end:
        text = lines[line]
        comment = ""
        while at < len(text):
            char = text[at]
            if quote:
                if char == "\\" and quote.startswith('"'):
                    at += 2
                elif text.startswith(quote, at):
                    at += len(quote)
                    quote = ""
                else:
                    at += 1
                continue
            if char == "#":
                comment = text[at:].rstrip()
                break
            if char in "\"'":
                quote = char * 3 if text.startswith(char * 3, at) else char
                at += len(quote)
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            at += 1
        if depth <= 0 and not quote:
            return line, comment
        line += 1
        at = 0
    return end - 1, ""


@dataclass(frozen=True)
class _Key:
    """One key of the ``[ci]`` table as written.

    Attributes:
        name: The key, unquoted.
        first: The line its name stands on.
        last: The line its value ends on, the same for a one-line value.
        comment: The comment after the value on its last line; empty
            without one.
        indent: What stands before the name on its line.
    """

    name: str
    first: int
    last: int
    comment: str
    indent: str


def _keys(lines: Sequence[str], start: int, end: int) -> list[_Key]:
    """The keys of the lines *start* to *end*, in order."""
    found: list[_Key] = []
    line = start
    while line < end:
        match = _KEY_LINE.match(lines[line])
        if match is None:
            line += 1
            continue
        last, comment = _value_end(lines, line, end, match.end())
        found.append(
            _Key(match.group(2) or match.group(3), line, last, comment, match.group(1))
        )
        line = last + 1
    return found


def _ci_table(lines: Sequence[str]) -> tuple[int, int] | None:
    """The ``[ci]`` header's line and the line after its last; None without one."""
    header = next((i for i, line in enumerate(lines) if _CI_HEADER.match(line)), None)
    if header is None:
        return None
    end = next(
        (i for i in range(header + 1, len(lines)) if _HEADER.match(lines[i])),
        len(lines),
    )
    return header, end


def holds_key(text: str, key: str) -> bool:
    """Whether *text*'s ``[ci]`` table spells *key* as one of its own keys."""
    lines = text.split("\n")
    table = _ci_table(lines)
    if table is None:
        return False
    header, end = table
    return any(found.name == key for found in _keys(lines, header + 1, end))


def rewrite(text: str, key: str, table: Mapping[str, int]) -> str:
    """*text* with ``[ci] <key>`` at *table*, every other line kept as written.

    A key the table holds is rewritten in place as one inline table,
    its trailing comment kept, however many lines it spanned; an
    empty *table* removes the key with the comment lines directly
    above it, since a comment on a key that is gone misleads. A key
    the table lacks is added after its last key, under its own comment
    ([livery.workshop._balance.COMMENTS][]), or under the header when
    the table has none; a contract without a ``[ci]`` table gains one
    at its end. An absent key with an empty *table* changes nothing.
    """
    lines = text.split("\n")
    spelled = f"{key} = {spell(table)}"
    found = _ci_table(lines)
    if found is None:
        if not table:
            return text
        base = text.rstrip("\n")
        return (base + "\n\n" if base else "") + f"[ci]\n{COMMENTS[key]}\n{spelled}\n"
    header, end = found
    keys = _keys(lines, header + 1, end)
    for held in keys:
        if held.name != key:
            continue
        if table:
            after = f"  {held.comment}" if held.comment else ""
            lines[held.first : held.last + 1] = [held.indent + spelled + after]
        else:
            above = held.first
            while above > header + 1 and lines[above - 1].strip().startswith("#"):
                above -= 1
            del lines[above : held.last + 1]
        return "\n".join(lines)
    if not table:
        return text
    if keys:
        indent, at = keys[-1].indent, keys[-1].last + 1
    else:
        indent, at = "", header + 1
    lines[at:at] = [indent + COMMENTS[key], indent + spelled]
    return "\n".join(lines)


def write(root: Path, balance: Balance) -> dict[str, dict[str, int]] | None:
    """Rewrite the contract's counts when `judge` says to; the tables written, or None.

    Prints what each key becomes and the wall the decision read, or
    why nothing is written. Refuses by name when a key the contract
    holds is not spelled as a key of the ``[ci]`` table, as a
    ``[ci.shards]`` table or a dotted key: the rewrite keeps the
    contract's text and cannot reach it there.
    """
    verdict = judge(balance)
    if verdict.tables is None:
        print(f"  {verdict.why}")
        return None
    path = root / "workshop.toml"
    text = path.read_text(encoding="utf-8")
    ci = load_contract(path).get("ci") or {}
    for key, table in verdict.tables.items():
        if ci.get(key) is not None and not holds_key(text, key):
            fail(
                f"[ci] {key} is not a key of the [ci] table in {path.name} (a"
                f" [ci.{key}] table, or a dotted key): spell it `{key} = {{ ... }}`"
                " under [ci] for the balancer to rewrite it"
            )
        text = rewrite(text, key, table)
    path.write_text(text, encoding="utf-8")
    for found in (balance.shards, balance.pull_request):
        if found is not None and found.key in verdict.tables:
            print(
                f"  [ci] {found.key}: {spell(found.held)} becomes"
                f" {spell(verdict.tables[found.key])}"
            )
    print(
        f"  the predicted {verdict.what} wall moves from {verdict.before:.0f} s to"
        f" {verdict.after:.0f} s"
    )
    return verdict.tables


def on_default_branch(root: Path) -> bool:
    """Whether HEAD is on the history of origin's default branch.

    Origin's default branch is fetched as ``HEAD``, so no branch name
    is needed and a checkout a merge moved on since still answers; a
    detached HEAD at a commit of that history answers True.

    Raises:
        Failed: when origin cannot be fetched, in git's words.
    """
    try:
        git.fetch(root, "HEAD")
    except git.GitError as error:
        fail(f"origin could not be fetched: {error}")
    answer = git.call(root, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    if answer.code not in (0, 1):
        said = (answer.stderr or answer.stdout).strip()
        fail(f"git merge-base exited {answer.code}: {said}")
    return answer.code == 0


def submit(root: Path, *, cap: int = 0, on: date | None = None) -> None:
    """Land a due change on the day's branch and open its pull request, never armed.

    The act, each step printed: off origin's default branch nothing is
    read or written, the line names the branch, and the verb exits
    green, so a nightly dispatched on another branch skips it. On it,
    the record is read and judged as `write` judges it; nothing due
    means no branch and no pull request. Otherwise origin is asked for
    the day's branch, ``chore/balance-<date>``, before anything in the
    checkout changes: one holding these counts, from an earlier run of
    the day, means the day's balance is already submitted, and one
    holding other counts refuses by name, for a person to merge or
    close its pull request first. Then, from a clean tree, the branch
    is started in this checkout from a fetched ``origin/main``
    ([livery.workshop._issue_tasks.start_here][]), the contract is
    rewritten, the sync renders the workflows at the counts
    ([livery.workshop._sync.sync_checkout][]), the change is committed
    as ``chore(ci): the check legs run as <counts>``
    ([livery.workshop._commit.commit_flow][]) and submitted unarmed
    ([livery.workshop._submit.submit_flow][]), its gate run there and
    not twice, and the checkout returns to where it was. *on* is the
    day, today when None; *cap* is ``--max``.

    Raises:
        Failed: naming what stopped it, with nothing written when it
            stops before the write.
    """
    from livery.git import remote_snapshot

    if not on_default_branch(root):
        where = git.current_branch(root) or "(detached)"
        print(
            f"  not on origin's default branch (on {where}): nothing is written or"
            " submitted; the balancer submits from the default branch alone"
        )
        return
    with remote_snapshot(state_store(root), fetch=("metrics",)):
        balance = read(root, cap=cap)
    for line in balance.lines:
        print(line)
    verdict = judge(balance)
    if verdict.tables is None:
        print(f"  {verdict.why}")
        print("  no branch, no pull request")
        return
    day = on or date.today()
    branch = f"chore/balance-{day:%Y%m%d}"
    if _origin_holds(root, branch):
        held = _held(root, branch, verdict.tables)
        if held == verdict.tables:
            print(
                f"  the day's balance is already submitted: {branch} on origin holds"
                " these counts; nothing is written"
            )
            return
        fail(
            f"{branch} on origin holds other counts ({_spell_tables(held)}) than"
            f" this reading proposes ({_spell_tables(verdict.tables)}): merge or"
            " close its pull request, then run the balancer again; nothing is"
            " written"
        )
    if not git.is_clean(root):
        fail(
            "the working tree has uncommitted changes: commit or stash them, then"
            " run the balancer again; nothing is written"
        )
    from livery.workshop._commit import commit_flow
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._issue_tasks import fetch_base, start_here
    from livery.workshop._submit import forge_failures_named, submit_flow
    from livery.workshop._sync import sync_checkout

    came_from = git.current_branch(root) or git.head_sha(root)
    repo = this_repository(root)
    fetch_base(root)
    start_here(root, repo, branch)
    write(root, balance)
    sync_checkout(root)
    subject = _subject(balance, verdict.tables)
    # The submit's gate judges this tree next; a gate at the commit
    # would judge the same tree twice.
    commit_flow(
        root,
        "chore",
        subject,
        scope="ci",
        body=(
            f"the predicted {verdict.what} wall moves from {verdict.before:.0f} s"
            f" to {verdict.after:.0f} s"
        ),
        check=False,
    )
    with forge_failures_named():
        number = submit_flow(
            repo,
            root,
            title=f"chore(ci): {subject}",
            armed=False,
            armed_reason="the balancer, which never arms",
            follow_to_verdict=False,
        )
    print(f"  submitted unarmed: PR #{number} waits for a person")
    if git.local_branch_exists(root, came_from):
        git.switch(root, came_from)
        print(f"  back on {came_from}")
    else:
        git.run(root, "checkout", "--detach", came_from)
        print(f"  back at {came_from[:12]}, detached as before")


def _subject(balance: Balance, tables: Mapping[str, Mapping[str, int]]) -> str:
    """The commit subject's words: the counts, and a pull request's that differ."""
    shards = tables["shards"]
    words = f"the check legs run as {_spoken(shards)}"
    pulls = balance.pull_request
    if pulls is not None:
        same = {r: n for r, n in shards.items() if r in pulls.runners}
        if tables["pull-request-shards"] != same:
            words += f", pull requests as {_spoken(tables['pull-request-shards'])}"
    return words


def _spoken(table: Mapping[str, int]) -> str:
    return ", ".join(f"{runner} {n}" for runner, n in table.items()) or "one shard each"


def _spell_tables(tables: Mapping[str, Mapping[str, int]]) -> str:
    return "; ".join(f"[ci] {key} {spell(table)}" for key, table in tables.items())


def _origin_holds(root: Path, branch: str) -> bool:
    """Whether origin holds *branch*, asked of origin itself; a refusal names it."""
    try:
        return bool(git.ls_remote(root, f"refs/heads/{branch}"))
    except git.GitError as error:
        fail(f"could not ask origin for {branch}: {error}\n  nothing is written")


def _held(
    root: Path, branch: str, tables: Mapping[str, Mapping[str, int]]
) -> dict[str, dict[str, int]]:
    """The counts origin's *branch* holds for the keys of *tables*, as written."""
    git.fetch(root, f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    text = git.file_at(root, f"refs/remotes/origin/{branch}", "workshop.toml")
    loaded: dict[str, Any] = tomllib.loads(text) if text else {}
    ci = _table_or_empty(loaded.get("ci"))
    return {key: _table_of(ci.get(key)) for key in tables}


def _table_or_empty(value: Any) -> dict[str, Any]:
    """*value* when it is a table, else an empty one."""
    if isinstance(value, dict):
        table: dict[str, Any] = value
        return table
    return {}


def _table_of(value: Any) -> dict[str, int]:
    """The counts above one in *value*, a table of runner to count; none otherwise."""
    return {
        runner: n
        for runner, n in _table_or_empty(value).items()
        if isinstance(n, int) and not isinstance(n, bool) and n > 1
    }
