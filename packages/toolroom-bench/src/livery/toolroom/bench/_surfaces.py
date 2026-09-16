"""What each curated tool accepted, version by version, in its record.

A tool's record ([livery.toolroom.store.Record][]) carries each
version's surface one verb at a time, forward and inherited; this
module is the bench's side of it. Reading a tool gives a surface,
`surface_of`; several platforms' readings of one version fold into one
surface and one sidecar of who missed what, `fold`; a version is placed
in its record at its own position, `place`, or a later reading is
merged into a version already there, `merge`; and `union` is what every
stub renders: every option the tool has ever had, each with the
interval and the platform verdicts the record can prove.

The sidecar and every standing claim are kept apart on purpose. A
record stores only what a platform saw at the version it looked at;
"Windows lacks `--fork`", "added in 1.2.0" and "gone since 2.0.0" are
derived at render time from those observations, where a later sighting
revises them, and never written down where they would harden.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.bench._toolspec import Option, ToolSpec, Verb
from livery.toolroom.store import (
    Observation,
    Record,
    RecordDelta,
    Surface,
    observations,
    surface_at,
)
from livery.toolroom.tools import version_tuple

EXTRACTOR = 4
"""The extractor generation that produced an observation.

Recorded per release so improving `_toolhelp`/`_toolspec`, or a tool
flipping between the click and `--help` paths, rewrites state without
counting as the tool having changed. Bump it when extraction starts
producing different words for the same tool, and a gather will offer those
releases again: a reading is only as good as the extractor that took it.

**2**, reading each release in the era it shipped in. Under today's
dependencies twine 5.1.0 indexes `metadata["home-page"]`, which
importlib_metadata 8 raises on, so it died before argparse ran and three
releases were recorded with no options at all. Pinning the resolution and
the interpreter to the release's own date changes what extraction can see,
which is exactly what this number is for.

**3**, reading a tool's description rather than the furniture around it.
A manual names itself after an en dash on mdoc pages, which the NAME
pattern did not match, so every OpenSSH page fell back to its running
header: `SSH(1)  General Commands Manual  SSH(1)`, justified to whatever
width the reader rendered at. A tool that signs its help instead of
describing itself, `git-cliff 2.13.1`, markdownlint-cli2's banner and
URL, had the signature stored as its description, and bun's trailing
build stamp went in with the sentence it followed. Each of those made a
version bump read back as the tool rewording itself, and none of them was
ever what the tool said it did.

**4**, reading the builder docker actually installs. `docker build` is
buildx wherever buildx is installed, and the driver installs it, but with
`DOCKER_BUILDKIT` unset the CLI only routes `build` that way for a Linux
daemon and a reader has no daemon. Linux and macOS read buildx while
Windows read the builder docker shipped with before 2019, so one verb was
stored as two surfaces and twelve options were recorded as a per-platform
difference that no user has. The opt-in is pinned for the read now.
"""


class LossyReading(Exception):
    """A reading that lost bytes on the way in, refused rather than stored.

    U+FFFD is the decoder saying it could not represent something it read.
    Whatever the tool printed there, this is not it, and because help text
    is state, storing it manufactures an event: djLint's banner `·` arrived
    as one cp1252 byte on Windows, decoded to U+FFFD under UTF-8, and the
    store recorded 1.43.2 as having changed a description that never moved.

    Refusing costs one release on one platform, reported as a hole and
    filled by the next run. Recording costs the store its meaning, because
    nothing downstream can tell a mangled byte from a real edit.
    """


def _refuse_lossy(spec: ToolSpec, surface: dict[str, Any]) -> None:
    """Guard the one door every stored reading comes through."""
    if "�" not in json.dumps(surface, ensure_ascii=False):
        return
    where = next(
        (
            verb.name
            for verb in spec.verbs
            if "�" in verb.help or any("�" in option.help for option in verb.options)
        ),
        "the tool's own help",
    )
    raise LossyReading(
        f"{spec.name or 'tool'} {spec.version or ''}: the reading of {where} "
        f"carries U+FFFD, bytes the decoder could not read. Refusing to "
        f"record it: a mangled character is indistinguishable from a real "
        f"change once it is in the record."
    )


def surface_of(spec: ToolSpec) -> dict[str, Any]:
    """A ToolSpec reduced to what a release *is*, losing nothing else.

    Refuses a reading that lost bytes, see `LossyReading`. This is the one
    door every stored surface comes through, whichever task minted it.
    """
    surface = _surface(spec)
    _refuse_lossy(spec, surface)
    return surface


def _surface(spec: ToolSpec) -> dict[str, Any]:
    return {
        "help": spec.help,
        "verbs": {
            verb.name: {
                "help": verb.help,
                "wraps": verb.wraps,
                "positional": verb.positional,
                "lead": verb.lead,
                "options": {
                    option.name: {
                        "flags": list(option.flags),
                        "negation": option.negation,
                        "help": option.help,
                        "type": option.type_name,
                        "default": option.default,
                        "choices": list(option.choices),
                    }
                    for option in verb.options
                },
            }
            for verb in spec.verbs
        },
    }


def spec_from(
    surface: dict[str, Any], *, name: str, version: str = "", in_process: bool = False
) -> ToolSpec:
    """The inverse of `surface_of`, what the stub renderer consumes."""
    return ToolSpec(
        name=name,
        help=surface.get("help", ""),
        version=version,
        in_process=in_process,
        verbs=tuple(
            Verb(
                name=verb_name,
                help=verb.get("help", ""),
                wraps=verb.get("wraps", False),
                positional=verb.get("positional", "any"),
                lead=verb.get("lead", ""),
                options=tuple(
                    Option(
                        name=option_name,
                        flags=tuple(option.get("flags", ())),
                        negation=option.get("negation", ""),
                        help=option.get("help", ""),
                        type_name=option.get("type", "str"),
                        default=option.get("default"),
                        choices=tuple(option.get("choices", ())),
                    )
                    for option_name, option in verb.get("options", {}).items()
                ),
            )
            for verb_name, verb in surface.get("verbs", {}).items()
        ),
    )


def delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """How to step back from *newer* to *older*, per option.

    Three moves, and an empty delta means the release was observed and
    changed nothing, which is not the same as a release nobody looked at.
    Those are simply absent.
    """
    out: dict[str, Any] = {}
    new_opts, old_opts = _flat(newer), _flat(older)
    if drop := sorted(set(new_opts) - set(old_opts)):
        out["drop"] = drop  # the newer release added these
    if add := sorted(set(old_opts) - set(new_opts)):
        out["add"] = {key: old_opts[key] for key in add}  # ...and removed these
    revert = {
        k: old_opts[k]
        for k in old_opts.keys() & new_opts.keys()
        if old_opts[k] != new_opts[k]
    }
    if revert:
        out["revert"] = dict(sorted(revert.items()))
    if verbs := _verb_delta(newer, older):
        out["verbs"] = verbs
    if older.get("help", "") != newer.get("help", ""):
        out["help"] = older.get("help", "")
    return out


PLATFORM_PRIORITY = ("Linux", "macOS", "Windows")
"""Whose words to keep when two platforms describe one option differently.

A tie-break, and nothing more. The store keeps one copy of an option's text,
so without a fixed, order-independent pick, alternating matrix legs would
flip a divergent help string every week, and every flip is a `revert` in a
store whose whole question is "did anything change". Fixed and explicit
because `sorted()` is not it: ASCII puts `macOS` after `Windows`.
"""


def fold(
    readings: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """One release seen by several platforms → one surface and its sidecar.

    *readings* maps platform to that platform's surface for a single
    release. Folding before the chain is touched is what keeps a matrix run
    from writing churn into history: an option only Linux has would
    otherwise be inserted, then dropped by macOS, then resurrected by
    Windows, each step a real delta. Folded first, the chain sees one
    finished surface and one sidecar.

    An option any platform saw is in the surface, the union, so nothing
    observed is lost, and the platforms that looked without finding it are
    the sidecar. Divergent text is settled by `PLATFORM_PRIORITY`.
    """
    looked = list(readings)
    surface: dict[str, Any] = {"help": "", "verbs": {}}
    missing: dict[str, list[str]] = {}

    def rank(platform: str) -> int:
        order = PLATFORM_PRIORITY
        return order.index(platform) if platform in order else len(order)

    for platform in sorted(looked, key=rank):  # best last: it overwrites
        surface["help"] = readings[platform].get("help", "") or surface["help"]
    for platform in sorted(looked, key=rank):
        for verb_name, verb in readings[platform].get("verbs", {}).items():
            held = surface["verbs"].setdefault(verb_name, _empty_verb())
            held.update({k: v for k, v in verb.items() if k != "options"})
            held["options"].update(verb.get("options", {}))

    for verb_name, verb in surface["verbs"].items():
        for platform in looked:
            seen = readings[platform].get("verbs", {})
            if verb_name not in seen:
                missing.setdefault(f"{verb_name}\t", []).append(platform)
                continue
            for option_name in verb["options"]:
                if option_name not in seen[verb_name].get("options", {}):
                    missing.setdefault(f"{verb_name}\t{option_name}", []).append(
                        platform
                    )
    # A whole verb nobody found on a platform is said once, not per option.
    for verb_name in surface["verbs"]:
        for platform in missing.get(f"{verb_name}\t", []):
            for key in list(missing):
                if key.startswith(f"{verb_name}\t") and key != f"{verb_name}\t":
                    missing[key] = [p for p in missing[key] if p != platform]
    return {"help": surface["help"], "verbs": _ordered(surface["verbs"])}, {
        key: who for key, who in missing.items() if who
    }


_TOOL_HELP = "\t\thelp"


def _preferred(
    stored: dict[str, Any] | None,
    incoming: dict[str, Any],
    was: list[str],
    now: list[str],
    missing: dict[str, list[str]],
    key: str,
) -> dict[str, Any]:
    """Whose words to keep for one option, independent of merge order.

    The stored text belongs to the highest-priority platform that had the
    option; the incoming text to whoever is merging. The higher rank wins,
    and a platform re-reading its own contribution always wins, so a week
    of legs arriving in any order settles on the same answer.
    """
    if stored is None:
        return incoming

    def rank(platforms: list[str]) -> int:
        holders = [p for p in platforms if p not in missing.get(key, [])]
        ranks = [PLATFORM_PRIORITY.index(p) for p in holders if p in PLATFORM_PRIORITY]
        return min(ranks) if ranks else len(PLATFORM_PRIORITY)

    return incoming if rank(now) <= rank(was) else stored


def _flat(surface: dict[str, Any]) -> dict[str, Any]:
    """Options keyed `verb\\toption`, so a diff is one flat set operation.

    A tab, because a verb is dotted (`compose.up`) and an option name can
    carry anything a tool's `--help` prints, but neither can hold a tab.
    """
    return {
        f"{verb_name}\t{option_name}": option
        for verb_name, verb in surface.get("verbs", {}).items()
        for option_name, option in verb.get("options", {}).items()
    }


def _verb_delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """Verb-level changes: a verb gained or lost, or its own metadata moved.

    Options ride the flat diff; this carries what hangs off the verb itself ,
    its help, whether it wraps another command, its positional shape.
    """
    fields = ("help", "wraps", "positional", "lead")
    out: dict[str, Any] = {}
    new_verbs, old_verbs = newer.get("verbs", {}), older.get("verbs", {})
    for name in set(new_verbs) - set(old_verbs):
        out[name] = None  # the newer release added it; stepping back drops it
    for name, verb in old_verbs.items():
        if name not in new_verbs:
            out[name] = {f: verb.get(f) for f in fields}
        elif changed := {
            f: verb.get(f) for f in fields if verb.get(f) != new_verbs[name].get(f)
        }:
            out[name] = changed
    return out


def _empty_verb() -> dict[str, Any]:
    return {"help": "", "wraps": False, "positional": "any", "lead": "", "options": {}}


def _ordered(verbs: dict[str, Any]) -> dict[str, Any]:
    """Verbs and their options in name order, so a replayed surface compares
    equal to a freshly extracted one however the deltas arrived.
    """
    return {
        name: {**verb, "options": dict(sorted(verb.get("options", {}).items()))}
        for name, verb in sorted(verbs.items())
    }


def _option_fields(option: Option, **replaced: Any) -> dict[str, Any]:
    """An Option's fields as one merged dict, its own values with *replaced*
    laid over them, annotated so the `Option(**…)` splat type-checks under
    every checker (an inline heterogeneous dict distributes its value union
    over each field otherwise).
    """
    return {**option.__dict__, **replaced}


# --- the record ----------------------------------------------------------------
#
# The record is read whole and rebuilt whole. Every write below resolves
# each version's surface through the record's inheritance, changes one
# version's reading, and derives every delta's sparse surface again from
# the sequence of whole ones. Rebuilding is what keeps the one invariant a
# record of observations must have: changing what one version says never
# changes what any other version resolves to, because the version after a
# changed one is re-anchored on the value it had.


def versions(record: Record) -> list[str]:
    """Every version read, newest first: the versions whose delta carries a surface."""
    return [seen.version for seen in reversed(observations(record))]


def floor(record: Record) -> str:
    """The oldest version read, how far back the record reaches."""
    return versions(record)[-1]


def observation(record: Record, version: str) -> Observation | None:
    """What *version* accepted and who read it, or `None` when no reading was taken.

    A version the record does not track answers `None` too: to a reader
    of surfaces an unknown version and an unread one are one case.
    """
    if version not in record.versions:
        return None
    return surface_at(record, version)


def at(record: Record, version: str) -> dict[str, Any] | None:
    """The surface of *version*, its help and verbs, or `None` when it was never read.

    Shared with the record, not copied: a caller that changes it copies
    it first.
    """
    seen = observation(record, version)
    if seen is None:
        return None
    return {"help": seen.help, "verbs": seen.verbs}


def platforms_of(record: Record, version: str) -> list[str]:
    """The platforms that read *version*; empty when none did."""
    seen = observation(record, version)
    return list(seen.platforms) if seen else []


def absent_of(record: Record, version: str) -> dict[str, list[str]]:
    """Who looked at *version* and did not find each option, keyed `verb\\toption`.

    A bare `verb\\t` marks the whole verb missing. Only ever what was seen
    to be missing: every platform named read the version.
    """
    seen = observation(record, version)
    return _flatten(seen.absent) if seen else {}


def _flatten(nested: dict[str, dict[str, tuple[str, ...]]]) -> dict[str, list[str]]:
    return {
        f"{verb}\t{option}": list(who)
        for verb, options in nested.items()
        for option, who in options.items()
    }


def _nest(flat: dict[str, list[str]]) -> dict[str, dict[str, tuple[str, ...]]]:
    nested: dict[str, dict[str, tuple[str, ...]]] = {}
    for key, who in sorted(flat.items()):
        if not who:
            continue
        verb, _, option = key.partition("\t")
        nested.setdefault(verb, {})[option] = tuple(sorted(set(who)))
    return nested


@dataclass
class _Reading:
    """One version as the rebuild sees it: its delta, and its surface whole."""

    delta: RecordDelta
    surface: dict[str, Any] | None
    platforms: list[str]
    extractor: int
    absent: dict[str, list[str]]


def _readings(record: Record) -> list[_Reading]:
    seen = {found.version: found for found in observations(record)}
    out: list[_Reading] = []
    for delta in record.deltas:
        found = seen.get(delta.version)
        out.append(
            _Reading(
                delta,
                None if found is None else {"help": found.help, "verbs": found.verbs},
                list(found.platforms) if found else [],
                found.extractor if found else EXTRACTOR,
                _flatten(found.absent) if found else {},
            )
        )
    return out


def _assemble(record: Record, readings: list[_Reading]) -> Record:
    """The record rebuilt from *readings*: sparse surfaces derived in sequence."""
    deltas: list[RecordDelta] = []
    previous: dict[str, Any] | None = None
    for sequence, reading in enumerate(readings, start=1):
        surface: Surface | None = None
        if reading.surface is not None:
            help_, verbs = _sparse(previous, reading.surface)
            surface = Surface(
                tuple(sorted(reading.platforms)),
                reading.extractor,
                help_,
                verbs,
                _nest(reading.absent),
            )
            previous = reading.surface
        deltas.append(
            RecordDelta(
                sequence,
                reading.delta.version,
                reading.delta.date,
                dict(reading.delta.artifacts),
                reading.delta.layout,
                dict(reading.delta.host_layouts),
                surface,
            )
        )
    return Record(
        record.name,
        record.description,
        record.kind,
        record.min_version,
        record.hosts,
        record.layout,
        record.host_layouts,
        tuple(deltas),
    )


def _sparse(
    previous: dict[str, Any] | None, surface: dict[str, Any]
) -> tuple[str | None, dict[str, dict[str, Any] | None]]:
    """What *surface* sets against *previous*: the help when it moved, and
    each verb that moved, whole, `None` for one withdrawn.
    """
    verbs = _ordered(surface.get("verbs", {}))
    help_ = surface.get("help", "")
    if previous is None:
        return help_, dict(verbs)
    before: dict[str, Any] = previous["verbs"]
    changed: dict[str, dict[str, Any] | None] = {
        name: verb for name, verb in verbs.items() if before.get(name) != verb
    }
    for name in before:
        if name not in verbs:
            changed[name] = None
    return (None if help_ == previous["help"] else help_), changed


def _set(
    record: Record,
    version: str,
    *,
    surface: dict[str, Any],
    platforms: list[str],
    absent: dict[str, list[str]],
    extractor: int,
) -> Record:
    """*version*'s reading replaced; every other version resolves as before."""
    readings = _readings(record)
    for reading in readings:
        if reading.delta.version == version:
            reading.surface = {
                "help": surface.get("help", ""),
                "verbs": _ordered(surface.get("verbs", {})),
            }
            reading.platforms = sorted(platforms)
            reading.absent = absent
            reading.extractor = extractor
    return _assemble(record, readings)


def new(
    name: str,
    *,
    kind: str,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str],
) -> Record:
    """A record of one version read. A record this short is a valid record,
    which is what lets a tool ship before anything has been primed.
    """
    return Record(
        name,
        kind=kind,
        deltas=(
            RecordDelta(
                1,
                version,
                date,
                surface=Surface(
                    tuple(sorted(platforms)),
                    EXTRACTOR,
                    surface.get("help", ""),
                    _ordered(surface.get("verbs", {})),
                ),
            ),
        ),
    )


def place(
    record: Record,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str],
    absent: dict[str, list[str]] | None = None,
) -> Record | None:
    """*version* placed at its own position in the record, wherever that is.

    Above the newest, below the oldest, or between two the record already
    holds: the deltas are renumbered and the version after it re-anchored,
    and nothing any other version resolves to moves. What it buys is that
    gathering need not be ordered: installing a release and reading its
    help depends on no other release having been read, so a walk fetches
    in whatever order it likes and assembles as results arrive. A release
    that would not install stops being fatal to a tool's whole walk, since
    the gap is filled by a later run.

    A gap costs precision until it is filled, not correctness. An option
    that arrived in the missing release reads as arriving at the next
    release that was read, the same honest imprecision the record carries
    wherever an index has no build to offer.

    A version the record tracks but never read, one with an artifact
    alone, takes this reading as its first. Returns `None` for a version
    already read, which is left exactly as it is.

    Raises:
        ValueError: when *version* and *date* tie a version already there
            on every component the comparator has, since there is then no
            honest place for it.
    """
    from livery.toolroom.bench._toolfetch import _patchlevel

    readings = _readings(record)
    for reading in readings:
        if reading.delta.version == version:
            if reading.surface is not None:
                return None
            return _set(
                record,
                version,
                surface=surface,
                platforms=platforms,
                absent=absent or {},
                extractor=EXTRACTOR,
            )

    def placed(name: str, when: str) -> tuple[tuple[int, ...], int, str]:
        # The patchlevel rides between the tuple and the date because
        # `version_tuple` deliberately reads OpenSSH's `9.9p1` and `9.9p2`
        # as one base and leaves the tie to the caller. Two patchlevels of
        # one release often share a day, so the date alone cannot break it.
        return version_tuple(name), _patchlevel(name), when

    mine = placed(version, date)
    keys = [placed(r.delta.version, r.delta.date) for r in readings]
    if mine in keys:
        raise ValueError(f"{version} ties a version already in the record")
    spot = sum(1 for key in keys if key < mine)
    readings.insert(
        spot,
        _Reading(
            RecordDelta(0, version, date),
            {
                "help": surface.get("help", ""),
                "verbs": _ordered(surface.get("verbs", {})),
            },
            sorted(platforms),
            EXTRACTOR,
            absent or {},
        ),
    )
    return _assemble(record, readings)


def merge(
    record: Record,
    *,
    version: str,
    surface: dict[str, Any],
    platforms: list[str],
    absent: dict[str, list[str]] | None = None,
) -> tuple[Record, bool]:
    """Fold another platform's reading into a version the record already has.

    The other way a reading reaches the record beside `place`, and the one
    a matrix needs: a version observed on macOS in May and on Windows in
    July is one version with two witnesses, not two records.

    Merging never removes an option. An absence is recorded in the sidecar,
    where it says who failed to find it rather than that it stopped
    existing, so the surface only grows, and the versions either side of
    this one move only when the tool differs, never when coverage does.
    The reading is stamped with today's `EXTRACTOR`, whatever it found.

    Returns the record and whether the stored surface changed. A merge
    that only widened coverage answers `False` and costs the neighbours
    nothing. A version tracked but never read takes the reading whole;
    a version the record does not track is left alone.
    """
    if version not in record.versions:
        return record, False
    stored = at(record, version)
    if stored is None:
        return (
            _set(
                record,
                version,
                surface=surface,
                platforms=platforms,
                absent=absent or {},
                extractor=EXTRACTOR,
            ),
            True,
        )
    was = platforms_of(record, version)
    observers = sorted({*was, *platforms})
    before = json.dumps(stored, sort_keys=True)

    known = _flat(stored)
    incoming = _flat(surface)
    missing = {key: list(who) for key, who in absent_of(record, version).items()}
    merged = json.loads(json.dumps(stored))  # a deep copy; surfaces are plain data

    for verb_name, reading in surface.get("verbs", {}).items():
        # A verb's own fields, its summary and its positional shape, settle
        # by the same rule its options do. Left out, they would be frozen at
        # whatever the first reading said, and no better extractor could
        # ever replace them, which is the one thing a record of
        # observations must never need a hand to fix.
        held = merged.setdefault("verbs", {}).setdefault(verb_name, _empty_verb())
        fields = {k: v for k, v in reading.items() if k != "options"}
        chosen = _preferred(
            {k: v for k, v in held.items() if k != "options"}
            if verb_name in stored.get("verbs", {})
            else None,
            fields,
            was,
            platforms,
            missing,
            f"{verb_name}\t",
        )
        held.update(chosen)

    for key, option in incoming.items():
        verb_name, _, option_name = key.partition("\t")
        verb = merged.setdefault("verbs", {}).setdefault(verb_name, _empty_verb())
        if key not in known:
            # Nobody who looked before found it, or it would be stored.
            missing[key] = [p for p in was if p not in platforms]
        verb["options"][option_name] = _preferred(
            known.get(key), option, was, platforms, missing, key
        )
    for key in known:
        if key not in incoming:
            missing.setdefault(key, [])
            missing[key] = sorted({*missing[key], *platforms})
    for key in list(missing):
        if key in incoming:
            missing[key] = [p for p in missing[key] if p not in platforms]
    for key, who in (absent or {}).items():
        missing[key] = sorted({*missing.get(key, []), *who})

    # The tool's own description settles by the rule its verbs' summaries
    # already use, never by whichever reading is non-empty. Ranked, an
    # empty reading from the platform that owns the text replaces it, so a
    # signature a better extractor stops reading as a description can be
    # corrected, while a lower-ranked platform's silence still cannot
    # erase a good line.
    widened = {
        "help": _preferred(
            {"help": stored.get("help", "")},
            {"help": surface.get("help", "")},
            was,
            platforms,
            missing,
            _TOOL_HELP,
        )["help"],
        "verbs": _ordered(merged.get("verbs", {})),
    }
    tidy = {
        key: [p for p in sorted(set(who)) if p in observers]
        for key, who in sorted(missing.items())
    }
    moved = json.dumps(widened, sort_keys=True) != before
    return (
        _set(
            record,
            version,
            surface=widened,
            platforms=observers,
            absent={key: who for key, who in tidy.items() if who},
            extractor=EXTRACTOR,
        ),
        moved,
    )


def changed(record: Record, version: str) -> bool:
    """Whether *version*'s reading moved the surface against the version read before it.

    The release decision, answered from the record rather than remembered
    from arrival order: a gap just below a version makes its delta span the
    gap, so the change is attributed to the version actually read. The
    oldest version read has nothing below it to have changed from.
    """
    delta = record.delta_for(version)
    if delta.surface is None:
        return False
    chain = versions(record)
    if chain.index(version) + 1 >= len(chain):
        return False
    return delta.surface.help is not None or bool(delta.surface.verbs)


def changes(record: Record, *, since: str, until: str = "") -> dict[str, Any]:
    """What changed between two versions read, as one step.

    The net effect, not a concatenation of the steps between: an option a
    tool added and then withdrew across the span cancels out, which is
    what someone reading a release note wants to know. Computed from both
    ends' surfaces, so it cannot disagree with the record.

    Returned in the shape of `delta` and read the same way round: `drop`
    is what the newer version added, `add` is what it removed, because it
    is a step back from *until* to *since*.
    """
    newer = at(record, until or versions(record)[0])
    older = at(record, since)
    if newer is None or older is None:
        return {}
    return delta(newer, older)


def spellings(record: Record, version: str, keys: Iterable[str]) -> dict[str, str]:
    """How *version* spells each option key on the command line.

    A step records the option's Python-side name, which is what the
    surface is keyed by; a reader of a release note recognises
    `--all-files`. The flags live in the surface, so the spelling is a
    lookup rather than something the step has to carry.
    """
    surface = at(record, version) or {}
    found: dict[str, str] = {}
    for key in keys:
        verb, _, option = key.partition("\t")
        entry = surface.get("verbs", {}).get(verb, {}).get("options", {}).get(option)
        flags = (entry or {}).get("flags") or []
        # The long spelling when there is one: `--all-files` over `-a`.
        found[key] = max(flags, key=len) if flags else option
    return found


def load(directory: Path) -> Record | None:
    """The record at *directory*, or `None` when there is none yet.

    Raises:
        RecordError: for a record that is there and does not validate;
            a broken record is corrected, never read as no record.
    """
    if not (directory / "tool.json").is_file():
        return None
    return Record.load(directory)


def save(record: Record, directory: Path) -> None:
    """Write *record* under *directory*, one file per delta."""
    record.save(directory)


# --- the union: what a stub renders --------------------------------------------


def union(record: Record, *, name: str, in_process: bool = False) -> ToolSpec:
    """Every option the tool has ever had, each with its interval.

    The stub renders this rather than the newest version alone: a removed
    flag stays completable, because the reader may be running a version
    that still has it, and its docstring says when it went. An option's
    properties come from the newest version that had it, the most recent
    word the tool said about itself.

    `since` is left empty for anything already present at the oldest
    version read. The record reaches only as far as it was primed, and
    "at or before the floor" is not a `since`.
    """
    chain = versions(record)  # newest first
    floor_ = chain[-1]
    surfaces = {version: at(record, version) for version in chain}
    verdicts = _verdicts(record, chain, surfaces)

    verbs: dict[str, dict[str, Any]] = {}
    first: dict[tuple[str, str], str] = {}
    last: dict[tuple[str, str], str] = {}
    for version in reversed(chain):  # oldest first, so "first" means first
        surface = surfaces[version] or {}
        for verb_name, verb in surface.get("verbs", {}).items():
            verbs.setdefault(verb_name, verb)
            merged = {
                **verbs[verb_name].get("options", {}),
                **verb.get("options", {}),
            }
            # Sorted, so the stub does not reorder itself as the record
            # deepens: merged oldest-first, insertion order would otherwise
            # mean "which version mentioned it first".
            verbs[verb_name] = {**verb, "options": dict(sorted(merged.items()))}
            for option_name in verb.get("options", {}):
                key = (verb_name, option_name)
                first.setdefault(key, version)
                last[key] = version

    newer = {older: new for new, older in itertools.pairwise(chain)}
    spec = spec_from(
        {"help": (surfaces[chain[0]] or {}).get("help", ""), "verbs": verbs},
        name=name,
        version=chain[0],
        in_process=in_process,
    )
    return ToolSpec(
        name=spec.name,
        help=spec.help,
        version=spec.version,
        in_process=spec.in_process,
        verbs=tuple(
            Verb(
                name=verb.name,
                help=verb.help,
                wraps=verb.wraps,
                positional=verb.positional,
                lead=verb.lead,
                not_on=verdicts.get(f"{verb.name}\t", ()),
                options=tuple(
                    Option(
                        **_option_fields(
                            option,
                            not_on=verdicts.get(f"{verb.name}\t{option.name}", ()),
                            since=""
                            if first[(verb.name, option.name)] == floor_
                            or _only_here(
                                record,
                                first[(verb.name, option.name)],
                                verb.name,
                                option,
                            )
                            else first[(verb.name, option.name)],
                            until=newer.get(last[(verb.name, option.name)], "")
                            if last[(verb.name, option.name)] != chain[0]
                            and _corroborated(
                                record,
                                last[(verb.name, option.name)],
                                verb.name,
                                option,
                            )
                            else "",
                        )
                    )
                    for option in verb.options
                ),
            )
            for verb in spec.verbs
        ),
    )


def _verdicts(
    record: Record,
    chain: list[str],
    surfaces: dict[str, dict[str, Any] | None],
) -> dict[str, tuple[str, ...]]:
    """Which platforms currently lack each option: derived, never stored.

    The record writes only what a platform saw at the version it looked
    at. The standing claim ("Windows does not have `--fork`") is this: for
    each platform, the newest version it observed that has anything to say
    about the option. Present there, the claim is dropped; missing there,
    it stands; never observed, it was never made.

    Derived for the same reason `since` and `until` are: a claim written
    into a younger version hardens into a fact nobody rechecks, and one
    later sighting on that platform would have to chase it back down the
    record. Walked newest-first, so the first verdict found wins.
    """
    settled: dict[str, dict[str, bool]] = {}
    for version in chain:  # newest first
        missing = absent_of(record, version)
        surface = surfaces.get(version) or {}
        here = set(_flat(surface))
        for verb_name, verb in surface.get("verbs", {}).items():
            here.add(f"{verb_name}\t")
            for option_name in verb.get("options", {}):
                here.add(f"{verb_name}\t{option_name}")
        for platform in platforms_of(record, version):
            for key in here:
                lacked = platform in missing.get(key, ())
                settled.setdefault(key, {}).setdefault(platform, lacked)
    return {
        key: tuple(sorted(p for p, lacked in who.items() if lacked))
        for key, who in settled.items()
        if any(who.values())
    }


def _holders(record: Record, version: str, verb: str, option: Option) -> list[str]:
    """The platforms that observed *version* and found this option."""
    missing = absent_of(record, version).get(f"{verb}\t{option.name}", ())
    return [p for p in platforms_of(record, version) if p not in missing]


def _only_here(record: Record, first: str, verb: str, option: Option) -> bool:
    """Whether a `since` at *first* would out-run the evidence.

    An option first seen where only one platform's floor reaches is not
    "added" there: the older versions were never read on that platform,
    so nobody could have seen it earlier. The same honesty as the record's
    own floor rule, one level down: at or before this platform's floor is
    not a since.
    """
    chain = versions(record)
    below = chain[chain.index(first) + 1 :]
    holders = set(_holders(record, first, verb, option))
    for older in below:
        was_read_by = platforms_of(record, older)
        if not was_read_by or holders.intersection(was_read_by):
            # Either a holder did read further back, or nobody recorded who
            # read it, and unknown coverage is not evidence of absence.
            return False
    return bool(holders)


def _corroborated(record: Record, last: str, verb: str, option: Option) -> bool:
    """Whether "gone since" is a claim the observations support.

    A platform that never held the option cannot witness its removal, and
    a version read only by such a platform is silence rather than
    evidence.
    """
    chain = versions(record)
    spot = chain.index(last)
    if spot == 0:
        return False
    holders = set(_holders(record, last, verb, option))
    witnesses = platforms_of(record, chain[spot - 1])
    if not holders or not witnesses:
        # No platform evidence either way: a single-platform record, or a
        # version whose readers were not recorded. The guard exists to stop
        # one platform speaking for another, and there is no other
        # platform here to speak for.
        return True
    return bool(holders.intersection(witnesses))
