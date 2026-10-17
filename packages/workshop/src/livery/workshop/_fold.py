"""The gate job's fold: a platform's shards become one check leg on the run's refs.

A platform's check leg may run as several shards, each on a runner of
its own, each leaving a per-run ref of its own ([livery.workshop.RUNS][])
with its timing row and the files its entries left. Every reader after
the gate job's first entries, the coverage union, the verified stamp
and the speed marks, reads one leg per platform, keyed by the platform
leg ([livery.workshop.platform_leg][]). `fold_run` is that fold, run
once in the gate job before any of them: it lists the run's refs,
groups them by platform, proves that the shards of a platform are one
leg's work, puts one folded ref under the platform leg's key, and drops
the shards' refs. A platform whose one ref is one shard of one is left
as it is.

The proof is the plan's first ground-truth contract: nothing is assumed
from a shard that did not write. A platform's shards fold only when
every shard the matrix produces left a ref with a row and the record of
what it partitioned, every row names one scope, every record names one
test-file set and one list of durations rows read under one bound, no
test file ran in two shards, and the files the shards ran are together
the set they partitioned. Anything else is red by name: the platform,
the shards and what differs. A shard whose record says it ran as one
shard of one fell open on the record and ran the whole set; when every
shard of the platform did, the first stands for the platform. A
platform none of whose shards left a record partitioned nothing: the
gate skipped on every shard, the tree already proved or nothing
affected, or no step that reads the shard ran. Its shards fold on their
scopes and files alone, with no partition to prove, and the line says
so; one shard with a record beside one without is red by name.

The folded row is the first shard's timing row (its tasks, steps and
waits) with every shard's package sums added and its seconds per test
file merged, keyed by the platform leg and named by the platform's own
display name ([livery.workshop._speed.platform_display][]), with the
shards listed under ``shard``. Every other file of the shards' refs
folds through the ``ci.fold`` query ([livery.workshop._queries.FOLDERS][]):
the extension whose leg entry leaves the file answers with the folder;
a file nobody answers for is red by name.

A fold that dies midway is re-run as its own recovery: the shards' refs
stay until the folded ref is put, a put replaces the folded ref in
place, and a shard's ref a failed drop left behind is known by the
stamp the folded row recorded for it and dropped on the next run.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery import git
from livery.footman import fail
from livery.workshop._ci_context import RunContext, state_store
from livery.workshop._metrics import ROW_FILE, RUNS, SLOWEST
from livery.workshop._queries import FOLDERS, Folder, QueryError, answer
from livery.workshop._shard import (
    ONE,
    SHARD_FILE,
    Shard,
    ShardMarker,
    files_digest,
    parse_marker,
    platform_leg,
    shard_of,
)
from livery.workshop._speed import leg_label, platform_display

#: How many files a refusal names before it counts the rest.
NAMED = 5


@dataclass(frozen=True)
class _Ref:
    """One per-run ref of the run, as the fold reads it.

    Attributes:
        key: The ref's key under the family, as the ref spells it.
        ref: The full ref.
        label: The leg's label: the row's own, else the one its
            display name spells, else the key.
        row: The leg's timing row, ``None`` when the ref carries none.
        files: The ref's other rows, by file name.
        marker: The record the shard left of what it partitioned, when
            the row carries one.
        why: Why the row's record is not one, when it is not.
        skipped: The ref's files that are not rows, named.
    """

    key: tuple[str, ...]
    ref: str
    label: str
    row: dict[str, Any] | None
    files: dict[str, dict[str, Any]]
    marker: ShardMarker | None
    why: str
    skipped: tuple[str, ...]

    @property
    def shard(self) -> Shard:
        """The shard the label spells; `ONE` for a label outside the form."""
        return _label_shard(self.label)

    @property
    def folded(self) -> bool:
        """Whether the row is a fold's own: its ``shard`` lists the shards."""
        shard = self.row.get("shard") if self.row is not None else None
        return isinstance(shard, dict) and "shards" in shard

    @property
    def sharded(self) -> bool:
        """Whether the ref is one shard's: its label or its record says so."""
        return self.shard != ONE or self.marker is not None or bool(self.why)

    @property
    def stamp(self) -> str:
        """When the store stamped the row; empty without a row."""
        return str(self.row.get("when", "") or "") if self.row is not None else ""


def _label_shard(label: str) -> Shard:
    try:
        return shard_of(label)
    except ValueError:
        return ONE


def _index(ref: _Ref) -> tuple[int, str]:
    return ref.shard.index, ref.label


def _seconds(item: Mapping[str, Any]) -> float:
    held = item.get("s", 0.0)
    return -float(held) if isinstance(held, int | float) else 0.0


def _read_ref(store: git.StateStore, key: tuple[str, ...]) -> _Ref:
    """The ref of *key*, its row, its other files and the shard record the row carries.

    Raises:
        Failed: When the ref cannot be read: a fold proves nothing from
            a ref it cannot read.
    """
    series = RUNS.at(*key)
    found = series.rows(store)
    if found.failed:
        fail(
            f"{series.ref}: {found.reason}; the fold proves nothing from a ref it"
            " cannot read"
        )
    rows = {row.name: row.data for row in found.rows}
    row = rows.pop(ROW_FILE, None)
    label = key[-1]
    marker: ShardMarker | None = None
    why = ""
    if row is not None:
        scope = row.get("scope")
        own = str(scope.get("leg") or "") if isinstance(scope, Mapping) else ""
        label = own or leg_label(str(row.get("job", ""))) or key[-1]
        shard = row.get("shard")
        if shard is not None and not (isinstance(shard, dict) and "shards" in shard):
            marker, why = parse_marker(shard, source=f"{series.ref}'s shard record")
    return _Ref(
        key,
        series.ref,
        label,
        row,
        rows,
        marker,
        why,
        tuple(skipped.name for skipped in found.skipped),
    )


def expected_shards(root: Path, run: RunContext) -> dict[str, tuple[str, ...]]:
    """The shard labels the matrix produces, by platform leg, where it splits one.

    From ``[ci] pull-request-shards`` on a pull request and ``[ci]
    shards`` otherwise ([livery.workshop.pull_request_shards][],
    [livery.workshop.check_shards][]). A platform of one shard is left
    out. Empty without a root contract, as a test workspace may be.
    """
    from livery.workshop._points import check_shards, pull_request_shards

    if not (root / "workshop.toml").is_file():
        return {}
    legs = (
        pull_request_shards(root) if run.event == "pull_request" else check_shards(root)
    )
    by_platform: dict[str, list[str]] = defaultdict(list)
    for leg in legs:
        by_platform[platform_leg(leg)].append(leg)
    return {
        platform: tuple(labels)
        for platform, labels in by_platform.items()
        if len(labels) > 1
    }


def fold_run(
    root: Path, run: RunContext, *, folders: Mapping[str, Folder] | None = None
) -> None:
    """Fold each platform's shards on *run*'s refs into one leg, or refuse by name.

    Lists the run's per-run refs and groups them by platform leg. A
    platform with one ref that is one shard of one is left as it is.
    A platform the matrix splits, or whose refs are shards', is proved
    and folded (`_fold_platform`), its line printed; with no platform
    sharded, one line says so. *folders* fold the files beside the
    row, by file name; the listed extensions' answers to the
    ``ci.fold`` query when absent.

    Raises:
        Failed: When the refs cannot be listed or read, when a
            platform's shards are not one leg's work, naming the
            platform, the shards and what differs, when a file nobody
            folds is on a shard's ref, and when the folded ref cannot
            be put; the shards' refs then stay for the next run.
    """
    store = state_store(root)
    keys = RUNS.listed(store, run.run_id)
    if keys is None:
        fail(
            f"{RUNS.prefix}{run.run_id}/*: the remote could not be listed; the fold"
            " proves nothing from refs it cannot read"
        )
    refs = [_read_ref(store, key) for key in keys]
    expected = expected_shards(root, run)
    # Grouped by the platform's slug, since a ref without a row has no
    # label but its key, which is one; named by the matrix's spelling
    # or the first row's.
    groups: dict[str, list[_Ref]] = defaultdict(list)
    names: dict[str, str] = {git.slug(platform): platform for platform in expected}
    for ref in refs:
        platform = platform_leg(ref.label)
        groups[git.slug(platform)].append(ref)
        if ref.row is not None:
            names.setdefault(git.slug(platform), platform)
    if folders is None:
        try:
            folders = answer(root, FOLDERS)
        except QueryError as error:
            fail(str(error))
    folded = 0
    for key in sorted(set(groups) | set(names)):
        platform = names.get(key, key)
        held = groups.get(key, [])
        shards = [ref for ref in held if ref.sharded]
        wanted = expected.get(platform, ())
        if not shards and not wanted and not any(ref.folded for ref in held):
            continue
        folded += 1
        _fold_platform(store, run, platform, shards, held, wanted, folders)
    if not folded:
        print("  fold: no check leg ran as shards; nothing to fold")


def _fold_platform(
    store: git.StateStore,
    run: RunContext,
    platform: str,
    shards: list[_Ref],
    held: list[_Ref],
    wanted: tuple[str, ...],
    folders: Mapping[str, Folder],
) -> None:
    """Prove *platform*'s *shards* are one leg's work, put the folded ref, drop theirs.

    *held* is every ref of the platform, *wanted* the shard labels the
    matrix produces for it. A folded ref already there with no shard
    ref beside it is the fold's earlier work, said so and left; one
    with shard refs beside it whose rows the fold stamped is a drop
    that failed, and the leftovers go.
    """
    whole = [ref for ref in held if not ref.sharded]
    already = [ref for ref in whole if ref.folded]
    stray = [ref for ref in whole if not ref.folded]
    if stray and (shards or wanted):
        fail(
            f"{platform}: {', '.join(ref.ref for ref in stray)} carries no shard"
            " record while the platform runs as shards; a leg that did not say"
            " what it partitioned proves nothing"
        )
    if already and not shards:
        count = _recorded_count(already[0])
        print(f"  fold: {platform}: already folded, {count} shard(s)")
        return
    if not shards:
        fail(
            f"{platform}: the matrix splits it into {len(wanted)} shards"
            f" ({', '.join(wanted)}) and none left a ref; a shard that did not"
            " write proves nothing"
        )
    if already and all(_leftover(already[0], ref) for ref in shards):
        for ref in shards:
            _drop(store, ref.ref)
        print(
            f"  fold: {platform}: already folded, {_recorded_count(already[0])}"
            f" shard(s); {len(shards)} ref(s) a failed drop left behind dropped"
        )
        return
    chosen, files, whole_set, unpartitioned = _prove(platform, shards, wanted)
    row = _folded_row(platform, chosen, shards)
    folded_files = _fold_files(platform, chosen, folders)
    why = RUNS.series(run.run_id, platform).put(
        store,
        {ROW_FILE: row, **folded_files},
        message=f"fold: {platform} of run {run.run_id}, {len(shards)} shard(s)",
    )
    if why:
        fail(
            f"{platform}: the folded ref could not be put ({why}); the shards' refs"
            " stay for the next run of the fold"
        )
    for ref in shards:
        _drop(store, ref.ref)
    print(
        f"  fold: {platform}: {len(shards)} shard(s) folded, {len(files)} file(s) ran"
    )
    if whole_set:
        print(
            f"  fold: {platform}: each shard ran the whole set as one shard of one;"
            f" {chosen[0].label} stands for the platform"
        )
    if unpartitioned:
        print(
            f"  fold: {platform}: no shard partitioned test files ({unpartitioned});"
            " folded without a partition"
        )


def _recorded_count(folded: _Ref) -> int:
    shard: Any = (folded.row or {}).get("shard")
    listed: Any = shard.get("shards") if isinstance(shard, dict) else None
    return len(listed) if isinstance(listed, list) else 0


def _leftover(folded: _Ref, ref: _Ref) -> bool:
    """Whether *ref* is a shard's ref the fold that put *folded* already read.

    The folded row records the stamp of each shard's row it folded; a
    shard ref whose row carries that stamp is the same write, left
    behind by a drop that failed, and a later write is a new one.
    """
    shard: Any = (folded.row or {}).get("shard")
    listed: Any = shard.get("shards") if isinstance(shard, dict) else None
    if not isinstance(listed, list):
        return False
    stamps = {
        str(item.get("leg", "")): str(item.get("when", ""))
        for item in listed
        if isinstance(item, dict)
    }
    return bool(ref.stamp) and stamps.get(ref.label) == ref.stamp


def _drop(store: git.StateStore, ref: str) -> None:
    gone = git.drop(store, ref)
    if gone:
        print(f"  fold: {ref}: not dropped ({gone}); the next fold drops it")


def _scope_of(row: Mapping[str, Any]) -> tuple[str, tuple[str, ...], str]:
    scope: Any = row.get("scope")
    if not isinstance(scope, Mapping):
        return "unknown", (), ""
    packages: Any = scope.get("packages") or []
    named = (
        tuple(sorted(str(item) for item in packages))
        if isinstance(packages, list)
        else ()
    )
    return (
        str(scope.get("scope", "unknown")),
        named,
        str(scope.get("base_tree", "") or ""),
    )


def _shown_scope(scope: tuple[str, tuple[str, ...], str]) -> str:
    name, packages, base = scope
    shown = name + (f" ({', '.join(packages)})" if packages else "")
    return shown + (f" from {base[:12]}" if base else "")


def _prove(
    platform: str, shards: list[_Ref], wanted: tuple[str, ...]
) -> tuple[list[_Ref], set[str], bool, str]:
    """Prove *shards* are one leg's work; what to fold, what ran, how partitioned.

    Each check fails by name, in this order: a shard without a row, a
    ref carrying a file that is not a row, a record that is not one, a
    shard without a record beside one with, a record whose shard is not
    the label's, a shard the matrix produces that left no ref (or,
    without a matrix, an index of the records' count that did, or of
    the labels' count when no shard left a record), the shards' scopes,
    their file sets, their durations rows, their bounds, and the files:
    a file in two shards, or a union whose digest is not the set's. A
    record of one shard of one says the shard fell open on the record
    and ran the whole set; every shard of the platform must have, and
    each one's files must be the set, and then the first is folded
    alone.

    Returns:
        The shards to fold, the files they ran, whether each ran the
        whole set, and the shards' one scope when no shard left a
        record, so the shards partitioned nothing and the proof ends
        at their scopes; empty when they partitioned.
    """
    shards = sorted(shards, key=_index)
    names = ", ".join(ref.label for ref in shards)
    without_row = [ref.ref for ref in shards if ref.row is None]
    if without_row:
        fail(
            f"{platform}: {', '.join(without_row)} left no row; a shard that did"
            " not write proves nothing"
        )
    for ref in shards:
        if ref.skipped:
            fail(
                f"{platform}: {ref.ref} carries {', '.join(ref.skipped)}, which is"
                " not a row; the fold cannot carry it"
            )
    for ref in shards:
        if ref.why:
            fail(f"{platform}: {ref.label}: {ref.why}")
    without_record = [ref.label for ref in shards if ref.marker is None]
    with_record = [ref.label for ref in shards if ref.marker is not None]
    if without_record and with_record:
        fail(
            f"{platform}: {', '.join(without_record)} left no shard record"
            f" ({SHARD_FILE} rides the leg's row) and {', '.join(with_record)} did;"
            " a shard that did not say what it partitioned proves nothing"
        )
    markers = {ref.label: ref.marker for ref in shards if ref.marker is not None}
    disagree = [
        f"{label} ran as {marker.shard}"
        for label, marker in markers.items()
        if marker.shard not in (ONE, _label_shard(label))
    ]
    if disagree:
        fail(
            f"{platform}: {', '.join(disagree)}; a shard's label and its record"
            " name one shard"
        )
    present = {git.slug(ref.label) for ref in shards}
    if wanted:
        missing = [label for label in wanted if git.slug(label) not in present]
        if missing:
            fail(
                f"{platform}: {', '.join(missing)} left no ref ({len(present)} of"
                f" {len(wanted)} shards did: {names}); a shard that did not write"
                " proves nothing"
            )
    else:
        # The records' counts, or the labels' when no shard left a
        # record: either way every index of the count must be present.
        counts = sorted(
            {marker.shard.count for marker in markers.values() if marker.shard != ONE}
            or {ref.shard.count for ref in shards if ref.shard != ONE}
        )
        if len(counts) > 1:
            fail(
                f"{platform}: the shards disagree on their count"
                f" ({', '.join(str(count) for count in counts)}): {names}"
            )
        if counts:
            spelled = [
                f"{platform}-{Shard(index, counts[0])}"
                for index in range(1, counts[0] + 1)
            ]
            missing = [label for label in spelled if git.slug(label) not in present]
            if missing:
                fail(
                    f"{platform}: {', '.join(missing)} left no ref ({len(present)}"
                    f" of {counts[0]} shards did: {names}); a shard that did not"
                    " write proves nothing"
                )
    scopes = {ref.label: _scope_of(ref.row or {}) for ref in shards}
    if len(set(scopes.values())) > 1:
        shown = ", ".join(
            f"{label} {_shown_scope(scope)}" for label, scope in scopes.items()
        )
        fail(
            f"{platform}: the shards name different scopes ({shown}); a platform's"
            " shards run one gate"
        )
    if not markers:
        # No shard partitioned: the gate skipped on every shard, or no
        # step that reads the shard ran. One scope is the whole proof.
        return shards, set(), False, _shown_scope(next(iter(scopes.values())))
    digests = {label: marker.digest for label, marker in markers.items()}
    if len(set(digests.values())) > 1:
        shown = ", ".join(f"{label} {digest[:12]}" for label, digest in digests.items())
        fail(
            f"{platform}: the shards partitioned different test-file sets ({shown});"
            " a platform's shards partition one set"
        )
    rows = {label: marker.rows for label, marker in markers.items()}
    if len(set(rows.values())) > 1:
        shown = ", ".join(
            f"{label} {', '.join(read) if read else 'none'}"
            for label, read in rows.items()
        )
        fail(
            f"{platform}: the shards read different durations rows ({shown}); a"
            " platform's shards partition by one row"
        )
    bounds = {label: marker.before for label, marker in markers.items()}
    if len(set(bounds.values())) > 1:
        shown = ", ".join(
            f"{label} {before.isoformat()}" for label, before in bounds.items()
        )
        fail(
            f"{platform}: the shards read the durations under different bounds"
            f" ({shown}); every shard of a run reads before its checkout's time"
        )
    recorded = next(iter(digests.values()))
    whole_set = [ref for ref in shards if markers[ref.label].shard == ONE]
    if whole_set and len(whole_set) != len(shards):
        sliced = [ref.label for ref in shards if ref not in whole_set]
        fail(
            f"{platform}: {', '.join(ref.label for ref in whole_set)} ran the whole"
            f" set as one shard of one while {', '.join(sliced)} ran a slice; a"
            " platform's shards ran one partition or none"
        )
    if whole_set:
        for ref in whole_set:
            own = files_digest(markers[ref.label].files)
            if own != recorded:
                fail(
                    f"{platform}: {ref.label} says it ran the whole set, and its"
                    f" files' digest {own[:12]} is not the set's {recorded[:12]}"
                )
        return [whole_set[0]], set(markers[whole_set[0].label].files), True, ""
    seen: dict[str, str] = {}
    twice: list[str] = []
    for ref in shards:
        for name in markers[ref.label].files:
            if name in seen:
                twice.append(f"{name} ({seen[name]} and {ref.label})")
            else:
                seen[name] = ref.label
    if twice:
        rest = f" and {len(twice) - NAMED} more" if len(twice) > NAMED else ""
        fail(
            f"{platform}: {len(twice)} file(s) ran in two shards:"
            f" {', '.join(twice[:NAMED])}{rest}; every file lands in exactly one"
            " shard"
        )
    together = files_digest(seen)
    if together != recorded:
        fail(
            f"{platform}: the files the shards ran together have digest"
            f" {together[:12]}, and the set they partitioned has {recorded[:12]}:"
            " a file of the set ran in no shard"
        )
    return shards, set(seen), False, ""


def _folded_row(
    platform: str, chosen: Sequence[_Ref], shards: Sequence[_Ref]
) -> dict[str, Any]:
    """The platform's one timing row from *chosen*'s rows, listing *shards*.

    The first chosen shard's row stands, its tasks, steps, waits and
    wall as they are; the package sums and the seconds per test file
    are every chosen shard's added, the slowest tests every chosen
    shard's, slowest first; the row is named by the platform's display
    name and keyed by the platform leg under ``scope.leg``; ``shard``
    records the set's digest, the rows and the bound the shards read,
    and each shard's label, job, row stamp, file count, wall and
    package sums. Shards that left no record partitioned nothing: the
    digest, the rows and the bound are empty.
    """
    first = chosen[0].row or {}
    marker = chosen[0].marker
    packages: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"tests_ms": 0.0, "tests": 0}
    )
    per_file: dict[str, float] = defaultdict(float)
    slowest: list[dict[str, Any]] = []
    for ref in chosen:
        row = ref.row or {}
        held: Any = row.get("packages")
        for name, data in held.items() if isinstance(held, dict) else ():
            if not isinstance(data, dict):
                continue
            ms = data.get("tests_ms", 0.0)
            tests = data.get("tests", 0)
            packages[str(name)]["tests_ms"] = round(
                float(packages[str(name)]["tests_ms"])
                + (float(ms) if isinstance(ms, int | float) else 0.0),
                1,
            )
            packages[str(name)]["tests"] = int(packages[str(name)]["tests"]) + (
                tests if isinstance(tests, int) else 0
            )
        files: Any = row.get("files")
        for path, seconds in files.items() if isinstance(files, dict) else ():
            if isinstance(seconds, int | float):
                per_file[str(path)] = round(per_file[str(path)] + float(seconds), 1)
        slow: Any = row.get("slowest")
        if isinstance(slow, list):
            slowest.extend(item for item in slow if isinstance(item, dict))
    slowest.sort(key=_seconds)
    scope: Any = first.get("scope")
    folded = {
        key: value for key, value in first.items() if key not in ("schema", "when")
    }
    folded.update(
        {
            "job": platform_display(str(first.get("job", ""))),
            "packages": dict(sorted(packages.items())),
            "files": dict(sorted(per_file.items())),
            "slowest": slowest[:SLOWEST],
            "scope": {**(scope if isinstance(scope, dict) else {}), "leg": platform},
            "shard": {
                "count": len(shards),
                "digest": marker.digest if marker is not None else "",
                "rows": list(marker.rows) if marker is not None else [],
                "before": marker.before.isoformat() if marker is not None else "",
                "shards": [
                    {
                        "shard": str(ref.shard),
                        "leg": ref.label,
                        "job": str((ref.row or {}).get("job", "")),
                        "when": ref.stamp,
                        "files": len(ref.marker.files) if ref.marker else 0,
                        "total_ms": (ref.row or {}).get("total_ms"),
                        "packages": (ref.row or {}).get("packages", {}),
                    }
                    for ref in sorted(shards, key=_index)
                ],
            },
        }
    )
    return folded


def _fold_files(
    platform: str, chosen: Sequence[_Ref], folders: Mapping[str, Folder]
) -> dict[str, Mapping[str, Any]]:
    """Fold every file beside the row through its folder; red by name when one cannot.

    Every chosen shard left the same file names, since a leg's entries
    run alike on every shard; a name some left and some did not fails
    naming both sides. A name nobody folds, and a folder that refuses,
    fail naming the platform and the file.
    """
    names = sorted({name for ref in chosen for name in ref.files})
    folded: dict[str, Mapping[str, Any]] = {}
    for name in names:
        lacking = [ref.label for ref in chosen if name not in ref.files]
        if lacking:
            having = [ref.label for ref in chosen if name in ref.files]
            fail(
                f"{platform}: {name} is on {', '.join(having)} and not on"
                f" {', '.join(lacking)}; a leg's entries leave the same files on"
                " every shard"
            )
        folder = folders.get(name)
        if folder is None:
            fail(
                f"{platform}: nobody folds {name}: an extension answers the ci.fold"
                " query with a folder for each file its leg entry leaves"
            )
        shards_files = [
            {
                key: value
                for key, value in ref.files[name].items()
                if key not in ("schema", "when")
            }
            for ref in chosen
        ]
        try:
            folded[name] = folder(shards_files)
        except ValueError as error:
            fail(f"{platform}: {name} does not fold ({error})")
    return folded
