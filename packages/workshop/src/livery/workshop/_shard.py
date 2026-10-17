"""A check leg's shard: its spelling, its share of the test files, and the durations.

A platform's check leg may run as several shards, each on a runner of
its own, each running a share of the test files. A shard is spelled
``<i>of<n>`` at the end of the leg's label, ``check-<os>-<python>-2of4``;
the label without it is the platform leg, the key every record below
the gate job's fold is kept by. [livery.workshop.Shard][] parses and
spells one, [livery.workshop.platform_leg][] and
[livery.workshop.shard_of][] split a label.

The share is a function of its inputs: [livery.workshop.partition][]
packs the test files longest first into the shard with the least
summed seconds, so every shard of a run computes the same partition
from the same file set and the same durations, and every file lands in
exactly one shard. The durations are the medians
[livery.workshop.read_durations][] takes over the newest rows of the
platform leg's series in the [livery.workshop.DURATIONS][] family,
bounded by the checkout's committer time
([livery.workshop.checkout_time][]) so a row written while the run is
in flight reaches no shard. A shard leaves what it partitioned in
`SHARD_FILE` beside the gate's marker ([livery.workshop.leave_shard][]),
and the gate job reads it back ([livery.workshop.read_shard][]) to
prove the shards of a platform partitioned one set from one row.
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

#: The file a shard leaves at the checkout root, beside the gate's
#: marker, naming the shard it ran, the digest of the file set it
#: partitioned, its own files, the rows it read and the bound it read
#: them under.
SHARD_FILE = "fm-shard.json"

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
    files: Iterable[str], durations: Mapping[str, float], count: int
) -> tuple[tuple[str, ...], ...]:
    """Pack *files* into *count* shards by their *durations*, every file in exactly one.

    The files go longest first, ties by name, each into the shard with
    the least summed seconds so far, ties to the lowest shard. A file
    *durations* does not name weighs the median of the named
    durations, or one second when none are named. The result is a
    function of the set and the durations alone: the same whatever
    order *files* comes in, and a file given twice is one file. With
    more shards than files the surplus shards are empty.

    Returns:
        One tuple of files per shard, in the order the files were
        packed.

    Raises:
        ValueError: When *count* is below 1.
    """
    if count < 1:
        raise ValueError(f"count is {count}; a partition has at least one shard")
    named = [float(seconds) for seconds in durations.values()]
    fill = statistics.median(named) if named else 1.0

    def weight(name: str) -> float:
        seconds = durations.get(name)
        return float(seconds) if seconds is not None else fill

    ordered = sorted(set(files), key=lambda name: (-weight(name), name))
    shards: list[list[str]] = [[] for _ in range(count)]
    sums = [0.0] * count
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
#: per run, ``{"run": "<id>", "files": {"<path>": <seconds>}}``, written
#: by the run's gate job from its shards' rows. A series of a leg the
#: matrix no longer produces is the janitor's to drop.
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
) -> tuple[dict[str, float], tuple[str, ...], str]:
    """The median seconds per test file on *leg*'s platform, from rows before *before*.

    The rows are the newest `NEWEST` of the series ``durations/<platform
    leg>`` whose store stamp is before *before*; a row stamped at or
    after it, or not stamped, is not read, so every shard of a run
    reads the same rows whatever lands while the run is in flight. The
    median of a file is over the rows that name it with a number. A
    naive *before* is read as UTC.

    Returns:
        The seconds per file, the names of the rows read, newest first,
        and why the series could not be read. A series that cannot be
        read is ``({}, (), reason)``, never a raise: the caller runs as
        one shard of one and says so. An absent series is ``({}, (),
        "")``.
    """
    bound = before if before.tzinfo is not None else before.replace(tzinfo=UTC)
    series = DURATIONS.series(platform_leg(leg))
    found = series.rows(state_store(root))
    if found.failed:
        return {}, (), found.reason
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
        files: Any = row.data.get("files")
        if not isinstance(files, dict):
            continue
        for path, seconds in files.items():
            if isinstance(seconds, int | float) and not isinstance(seconds, bool):
                seen[str(path)].append(float(seconds))
    medians = {
        path: round(statistics.median(values), 1)
        for path, values in sorted(seen.items())
    }
    return medians, tuple(row.name for row in chosen), ""


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
        digest: The digest of the test-file set it partitioned,
            [livery.workshop.files_digest][] of every file of every
            shard.
        files: The files this shard ran, in packing order.
        rows: The durations rows it read, newest first.
        before: The bound it read them under, the checkout's committer
            time.
    """

    shard: Shard
    digest: str
    files: tuple[str, ...]
    rows: tuple[str, ...]
    before: datetime


def leave_shard(
    root: Path,
    *,
    shard: Shard,
    digest: str,
    files: Iterable[str],
    rows: Iterable[str],
    before: datetime,
) -> Path:
    """Leave the shard's record in `SHARD_FILE` at *root*, for the gate job's proof.

    Returns:
        The path of the file left.
    """
    path = root / SHARD_FILE
    path.write_text(
        json.dumps(
            {
                "shard": str(shard),
                "digest": digest,
                "files": list(files),
                "rows": list(rows),
                "before": before.isoformat(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
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
    if not isinstance(loaded, dict):
        return None, f"{SHARD_FILE} is not a JSON object"
    try:
        shard = Shard.parse(str(loaded.get("shard", "")))
    except ValueError as error:
        return None, f"{SHARD_FILE}: {error}"
    stamp = str(loaded.get("before", "") or "")
    before = _moment(stamp)
    if before is None:
        return None, f"{SHARD_FILE}: {stamp!r} is not a time"
    return ShardMarker(
        shard=shard,
        digest=str(loaded.get("digest", "") or ""),
        files=tuple(str(name) for name in loaded.get("files", []) or []),
        rows=tuple(str(name) for name in loaded.get("rows", []) or []),
        before=before,
    ), ""
