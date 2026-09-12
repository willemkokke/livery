"""What each curated tool accepted, release by release.

A stub is a rendering, not a record. The record is one JSON file per tool
under `_history/`: the **newest** observed release stored whole, and every
older one a delta describing how to step back to it.

Pointing the deltas backwards is what makes the format fit the work:

* priming backwards is pure append — an older release adds one delta against
  the current oldest, and nothing already written is touched;
* the current version costs no replay, because it *is* the base;
* midfill rewrites exactly one entry, the inserted release's successor;
* "did anything change in this release" is "is its delta non-empty", which
  is the question a release job actually asks.

`since` / `until` are never stored. They are derived by walking the chain,
so a half-primed file can never assert history it has not looked at —
`observed_from` says how far back the chain reaches, and that is a fact
about what was read rather than a policy about what we meant to read.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from livery.toolroom.tools._machinery._toolspec import Option, ToolSpec, Verb

SCHEMA = 1
"""Bumped when the on-disk shape changes in a way a reader must know about."""

EXTRACTOR = 4
"""The extractor generation that produced an observation.

Recorded per release so improving `_toolhelp`/`_toolspec` — or a tool
flipping between the click and `--help` paths — rewrites state without
counting as the tool having changed. Bump it when extraction starts
producing different words for the same tool, and a gather will offer those
releases again: a reading is only as good as the extractor that took it.

**2** — reading each release in the era it shipped in. Under today's
dependencies twine 5.1.0 indexes `metadata["home-page"]`, which
importlib_metadata 8 raises on, so it died before argparse ran and three
releases were recorded with no options at all. Pinning the resolution and
the interpreter to the release's own date changes what extraction can see,
which is exactly what this number is for.

**3** — reading a tool's description rather than the furniture around it.
A manual names itself after an en dash on mdoc pages, which the NAME
pattern did not match, so every OpenSSH page fell back to its running
header: `SSH(1)  General Commands Manual  SSH(1)`, justified to whatever
width the reader rendered at. A tool that signs its help instead of
describing itself — `git-cliff 2.13.1`, markdownlint-cli2's banner and
URL — had the signature stored as its description, and bun's trailing
build stamp went in with the sentence it followed. Each of those made a
version bump read back as the tool rewording itself, and none of them was
ever what the tool said it did.

**4** — reading the builder docker actually installs. `docker build` is
buildx wherever buildx is installed, and the driver installs it, but with
`DOCKER_BUILDKIT` unset the CLI only routes `build` that way for a Linux
daemon and a reader has no daemon. Linux and macOS read buildx while
Windows read the builder docker shipped with before 2019, so one verb was
stored as two surfaces and twelve options were recorded as a per-platform
difference that no user has. The opt-in is pinned for the read now.
"""


# --- a surface: one release's option tree, as data ---------------------------
#
# Deliberately not the whole ToolSpec. `version` belongs to the release that
# keyed it, and `in_process` is a fact about the machine that looked (does the
# tool publish a console-script entry point), not about the release — both are
# supplied at render time instead.
#
# The *platforms* are neither: they are a fact about the observation, like
# its date, and ride beside the surface. A list rather than one name, because
# a release read on three platforms is **one** observation of a merged
# surface — storing it three times would triple a store whose options are
# nearly all universal, to carry the rare one that is not. The list says who
# looked; a per-option `not_on` will later say who disagreed, which is the
# efficient way round. Until a refresh runs a matrix there is one name in it,
# and that is the honest record: only this OS ever looked.


def _option_fields(option: Option, **replaced: Any) -> dict[str, Any]:
    """An Option's fields as one merged dict — its own values with *replaced*
    laid over them, annotated so the `Option(**…)` splat type-checks under
    every checker (an inline heterogeneous dict distributes its value union
    over each field otherwise).
    """
    return {**option.__dict__, **replaced}


class LossyReading(Exception):
    """A reading that lost bytes on the way in, refused rather than stored.

    U+FFFD is the decoder saying it could not represent something it read.
    Whatever the tool printed there, this is not it — and because help text
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
        f"carries U+FFFD — bytes the decoder could not read. Refusing to "
        f"record it: a mangled character is indistinguishable from a real "
        f"change once it is in the history."
    )


def surface_of(spec: ToolSpec) -> dict[str, Any]:
    """A ToolSpec reduced to what a release *is*, losing nothing else.

    Refuses a reading that lost bytes — see `LossyReading`. This is the one
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
    """The inverse of `surface_of` — what the stub renderer consumes."""
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


# --- the chain ---------------------------------------------------------------


def new(
    tool: str,
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """A history of one release. A short history is a valid history — which is
    what lets the store ship before anything has been primed.
    """
    return {
        "schema": SCHEMA,
        "tool": tool,
        "observed_from": version,
        "base": {
            "version": version,
            "date": date,
            "platforms": sorted(platforms or []),
            "extractor": EXTRACTOR,
            "surface": surface,
        },
        "deltas": {},
    }


def delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """How to step back from *newer* to *older*, per option.

    Three moves, and an empty delta means the release was observed and
    changed nothing — which is not the same as a release nobody looked at.
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


def apply(surface: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    """*surface* stepped back by one delta — the inverse of `delta`."""
    out = json.loads(json.dumps(surface))  # a deep copy; surfaces are plain data
    for key in step.get("drop", ()):
        verb, _, option = key.partition("\t")
        out["verbs"][verb]["options"].pop(option, None)
    for key, option in {**step.get("add", {}), **step.get("revert", {})}.items():
        verb, _, name = key.partition("\t")
        out["verbs"].setdefault(verb, _empty_verb())["options"][name] = option
    for name, changed in step.get("verbs", {}).items():
        if changed is None:
            out["verbs"].pop(name, None)
        else:
            verb = out["verbs"].setdefault(name, _empty_verb())
            verb.update({k: v for k, v in changed.items() if k != "options"})
            verb.setdefault("options", {})
    if "help" in step:
        out["help"] = step["help"]
    return {"help": out.get("help", ""), "verbs": _ordered(out["verbs"])}


def extend(
    doc: dict[str, Any],
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> bool:
    """Append an *older* release to the end of the chain.

    This is what priming does, and why the deltas point backwards: the new
    entry is a delta from the current oldest release to this one, and nothing
    already written moves. Returns whether anything was added — a release the
    chain already holds is skipped, which is what makes a prime resumable
    against a rate limit.
    """
    if version in observed(doc):
        return False
    oldest = doc["observed_from"]
    previous = at(doc, oldest)
    if previous is None:  # pragma: no cover — observed_from is always in the chain
        raise ValueError(f"{oldest} is not in the chain")
    doc["deltas"][version] = {
        "date": date,
        "platforms": sorted(platforms or []),
        "extractor": EXTRACTOR,
        **delta(previous, surface),
    }
    doc["observed_from"] = version
    return True


def promote(
    doc: dict[str, Any],
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> bool:
    """Make *version* the new base, demoting the old one to a delta.

    The forward counterpart of `extend`, and the other half of why the deltas
    point backwards: a newer release touches exactly two entries, the new
    base and the one it displaces, whatever the chain's length.

    Returns **whether anything changed** — which is the release gate's whole
    question. An empty delta means this release was observed and altered
    nothing, so there is a new version to record and nothing to release for.
    """
    previous = doc["base"]
    step = delta(surface, previous["surface"])
    doc["deltas"] = {
        previous["version"]: {
            "date": previous["date"],
            "platforms": previous.get("platforms", []),
            "extractor": previous["extractor"],
            **step,
        },
        **doc["deltas"],
    }
    doc["base"] = {
        "version": version,
        "date": date,
        "platforms": sorted(platforms or []),
        "extractor": EXTRACTOR,
        "surface": surface,
    }
    return bool(step)


PLATFORM_PRIORITY = ("Linux", "macOS", "Windows")
"""Whose words to keep when two platforms describe one option differently.

A tie-break, and nothing more. The store keeps one copy of an option's text,
so without a fixed, order-independent pick, alternating matrix legs would
flip a divergent help string every week — and every flip is a `revert` in a
store whose whole question is "did anything change". Fixed and explicit
because `sorted()` is not it: ASCII puts `macOS` after `Windows`.
"""


def entry_of(doc: dict[str, Any], version: str) -> dict[str, Any] | None:
    """The stored record for *version* — the base, or one of the deltas.

    The record, not the surface: `at()` replays a surface, while this is the
    entry itself, carrying who observed the release and what they missed.
    """
    base: dict[str, Any] = doc["base"]
    if version == base["version"]:
        return base
    deltas: dict[str, dict[str, Any]] = doc["deltas"]
    return deltas.get(version)


def absent_at(doc: dict[str, Any], version: str) -> dict[str, list[str]]:
    """Who looked at *version* and did not find each option.

    A **sidecar** beside the surface, keyed `verb\toption` the way deltas
    key their own moves (a bare `verb\t` marks a whole subcommand missing).
    Kept out of the surface deliberately: an absence is a fact about an
    *observation*, not about the release, so it never enters a delta, never
    lands in a `revert` payload, and can never make platform coverage look
    like the tool changing.

    Only ever what was *seen* to be missing — the invariant is
    `absent[key] ⊆ entry["platforms"]`. Nothing here is inferred; a claim
    about a platform that did not look is derived at render time, where it
    can be revised, rather than written down where it would harden.
    """
    entry = entry_of(doc, version) or {}
    absent: dict[str, list[str]] = entry.get("absent", {})
    return absent


def _set_absent(entry: dict[str, Any], missing: dict[str, list[str]]) -> None:
    """Record the sidecar, or drop it when there is nothing to say."""
    tidy = {key: sorted(set(who)) for key, who in sorted(missing.items()) if who}
    if tidy:
        entry["absent"] = tidy
    else:
        entry.pop("absent", None)


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

    An option any platform saw is in the surface — the union, so nothing
    observed is lost — and the platforms that looked without finding it are
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


def merge(
    doc: dict[str, Any],
    *,
    version: str,
    surface: dict[str, Any],
    platforms: list[str],
    absent: dict[str, list[str]] | None = None,
) -> bool:
    """Fold another platform's reading into a release the chain already has.

    The third way a release reaches the store, beside `insert` and `extend`,
    and the one the matrix needs: a version observed on macOS in May and on
    Windows in July is **one** release with two witnesses, not two records.

    Merging never removes an option — an absence is recorded in the sidecar,
    where it says who failed to find it rather than that it stopped
    existing. So the surface only grows, and the delta either side of this
    release moves only when the *tool* differs, never when coverage does.

    Returns whether the stored **surface** changed, which is the caller's
    signal that the two entries referencing it need recomputing. A merge
    that only widened coverage returns False and costs the chain nothing.
    """
    entry = entry_of(doc, version)
    if entry is None:
        return False
    # What this release already says, replayed — NOT `entry["surface"]`,
    # which only the base carries. A delta entry stores the step down to its
    # release, so reading it as a surface makes every option the incoming
    # platform saw look like one nobody had ever found: a first Linux fold
    # tagged 25,802 options as missing on macOS, for a store macOS itself
    # had written.
    stored = at(doc, version)
    if stored is None:  # pragma: no cover - entry_of found it, so replay must
        return False
    observers = sorted({*entry.get("platforms", []), *platforms})
    before = json.dumps(stored, sort_keys=True)

    known = _flat(stored)
    incoming = _flat(surface)
    missing = {key: list(who) for key, who in entry.get("absent", {}).items()}
    merged = json.loads(json.dumps(stored))

    for verb_name, reading in surface.get("verbs", {}).items():
        # A verb's own fields — its summary, its positional shape — settle
        # by the same rule its options do. Left out, they were frozen at
        # whatever the first reading said: git's root verb kept a fragment
        # of a usage line as its description, and no better extractor
        # could ever replace it, which is the one thing a record of
        # observations must never need a hand to fix.
        held = merged.setdefault("verbs", {}).setdefault(verb_name, _empty_verb())
        fields = {k: v for k, v in reading.items() if k != "options"}
        chosen = _preferred(
            {k: v for k, v in held.items() if k != "options"}
            if verb_name in (stored.get("verbs", {}))
            else None,
            fields,
            entry.get("platforms", []),
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
            missing[key] = [p for p in entry.get("platforms", []) if p not in platforms]
        verb["options"][option_name] = _preferred(
            known.get(key), option, entry.get("platforms", []), platforms, missing, key
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
    # already use, rather than "whichever is non-empty". Falling back on
    # truthiness made *no description* unsayable: once a tool had one
    # stored, a reading that correctly found none read as a platform that
    # had failed to look, and the stale text was kept forever. That is how
    # markdownlint-cli2 held its own version banner as a description
    # through every re-read — and it is precisely what a better extractor
    # must be able to correct. Ranked, an empty reading from the platform
    # that owns the text replaces it, while a lower-ranked platform's
    # silence still cannot erase a good line.
    widened = {
        "help": _preferred(
            {"help": stored.get("help", "")} if "help" in stored else None,
            {"help": surface.get("help", "")},
            entry.get("platforms", []),
            platforms,
            missing,
            _TOOL_HELP,
        )["help"],
        "verbs": _ordered(merged.get("verbs", {})),
    }
    entry["platforms"] = observers
    _set_absent(
        entry,
        {key: [p for p in who if p in observers] for key, who in missing.items()},
    )
    if json.dumps(widened, sort_keys=True) == before:
        return False  # coverage widened, the tool did not: the chain is untouched
    _rewrite(doc, version, widened)
    return True


def _rewrite(doc: dict[str, Any], version: str, surface: dict[str, Any]) -> None:
    """Record a new surface for a release the chain already holds.

    The base *is* its surface, so it is written. Anything else is described
    by two steps — the one keyed at the release (down *to* it) and the one
    below (down *from* it) — and both are recomputed here, against surfaces
    read before either is touched. Everything else in the chain stays
    byte-identical, which is the same locality a midfill has.
    """
    chain = observed(doc)
    spot = chain.index(version)
    above = at(doc, chain[spot - 1]) if spot else None
    below = at(doc, chain[spot + 1]) if spot + 1 < len(chain) else None

    if version == doc["base"]["version"]:
        doc["base"]["surface"] = surface
    else:
        entry = doc["deltas"][version]
        kept = {
            k: entry[k]
            for k in ("date", "platforms", "extractor", "absent")
            if k in entry
        }
        doc["deltas"][version] = {**kept, **delta(above or surface, surface)}
    if below is not None:
        older = chain[spot + 1]
        entry = doc["deltas"][older]
        kept = {
            k: entry[k]
            for k in ("date", "platforms", "extractor", "absent")
            if k in entry
        }
        doc["deltas"][older] = {**kept, **delta(surface, below)}


# The `missing` key for a tool's own description. Two tabs, which no real
# key can be: a verb's fields are keyed `<verb>\t` and an option `<verb>\t<name>`,
# and neither a verb nor an option name may contain the separator. Only ever
# read — nothing records a platform as lacking a description — so it never
# reaches the stored sidecar.
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
    and a platform re-reading its own contribution always wins — so a week
    of legs arriving in any order settles on the same answer.
    """
    if stored is None:
        return incoming

    def rank(platforms: list[str]) -> int:
        holders = [p for p in platforms if p not in missing.get(key, [])]
        ranks = [PLATFORM_PRIORITY.index(p) for p in holders if p in PLATFORM_PRIORITY]
        return min(ranks) if ranks else len(PLATFORM_PRIORITY)

    return incoming if rank(now) <= rank(was) else stored


def insert(
    doc: dict[str, Any],
    *,
    version: str,
    date: str,
    surface: dict[str, Any],
    platforms: list[str] | None = None,
) -> bool:
    """Place a release at its own position in the chain, wherever that is.

    `extend` appends below the floor and `promote` replaces the head; this is
    the third case the format was designed for and the one neither covers — a
    release that belongs *between* two the chain already holds. Exactly one
    entry is recomputed, the inserted release's successor, because that is the
    only delta whose starting point moved. Nothing else is touched, however
    long the chain.

    What it buys is that **gathering need not be ordered**. Installing a
    release and reading its `--help` does not depend on any other release
    having been read; only the arithmetic afterwards does, and that is a dict
    diff over surfaces already in hand. So a walk can fetch in whatever order
    it likes — in parallel, or across several runs — and assemble as results
    arrive. It also means a release that would not install stops being fatal
    to a tool's whole walk: the gap is filled by a later run.

    A gap costs precision until it is filled, not correctness. An option that
    arrived in the missing release reads as arriving at the next release that
    *was* read, which is the same honest imprecision the chain already carries
    wherever an index has no build to offer.

    Returns whether the release was added; a release the chain already holds
    is left exactly as it is.
    """
    from livery.toolroom.tools import version_tuple
    from livery.toolroom.tools._machinery._toolfetch import _patchlevel

    if version in observed(doc):
        return False
    chain = observed(doc)  # newest first

    # The patchlevel rides between the tuple and the date because
    # `version_tuple` deliberately reads OpenSSH's `9.9p1` and `9.9p2` as
    # the same base and leaves the tie to the caller. The date can break it
    # now that the listing's own dates are read, but two patchlevels of one
    # release often share a day, so the middle component still earns its
    # place: without it they compare *equal*, the strict scan below finds no
    # strictly-older entry, and the walk dies on the `next()`.
    def placed(name: str) -> tuple[tuple[int, ...], int, str]:
        entry = doc["base"] if name == doc["base"]["version"] else doc["deltas"][name]
        return version_tuple(name), _patchlevel(name), entry.get("date", "")

    mine = (version_tuple(version), _patchlevel(version), date)
    if mine > placed(chain[0]):
        promote(doc, version=version, date=date, surface=surface, platforms=platforms)
        return True
    if mine < placed(chain[-1]):
        return extend(
            doc, version=version, date=date, surface=surface, platforms=platforms
        )

    older = next((name for name in chain if placed(name) < mine), None)
    if older is None:
        # Every comparator component ties an existing entry (same base, same
        # patchlevel, no dates to separate them): there is no honest place
        # in the chain, and a named refusal beats a StopIteration escaping
        # the walk.
        raise ValueError(f"{version} ties an entry already in the chain")
    newer = chain[chain.index(older) - 1]
    before, after = at(doc, newer), at(doc, older)
    if before is None or after is None:  # pragma: no cover — both are in the chain
        raise ValueError(f"{newer} or {older} is not in the chain")

    rebuilt: dict[str, Any] = {}
    for name, entry in doc["deltas"].items():
        if name == older:
            rebuilt[version] = {
                "date": date,
                "platforms": sorted(platforms or []),
                "extractor": EXTRACTOR,
                **delta(before, surface),
            }
            # The one recomputed entry: `older` used to step back from
            # `newer`, and now steps back from the release between them.
            rebuilt[name] = {
                "date": entry.get("date", ""),
                "platforms": entry.get("platforms", []),
                "extractor": entry.get("extractor", EXTRACTOR),
                **delta(surface, after),
            }
        else:
            rebuilt[name] = entry
    doc["deltas"] = rebuilt
    return True


def at(doc: dict[str, Any], version: str) -> dict[str, Any] | None:
    """The surface of *version*, replayed from the base. `None` when that
    release was never observed — which a caller must not read as "empty".
    """
    base = doc["base"]
    surface: dict[str, Any] = base["surface"]
    if version == base["version"]:
        return surface
    for older, step in doc["deltas"].items():
        surface = apply(surface, step)
        if older == version:
            return surface
    return None


def union(doc: dict[str, Any], *, name: str, in_process: bool = False) -> ToolSpec:
    """Every option the tool has *ever* had, each with its interval.

    The stub renders this rather than the newest release alone: a removed
    flag stays completable, because the reader may be running a version that
    still has it, and its docstring says when it went. An option's properties
    come from the newest release that had it — the most recent word the tool
    said about itself.

    `since` is left empty for anything already present at the oldest release
    read. The history reaches only as far as it was primed, and "at or before
    the floor" is not a `since`.
    """
    chain = observed(doc)  # newest first
    floor = chain[-1]
    surfaces = {version: at(doc, version) for version in chain}
    verdicts = _verdicts(doc, chain, surfaces)

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
            # Sorted, so the stub does not reorder itself as the history
            # deepens: merged oldest-first, insertion order would otherwise
            # mean "which release mentioned it first".
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
                            if first[(verb.name, option.name)] == floor
                            or _only_here(
                                doc, first[(verb.name, option.name)], verb.name, option
                            )
                            else first[(verb.name, option.name)],
                            until=newer.get(last[(verb.name, option.name)], "")
                            if last[(verb.name, option.name)] != chain[0]
                            and _corroborated(
                                doc, last[(verb.name, option.name)], verb.name, option
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
    doc: dict[str, Any],
    chain: list[str],
    surfaces: dict[str, dict[str, Any] | None],
) -> dict[str, tuple[str, ...]]:
    """Which platforms currently lack each option — derived, never stored.

    The store writes only what a platform saw at the release it looked at.
    The standing claim ("Windows does not have `--fork`") is this: for each
    platform, the newest release it observed that has anything to say about
    the option. Present there, the claim is dropped; missing there, it
    stands; never observed, it was never made.

    Derived for the same reason `since` and `until` are: a claim written
    into a younger release hardens into a fact nobody rechecks, and one
    later sighting on that platform would have to chase it back down the
    chain. Walked newest-first, so the first verdict found wins.
    """
    settled: dict[str, dict[str, bool]] = {}
    for version in chain:  # newest first
        entry = entry_of(doc, version) or {}
        missing = entry.get("absent", {})
        surface = surfaces.get(version) or {}
        here = set(_flat(surface))
        for verb_name, verb in surface.get("verbs", {}).items():
            here.add(f"{verb_name}\t")
            for option_name in verb.get("options", {}):
                here.add(f"{verb_name}\t{option_name}")
        for platform in entry.get("platforms", []):
            for key in here:
                lacked = platform in missing.get(key, ())
                settled.setdefault(key, {}).setdefault(platform, lacked)
    return {
        key: tuple(sorted(p for p, lacked in who.items() if lacked))
        for key, who in settled.items()
        if any(who.values())
    }


def _holders(doc: dict[str, Any], version: str, verb: str, option: Option) -> list[str]:
    """The platforms that observed *version* and found this option."""
    entry = entry_of(doc, version) or {}
    missing = entry.get("absent", {}).get(f"{verb}\t{option.name}", ())
    return [p for p in entry.get("platforms", []) if p not in missing]


def _only_here(doc: dict[str, Any], first: str, verb: str, option: Option) -> bool:
    """Whether a `since` at *first* would out-run the evidence.

    An option first seen where only one platform's floor reaches is not
    "added" there — the older releases were never read on that platform, so
    nobody could have seen it earlier. The same honesty as the chain's own
    floor rule, one level down: at or before *this platform's* floor is not
    a since.
    """
    chain = observed(doc)
    below = chain[chain.index(first) + 1 :]
    holders = set(_holders(doc, first, verb, option))
    for older in below:
        entry = entry_of(doc, older) or {}
        was_read_by = entry.get("platforms", [])
        if not was_read_by or holders.intersection(was_read_by):
            # Either a holder did read further back, or nobody recorded who
            # read it — and unknown coverage is not evidence of absence.
            return False
    return bool(holders)


def _corroborated(doc: dict[str, Any], last: str, verb: str, option: Option) -> bool:
    """Whether "gone since" is a claim the observations support.

    A platform that never held the option cannot witness its removal, and a
    release read only by such a platform is silence rather than evidence.
    """
    chain = observed(doc)
    spot = chain.index(last)
    if spot == 0:
        return False
    holders = set(_holders(doc, last, verb, option))
    dropped_at = entry_of(doc, chain[spot - 1]) or {}
    witnesses = dropped_at.get("platforms", [])
    if not holders or not witnesses:
        # No platform evidence either way — a single-platform history, or a
        # chain built before anyone recorded who looked. The guard exists to
        # stop one platform speaking for another, and there is no other
        # platform here to speak for.
        return True
    return bool(holders.intersection(witnesses))


def changes(doc: dict[str, Any], *, since: str, until: str = "") -> dict[str, Any]:
    """What changed between two observed releases, as one step.

    The *net* effect, not a concatenation of the steps between: an option a
    tool added and then withdrew across the span cancels out, which is what
    someone reading a release note wants to know. Computed by replaying both
    ends and taking one delta, so it cannot disagree with the chain.

    Returned in the same shape as `delta` and read the same way round —
    `drop` is what the newer release *added*, `add` is what it removed —
    because it is a step back from *until* to *since*.
    """
    newer = at(doc, until or doc["base"]["version"])
    older = at(doc, since)
    if newer is None or older is None:
        return {}
    return delta(newer, older)


def spellings(doc: dict[str, Any], version: str, keys: Iterable[str]) -> dict[str, str]:
    """How *version* spells each option key on the command line.

    A delta records the option's Python-side name, which is what the surface
    is keyed by; a reader of a release note recognises `--all-files`. The
    flags live in the surface, so the spelling is a lookup rather than
    something the delta has to carry.
    """
    surface = at(doc, version) or {}
    found: dict[str, str] = {}
    for key in keys:
        verb, _, option = key.partition("\t")
        entry = surface.get("verbs", {}).get(verb, {}).get("options", {}).get(option)
        flags = (entry or {}).get("flags") or []
        # The long spelling when there is one: `--all-files` over `-a`.
        found[key] = max(flags, key=len) if flags else option
    return found


def observed(doc: dict[str, Any]) -> list[str]:
    """Every release in the chain, newest first."""
    return [doc["base"]["version"], *doc["deltas"]]


def load(path: Path) -> dict[str, Any] | None:
    """A tool's history, or `None` when it has none yet."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(doc: dict[str, Any], path: Path) -> None:
    """Write *doc* atomically, formatted for a diff a human reads.

    The temp name carries the thread id beside the pid: assembly is
    single-threaded by design, but a rule enforced by a filename is cheaper
    than one enforced by remembering, and two threads that ever do write one
    tool's file will each replace whole documents instead of corrupting a
    shared temp.
    """
    import os
    import threading

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)


# --- helpers -----------------------------------------------------------------


def _flat(surface: dict[str, Any]) -> dict[str, Any]:
    """Options keyed `verb\\toption`, so a diff is one flat set operation.

    A tab, because a verb is dotted (`compose.up`) and an option name can
    carry anything a tool's `--help` prints — but neither can hold a tab.
    """
    return {
        f"{verb_name}\t{option_name}": option
        for verb_name, verb in surface.get("verbs", {}).items()
        for option_name, option in verb.get("options", {}).items()
    }


def _verb_delta(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """Verb-level changes: a verb gained or lost, or its own metadata moved.

    Options ride the flat diff; this carries what hangs off the verb itself —
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
