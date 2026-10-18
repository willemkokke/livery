"""A check leg's shard: its spelling, its share of the items, and the durations.

A platform's check leg may run as several shards, each on a runner of
its own, each running a share of the items a test step partitions;
pytest's items are test files. A shard is spelled ``<i>of<n>`` at the
end of the leg's label, ``check-<os>-<python>-2of4``;
the label without it is the platform leg, the key every record below
the gate job's fold is kept by. [livery.workshop.Shard][] parses and
spells one, [livery.workshop.platform_leg][] and
[livery.workshop.shard_of][] split a label.

The share is a function of its inputs: [livery.workshop.partition][]
packs the items longest first into the shard with the least summed
seconds, the first shard's sum starting at the head, so every shard of
a run computes the same partition from the same set, the same
durations and the same head, and every item lands in exactly one
shard. The durations and the head are the medians
[livery.workshop.read_durations][] takes over the newest rows of the
platform leg's series in the [livery.workshop.DURATIONS][] family,
bounded by the checkout's committer time
([livery.workshop.checkout_time][]) so a row written while the run is
in flight reaches no shard. A shard leaves what it partitioned in
`SHARD_FILE`, under the checkout's ``.workshop/`` directory
([livery.workshop.leave_shard][]), and the gate job reads it back
([livery.workshop.read_shard][]) to prove the shards of a platform
partitioned one set from one row.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery import git
from livery.workshop._ci_context import state_store

#: The durations rows' schema. A reader skips a row of another version
#: and names it; a new field is the same version, a changed meaning is
#: the next.
SCHEMA = 1

#: How many rows a platform leg's durations series keeps.
WINDOW = 30

#: How many of the newest rows before the bound a read takes the
#: median over.
NEWEST = 5

#: The file a shard leaves under the checkout's ``.workshop/``
#: directory, which git ignores, naming the shard it ran, the digest of
#: the set it partitioned, its own items, the rows it read and the
#: bound it read them under.
SHARD_FILE = ".workshop/fm-shard.json"

_SPELLING = re.compile(r"^(\d+)of(\d+)$")
_TAIL = re.compile(r"^(?P<leg>.+)-(?P<shard>\d+of\d+)$")


@dataclass(frozen=True)
class Shard:
    """One shard of a leg: which of how many, spelled ``<i>of<n>``.

    Attributes:
        index: The shard's number, from 1.
        count: How many shards the leg runs as.

    Raises:
        ValueError: When the count is below 1, the index below 1, or
            the index above the count.
    """

    index: int
    count: int

    def __post_init__(self) -> None:
        why = _unsound(self.index, self.count)
        if why:
            raise ValueError(f"shard {self.index} of {self.count}: {why}")

    @classmethod
    def parse(cls, text: str) -> Shard:
        """The shard *text* spells, ``2of4``.

        Raises:
            ValueError: When *text* is not of the form, naming it.
        """
        matched = _SPELLING.match(text)
        if matched is None:
            raise ValueError(
                f"{text!r} is not a shard; a shard is spelled <i>of<n>, such as 2of4"
            )
        index, count = int(matched.group(1)), int(matched.group(2))
        why = _unsound(index, count)
        if why:
            raise ValueError(f"{text!r} is not a shard: {why}")
        return cls(index, count)

    @property
    def first(self) -> bool:
        """Whether this is the first shard, the one that runs what is not split."""
        return self.index == 1

    def __str__(self) -> str:
        return f"{self.index}of{self.count}"


def _unsound(index: int, count: int) -> str:
    if count < 1:
        return "the count is at least 1"
    if index < 1:
        return "the index counts from 1"
    if index > count:
        return "the index is at most the count"
    return ""


#: The one shard of a leg that is not split.
ONE = Shard(1, 1)


def platform_leg(label: str) -> str:
    """The leg *label* without its shard; a label without one is returned as it is.

    ``check-windows-latest-3.14-2of4`` gives ``check-windows-latest-3.14``.
    """
    matched = _TAIL.match(label)
    return matched.group("leg") if matched else label


def shard_of(label: str) -> Shard:
    """The shard the leg *label* ends in; `ONE` when it ends in none.

    Raises:
        ValueError: When the label ends in a shard spelling outside the
            form, such as ``0of2``, naming it.
    """
    matched = _TAIL.match(label)
    return Shard.parse(matched.group("shard")) if matched else ONE


def partition(
    items: Iterable[str],
    durations: Mapping[str, float],
    count: int,
    *,
    head: float = 0.0,
) -> tuple[tuple[str, ...], ...]:
    """Pack *items* into *count* shards by their *durations*, every item in exactly one.

    The items go longest first, ties by name, each into the shard with
    the least summed seconds so far, ties to the lowest shard. The
    first shard's sum starts at *head* seconds, the time of the steps
    it runs that the other shards leave to it, so its share of the
    items is lighter by about that much; a head above every other
    shard's final sum leaves the first shard no item. An item
    *durations* does not name weighs the median of the named
    durations, or one second when none are named. The result is a
    function of the set, the durations and the head alone: the same
    whatever order *items* comes in, and an item given twice is one
    item. With more shards than items the surplus shards are empty.

    Returns:
        One tuple of items per shard, in the order the items were
        packed.

    Raises:
        ValueError: When *count* is below 1, or *head* is below 0.
    """
    if count < 1:
        raise ValueError(f"count is {count}; a partition has at least one shard")
    if head < 0:
        raise ValueError(f"head is {head}; a head is at least 0 seconds")
    named = [float(seconds) for seconds in durations.values()]
    fill = statistics.median(named) if named else 1.0

    def weight(name: str) -> float:
        seconds = durations.get(name)
        return float(seconds) if seconds is not None else fill

    ordered = sorted(set(items), key=lambda name: (-weight(name), name))
    shards: list[list[str]] = [[] for _ in range(count)]
    sums = [0.0] * count
    sums[0] = float(head)
    for name in ordered:
        at = min(range(count), key=lambda index: (sums[index], index))
        shards[at].append(name)
        sums[at] += weight(name)
    return tuple(tuple(shard) for shard in shards)


def files_digest(files: Iterable[str]) -> str:
    """A hex digest of the sorted names in *files*.

    Two shards that partitioned one set have one digest, so the gate
    job can prove they did.
    """
    text = "\n".join(sorted(set(files)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current_legs(root: Path) -> set[tuple[str, ...]] | None:
    """The keys the matrix produces, one per check leg; ``None`` when unknown.

    The contract or the matrix cannot be read, and then the janitor
    drops nothing.
    """
    from livery.workshop import check_legs

    try:
        legs = check_legs(root)
    except (Exception, SystemExit):
        return None
    return {(git.slug(leg),) for leg in legs}


#: The family: one series per platform leg, ``durations/<leg>``, one row
#: per run, ``{"run": "<id>", "items": {"<item>": <seconds>}, "head":
#: <seconds>}``, written by the run's gate job from its shards' rows.
#: ``head`` is the item seconds the first shard's unsplit steps stand
#: for on the run ([livery.workshop._metrics.first_shard_head][]); a row
#: without it reads as 0. A series of a leg the matrix no longer
#: produces is the janitor's to drop.
DURATIONS: git.Keyed = git.Keyed(
    "durations", ("leg",), window=WINDOW, schema=SCHEMA, current=_current_legs
)


def _moment(stamp: str) -> datetime | None:
    """The time *stamp* names, timezone-aware; ``None`` when it names none."""
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def read_durations(
    root: Path, leg: str, *, before: datetime
) -> tuple[dict[str, float], float, tuple[str, ...], str]:
    """The median seconds per item, and the head, of *leg*'s rows before *before*.

    The rows are the newest `NEWEST` of the series ``durations/<platform
    leg>`` whose store stamp is before *before*; a row stamped at or
    after it, or not stamped, is not read, so every shard of a run
    reads the same rows whatever lands while the run is in flight. The
    median of an item is over the rows that name it with a number
    under ``items``. The head is the median of every row read's
    ``head``, a row without a number there counting as 0, and is what
    [livery.workshop.partition][] starts the first shard's sum at. A
    naive *before* is read as UTC.

    Returns:
        The seconds per item, the head in seconds, the names of the
        rows read, newest first, and why the series could not be read.
        A series that cannot be read is ``({}, 0.0, (), reason)``,
        never a raise: the caller runs as one shard of one and says
        so. An absent series is ``({}, 0.0, (), "")``.
    """
    bound = before if before.tzinfo is not None else before.replace(tzinfo=UTC)
    series = DURATIONS.series(platform_leg(leg))
    found = series.rows(state_store(root))
    if found.failed:
        return {}, 0.0, (), found.reason
    chosen: list[git.Row] = []
    for row in found.rows:
        when = _moment(row.when)
        if when is None or when >= bound:
            continue
        chosen.append(row)
        if len(chosen) == NEWEST:
            break
    seen: dict[str, list[float]] = defaultdict(list)
    for row in chosen:
        held: Any = row.data.get("items")
        if not isinstance(held, dict):
            continue
        for name, seconds in held.items():
            if isinstance(seconds, int | float) and not isinstance(seconds, bool):
                seen[str(name)].append(float(seconds))
    medians = {
        name: round(statistics.median(values), 1)
        for name, values in sorted(seen.items())
    }
    heads = [_seconds(row.data.get("head")) for row in chosen]
    head = round(statistics.median(heads), 1) if heads else 0.0
    return medians, head, tuple(row.name for row in chosen), ""


def _seconds(value: Any) -> float:
    """*value* as seconds when it is a number of at least 0; else 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return max(0.0, float(value))


def checkout_time(root: Path) -> datetime:
    """The committer time of HEAD's commit in the checkout at *root*, timezone-aware.

    Every shard of a run checks out the same commit, so this bounds
    the durations rows every shard reads without a call to the forge.

    Raises:
        GitError: When HEAD names no commit.
    """
    spelled = git.run(root, "log", "-1", "--format=%cI", "HEAD").strip()
    return datetime.fromisoformat(spelled)


@dataclass(frozen=True)
class ShardMarker:
    """What a shard left in `SHARD_FILE`: what it ran and what it partitioned from.

    Attributes:
        shard: The shard the leg ran as.
        digest: The digest of the set it partitioned,
            [livery.workshop.files_digest][] of every item of every
            shard.
        items: The items this shard ran, in packing order; pytest's
            are test files.
        rows: The durations rows it read, newest first.
        before: The bound it read them under, the checkout's committer
            time.
        suites: The units whose items were in the set, by path. A
            unit the record does not name ran whole on the first shard,
            with every step that does not read the shard, so the first
            shard leaves its parts. A record written without the key
            names none.
        attempt: The attempt of the run the shard ran in, as the runner
            counts them ([livery.workshop.RunContext][]'s ``attempt``);
            empty when the runner did not say, and for a record written
            without the key. Two shards of one platform from two
            attempts partitioned from the record as it was each time,
            which the fold names when their partitions differ.
    """

    shard: Shard
    digest: str
    items: tuple[str, ...]
    rows: tuple[str, ...]
    before: datetime
    suites: tuple[str, ...] = ()
    attempt: str = ""


def marker_fields(marker: ShardMarker) -> dict[str, Any]:
    """*marker* as `SHARD_FILE` holds it, and a leg's row under ``shard``."""
    return {
        "shard": str(marker.shard),
        "digest": marker.digest,
        "items": list(marker.items),
        "rows": list(marker.rows),
        "before": marker.before.isoformat(),
        "suites": list(marker.suites),
        "attempt": marker.attempt,
    }


def parse_marker(loaded: Any, *, source: str) -> tuple[ShardMarker | None, str]:
    """The record *loaded* holds, as `marker_fields` wrote it; or why it is none.

    *source* names where it came from, for the reason.

    Returns:
        ``(record, "")`` when it reads; ``(None, reason)`` when *loaded*
        is not a record.
    """
    if not isinstance(loaded, dict):
        return None, f"{source} is not a JSON object"
    try:
        shard = Shard.parse(str(loaded.get("shard", "")))
    except ValueError as error:
        return None, f"{source}: {error}"
    stamp = str(loaded.get("before", "") or "")
    before = _moment(stamp)
    if before is None:
        return None, f"{source}: {stamp!r} is not a time"
    return ShardMarker(
        shard=shard,
        digest=str(loaded.get("digest", "") or ""),
        items=tuple(str(name) for name in loaded.get("items", []) or []),
        rows=tuple(str(name) for name in loaded.get("rows", []) or []),
        before=before,
        suites=tuple(str(name) for name in loaded.get("suites", []) or []),
        attempt=str(loaded.get("attempt", "") or ""),
    ), ""


def leave_shard(
    root: Path,
    *,
    shard: Shard,
    digest: str,
    items: Iterable[str],
    rows: Iterable[str],
    before: datetime,
    suites: Iterable[str] = (),
    attempt: str = "",
) -> Path:
    """Leave the shard's record in `SHARD_FILE` at *root*, for the gate job's proof.

    *suites* names the units whose items are in the set; a unit
    left out ran whole on the first shard. *attempt* is the run's
    attempt the shard ran in, the run context's; empty when unknown.
    The leg's one write copies the record into its timing row under
    ``shard``, where the gate job's fold reads it.

    Returns:
        The path of the file left.
    """
    path = root / SHARD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = ShardMarker(
        shard, digest, tuple(items), tuple(rows), before, tuple(suites), attempt
    )
    path.write_text(json.dumps(marker_fields(marker), sort_keys=True), encoding="utf-8")
    return path


def read_shard(root: Path) -> tuple[ShardMarker | None, str]:
    """Read the record a shard left in `SHARD_FILE` at *root*.

    Returns:
        ``(record, "")`` when it reads; ``(None, "")`` when there is no
        file; ``(None, reason)`` when the file is not a record.
    """
    path = root / SHARD_FILE
    if not path.is_file():
        return None, ""
    try:
        loaded: Any = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        return None, f"{SHARD_FILE} does not parse ({error})"
    return parse_marker(loaded, source=SHARD_FILE)
