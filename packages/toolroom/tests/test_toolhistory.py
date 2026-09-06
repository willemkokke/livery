"""The option history: a base at HEAD, deltas pointing backwards.

Three properties carry the whole format, and each has a way of failing
silently — a lossy round-trip degrades stubs without erroring, a wrong delta
replays into a surface nobody notices is wrong, and an empty delta read as
"never observed" would make a release job redo work it already did.
"""

from __future__ import annotations

import collections
import contextlib
import itertools
import json
import pathlib
from typing import Any

import pytest

from livery.toolroom._machinery import _toolhistory
from livery.toolroom._machinery._toolspec import Option, ToolSpec, Verb


def test_a_reading_that_lost_bytes_is_refused(tmp_path):
    """U+FFFD in a surface is the decoder admitting it could not read
    something. Storing it manufactures an event, because help text is state
    and nothing downstream can tell a mangled byte from a real edit — djLint
    printed its banner separator as one cp1252 byte on Windows, UTF-8 turned
    it into a replacement character, and the store credited 1.43.2 with a
    description change that never happened.
    """
    lossy = _spec(help="djLint \ufffd HTML template linter and formatter.")
    with pytest.raises(_toolhistory.LossyReading, match=r"U\+FFFD"):
        _toolhistory.surface_of(lossy)

    # Named where it is, so the report says which reading to distrust.
    verbs = _spec().verbs
    deep = _spec(
        verbs=(verbs[0], Verb(name="build", help="Build it \ufffd fast.")),
    )
    with pytest.raises(_toolhistory.LossyReading, match="build"):
        _toolhistory.surface_of(deep)

    # A clean reading is untouched — the guard is a tripwire, not a filter.
    assert _toolhistory.surface_of(_spec())["help"] == "A demo tool."


def _spec(**over) -> ToolSpec:
    base = ToolSpec(
        name="demo",
        help="A demo tool.",
        version="1.0.0",
        verbs=(
            Verb(
                name="",
                help="The tool itself.",
                options=(Option("quiet", ("-q", "--quiet"), type_name="bool"),),
            ),
            Verb(
                name="build",
                help="Build it.",
                wraps=False,
                positional="required",
                lead="target",
                options=(
                    Option(
                        "output",
                        ("-o", "--output"),
                        help="Where to write.",
                        type_name="str",
                        default="dist",
                    ),
                    Option(
                        "clean",
                        ("--clean",),
                        negation="--dirty",
                        help="Clean first.",
                        type_name="bool",
                        default=True,
                    ),
                    Option(
                        "mode",
                        ("--mode",),
                        type_name="choice",
                        choices=("fast", "safe"),
                    ),
                ),
            ),
        ),
    )
    return ToolSpec(**{**base.__dict__, **over})


def test_a_surface_round_trips_without_losing_a_field():
    """Every field the stub renderer reads must survive the store, or a
    regenerated stub quietly loses a negation, a default or a Literal.
    """
    spec = _spec()
    back = _toolhistory.spec_from(
        _toolhistory.surface_of(spec),
        name=spec.name,
        version=spec.version,
        in_process=spec.in_process,
    )
    assert back == spec


def test_the_surface_leaves_out_what_is_not_the_release():
    """`version` keys the release and `in_process` is a fact about the machine
    that looked — neither describes what the tool accepts.
    """
    surface = _toolhistory.surface_of(_spec(version="9.9.9", in_process=True))
    blob = json.dumps(surface)
    assert "9.9.9" not in blob
    assert "in_process" not in blob


def test_a_delta_steps_back_exactly():
    """The chain's whole claim: replaying a delta reproduces the older
    surface, option for option.
    """
    new = _toolhistory.surface_of(_spec())
    older = _toolhistory.surface_of(
        _spec(
            help="An older demo tool.",
            verbs=(
                Verb(name="", help="The tool itself.", options=()),
                Verb(
                    name="build",
                    help="Build it, once.",
                    positional="any",
                    options=(
                        Option("output", ("-o",), help="Older help.", type_name="str"),
                    ),
                ),
            ),
        )
    )
    step = _toolhistory.delta(new, older)
    assert _toolhistory.apply(new, step) == older
    # ...and it says what moved, rather than restating the whole surface.
    assert "\tquiet" in " ".join(step["drop"])
    assert "help" in step and step["help"] == "An older demo tool."


def test_a_verb_that_arrived_is_dropped_when_stepping_back():
    new = _toolhistory.surface_of(_spec())
    older = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="", help="The tool itself.", options=()),))
    )
    step = _toolhistory.delta(new, older)
    assert _toolhistory.apply(new, step) == older
    assert step["verbs"]["build"] is None  # arrived in the newer release


def test_an_unchanged_release_records_an_empty_delta():
    """Observed and changed nothing is not the same as never looked at — the
    first is an empty delta, the second is simply absent. A release job reads
    the difference to decide whether to work.
    """
    surface = _toolhistory.surface_of(_spec())
    assert _toolhistory.delta(surface, surface) == {}

    doc = _toolhistory.new("demo", version="1.0.0", date="2026-01-02", surface=surface)
    doc["deltas"]["0.9.0"] = {"date": "2026-01-01", "extractor": 1}
    assert _toolhistory.at(doc, "0.9.0") == surface  # replays to the same thing
    assert _toolhistory.at(doc, "0.5.0") is None  # never observed
    assert _toolhistory.observed(doc) == ["1.0.0", "0.9.0"]


def test_replay_reaches_every_release_in_a_chain():
    """Built the way priming builds it — newest first, each older release
    appended — and every point still reconstructs.
    """
    surfaces = {
        f"1.{n}.0": _toolhistory.surface_of(
            _spec(
                verbs=(
                    Verb(
                        name="build",
                        help=f"Build at {n}.",
                        options=tuple(
                            Option(f"opt{i}", (f"--opt{i}",), help=f"Option {i}.")
                            for i in range(n + 1)
                        ),
                    ),
                )
            )
        )
        for n in range(5)
    }
    order = sorted(surfaces, reverse=True)  # newest first, as the prime walks
    doc = _toolhistory.new(
        "demo", version=order[0], date="2026-01-05", surface=surfaces[order[0]]
    )
    for newer, older in itertools.pairwise(order):
        doc["deltas"][older] = {
            "date": "2026-01-01",
            "extractor": _toolhistory.EXTRACTOR,
            **_toolhistory.delta(surfaces[newer], surfaces[older]),
        }
        doc["observed_from"] = older

    for version, expected in surfaces.items():
        assert _toolhistory.at(doc, version) == expected, version


def test_load_of_a_missing_or_broken_file_is_none(tmp_path):
    assert _toolhistory.load(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert _toolhistory.load(broken) is None


def test_save_writes_atomically_and_leaves_no_temp(tmp_path):
    doc = _toolhistory.new(
        "demo",
        version="1.0.0",
        date="2026-01-02",
        surface=_toolhistory.surface_of(_spec()),
    )
    path = tmp_path / "demo.json"
    _toolhistory.save(doc, path)
    assert _toolhistory.load(path) == doc
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("key", ["prek", "docker", "ruff"])
def test_the_checked_in_history_regenerates_its_stub(key):
    """The seeding claim, checked against what ships: rendering from the
    history reproduces the checked-in stub.

    Compared as parsed source, not as bytes. Two things in a stub file are
    nobody's business but the machine that wrote it — the header stamps which
    platform looked and whether it could import the tool, and the layout is
    the formatter's, whose isort splits an aliased import on some platforms
    and joins it on others. Neither is information the store holds. What the
    store owes is every verb, option, flag, negation, default and choice set,
    and an AST comparison is exactly that claim.

    (Byte-identity is checked where it means something: `fm tools.sync`
    rewrites a stub only when the text differs, and after this landed it
    rewrote none of the 26.)
    """
    import ast

    from livery.toolroom._machinery import _stubgen
    from livery.toolroom._machinery import _tasks as tools_tasks

    stub = tools_tasks._stub_path(key)
    doc = _toolhistory.load(tools_tasks._history_path(key))
    assert doc is not None, f"{key} has no history"

    recorded, mode = tools_tasks._header(stub)
    version, _, platform = recorded.partition(" ")
    base = doc["base"]
    assert base["version"] == version, (
        "the history and the stub disagree about which release was read"
    )

    rendered = _stubgen.render(
        # The union, as generation renders it: every option the tool has ever
        # had, so a flag it later dropped stays completable.
        _toolhistory.union(doc, name=key.replace("_", "-")),
        platform=platform.strip("()"),
        class_name=_stubgen._class_name(key),
        in_process=mode,
    )

    def classes(source: str) -> str:
        """The class tree alone — every verb, option, flag, negation, default
        and choice set. The import block is derived from the body and laid out
        by the formatter, which splits an aliased import on some platforms and
        joins it on others; that is layout, not content.
        """
        parsed = ast.parse(source)
        return ast.dump(
            ast.Module(
                body=[n for n in parsed.body if isinstance(n, ast.ClassDef)],
                type_ignores=[],
            )
        )

    assert classes(rendered) == classes(stub.read_text(encoding="utf-8"))


# --- priming: walking backwards, resumably ----------------------------------


def test_extend_appends_older_and_skips_what_it_has():
    """The prime's whole write pattern: append-only, and a release already in
    the chain is skipped — which is what makes an interrupted run resumable
    rather than duplicative.
    """
    surface = _toolhistory.surface_of(_spec())
    doc = _toolhistory.new("demo", version="1.2.0", date="2026-02-01", surface=surface)

    older = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="build", help="Older.", options=()),))
    )
    assert _toolhistory.extend(doc, version="1.1.0", date="2026-01-01", surface=older)
    assert doc["observed_from"] == "1.1.0"
    assert _toolhistory.at(doc, "1.1.0") == older
    assert _toolhistory.at(doc, "1.2.0") == surface  # the base did not move

    # A second pass over the same release adds nothing.
    assert not _toolhistory.extend(
        doc, version="1.1.0", date="2026-01-01", surface=older
    )
    assert _toolhistory.observed(doc) == ["1.2.0", "1.1.0"]


def test_releases_break_a_same_day_tie_by_version(monkeypatch):
    """Two releases on one day are common — prek shipped 0.4.7 and 0.4.8
    together. Resolved by dict order the walk skips one and a later prime
    appends it *below* its own successor, which corrupts the chain.
    """
    import io
    import json as _json

    from livery.toolroom._machinery import _drivers, _toolfetch

    index = {
        "releases": {
            "0.4.7": [{"upload_time": "2026-07-04T10:00:00"}],
            "0.4.8": [{"upload_time": "2026-07-04T18:00:00"}],
            "0.4.9": [{"upload_time": "2026-07-11T09:00:00"}],
            "0.4.6": [],  # no files: not installable, so not a release to read
        }
    }
    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(index).encode()),
    )
    driver = _drivers.find("prek")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == [
        "0.4.9",
        "0.4.8",
        "0.4.7",
    ]


def test_only_listable_tiers_are_primed():
    """A tool footman cannot enumerate is named and skipped, never treated as
    a tool with no history.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    uv_tier = _drivers.find("prek")
    manual = _drivers.find("bash")
    assert uv_tier and manual
    assert _toolfetch.can_list(uv_tier)
    assert not _toolfetch.can_list(manual)  # hand-written stub, nothing to read
    parked = _drivers.Driver("nope", provision=_drivers.Provision(kind="deferred"))
    assert not _toolfetch.can_list(parked)


def test_release_is_read_in_the_era_it_shipped_in():
    """Each release gets the newest CPython that existed when it shipped.

    Not one fixed interpreter: reading everything on the oldest supported
    Python would drop argparse aliases from releases that really do show
    them, because 3.13 taught argparse to print every alias. Reading a
    release in its own era records what it actually printed, and the
    surface corrects itself as the walk crosses October 2024.
    """
    from livery.toolroom._machinery._toolfetch import (
        PYTHON_RELEASES,
        READ_PYTHON,
        read_python,
    )

    assert read_python(date="2026-06-19") == "3.14"  # pytest 9.1.1, aliases shown
    assert read_python(date="2025-01-01") == "3.13"  # after 3.13, before 3.14
    assert read_python(date="2024-05-16") == "3.12"  # twine 5.1.0
    assert read_python(date="2022-12-01") == "3.11"  # twine 4.0.2
    assert read_python(date="2022-06-01") == "3.10"  # twine 4.0.1
    # A boundary belongs to the version that shipped that day, not before it.
    for version, since in PYTHON_RELEASES:
        assert read_python(date=since) == version
    # No date is no era: the newest, which is what an unstamped release meant.
    assert read_python() == PYTHON_RELEASES[0][0]
    # Older than every era — filtered by the horizon, but never below the floor.
    assert read_python(date="2001-01-01") == READ_PYTHON
    # A tool asking for more than its era offered is read on what it will run on.
    assert read_python(">=3.13", "2022-06-01") == "3.13"
    assert read_python(">=3.8", "2022-06-01") == "3.10"  # below the era: the era


def test_cutoff_takes_the_far_edge_of_the_publishing_window(monkeypatch):
    """Publishing is a window, and the cutoff belongs at the end of it.

    ninja 1.11.1 uploaded its seventeen files over 76 days; cmake 4.3.1 took
    three. A cutoff at the *first* upload filters out the release's own later
    files, and uv then reports "no version of cmake==4.3.1" while being asked
    for exactly that — or assembles a partial set whose `--help` segfaults.
    Both holes in a 709-release walk were this, on every platform at once,
    which is what makes it look like a platform difference.
    """
    import io
    import json as _json

    from livery.toolroom._machinery import _drivers, _toolfetch

    index = {
        "releases": {
            # ninja 1.11.1's real shape: first file 2022-11-05, last 2023-01-20
            "1.11.1": [
                {"upload_time": "2022-11-06T12:00:00", "requires_python": ""},
                {"upload_time": "2023-01-20T09:00:00", "requires_python": ""},
                {"upload_time": "2022-11-05T18:00:00", "requires_python": ""},
            ],
        }
    }
    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(index).encode()),
    )
    driver = _drivers.find("ninja")
    assert driver is not None
    (release,) = _toolfetch.releases(driver)
    # Both edges taken across the files, not off whichever the index listed
    # first — here that is neither the earliest nor the latest.
    assert release.date == "2022-11-05"  # when it started publishing
    assert release.published == "2023-01-20"  # when it finished


def test_release_date_cutoff_is_spelled_in_utc(monkeypatch, tmp_path):
    """The cutoff has to say UTC, or it excludes the release it is pinning.

    The index reports `upload_time` in UTC; a bare date reaches uv as local
    midnight. East of UTC that lands before the UTC day ends, so a release
    published in its last hour is filtered out by its own release date — uv
    0.11.32 went up at 23:05Z against a 23:00Z cutoff in BST, and resolved
    to "no version of uv==0.11.32". On a UTC CI runner it would have passed.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    calls: list[list[str]] = []

    def fake_run(argv, env=None):
        calls.append(argv)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    driver = _drivers.find("prek")
    assert driver is not None
    release = _toolfetch.Release(version="0.4.11", date="2026-07-23")
    _toolfetch._install_pypi(driver, release, tmp_path / "prek")
    install = next(c for c in calls if "pip" in c)
    assert "--exclude-newer" in install
    assert install[install.index("--exclude-newer") + 1] == "2026-07-23T23:59:59Z"


def test_releases_older_than_the_interpreter_are_not_offered(monkeypatch):
    """A release older than `READ_PYTHON` is out of scope, not a hole.

    Nothing was built for an interpreter before it existed, so such a
    release has no period wheels to resolve against and cannot be read on
    any interpreter this walk uses. A hole says "this could not be read",
    which would be a shrug recorded about a release nobody needs.
    """
    import io
    import json as _json

    from livery.toolroom._machinery import _drivers, _toolfetch

    index = {
        "releases": {
            # the day READ_PYTHON shipped, and one day either side of it
            "2.0.0": [{"upload_time": "2021-10-05T00:00:00", "requires_python": ""}],
            "1.9.0": [{"upload_time": "2021-10-04T00:00:00", "requires_python": ""}],
            "1.8.0": [{"upload_time": "2021-10-03T00:00:00", "requires_python": ""}],
        }
    }
    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(index).encode()),
    )
    driver = _drivers.find("prek")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["2.0.0", "1.9.0"]


def test_walk_caches_nothing_it_will_not_reread(tmp_path):
    """A walk must not leave behind what it unpacked from.

    `_discard` deletes each release once its surface is read, but uv had
    already unpacked that release into its cache, where nothing collects it
    until the run ends: a full gather put 5 GB into `archive-v0` while the
    interpreter store sat at 110 MB. Peak disk has to scale with how many
    installs run at once, not with how many the walk performs — the walk is
    parallel on purpose, so throttling it to save disk pays for the space
    with wall-clock instead. The cache is inside the scratch directory the
    walk deletes at the end, so nothing ever reads it twice.
    """
    import os

    from livery.toolroom._machinery._tasks import _sandboxed

    with _sandboxed(tmp_path):
        assert os.environ["UV_NO_CACHE"] == "1"
        assert os.environ["UV_CACHE_DIR"] == str(tmp_path / "cache")
        assert os.environ["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "pythons")
    assert "UV_NO_CACHE" not in os.environ  # restored, like the other two


def test_the_primed_history_ships_a_contiguous_chain():
    """What is checked in must replay end to end — a hole would mean a delta
    computed against a release that is not its neighbour.
    """
    from livery.toolroom._machinery import _tasks as tools_tasks

    doc = _toolhistory.load(tools_tasks._history_path("prek"))
    assert doc is not None
    chain = _toolhistory.observed(doc)
    assert len(chain) > 1, "prek's history was primed; it should carry deltas"
    for version in chain:
        assert _toolhistory.at(doc, version) is not None, version
    assert doc["observed_from"] == chain[-1]


def test_the_union_carries_intervals_the_history_can_prove():
    """What a stub may say about an option's life, and what it may not.

    An option already present at the oldest release read has no `since` — the
    chain never looked far enough back to claim one, and "at or before the
    floor" is not a `since`. An option the tool has dropped keeps its entry
    and gains an `until`, because a reader may be running a version that
    still has it.
    """
    old = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("doomed", ("--doomed",)),
                    ),
                ),
            )
        )
    )
    middle = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("doomed", ("--doomed",)),
                        Option("fresh", ("--fresh",)),
                    ),
                ),
            )
        )
    )
    newest = _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="build",
                    options=(
                        Option("ancient", ("--ancient",)),
                        Option("fresh", ("--fresh",)),
                    ),
                ),
            )
        )
    )
    doc = _toolhistory.new("demo", version="3.0.0", date="2026-03-01", surface=newest)
    _toolhistory.extend(doc, version="2.0.0", date="2026-02-01", surface=middle)
    _toolhistory.extend(doc, version="1.0.0", date="2026-01-01", surface=old)

    options = {
        o.name: o for v in _toolhistory.union(doc, name="demo").verbs for o in v.options
    }
    assert set(options) == {"ancient", "doomed", "fresh"}  # every option ever
    assert options["ancient"].since == ""  # there at the floor: nothing provable
    assert options["fresh"].since == "2.0.0"  # arrived, and the chain saw it
    assert options["doomed"].until == "3.0.0"  # the release it stopped appearing in
    assert options["doomed"].since == ""


def test_a_history_of_one_release_claims_nothing():
    """The seeded state: no chain, so no interval is provable and the stub
    says only what the tool says.
    """
    doc = _toolhistory.new(
        "demo",
        version="1.0.0",
        date="2026-01-01",
        surface=_toolhistory.surface_of(_spec()),
    )
    spec = _toolhistory.union(doc, name="demo")
    assert spec.verbs, "the union of one release is that release"
    assert not any(o.since or o.until for v in spec.verbs for o in v.options)


def test_an_observation_records_which_platforms_read_it():
    """A fact about the observation, like its date — and the groundwork for
    exclusions: "absent on Windows, and Windows was read" is an exclusion,
    while "absent on Windows, which never ran" is silence.

    A *list*, because a release read on three platforms is one observation of
    a merged surface. Storing it three times would triple a store whose
    options are nearly all universal, to carry the rare one that is not.
    """
    surface = _toolhistory.surface_of(_spec())
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-02-01",
        surface=surface,
        platforms=["Linux", "macOS"],
    )
    assert doc["base"]["platforms"] == ["Linux", "macOS"]  # sorted, one entry

    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=surface,
        platforms=["Windows"],
    )
    assert doc["deltas"]["1.0.0"]["platforms"] == ["Windows"]


def test_every_checked_in_observation_names_its_platforms():
    """The store must not grow observations that cannot say where they came
    from; a later multi-platform refresh reads this to decide what is an
    exclusion and what was simply never looked at.
    """
    from livery.toolroom._machinery import _drivers
    from livery.toolroom._machinery import _tasks as tools_tasks

    for driver in _drivers.DRIVERS:
        doc = _toolhistory.load(tools_tasks._history_path(driver.key))
        if doc is None:
            continue
        assert doc["base"].get("platforms"), f"{driver.key} base"
        for version, step in doc["deltas"].items():
            assert step.get("platforms"), f"{driver.key} {version}"


def test_priming_rewrites_the_stub_it_invalidates(monkeypatch, tmp_path):
    """A deeper history changes what a stub may say — an option that looked
    original at the old floor may turn out to have arrived. The stub is a
    rendering of the record, so extending the record rewrites it rather than
    waiting for someone to remember a `sync`.
    """
    from livery.toolroom._machinery import _tasks as tools_tasks

    doc = _toolhistory.load(tools_tasks._history_path("prek"))
    assert doc is not None
    chain = _toolhistory.observed(doc)
    assert len(chain) > 5, "prek is the primed tool; this test needs its chain"

    stub = tools_tasks._stub_path("prek").read_text(encoding="utf-8")
    assert "Added in" in stub, "a primed tool's stub carries what the chain proved"
    # ...and only versions the chain actually holds.
    import re

    for claimed in set(re.findall(r"Added in ([0-9][^.\s]*(?:\.[^.\s]+)*)\.", stub)):
        assert claimed in chain, claimed


def test_an_older_reading_never_becomes_the_head(tmp_path, monkeypatch):
    """A machine with a stale tool must not rewrite the base and push the
    newer release down the chain as though it came first. Recording on any
    change did exactly that: ruff's history ended up with 0.16.0 as both the
    base and one of its own ancestors.
    """
    from livery.toolroom._machinery import _drivers
    from livery.toolroom._machinery import _tasks as tools_tasks

    monkeypatch.setattr(tools_tasks, "_HISTORY", tmp_path)
    driver = _drivers.find("prek")
    assert driver is not None

    def spec_at(version: str):
        return ToolSpec(name="prek", version=version, verbs=_spec().verbs)

    tools_tasks._observe(driver, spec_at("0.5.0"))
    doc = tools_tasks._observe(driver, spec_at("0.4.0"))  # a laggard machine
    assert doc["base"]["version"] == "0.5.0"  # the head stands
    assert list(doc["deltas"]) == ["0.4.0"]  # ...and the older read is history

    doc = tools_tasks._observe(driver, spec_at("0.6.0"))  # a newer release
    assert doc["base"]["version"] == "0.6.0"
    assert list(doc["deltas"]) == ["0.5.0", "0.4.0"]
    assert _toolhistory.observed(doc) == ["0.6.0", "0.5.0", "0.4.0"]


# --- the tiers a prime can read ---------------------------------------------


def _index(monkeypatch, payload):
    """Serve *payload* as the registry's JSON, whatever URL is asked for."""
    import io
    import json as _json

    from livery.toolroom._machinery import _toolfetch

    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(_json.dumps(payload).encode()),
    )


def test_npm_releases_come_from_the_time_map(monkeypatch):
    """Npm keeps publication dates in `time`, alongside two entries that are
    not versions at all.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _index(
        monkeypatch,
        {
            "versions": {"9.0.0": {}, "10.0.0": {}, "10.0.1": {}},
            "time": {
                "created": "2020-01-01T00:00:00Z",
                "modified": "2026-05-31T00:00:00Z",
                "9.0.0": "2026-01-05T00:00:00Z",
                "10.0.0": "2026-05-30T00:00:00Z",
                "10.0.1": "2026-05-31T00:00:00Z",
                "10.0.2": "2026-06-01T00:00:00Z",  # in `time`, not in `versions`
            },
        },
    )
    driver = _drivers.find("cspell")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["10.0.1", "10.0.0", "9.0.0"]
    assert got[0].date == "2026-05-31"


def test_github_releases_normalise_the_tag_and_drop_the_unreleased(monkeypatch):
    """A tag is `v2.96.0` on one project and `2.96.0` on the next, while the
    binary reports the bare number — and the history keys on what the binary
    says, or a primed release never matches the base it belongs under.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v2.96.0", "published_at": "2026-07-02T00:00:00Z"},
            {"tag_name": "v2.95.0", "published_at": "2026-06-01T00:00:00Z"},
            {
                "tag_name": "v3.0.0-rc1",
                "published_at": "2026-07-20T00:00:00Z",
                "prerelease": True,
            },
            {
                "tag_name": "v2.97.0",
                "published_at": "2026-07-10T00:00:00Z",
                "draft": True,
            },
        ],
    )
    driver = _drivers.find("gh")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["2.96.0", "2.95.0"]


def _dirlisting(monkeypatch, html):
    """Serve *html* as a directory index, whatever URL is asked for.

    The engine listing that dates those files is stubbed empty, so a test
    about the directory is about the directory; a test about dates says so.
    """
    import io

    from livery.toolroom._machinery import _toolfetch

    monkeypatch.setattr(
        _toolfetch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(html.encode()),
    )
    monkeypatch.setattr(_toolfetch, "_docker_dates", dict)


DOCKER_LISTING = """<html><body><pre>
<a href="../">../</a>
<a href="sbx/">sbx/</a>
<a href="docker-17.03.0-ce.tgz">ce</a>            2025-08-06 10:05  44MB
<a href="docker-rootless-extras-27.5.1.tgz">rl</a>  2025-01-22 09:00  20MB
<a href="docker-27.5.1.tgz">docker-27.5.1</a>      2025-01-22 09:00  44MB
<a href="docker-29.4.2.tgz">docker-29.4.2</a>      2026-06-01 10:05  46MB
<a href="docker-29.4.2-2.tgz">rebuild</a>         2026-06-03 10:05  46MB
<a href="docker-29.6.2.tgz">docker-29.6.2</a>      2026-07-16 12:00  46MB
</pre></body></html>"""


def test_docker_reads_its_versions_from_a_directory_listing(monkeypatch):
    """Docker publishes a static build of every release per platform, so the
    index is a folder rather than an asset list. Three neighbours sit in the
    same folder and none of them is a docker release: the rootless extras,
    the 2017 `-ce` spelling, and a `-2` rebuild of a version already there.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _dirlisting(monkeypatch, DOCKER_LISTING)
    driver = _drivers.find("docker")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["29.6.2", "29.4.2", "27.5.1"]
    assert got[0].date == "2026-07-16"


def test_docker_index_is_chosen_by_platform_and_architecture(monkeypatch):
    """One index per (os, arch) pair, and Windows publishes no arm64 build —
    an arm Windows box takes the x86_64 zip and runs it in emulation.
    """
    import platform as _platform_mod

    from livery.toolroom._machinery import _toolfetch

    def channel(platform, machine, windows):
        monkeypatch.setattr(_toolfetch.sys, "platform", platform)
        monkeypatch.setattr(_toolfetch, "_windows", lambda: windows)
        monkeypatch.setattr(_platform_mod, "machine", lambda: machine)
        return _toolfetch._docker_channel()

    assert channel("darwin", "arm64", False) == ("mac", "aarch64", "tgz")
    assert channel("linux", "x86_64", False) == ("linux", "x86_64", "tgz")
    assert channel("win32", "ARM64", True) == ("win", "x86_64", "zip")


def test_docker_is_fetched_rather_than_read_from_the_host():
    """It used to be a `system` tool, read from whatever the laptop had
    installed — so its history could only ever hold one version.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    driver = _drivers.find("docker")
    assert driver is not None
    assert driver.provision.kind == "docker"
    assert _toolfetch.can_list(driver)


COMPOSE_RELEASES = [
    {"tag_name": "v5.3.1", "published_at": "2026-07-07T00:00:00Z"},
    {"tag_name": "v2.32.4", "published_at": "2025-01-15T00:00:00Z"},
    {"tag_name": "v2.0.0", "published_at": "2021-09-28T00:00:00Z"},
    {"tag_name": "1.29.2", "published_at": "2021-05-10T00:00:00Z"},
]


def _plugin_fetch(monkeypatch, placed):
    """Serve the compose listing, and record what was asked for."""
    from livery.toolroom._machinery import _provision, _toolfetch

    _index(monkeypatch, COMPOSE_RELEASES)
    monkeypatch.setattr(_toolfetch, "_LISTINGS", {})
    asked = []

    def assets_for(_host, _repo, tag=""):
        asked.append(tag)
        if not placed:
            raise _provision.ProvisionError("rate limited")
        return [("docker-compose-linux-x86_64", "http://x/bin")]

    monkeypatch.setattr(_provision, "assets_for", assets_for)
    monkeypatch.setattr(_provision, "_pick_asset", lambda a: a[0])
    monkeypatch.setattr(
        _provision, "_download", lambda url, into: _written(into / "docker-compose")
    )
    monkeypatch.setattr(
        _provision, "_extract_binary", lambda archive, tool, into: into / tool
    )
    return asked


def _written(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"plugin")
    return path


def test_a_plugin_is_paired_with_the_release_that_shipped_alongside(
    monkeypatch, tmp_path
):
    """Compose has its own release line, so there is no version to match on
    — but "what a user of that docker would have had" is a fact the two
    dates settle between them, the same answer every time.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    asked = _plugin_fetch(monkeypatch, placed=True)
    docker = _drivers.find("docker")
    assert docker is not None
    compose = docker.plugins[0]
    assert _toolfetch.install_plugin(compose, "2025-06-01", tmp_path) is True
    assert asked == ["v2.32.4"]  # not v5.3.1, which had not shipped yet


def test_an_era_before_the_plugin_existed_pairs_with_nothing(monkeypatch, tmp_path):
    """Compose 1.x was a program you ran as `docker-compose`; `docker
    compose` did not exist until 2.0, and dropping a 1.x binary into the
    plugin directory would not make it one.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    asked = _plugin_fetch(monkeypatch, placed=True)
    docker = _drivers.find("docker")
    assert docker is not None
    assert _toolfetch.install_plugin(docker.plugins[0], "2021-06-01", tmp_path) is False
    assert asked == []


def test_a_plugin_that_cannot_be_fetched_is_unreachable_not_absent(
    monkeypatch, tmp_path
):
    """A rate limit read past becomes "this docker had no compose" — a
    different claim, and one the history would write down as a removal.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _plugin_fetch(monkeypatch, placed=False)
    docker = _drivers.find("docker")
    assert docker is not None
    with pytest.raises(_toolfetch.Unreachable, match=r"docker/compose 2\.32\.4"):
        _toolfetch.install_plugin(docker.plugins[0], "2025-06-01", tmp_path)


def test_a_gateway_timeout_on_an_index_is_retried(monkeypatch):
    """The gap the retry left. `_download` retries a dropped connection;
    the listing path did not, so a leg died on

        Unreachable: cannot read .../docker/buildx/releases?…:
        HTTP Error 504: Gateway Timeout

    and took the whole platform's observations with it — the same failure
    the download retry exists to prevent, one layer up.
    """
    import email.message
    import urllib.error

    from livery.toolroom._machinery import _toolfetch

    calls = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def flaky(request, timeout=0):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(
                "http://x", 504, "Gateway Timeout", email.message.Message(), None
            )
        return Answer()

    monkeypatch.setattr(_toolfetch.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(_toolfetch.time, "sleep", lambda _s: None)
    request = _toolfetch.urllib.request.Request("http://x")
    assert _toolfetch._read_index(request, "http://x") == b"[]"
    assert len(calls) == 3

    # A 404 is an answer: raised at once, and still as Unreachable.
    calls.clear()

    def gone(request, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(
            "http://x", 404, "Not Found", email.message.Message(), None
        )

    monkeypatch.setattr(_toolfetch.urllib.request, "urlopen", gone)
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch._read_index(request, "http://x")
    assert len(calls) == 1


def test_a_listing_is_read_once_per_process(monkeypatch):
    """Every release of a walk asks the same question of the same
    repository, and the answer cannot change while the walk runs.
    """
    from livery.toolroom._machinery import _toolfetch

    calls: list[tuple[object, ...]] = []
    _index(monkeypatch, COMPOSE_RELEASES)
    monkeypatch.setattr(_toolfetch, "_LISTINGS", {})
    real = _toolfetch._forge

    def fake_forge(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(_toolfetch, "_forge", fake_forge)
    first = _toolfetch._listing("docker/compose", 3)
    again = _toolfetch._listing("docker/compose", 3)
    assert first == again and len(calls) == 1


def test_docker_dates_come_from_the_engine_not_the_upload(monkeypatch):
    """The static index dates its files by upload time, and docker
    re-uploads in bulk: a third of the archives are stamped one day in 2025,
    including 20.10.6, which shipped in April 2021. Those dates decide which
    compose a release is paired with.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _dirlisting(monkeypatch, DOCKER_LISTING)
    monkeypatch.setattr(
        _toolfetch,
        "_docker_dates",
        lambda: {"27.5.1": "2025-01-22", "29.6.2": "2026-07-16"},
    )
    driver = _drivers.find("docker")
    assert driver is not None
    dated = {r.version: r.date for r in _toolfetch.releases(driver)}
    assert dated["27.5.1"] == "2025-01-22"
    assert dated["29.4.2"] == "2026-06-01"  # not in the engine listing: the mtime


def test_man_index_reads_a_dated_and_an_undated_listing(monkeypatch):
    """One reader serves both manual publishers, and both date their rows —
    kernel.org on the row itself, OpenSSH in the next cell a line below, which
    is why its pattern crosses newlines where kernel.org's need not.
    """
    from livery.toolroom._machinery import _toolfetch
    from livery.toolroom._machinery._drivers import Driver, Manual, Provision

    kernel = (
        '<a href="git-manpages-2.50.0.tar.gz">x</a> 16-Jun-2025 16:31\n'
        '<a href="git-manpages-2.49.0.tar.gz">x</a> 14-Mar-2025 18:34\n'
    )
    monkeypatch.setattr(_toolfetch, "_read_index", lambda *_a: kernel.encode())
    git = Driver(
        "git",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://k.org/",
                archive="git-manpages-{version}.tar.gz",
                listing=(
                    r'href="git-manpages-(?P<version>\d+(?:\.\d+)+)\.tar\.gz"'
                    r".*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
            ),
        ),
    )
    got = _toolfetch.releases(git)
    assert [(r.version, r.date) for r in got] == [
        ("2.50.0", "2025-06-16"),
        ("2.49.0", "2025-03-14"),
    ]

    # The real shape: the date lives in the row's *next* cell, on its own
    # line, and the `.asc` beside each tarball must not read as a release.
    openssh = (
        '<tr><td><a href="openssh-9.9p1.tar.gz">openssh-9.9p1.tar.gz</a></td>\n'
        '    <td data-o="1">19-Sep-2024 01:17</td><td>1.9M</td></tr>\n'
        '<tr><td><a href="openssh-9.9p1.tar.gz.asc">x</a></td>\n'
        '    <td data-o="1">19-Sep-2024 01:17</td><td>833B</td></tr>\n'
        '<tr><td><a href="openssh-10.0p1.tar.gz">openssh-10.0p1.tar.gz</a></td>\n'
        '    <td data-o="1">09-Apr-2025 07:00</td><td>1.9M</td></tr>\n'
        '<tr><td><a href="openssh-9.9p2.tar.gz">openssh-9.9p2.tar.gz</a></td>\n'
        '    <td data-o="1">18-Feb-2025 01:17</td><td>1.9M</td></tr>\n'
    )
    monkeypatch.setattr(_toolfetch, "_read_index", lambda *_a: openssh.encode())
    ssh = Driver(
        "ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://o.org/",
                archive="openssh-{version}.tar.gz",
                listing=(
                    r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"'
                    r"[\s\S]*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
            ),
        ),
    )
    got = _toolfetch.releases(ssh)
    # `version_tuple` reads 9.9p1 and 9.9p2 as the same base; the portable
    # patchlevel breaks the tie, and each release carries its own date.
    assert [(r.version, r.date) for r in got] == [
        ("10.0p1", "2025-04-09"),
        ("9.9p2", "2025-02-18"),
        ("9.9p1", "2024-09-19"),
    ]


def test_install_man_pulls_named_pages_from_a_source_tarball(tmp_path, monkeypatch):
    """OpenSSH's release tarball carries its pages beside the sources: only
    the named pages land, by basename, and nothing else escapes.
    """
    import io
    import tarfile

    from livery.toolroom._machinery import _provision, _toolfetch
    from livery.toolroom._machinery._drivers import Driver, Manual, Provision

    archive = tmp_path / "openssh-9.9p2.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for member, payload in [
            ("openssh-9.9p2/ssh.1", b".Dd ssh page"),
            ("openssh-9.9p2/ssh-keygen.1", b".Dd keygen page"),
            ("openssh-9.9p2/configure", b"#!/bin/sh"),
        ]:
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    monkeypatch.setattr(_provision, "_download", lambda _url, _into, **_kw: archive)
    driver = Driver(
        "ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://o.org/",
                archive="openssh-{version}.tar.gz",
                listing=r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"',
                pages=("ssh.1", "ssh-keygen.1"),
            ),
        ),
    )
    release = _toolfetch.Release(version="9.9p2", date="")
    placed = _toolfetch.install(driver, release, tmp_path / "into")
    assert placed is not None
    assert sorted(p.name for p in placed.glob("man1/*")) == ["ssh-keygen.1", "ssh.1"]
    assert (placed / "man1" / "ssh.1").read_bytes() == b".Dd ssh page"


def test_man_tier_merges_every_tools_pages(tmp_path, monkeypatch):
    """The man tier holds more than one tool's pages: a second driver merges
    into the shared tree rather than replacing the first's.
    """
    from livery.toolroom._machinery import _provision, _toolfetch
    from livery.toolroom._machinery._drivers import Driver, Manual, Provision

    manual = Manual(index="https://x/", archive="{version}.tar.gz", listing="x")
    drivers = [
        Driver("git", provision=Provision(kind="man", manual=manual)),
        Driver("ssh", provision=Provision(kind="man", manual=manual)),
    ]
    release = _toolfetch.Release(version="1.0", date="")
    monkeypatch.setattr(_toolfetch, "releases", lambda _d: [release])

    def fake_install(driver, _release, into):
        tree = into / "man"
        (tree / "man1").mkdir(parents=True)
        (tree / "man1" / f"{driver.name}.1").write_text("page")
        return tree

    monkeypatch.setattr(_toolfetch, "install", fake_install)
    prefix = tmp_path / "prefix"
    outcomes = _provision._man_tier(prefix, drivers)
    assert [(o.status, o.detail) for o in outcomes] == [("ok", "1.0"), ("ok", "1.0")]
    assert sorted(p.name for p in (prefix / "man" / "man1").glob("*.1")) == [
        "git.1",
        "ssh.1",
    ]


def test_gitlab_releases_read_their_own_field_names(monkeypatch):
    from livery.toolroom._machinery import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v0.6.0-wk.5", "released_at": "2026-07-07T00:00:00Z"},
            {"tag_name": "v0.6.0-wk.4", "released_at": "2026-06-07T00:00:00Z"},
        ],
    )
    driver = _drivers.find("eclint")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.6.0-wk.5", "0.6.0-wk.4"]


def test_gitea_releases_read_the_github_shape(monkeypatch):
    """Gitea's API answers with GitHub's field names — one reading serves
    both hosts, and the prerelease/draft filter applies the same way.
    """
    from livery.toolroom._machinery import _toolfetch
    from livery.toolroom._machinery._drivers import Driver, Provision

    _index(
        monkeypatch,
        [
            {
                "tag_name": "v0.16.0-rc1",
                "published_at": "2026-07-28T00:00:00Z",
                "prerelease": True,
            },
            {"tag_name": "v0.15.0", "published_at": "2026-07-27T00:00:00Z"},
            {"tag_name": "v0.14.2", "published_at": "2026-06-26T00:00:00Z"},
        ],
    )
    driver = Driver("x", provision=Provision(kind="gitea", repo="o/r"))
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.15.0", "0.14.2"]
    assert got[0].tag == "v0.15.0" and got[0].date == "2026-07-27"


def test_observe_carries_every_release_field_through(tmp_path, monkeypatch):
    """The walk funnels a release through the observe task's parameters, and
    a field the funnel drops silently reverts its fix inside the walk: the
    publishing-window cutoff never reached _install_pypi through here, so
    cmake 4.3.1 kept resolving at the near edge and holing — while the
    identical install ran clean by hand.
    """
    from livery.toolroom._machinery import _tasks as tools_tasks

    seen: list[Any] = []

    def fake_install(driver, release, into):
        seen.append(release)
        return None  # stop before extraction — the release is the assertion

    monkeypatch.setattr("livery.toolroom._machinery._toolfetch.install", fake_install)
    monkeypatch.setattr(tools_tasks, "_refuse_a_broken_environment", lambda p: None)
    tools_tasks.observe(
        "cmake",
        "4.3.1",
        date="2026-03-28",
        published="2026-03-31",
        requires_python=">=3.8",
        scratch=str(tmp_path),
    )
    assert seen[0].published == "2026-03-31"
    assert seen[0].requires_python == ">=3.8"
    assert seen[0].date == "2026-03-28"


def test_a_provision_floor_takes_releases_out_of_scope(monkeypatch):
    """Below the floor is not *offered* — not walked, never holes. Unlike
    `deferred` the tool stays curated; its history just starts at the
    floor. tea's sits at 0.15.0, above the console-hang band.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v0.15.0", "published_at": "2026-07-27T00:00:00Z"},
            {"tag_name": "v0.14.2", "published_at": "2026-06-26T00:00:00Z"},
            {"tag_name": "v0.9.0", "published_at": "2024-01-01T00:00:00Z"},
        ],
    )
    driver = _drivers.find("tea")
    assert driver is not None
    assert driver.provision.floor == "0.15.0"
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.15.0"]


def test_an_unreadable_index_is_not_an_empty_one(monkeypatch):
    """The distinction the release gate rests on.

    "Is there anything new" is answered "no" by a throttled registry exactly
    as it is by a tool that has genuinely not moved — and one of those means
    stop, while the other means nobody looked. Sharing the empty list would
    let a rate limit read as "nothing to release".

    A prime still skips such a tool rather than failing the run, but it has
    to *choose* to, which is the point of raising.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    def boom(*a, **k):
        raise _toolfetch.urllib.error.URLError("no network")

    monkeypatch.setattr(_toolfetch.urllib.request, "urlopen", boom)
    driver = _drivers.find("prek")
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def test_which_tiers_can_be_listed():
    """Every tier that can name its past releases. A hand-written stub has
    nothing to read at all, and a deferred tool is parked on purpose.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    expected = {
        "prek": True,  # uv
        "cspell": True,  # node
        "gh": True,  # github
        "eclint": True,  # gitlab
        "tea": True,  # gitea
        "bun": True,  # bun's own releases
        "python": True,  # uv carries CPython's own download index
        "docker": True,  # its own static-build index
        "git": True,  # kernel.org's per-release manuals
        "bash": False,  # manual stub
    }
    for key, listable in expected.items():
        driver = _drivers.find(key)
        assert driver is not None, key
        assert _toolfetch.can_list(driver) is listable, key
        if not listable:
            assert _toolfetch.releases(driver) == [], key


def test_installing_an_unlistable_tier_declines(tmp_path):
    from livery.toolroom._machinery import _drivers, _toolfetch

    # No curated tool sits in a tier that cannot be fetched any more — git
    # was the last, and it is read from kernel.org's manuals now (the
    # `system` tier it sat in is deleted). The rule still holds, so it is
    # stated against a driver rather than a tool.
    driver = _drivers.Driver("nope", provision=_drivers.Provision(kind="deferred"))
    release = _toolfetch.Release("2.50.0")
    assert _toolfetch.install(driver, release, tmp_path / "nope") is None


def test_the_npm_tier_needs_bun_and_says_so(tmp_path, monkeypatch):
    """Bun is how the node tier is provisioned, so priming borrows it. Without
    it the walk stops — and reports why, because a scheduled job reading '+0'
    cannot tell that from 'nothing left to read'.
    """
    import shutil

    from livery.toolroom._machinery import _drivers, _toolfetch

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    driver = _drivers.find("cspell")
    assert driver is not None
    release = _toolfetch.Release("10.0.0")
    assert _toolfetch.install(driver, release, tmp_path / "cspell") is None

    # ...and the walk says so rather than walking into holes. Without this
    # the docstring above was a claim the test never checked: a macOS
    # gather with no bun reported 23 releases across cspell and
    # markdownlint as holes — "these could not be had" — when every one of
    # them reads fine the moment bun is on PATH.
    from livery.toolroom._machinery import _toolfetch as fetch
    from livery.toolroom._machinery._tasks import _curated

    chosen, skipped = _curated("", fetch)
    assert "cspell (no bun to install with)" in skipped
    assert "markdownlint (no bun to install with)" in skipped
    assert not any(d.provision.kind == "node" for d in chosen)


# --- CPython: a tool with more than one release line at a time ---------------


def _uv_listing(monkeypatch, entries):
    """Serve *entries* as `uv python list --output-format json` would."""
    import json as _json

    from livery.toolroom._machinery import _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: _json.dumps(entries))


def _cpython(version, day, **over):
    """One entry of uv's listing, defaulting to a downloadable stable build."""
    return {
        "version": version,
        "implementation": "cpython",
        "variant": "default",
        "path": None,
        "url": f"https://example.invalid/releases/download/{day}/cpython-{version}.tar.gz",
        **over,
    }


def test_the_python_listing_keeps_only_what_is_a_release(monkeypatch):
    """A pre-release is not something to claim an option arrived in, a
    free-threaded build is a build of a release rather than one of its own,
    and pypy is a different tool.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.14.6", "20260718"),
            _cpython("3.15.0a7", "20260801"),
            _cpython("3.13.14", "20260718", variant="freethreaded"),
            _cpython("3.12.0", "20231002", implementation="pypy"),
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["3.14.6"]


def test_the_python_listing_asks_only_for_downloads(monkeypatch):
    """Installing a version replaces its download entry with the local path
    and drops the URL the date is read from. Asking for anything but downloads
    would therefore make the index answer differently on every machine — and a
    prime would erase releases from the listing it is walking.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    seen: list[list[str]] = []

    def fake_capture(argv):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(_toolfetch, "_capture", fake_capture)
    driver = _drivers.find("python")
    assert driver is not None
    _toolfetch.releases(driver)
    assert "--only-downloads" in seen[0]


def test_a_uv_that_will_not_answer_is_unreachable_not_empty(monkeypatch):
    """Uv carries the index inside itself, so "no uv" is "nothing seen" — and
    emphatically not "CPython has no releases".
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: "")
    driver = _drivers.find("python")
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def _drivers_find(key):
    from livery.toolroom._machinery import _drivers

    driver = _drivers.find(key)
    assert driver is not None, key
    return driver


def test_a_chain_is_ordered_by_version_not_by_publication_date(monkeypatch):
    """Three curated tools keep more than one series alive at once — cmake
    3.31.x beside 4.x, pytest's 4.6 LTS beside 5.x, CPython's five — so the
    newest release is not the most recently published one.

    Ordered by date, a walk back from 3.14.6 steps to 3.13.14 and records
    every 3.14 option as dropped, then re-adds them lower down; every
    interval derived from that chain is then wrong. The history answers a
    version question, so version is what orders it.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.13.14", "20260718"),  # same build date as 3.14.6...
            _cpython("3.14.6", "20260718"),
            _cpython("3.12.13", "20260718"),
            _cpython("3.14.5", "20260611"),  # ...and published before 3.13.14
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    found = [r.version for r in _toolfetch.releases(driver)]
    assert found == ["3.14.6", "3.14.5", "3.13.14", "3.12.13"]


def test_a_tie_the_comparator_cannot_break_leaves_the_base_alone(tmp_path, monkeypatch):
    """`0.6.0-wk.3` and `0.6.0-wk.5` are two builds of one base, and the
    comparator reduces both to `(0, 6, 0)` — a build tail says nothing about
    which flags exist. A chain breaks that tie on publication date, but a
    fresh reading is stamped today whatever build it holds, so the snapshot
    guard has nothing to break it with.

    It must therefore decline. Treating the tie as "not older" is what let a
    stale checkout promote `wk.3` over the recorded `wk.5` and push the newer
    build down the chain — the exact rewrite the guard exists to refuse.
    """
    from livery.toolroom._machinery import _tasks as tools

    surface = _toolhistory.surface_of(
        _spec(verbs=(Verb(name="", options=(Option("fix", ("--fix",)),)),))
    )
    doc = _toolhistory.new(
        "eclint", version="0.6.0-wk.5", date="2026-07-01", surface=surface
    )
    monkeypatch.setattr(tools, "_HISTORY", tmp_path)
    _toolhistory.save(doc, tmp_path / "eclint.json")

    driver = _drivers_find("eclint")
    spec = _toolhistory.spec_from(surface, name="eclint", version="0.6.0-wk.3")
    with pytest.raises(tools._Ambiguous) as raised:
        tools._observe(driver, spec)
    assert raised.value.reading == "0.6.0-wk.3"
    assert raised.value.base == "0.6.0-wk.5"
    # and the file is untouched
    stored = _toolhistory.load(tmp_path / "eclint.json")
    assert stored is not None
    assert stored["base"]["version"] == "0.6.0-wk.5"


# --- the forward walk: catching a history up to its index --------------------


# --- the CHANGELOG entry the events write ------------------------------------


def _chain(*surfaces):
    """A history built newest-last, the way a refresh promotes into one."""
    versions = [f"1.0.{n}" for n in range(len(surfaces))]
    doc = _toolhistory.new(
        "demo", version=versions[0], date="2026-01-01", surface=surfaces[0]
    )
    for n, surface in enumerate(surfaces[1:], start=1):
        _toolhistory.promote(
            doc, version=versions[n], date=f"2026-01-0{n + 1}", surface=surface
        )
    return doc, versions


def _with(*options, verb=""):
    return _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name=verb,
                    options=tuple(
                        Option(n, (f"-{n[0]}", f"--{n.replace('_', '-')}"))
                        for n in options
                    ),
                ),
            )
        )
    )


def test_an_entry_names_what_changed_and_counts_what_it_will_not_list():
    """Added and dropped options are what a reader acts on, so they are named
    — by their command-line spelling, which is what they recognise, and not
    by the Python-side key the surface happens to store them under.

    Rewordings are counted. A release can restate half a dozen descriptions
    without changing what the tool accepts, and listing those turns a release
    note into a diff dump.
    """
    from livery.toolroom._machinery import _tasks as tools

    doc, versions = _chain(
        _with("quiet", "install_hooks"),
        _with("quiet", "prepare_hooks"),  # one added, one dropped
    )
    entry = tools._entry_for("prek", doc, versions[1:])

    assert entry.startswith("- **prek 1.0.1**")
    assert "`--prepare-hooks`" in entry  # the spelling, not `prepare_hooks`
    assert "drops `--install-hooks`" in entry


def test_an_entry_spans_from_the_release_before_the_first_change():
    """A release compared against itself is empty by construction, so the
    span has to start one earlier or the first change is invisible — which
    is exactly what a bullet saying "changes its option surface" looked
    like.
    """
    from livery.toolroom._machinery import _tasks as tools

    doc, versions = _chain(_with("quiet"), _with("quiet", "fix"))
    assert "adds `--fix`" in tools._entry_for("demo", doc, [versions[1]])


def test_an_entry_names_a_flag_once_however_many_verbs_carry_it():
    """The same flag on the bare command and on one of its verbs is two keys
    in the surface and one thing to tell a reader about.
    """
    from livery.toolroom._machinery import _tasks as tools

    def both(*names):
        return _toolhistory.surface_of(
            _spec(
                verbs=tuple(
                    Verb(
                        name=verb,
                        options=tuple(Option(n, (f"--{n}",)) for n in names),
                    )
                    for verb in ("", "run")
                )
            )
        )

    doc, versions = _chain(both("quiet"), both("quiet", "glob"))
    entry = tools._entry_for("prek", doc, [versions[1]])
    assert entry.count("`--glob`") == 1


def test_the_entry_lands_under_unreleased_changed(tmp_path):
    """`### Changed`, because a tool gaining a flag changes footman's *stub*
    — footman itself added nothing. And under `[Unreleased]`, never the
    released section above it, which is where a careless insert lands.
    """
    from livery.toolroom._machinery import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something.\n\n"
        "### Fixed\n\n- Something else.\n\n## [0.23.0] — 2026-07-27\n\n"
        "### Changed\n\n- Already released.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is True

    written = path.read_text(encoding="utf-8")
    unreleased, released = written.split("## [0.23.0]")
    assert "- **prek 0.4.11** adds `--glob`." in unreleased
    assert "- **prek 0.4.11**" not in released  # not swept into a shipped version
    # Keep a Changelog's order: Changed sits above Fixed, not appended anywhere.
    assert unreleased.index("### Changed") < unreleased.index("### Fixed")


def test_an_entry_joins_a_changed_section_that_already_exists(tmp_path):
    from livery.toolroom._machinery import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- An earlier note.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **ruff 0.16.1** adds `--fix`."], path) is True
    written = path.read_text(encoding="utf-8")
    assert written.count("### Changed") == 1
    assert "- **ruff 0.16.1** adds `--fix`." in written
    assert "- An earlier note." in written


def test_a_changelog_with_nowhere_to_write_says_so(tmp_path):
    """Reported rather than guessed at: a caller must not read "no entry
    written" as "there was nothing to write".
    """
    from livery.toolroom._machinery import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [0.23.0]\n\n- Released.\n", encoding="utf-8")
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is False
    assert tools._write_changelog(["- x"], tmp_path / "absent.md") is False


def test_an_entry_tells_commands_apart_from_their_descriptions():
    """Three things hide in one delta field. A verb the newer release added
    steps back as `None`; one it withdrew steps back as a whole verb; one
    whose description merely moved steps back as a couple of fields. Only the
    first two are news — the third is a rewording, and counting it as a lost
    command would be a lie in both directions.
    """
    from livery.toolroom._machinery import _tasks as tools

    def tree(*verbs):
        return _toolhistory.surface_of(
            _spec(
                verbs=tuple(
                    Verb(name=name, help=help_, options=()) for name, help_ in verbs
                )
            )
        )

    doc, versions = _chain(
        tree(("", "The tool."), ("run", "Run it."), ("clean", "Clean up.")),
        tree(
            ("", "The tool."),
            ("run", "Run the hooks."),  # reworded, not lost
            ("autoupdate", "Update."),  # gained
        ),  # `clean` withdrawn
    )
    entry = tools._entry_for("prek", doc, versions[1:])

    assert "gains the `autoupdate` command" in entry
    assert "withdraws the `clean` command" in entry
    assert "rewords 1 description" in entry
    assert "`run`" not in entry  # a reworded verb is not a lost one


def test_a_bullet_joins_several_names_and_clauses_readably():
    from livery.toolroom._machinery import _tasks as tools

    assert tools._names(["--a"]) == "`--a`"
    assert tools._names(["--a", "--b"]) == "`--a` and `--b`"
    assert tools._names(["--a", "--b", "--c"]) == "`--a`, `--b` and `--c`"
    assert tools._and(["one"]) == "one"
    assert tools._and(["one", "two", "three"]) == "one, two and three"
    assert tools._plural("release", 1) == "release"
    assert tools._plural("release", 2) == "releases"


# --- what a prime leaves behind ----------------------------------------------


def test_a_prime_keeps_uv_downloads_inside_its_own_scratch(tmp_path):
    """Uv writes to two places of its own accord — a wheel cache, and the
    store holding the interpreters this machine actually runs. Neither is a
    prime's to fill, and a walk of CPython's releases put 90 interpreters in
    that store and left them there.

    Pointed inside the scratch directory, the cleanup is structural: one
    rmtree removes every byte the walk caused, and the python you develop
    against is never a candidate for deletion.
    """
    import os

    from livery.toolroom._machinery import _tasks as tools

    was = {k: os.environ.get(k) for k in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")}
    with tools._sandboxed(tmp_path):
        assert os.environ["UV_CACHE_DIR"].startswith(str(tmp_path))
        assert os.environ["UV_PYTHON_INSTALL_DIR"].startswith(str(tmp_path))
    assert {k: os.environ.get(k) for k in was} == was  # and put back


def test_an_overlay_restores_a_variable_that_was_not_set(tmp_path):
    """Restoring must remove what it added, not write an empty string —
    an empty `UV_CACHE_DIR` is a cache directory, not the absence of one.
    """
    import os

    from livery.toolroom._machinery import _tasks as tools

    os.environ.pop("FOOTMAN_TEST_ABSENT", None)
    with tools._overlay(FOOTMAN_TEST_ABSENT="x"):
        assert os.environ["FOOTMAN_TEST_ABSENT"] == "x"
    assert "FOOTMAN_TEST_ABSENT" not in os.environ


def test_a_release_is_discarded_once_its_surface_is_read(tmp_path):
    """Peak disk is one release rather than all of them. Without this a prime
    holds everything it ever fetched until the run ends — ruff alone would
    stand up 416 environments at once.
    """
    from livery.toolroom._machinery import _tasks as tools

    release = tmp_path / "1.2.3"
    (release / "bin").mkdir(parents=True)
    (release / "bin" / "tool").write_text("x")
    tools._discard(release / "bin")
    assert not release.exists()
    tools._discard(release / "bin")  # gone already: not an error


# --- insert: a release arriving at any position ------------------------------


def _surface_at(n: int) -> dict[str, Any]:
    """A surface that differs at every release, so a wrong delta cannot pass."""
    return _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name="",
                    help=f"Release {n}.",
                    options=tuple(
                        Option(f"opt{i}", (f"--opt{i}",), help=f"Option {i} at {n}.")
                        for i in range(n + 1)
                    ),
                ),
            )
        )
    )


def test_a_release_can_arrive_at_any_position_and_the_chain_still_replays():
    """The property the whole format was chosen for, and the one that lets
    gathering be unordered: releases inserted in a shuffled order must build
    the same chain as releases inserted newest-first.

    Every observed release is replayed and compared against the surface it was
    built from — a delta computed against the wrong neighbour reconstructs
    something plausible, so only replaying all of them catches it.
    """
    surfaces = {f"1.0.{n}": _surface_at(n) for n in range(8)}
    dates = {v: f"2026-01-{n + 1:02d}" for n, v in enumerate(surfaces)}

    # Deliberately not in order: newest, oldest, then the middle scattered.
    arrival = ["1.0.7", "1.0.0", "1.0.4", "1.0.2", "1.0.6", "1.0.1", "1.0.5", "1.0.3"]
    doc = _toolhistory.new(
        "demo", version="1.0.7", date=dates["1.0.7"], surface=surfaces["1.0.7"]
    )
    for version in arrival[1:]:
        assert _toolhistory.insert(
            doc, version=version, date=dates[version], surface=surfaces[version]
        )

    assert _toolhistory.observed(doc) == sorted(surfaces, reverse=True)
    for version, expected in surfaces.items():
        assert _toolhistory.at(doc, version) == expected, version


def test_inserting_between_undated_patchlevels_of_one_base():
    """OpenSSH's shape: `version_tuple` reads 9.9p1 and 9.9p2 as the same
    base, and the portable listing carries no dates to break the tie — the
    patchlevel itself must place the release, or the middle-insertion scan
    finds no strictly-older entry and the walk dies on a StopIteration.
    """
    doc = _toolhistory.new("ssh", version="9.9p3", date="", surface=_surface_at(3))
    assert _toolhistory.insert(doc, version="9.9p1", date="", surface=_surface_at(1))
    assert _toolhistory.insert(doc, version="9.9p2", date="", surface=_surface_at(2))
    assert _toolhistory.observed(doc) == ["9.9p3", "9.9p2", "9.9p1"]
    for version, n in (("9.9p1", 1), ("9.9p2", 2), ("9.9p3", 3)):
        assert _toolhistory.at(doc, version) == _surface_at(n), version


def test_inserting_a_release_the_chain_already_holds_changes_nothing():
    """What makes an interrupted gather resumable: run it again and the
    releases already recorded are skipped rather than rewritten.
    """
    doc = _toolhistory.new(
        "demo", version="1.0.2", date="2026-01-03", surface=_surface_at(2)
    )
    _toolhistory.insert(doc, version="1.0.0", date="2026-01-01", surface=_surface_at(0))
    before = json.dumps(doc, sort_keys=True)

    assert not _toolhistory.insert(
        doc, version="1.0.0", date="2026-01-01", surface=_surface_at(0)
    )
    assert json.dumps(doc, sort_keys=True) == before


def test_a_midfill_recomputes_exactly_one_entry():
    """§1's claim, checked: local, never cascading. Inserting into a long
    chain must leave every delta below the insertion byte-identical, or the
    format's cost argument does not hold.
    """
    surfaces = {f"1.0.{n}": _surface_at(n) for n in range(8)}
    doc = _toolhistory.new(
        "demo", version="1.0.7", date="2026-01-08", surface=surfaces["1.0.7"]
    )
    for n in (6, 5, 3, 2, 1, 0):  # 1.0.4 deliberately missing
        _toolhistory.extend(
            doc,
            version=f"1.0.{n}",
            date=f"2026-01-{n + 1:02d}",
            surface=surfaces[f"1.0.{n}"],
        )
    untouched = {v: json.dumps(d, sort_keys=True) for v, d in doc["deltas"].items()}

    _toolhistory.insert(
        doc, version="1.0.4", date="2026-01-05", surface=surfaces["1.0.4"]
    )

    # 1.0.3 is the inserted release's successor and is the only one recomputed.
    changed = [
        v
        for v, before in untouched.items()
        if json.dumps(doc["deltas"][v], sort_keys=True) != before
    ]
    assert changed == ["1.0.3"]
    for version, expected in surfaces.items():
        assert _toolhistory.at(doc, version) == expected, version


def test_a_gap_costs_precision_and_not_correctness():
    """Until a missing release is filled, an option it introduced reads as
    arriving at the next release actually read — the same honest imprecision
    the chain already carries where an index has no build to offer.
    """
    doc = _toolhistory.new(
        "demo", version="1.0.2", date="2026-01-03", surface=_surface_at(2)
    )
    _toolhistory.extend(doc, version="1.0.0", date="2026-01-01", surface=_surface_at(0))
    spec = _toolhistory.union(doc, name="demo")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["opt2"] == "1.0.2"  # 1.0.1 unread: attributed to what was seen

    _toolhistory.insert(doc, version="1.0.1", date="2026-01-02", surface=_surface_at(1))
    spec = _toolhistory.union(doc, name="demo")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["opt1"] == "1.0.1"  # filled, and now attributed exactly


# --- the walks, driven through footman's own runner --------------------------
#
# The gather runs releases in parallel and leans on the run infrastructure —
# the environ router, the per-call env copy at the task boundary — so these
# tests drive the real thing: `footman.testing.Runner` is an in-process run,
# routers installed, and the result rows include every `observe` the engine
# fanned out. A bare call is refused (tested last), so there is no sequential
# twin for tests to accidentally exercise instead.


def _tools_run(line):
    """Drive the real CLI in-process.

    A list of arguments is passed through unsplit — a Windows path in a
    command *string* would be shlex-split and lose its backslashes, which is
    a fine way to make a cross-platform feature fail only on the platform it
    is about.
    """
    from livery.footman.testing import Runner
    from livery.toolroom._machinery._tasks import tasks as tools_group

    return Runner().invoke(line, tasks=tools_group)


def _isolate(tools, monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "_HISTORY", tmp_path / "history")
    monkeypatch.setattr(tools, "_STUBS", tmp_path / "stubs")
    monkeypatch.setattr(tools, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    (tmp_path / "history").mkdir()
    (tmp_path / "stubs").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n", encoding="utf-8"
    )


def _serve(monkeypatch, listings, surfaces, installed=None):
    """The fake tiers: a listing per tool, a surface per (tool, version).

    Called from worker threads, so mutation is `list.append`-shaped. An
    absent (tool, version) makes the install fail — a hole.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    installed = installed if installed is not None else []
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: list(listings[driver.key])
    )

    def install(driver, release, into):
        installed.append((driver.key, release.version))
        if surfaces.get((driver.key, release.version)) is None:
            return None
        (into / "bin").mkdir(parents=True, exist_ok=True)
        (into / "bin" / driver.name).write_text("x")
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)

    def extract(driver):
        import os

        spot = os.environ.get("PATH", "").split(os.pathsep)[0]
        version = pathlib.Path(spot).parent.name.split("==")[-1].rsplit("-", 1)[-1]
        return _toolhistory.spec_from(surfaces[(driver.key, version)], name=driver.name)

    monkeypatch.setattr(_drivers, "extract", extract)
    return installed


def _with_flags(*names):
    return _toolhistory.surface_of(
        _spec(
            verbs=(Verb(name="", options=tuple(Option(n, (f"--{n}",)) for n in names)),)
        )
    )


def test_a_refresh_reads_every_release_it_missed_not_just_the_newest(
    tmp_path, monkeypatch
):
    """Attribution is the whole point of the log: three releases behind means
    three observations, and the flag that arrived in 1.0.1 is recorded there,
    not at 1.0.3. The observations run in parallel and land in whatever order
    the pool finishes; the chain assembles the same either way.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n + 1}")
            for n in (3, 2, 1, 0)
        ]
    }
    surfaces = {
        ("ruff", "1.0.1"): _with_flags("quiet", "fix"),  # the event
        ("ruff", "1.0.2"): _with_flags("quiet", "fix"),
        ("ruff", "1.0.3"): _with_flags("quiet", "fix"),
    }
    installed = _serve(monkeypatch, listings, surfaces)

    result = _tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr

    assert sorted(installed) == [("ruff", f"1.0.{n}") for n in (1, 2, 3)]
    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    assert stored["base"]["date"] == "2026-02-04"  # the index's date, not today's
    assert "adds `--fix`" not in result.stdout  # changelog was off
    assert "release warranted: yes" in result.stdout
    # every observation is a row in the run's own report — the audit trail
    assert sum(1 for row in result.results if row.task == "observe") == 3


def test_a_release_that_will_not_install_is_a_hole_not_a_dead_walk(
    tmp_path, monkeypatch
):
    """The break-on-hole rule is gone: the releases beyond a failed install
    are still observed, the hole is named in the report, and a later run
    fills it through `insert` — at which point its changes are attributed
    exactly.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n + 1}")
            for n in (3, 2, 1, 0)
        ]
    }
    surfaces = {
        ("ruff", "1.0.1"): None,  # will not install
        ("ruff", "1.0.2"): _with_flags("quiet", "fix"),
        ("ruff", "1.0.3"): _with_flags("quiet", "fix"),
    }
    _serve(monkeypatch, listings, surfaces)

    result = _tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    assert "holes in ruff: 1.0.1" in result.stdout

    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.3", "1.0.2", "1.0.0"]
    # the gap costs precision, not correctness: --fix reads as arriving at
    # the release actually read...
    spec = _toolhistory.union(stored, name="ruff")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["fix"] == "1.0.2"

    # ...and the next run fills the hole and sharpens the claim.
    surfaces[("ruff", "1.0.1")] = _with_flags("quiet", "fix")
    result = _tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    spec = _toolhistory.union(stored, name="ruff")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["fix"] == "1.0.1"


def test_a_refresh_with_nothing_new_warrants_no_release(tmp_path, monkeypatch):
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.3",
            date="2026-02-04",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {"ruff": [_toolfetch.Release(version="1.0.3", date="2026-02-04")]}
    installed = _serve(monkeypatch, listings, {})

    result = _tools_run("refresh --only=ruff")
    assert result.ok, result.stderr
    assert installed == []
    assert "release warranted: no" in result.stdout


def test_a_refresh_that_could_not_look_does_not_report_nothing_new(
    tmp_path, monkeypatch
):
    """The two answers a release job must never confuse: an index that would
    not answer exits 75 (EX_TEMPFAIL) and names the tool, instead of reading
    as a tool with nothing new.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.3",
            date="2026-02-04",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )

    def throttled(_driver):
        raise _toolfetch.Unreachable("https://pypi.org/pypi/ruff/json", "429")

    monkeypatch.setattr(_toolfetch, "releases", throttled)

    result = _tools_run("refresh --only=ruff")
    assert result.exit_code == 75
    assert "ruff" in result.stderr


def test_a_refresh_writes_its_own_events_into_the_changelog(tmp_path, monkeypatch):
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version="1.0.1", date="2026-02-02"),
            _toolfetch.Release(version="1.0.0", date="2026-02-01"),
        ]
    }
    _serve(monkeypatch, listings, {("ruff", "1.0.1"): _with_flags("quiet", "fix")})

    result = _tools_run("refresh --only=ruff")
    assert result.ok, result.stderr
    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "adds `--fix`" in written


def test_the_pre_pass_answers_without_installing_anything(tmp_path, monkeypatch):
    """A gather provisions every tool and then discovers there is nothing
    to observe, which is most weeks. Listing is network and nothing else,
    so the question can be answered before the work is prepared for.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version="1.0.1", date="2026-02-02"),
            _toolfetch.Release(version="1.0.0", date="2026-02-01"),
        ]
    }
    installed = _serve(monkeypatch, listings, {})

    result = _tools_run("owed --only=ruff")
    assert result.ok, result.stderr
    answer = result.results[0].returned
    assert answer.releases == {"ruff": ["1.0.1"]}
    assert answer.total == 1
    assert installed == []  # nothing fetched to find that out
    assert "owed: 1" in result.stdout


def test_an_unreadable_index_is_not_nothing_to_do(tmp_path, monkeypatch):
    """The distinction the release gate turns on. A walk that cannot see an
    index cannot say the index has nothing new, so the caller is told
    separately rather than reading a total of zero as "all quiet".
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )

    def blocked(driver):
        raise _toolfetch.Unreachable("the index", "429")

    monkeypatch.setattr(_toolfetch, "releases", blocked)
    result = _tools_run("owed --only=ruff")
    assert result.ok, result.stderr
    answer = result.results[0].returned
    assert answer.total == 0
    assert "ruff" in answer.unreachable
    assert "unreachable: ruff" in result.stdout


def test_a_backfill_is_recorded_but_never_announced(tmp_path, monkeypatch):
    """A walk that reaches backwards changes the surface at every step it
    takes, and every one of those steps is a change the tool made years
    ago. Filling git's history announced that 2.44.0 "adds `--no-checkout`"
    as though it had happened that week.

    The releases are still read, still folded, still stubbed — a changelog
    reports a release nobody had seen before, not one footman had not got
    around to reading.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.3",
            date="2026-02-04",
            surface=_with_flags("quiet", "fix"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n + 1}")
            for n in (3, 2, 1, 0)
        ]
    }
    # Each older release really does differ — the backfill is not a no-op.
    surfaces = {
        ("ruff", "1.0.2"): _with_flags("quiet", "fix"),
        ("ruff", "1.0.1"): _with_flags("quiet"),
        ("ruff", "1.0.0"): _with_flags("quiet"),
    }
    _serve(monkeypatch, listings, surfaces)

    out = tmp_path / "obs.json"
    assert _tools_run(["gather", "--only=ruff", "--count=3", f"--out={out}"]).ok
    result = _tools_run(["assemble", str(out)])
    assert result.ok, result.stderr

    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    assert "release warranted: no" in result.stdout
    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "`--fix`" not in written


def test_a_release_above_the_chain_is_still_announced_beside_a_backfill(
    tmp_path, monkeypatch
):
    """The rule is about direction, not about how much a run read: one run
    can do both, and only the release nobody had seen is news.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.2",
            date="2026-02-03",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n + 1}")
            for n in (3, 2, 1, 0)
        ]
    }
    surfaces = {
        ("ruff", "1.0.3"): _with_flags("quiet", "fix"),  # above: news
        ("ruff", "1.0.1"): _with_flags("quiet", "cache"),  # below: history
        ("ruff", "1.0.0"): _with_flags("quiet"),
    }
    _serve(monkeypatch, listings, surfaces)

    out = tmp_path / "obs.json"
    assert _tools_run(["gather", "--only=ruff", "--count=2", f"--out={out}"]).ok
    result = _tools_run(["assemble", str(out)])
    assert result.ok, result.stderr

    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "`--fix`" in written  # 1.0.3, newer than anything seen
    assert "`--cache`" not in written  # 1.0.1, filled in behind it


def test_a_refresh_with_no_events_writes_no_note(tmp_path, monkeypatch):
    """A new release that changed nothing is recorded — an empty delta — and
    warrants neither a release nor a line about one.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version="1.0.1", date="2026-02-02"),
            _toolfetch.Release(version="1.0.0", date="2026-02-01"),
        ]
    }
    _serve(monkeypatch, listings, {("ruff", "1.0.1"): _with_flags("quiet")})

    result = _tools_run("refresh --only=ruff")
    assert result.ok, result.stderr
    assert "release warranted: no" in result.stdout
    assert "###" not in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.1", "1.0.0"]  # observed, unchanged


def test_a_prime_reaches_below_the_floor_and_only_below_it(tmp_path, monkeypatch):
    """The backward walk: newer releases are the refresh's business, and a
    prime must never lift the head — only deepen the tail, up to its count.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.2",
            date="2026-02-03",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version=f"1.0.{n}", date=f"2026-02-0{n + 1}")
            for n in (4, 3, 2, 1, 0)
        ]
    }
    surfaces = {
        ("ruff", "1.0.0"): _with_flags("quiet"),
        ("ruff", "1.0.1"): _with_flags("quiet"),
    }
    installed = _serve(monkeypatch, listings, surfaces)

    result = _tools_run("prime --only=ruff --count=1")
    assert result.ok, result.stderr
    assert installed == [("ruff", "1.0.1")]  # one below the floor; never 1.0.3+

    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert stored["base"]["version"] == "1.0.2"  # the head did not move
    assert stored["observed_from"] == "1.0.1"


def test_a_floor_the_index_cannot_place_refuses_the_tool(tmp_path, monkeypatch):
    """A stub synced from an outdated binary leaves a floor no listing holds;
    priming from the top would file the newest release as the oldest.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="0.9.9",
            date="2026-01-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {"ruff": [_toolfetch.Release(version="1.0.3", date="2026-02-04")]}
    installed = _serve(monkeypatch, listings, {})

    result = _tools_run("prime --only=ruff")
    assert result.ok, result.stderr
    assert installed == []
    assert "sync it forward first" in result.stdout


def test_parallel_observations_each_own_their_environment(tmp_path, monkeypatch):
    """The reason each observation is a task: the PATH written around one
    extraction is that observation's alone. Two releases are held at the
    barrier until both are inside their extract, then each asserts it sees
    its own binary first on PATH and the sibling's nowhere.
    """
    import os
    import threading

    from livery.toolroom._machinery import _drivers, _toolfetch
    from livery.toolroom._machinery import _tasks as tools

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version="1.0.2", date="2026-02-03"),
            _toolfetch.Release(version="1.0.1", date="2026-02-02"),
            _toolfetch.Release(version="1.0.0", date="2026-02-01"),
        ]
    }
    surface = _with_flags("quiet", "fix")
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: list(listings[driver.key])
    )

    def install(driver, release, into):
        (into / "bin").mkdir(parents=True, exist_ok=True)
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)

    both_inside = threading.Barrier(2)
    seen: list[tuple[str, str]] = []

    def extract(driver):
        with contextlib.suppress(threading.BrokenBarrierError):
            both_inside.wait(timeout=10)  # overlap for real, or say so
        head = os.environ.get("PATH", "").split(os.pathsep)[0]
        seen.append((head, os.environ.get("PATH", "")))
        return _toolhistory.spec_from(surface, name=driver.name)

    monkeypatch.setattr(_drivers, "extract", extract)

    result = _tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    assert len(seen) == 2
    heads = {head for head, _ in seen}
    assert len(heads) == 2  # two different bindirs won the front of PATH
    for head, path in seen:
        other = next(h for h in heads if h != head)
        assert other not in path  # and the sibling's never leaked in


def test_the_same_release_is_observed_once_per_run(tmp_path, monkeypatch):
    """Observations are work-keyed by footman's futures layer: a second
    request for the same (tool, version) joins the first execution and is
    reported as a shared row, not re-installed.
    """
    from livery.footman.registry import Group
    from livery.footman.testing import Runner
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    installed: list[str] = []

    def install(driver, release, into):
        installed.append(release.version)
        (into / "bin").mkdir(parents=True, exist_ok=True)
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)
    from livery.toolroom._machinery import _drivers

    monkeypatch.setattr(
        _drivers,
        "extract",
        lambda driver: _toolhistory.spec_from(_with_flags("quiet"), name=driver.name),
    )

    demo = Group("demo")

    @demo.task
    def twice():
        first = tools.observe(
            tool="ruff", version="1.0.1", scratch=str(tmp_path / "scratch")
        )
        second = tools.observe(
            tool="ruff", version="1.0.1", scratch=str(tmp_path / "scratch")
        )
        assert first == second

    result = Runner().invoke("twice", tasks=demo)
    assert result.ok, result.stderr
    assert installed == ["1.0.1"]  # one execution; the second request joined it


def test_a_bare_call_is_refused_with_directions(tmp_path, monkeypatch):
    """No sequential twin: outside a run the isolation the gather leans on
    does not exist, so the walk refuses and says how to run it instead of
    degrading into the exact race it was built to remove.
    """
    from livery.footman.context import Failed
    from livery.toolroom._machinery import _tasks as tools

    _isolate(tools, monkeypatch, tmp_path)
    with pytest.raises(Failed, match=r"footman\.testing\.Runner"):
        tools.refresh(only="ruff")
    with pytest.raises(Failed, match=r"needs a run"):
        tools.prime(only="ruff")


# --- cross-platform observations ---------------------------------------------
#
# One observation comes from one platform. Observations merge; the store
# records only what a platform SAW (`absent` beside the surface, never inside
# it), and every standing claim — who lacks an option now, since, until — is
# derived at render time from those verdicts.


def _reading(*options, verb="", help_of=None):
    """One platform's surface for one release."""
    help_of = help_of or {}
    return _toolhistory.surface_of(
        _spec(
            verbs=(
                Verb(
                    name=verb,
                    options=tuple(
                        Option(n, (f"--{n}",), help=help_of.get(n, f"The {n}."))
                        for n in options
                    ),
                ),
            )
        )
    )


def test_folding_keeps_every_option_and_names_who_missed_it():
    """A matrix run folds before it touches the chain. Otherwise an option
    only Linux has would be inserted, dropped by macOS, resurrected by
    Windows — three real deltas for one release nobody changed.
    """
    surface, absent = _toolhistory.fold(
        {
            "Linux": _reading("quiet", "fork"),
            "macOS": _reading("quiet", "fork"),
            "Windows": _reading("quiet"),
        }
    )
    assert sorted(surface["verbs"][""]["options"]) == ["fork", "quiet"]
    assert absent == {"\tfork": ["Windows"]}  # the exception, and only it


def test_a_verb_missing_whole_is_said_once_not_per_option():
    """`docker compose` absent on a platform is one fact about the command,
    not forty about its flags — smaller, and what a reader wants.
    """
    surface, absent = _toolhistory.fold(
        {
            "Linux": {
                "help": "",
                "verbs": {"up": _reading("build", "detach", verb="up")["verbs"]["up"]},
            },
            "Windows": {"help": "", "verbs": {}},
        }
    )
    assert absent == {"up\t": ["Windows"]}
    assert sorted(surface["verbs"]["up"]["options"]) == ["build", "detach"]


def _described(text):
    """A one-verb surface whose tool description is *text*."""
    return {"help": text, "verbs": {"": {"help": text, "options": {}}}}


def test_a_re_read_can_say_a_tool_has_no_description():
    """`markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)` is a signature, not
    a description, and the tool has none to give. Settling the tool's own
    line on truthiness made that unsayable: an empty reading looked like a
    platform that had failed to look, so the banner survived every re-read
    and no better extractor could ever correct it.
    """
    doc = _toolhistory.new(
        "markdownlint",
        version="0.23.2",
        date="2026-01-01",
        surface=_described("markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)"),
        platforms=["Linux", "Windows", "macOS"],
    )
    # The platform whose words these are reads again and finds none.
    _toolhistory.merge(
        doc, version="0.23.2", surface=_described(""), platforms=["Linux"]
    )
    assert (_toolhistory.at(doc, "0.23.2") or {})["help"] == ""


def test_a_quieter_platform_still_cannot_erase_a_description():
    """The other half of the same rule, and the reason truthiness was there:
    a platform that reads nothing must not blank a line another platform
    read properly.
    """
    doc = _toolhistory.new(
        "demo",
        version="1.0.0",
        date="2026-01-01",
        surface=_described("A real description"),
        platforms=["Linux"],
    )
    _toolhistory.merge(
        doc, version="1.0.0", surface=_described(""), platforms=["macOS"]
    )
    assert (_toolhistory.at(doc, "1.0.0") or {})["help"] == "A real description"


def test_a_better_reading_replaces_a_signature_with_the_real_line():
    """git-cliff opens `git-cliff 2.13.1` and describes itself on the next
    line. The extractor that learned to skip the signature must be able to
    put the real description in its place.
    """
    doc = _toolhistory.new(
        "git_cliff",
        version="2.13.1",
        date="2026-01-01",
        surface=_described("git-cliff 2.13.1"),
        platforms=["Linux"],
    )
    _toolhistory.merge(
        doc,
        version="2.13.1",
        surface=_described("A highly customizable changelog generator"),
        platforms=["Linux"],
    )
    assert (_toolhistory.at(doc, "2.13.1") or {})["help"] == (
        "A highly customizable changelog generator"
    )


def test_a_merge_widening_coverage_costs_the_chain_nothing():
    """The sidecar never enters a delta, so a platform looking for the first
    time cannot make the tool look like it changed — no recompute, no event,
    no changelog line.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet", "fork"),
        platforms=["macOS"],
    )
    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=_reading("quiet"),
        platforms=["macOS"],
    )
    before = json.dumps(doc["deltas"], sort_keys=True)

    moved = _toolhistory.merge(
        doc,
        version="2.0.0",
        surface=_reading("quiet"),  # Windows lacks --fork
        platforms=["Windows"],
    )
    assert moved is False  # the surface did not change; nothing to recompute
    assert json.dumps(doc["deltas"], sort_keys=True) == before
    assert doc["base"]["platforms"] == ["Windows", "macOS"]
    assert doc["base"]["absent"] == {"\tfork": ["Windows"]}
    assert "absent" not in json.dumps(doc["deltas"])  # never in a delta


def test_a_merge_bringing_an_option_records_who_had_looked_without_it():
    """An option only the newcomer sees was, by construction, missing for
    everyone who looked before — that is an observed absence, and it is the
    only reason the store may tag them.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet"),
        platforms=["macOS"],
    )
    _toolhistory.merge(
        doc,
        version="2.0.0",
        surface=_reading("quiet", "winonly"),
        platforms=["Windows"],
    )
    assert doc["base"]["absent"] == {"\twinonly": ["macOS"]}
    assert "winonly" in doc["base"]["surface"]["verbs"][""]["options"]
    # ...and never the merging platform itself, which is what saw it.
    assert "Windows" not in json.dumps(doc["base"]["absent"])


def test_the_store_records_only_absences_that_were_observed():
    """The invariant the whole design rests on: `absent ⊆ platforms`. A
    claim about a platform that never looked is derived at render time,
    where a later sighting revises it — never written down, where it would
    harden into a fact nobody rechecks.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet", "fork"),
        platforms=["macOS"],
    )
    _toolhistory.merge(
        doc, version="2.0.0", surface=_reading("quiet"), platforms=["Windows"]
    )
    for version in _toolhistory.observed(doc):
        entry = _toolhistory.entry_of(doc, version) or {}
        looked = set(entry.get("platforms", []))
        for key, who in entry.get("absent", {}).items():
            assert set(who) <= looked, f"{version} {key} claims an unobserved absence"


def test_a_sighting_on_a_platform_clears_its_standing_absence():
    """Nothing means cross-platform. Windows lacked `--fork` at 1.0.0 and
    has it at 2.0.0, so the claim is dropped — derived from the newest
    verdict rather than chased back through the chain.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet", "fork"),
        platforms=["Windows", "macOS"],
    )
    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=_reading("quiet", "fork"),
        platforms=["Windows", "macOS"],
    )
    doc["deltas"]["1.0.0"]["absent"] = {"\tfork": ["Windows"]}  # the old verdict

    options = {
        o.name: o for v in _toolhistory.union(doc, name="demo").verbs for o in v.options
    }
    assert options["fork"].not_on == ()  # the newer sighting wins
    assert options["quiet"].not_on == ()


def test_an_absence_stands_until_that_platform_looks_again():
    """A Linux-only week can neither set nor clear a Windows claim: the
    verdict is per platform, and silence is not evidence.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet", "fork"),
        platforms=["Linux"],  # only Linux looked at the newer release
    )
    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=_reading("quiet", "fork"),
        platforms=["Linux", "Windows"],
    )
    doc["deltas"]["1.0.0"]["absent"] = {"\tfork": ["Windows"]}

    options = {
        o.name: o for v in _toolhistory.union(doc, name="demo").verbs for o in v.options
    }
    assert options["fork"].not_on == ("Windows",)  # still standing, still honest


def test_a_platforms_own_floor_is_not_a_since():
    """An option first seen where only one platform's coverage reaches was
    not "added" there — the older releases were never read on that platform.
    The chain's floor rule, one level down.
    """
    doc = _toolhistory.new(
        "demo",
        version="2.0.0",
        date="2026-01-02",
        surface=_reading("quiet", "winonly"),
        platforms=["Windows", "macOS"],
    )
    doc["base"]["absent"] = {"\twinonly": ["macOS"]}
    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-01-01",
        surface=_reading("quiet"),
        platforms=["macOS"],  # Windows never read this far back
    )

    options = {
        o.name: o for v in _toolhistory.union(doc, name="demo").verbs for o in v.options
    }
    assert options["winonly"].since == ""  # Windows' floor is 2.0.0, not a since
    assert options["winonly"].not_on == ("macOS",)


def test_divergent_words_settle_the_same_whichever_leg_arrives_first():
    """One copy of the text is stored, so the pick must not depend on merge
    order — or two legs would flip a divergent help string every week, each
    flip a delta in a store whose question is "did anything change".
    """
    words = {"Linux": {"quiet": "Hush, penguin."}, "Windows": {"quiet": "Hush, PC."}}

    def built(order):
        doc = _toolhistory.new(
            "demo",
            version="1.0.0",
            date="2026-01-01",
            surface=_reading("quiet", help_of=words[order[0]]),
            platforms=[order[0]],
        )
        _toolhistory.merge(
            doc,
            version="1.0.0",
            surface=_reading("quiet", help_of=words[order[1]]),
            platforms=[order[1]],
        )
        return doc["base"]["surface"]["verbs"][""]["options"]["quiet"]["help"]

    assert built(("Linux", "Windows")) == built(("Windows", "Linux"))
    assert built(("Linux", "Windows")) == "Hush, penguin."  # priority, not order


def test_a_merged_surface_change_recomputes_exactly_the_two_entries_that_saw_it():
    """A merge that genuinely widens the surface is local, like a midfill:
    the entry itself and the one below reference that surface, and nothing
    else in the chain does.
    """
    doc = _toolhistory.new(
        "demo", version="3.0.0", date="2026-01-03", surface=_reading("quiet")
    )
    for version, surface in (
        ("2.0.0", _reading("quiet")),
        ("1.0.0", _reading("quiet")),
    ):
        _toolhistory.extend(
            doc,
            version=version,
            date="2026-01-01",
            surface=surface,
            platforms=["macOS"],
        )
    doc["base"]["platforms"] = ["macOS"]
    untouched = {v: json.dumps(d, sort_keys=True) for v, d in doc["deltas"].items()}

    moved = _toolhistory.merge(
        doc,
        version="3.0.0",
        surface=_reading("quiet", "winonly"),
        platforms=["Windows"],
    )
    assert moved is True  # the surface grew, so the chain must be told
    doc["deltas"]["2.0.0"] = {
        **doc["deltas"]["2.0.0"],
        **_toolhistory.delta(
            doc["base"]["surface"], _toolhistory.at(doc, "2.0.0") or {}
        ),
    }
    assert json.dumps(doc["deltas"]["1.0.0"], sort_keys=True) == untouched["1.0.0"]
    for version in _toolhistory.observed(doc):
        assert _toolhistory.at(doc, version) is not None, version


def _elsewhere() -> str:
    """A platform name that is never the one running the suite.

    The tests run on all three, so "a platform that has not looked" cannot
    be spelled with a literal — on the Linux runner, `"Linux"` is the host.
    """
    from livery.toolroom._machinery import _tasks as tools

    return next(p for p in ("Linux", "Windows", "macOS") if p != tools._platform())


def test_gather_writes_a_document_another_machine_can_fold(tmp_path, monkeypatch):
    """The two halves are split because a Linux box cannot tell you what a
    tool's `--help` says on Windows. So the observation travels as a
    self-describing document — copied off that machine by hand if that is
    how the week goes — and the assembler folds it wherever the store is.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=[tools._platform()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {
        "ruff": [
            _toolfetch.Release(version="1.0.1", date="2026-02-02"),
            _toolfetch.Release(version="1.0.0", date="2026-02-01"),
        ]
    }
    _serve(monkeypatch, listings, {("ruff", "1.0.1"): _with_flags("quiet", "fix")})

    out = tmp_path / "obs.json"
    result = _tools_run(["gather", "--only=ruff", f"--out={out}"])
    assert result.ok, result.stderr

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema"] == tools.OBSERVATION_SCHEMA
    assert document["platform"] == tools._platform()
    assert list(document["observations"]["ruff"]) == ["1.0.1"]
    # the store is untouched: gathering writes nothing but the document
    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.0"]

    result = _tools_run(["assemble", str(out), "--no-changelog"])
    assert result.ok, result.stderr
    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert _toolhistory.observed(stored) == ["1.0.1", "1.0.0"]


def test_a_correction_below_the_head_is_saved_and_stamped():
    """A better extractor corrects a *delta*, and the fold must notice.

    Rewriting a delta replaces its entry; only the base is rewritten in
    place. The fold used to decide whether anything moved by comparing the
    entry object it had taken a reference to before merging — stale exactly
    when the merge did the most work — so every correction below the head
    was computed, believed, and dropped without ever being written. ssh
    kept a rendering of its manual header as the description of every
    release but its newest, through a walk that had just read all 84 of
    them correctly.

    The stamp is the other half: a release read by today's extractor has to
    say so, or `_plan_gather` offers it again every run and the walk that
    heals the store never records that it healed it.
    """
    from livery.toolroom._machinery import _tasks as tools

    def surface(text):
        return {"help": text, "verbs": {"": {"help": text, "options": {}}}}

    doc = _toolhistory.new(
        "ssh",
        version="2.0",
        date="2026-02-01",
        surface=surface("OpenSSH remote login client"),
        platforms=["Linux"],
    )
    _toolhistory.extend(
        doc,
        version="1.0",
        date="2026-01-01",
        surface=surface("SSH(1)      General Commands Manual      SSH(1)"),
        platforms=["Linux"],
    )
    # Pretend that older reading came from the previous generation.
    doc["deltas"]["1.0"]["extractor"] = _toolhistory.EXTRACTOR - 1
    assert (_toolhistory.at(doc, "1.0") or {})["help"].startswith("SSH(1)")

    fresh, touched = tools._fold_into(
        doc,
        "ssh",
        {"1.0": {"Linux": surface("OpenSSH remote login client")}},
        {"1.0": {"date": "2026-01-01", "tag": ""}},
    )
    assert fresh == [], "correcting a release the chain has is not a new release"
    assert touched, "a correction below the head must be saved"
    assert (_toolhistory.at(doc, "1.0") or {})["help"] == "OpenSSH remote login client"
    assert doc["deltas"]["1.0"]["extractor"] == _toolhistory.EXTRACTOR


def test_two_platforms_fold_into_one_release_with_the_exception_named(
    tmp_path, monkeypatch
):
    """The whole point: one release, two witnesses, one record — and the
    option only one of them has is the exception the store keeps.
    """
    from livery.toolroom._machinery import _tasks as tools

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet"),
            platforms=["Linux", "Windows"],
        ),
        tmp_path / "history" / "ruff.json",
    )

    def document(platform, *options):
        path = tmp_path / f"obs-{platform}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": tools.OBSERVATION_SCHEMA,
                    "platform": platform,
                    "observations": {
                        "ruff": {
                            "1.0.1": {
                                "date": "2026-02-02",
                                "tag": "",
                                "surface": _with_flags(*options),
                            }
                        }
                    },
                    "holes": {},
                    "unreachable": {},
                    "skipped": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    linux = document("Linux", "quiet", "fork")
    windows = document("Windows", "quiet")

    result = _tools_run(["assemble", str(linux), str(windows), "--no-changelog"])
    assert result.ok, result.stderr

    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    entry = _toolhistory.entry_of(stored, "1.0.1")
    assert entry is not None
    assert entry["platforms"] == ["Linux", "Windows"]
    assert entry["absent"] == {"\tfork": ["Windows"]}
    # folded once: the option is in the surface, and no drop/resurrect churn
    assert "fork" in entry["surface"]["verbs"][""]["options"]
    assert "absent" not in json.dumps(stored["deltas"])

    spec = _toolhistory.union(stored, name="ruff")
    options = {o.name: o for v in spec.verbs for o in v.options}
    assert options["fork"].not_on == ("Windows",)
    assert options["quiet"].not_on == ()


def test_a_platform_new_to_a_tool_backfills_the_version_people_run(
    tmp_path, monkeypatch
):
    """A platform that has never looked at a tool starts with the base —
    otherwise its coverage would begin at whatever ships next, and the
    version everyone is actually running would stay unaccounted for.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    _isolate(tools, monkeypatch, tmp_path)
    _toolhistory.save(
        _toolhistory.new(
            "ruff",
            version="1.0.0",
            date="2026-02-01",
            surface=_with_flags("quiet", "fork"),
            # Somebody, and never this machine — the test is about a
            # platform's first look, so the fixture must not accidentally
            # name the runner it happens to be running on.
            platforms=[_elsewhere()],
        ),
        tmp_path / "history" / "ruff.json",
    )
    listings = {"ruff": [_toolfetch.Release(version="1.0.0", date="2026-02-01")]}
    installed = _serve(monkeypatch, listings, {("ruff", "1.0.0"): _with_flags("quiet")})

    result = _tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    assert installed == [("ruff", "1.0.0")]  # the base, backfilled

    stored = _toolhistory.load(tmp_path / "history" / "ruff.json")
    assert stored is not None
    assert stored["base"]["platforms"] == sorted([_elsewhere(), tools._platform()])
    assert stored["base"]["absent"] == {"\tfork": [tools._platform()]}
    assert "release warranted: no" in result.stdout  # coverage is not an event


# --- rolling a release the way the runbook does ------------------------------


def _repo(tmp_path, version="1.2.3", entries=("- **A thing.** It happened.",)):
    """A miniature checkout: the two files that must agree, and the README
    whose minor-pin example follows them.
    """
    (tmp_path / "src" / "toolroom").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "toolroom"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "src" / "toolroom" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "Pin the minor (`toolroom~={}.{}.0`) if you build on it.\n".format(
            *version.split(".")[:2]
        ),
        encoding="utf-8",
    )
    repo = "https://github.com/willemkokke/toolroom"
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n"
        + "\n".join(entries)
        + f"\n\n## [{version}] — 2026-07-01\n\n- Older.\n\n"
        f"[Unreleased]: {repo}/compare/v{version}...HEAD\n",
        encoding="utf-8",
    )
    return tmp_path


def test_a_stub_only_release_is_a_patch_and_moves_what_must_agree(
    tmp_path, monkeypatch
):
    """The tools moved, toolroom did not. Two files must agree or the
    release workflow refuses the tag.
    """
    from livery.toolroom._machinery import _tasks as tools

    root = _repo(tmp_path)
    monkeypatch.setattr(tools, "_HISTORY", root / "tool-history")

    prepared = tools.prepare_release()
    assert (prepared.previous, prepared.version, prepared.entries) == (
        "1.2.3",
        "1.2.4",
        1,
    )
    assert 'version = "1.2.4"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "1.2.4"' in (
        root / "src" / "toolroom" / "__init__.py"
    ).read_text(encoding="utf-8")
    # The README's pin already admits a patch: ~=1.2.0 covers 1.2.4, so a
    # patch bump leaves the advice exactly as it was.
    assert "toolroom~=1.2.0" in (root / "README.md").read_text(encoding="utf-8")


def test_a_minor_release_rolls_the_readme_pin(tmp_path, monkeypatch):
    """The beta note's pin example names the released minor; a minor bump
    is the one move that dates it, so the roll rewrites it in the same
    breath as the two version files.
    """
    from livery.toolroom._machinery import _tasks as tools

    root = _repo(tmp_path)
    monkeypatch.setattr(tools, "_HISTORY", root / "tool-history")

    prepared = tools.prepare_release(bump="minor")
    assert (prepared.previous, prepared.version) == ("1.2.3", "1.3.0")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "toolroom~=1.3.0" in readme
    assert "toolroom~=1.2.0" not in readme


def test_rolling_the_changelog_dates_the_release_and_repoints_the_links(
    tmp_path, monkeypatch
):
    from livery.toolroom._machinery import _tasks as tools

    root = _repo(tmp_path)
    monkeypatch.setattr(tools, "_HISTORY", root / "tool-history")
    tools.prepare_release()

    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.2.4] — " in text
    assert "- **A thing.** It happened." in text.split("## [1.2.4]")[1]
    assert "## [Unreleased]" in text.split("## [1.2.4]")[0]  # kept, and empty
    assert "compare/v1.2.4...HEAD" in text
    assert (
        "[1.2.4]: https://github.com/willemkokke/toolroom/compare/v1.2.3...v1.2.4"
        in text
    )


def test_a_release_is_refused_when_there_is_nothing_to_release(tmp_path, monkeypatch):
    """A tag on an empty section is a release that says nothing — refused
    rather than cut, which is what stops an automatic path from shipping
    noise every week it finds none.
    """
    from livery.footman.context import Failed
    from livery.toolroom._machinery import _tasks as tools

    root = _repo(tmp_path, entries=())
    monkeypatch.setattr(tools, "_HISTORY", root / "tool-history")
    with pytest.raises(Failed, match=r"nothing under \[Unreleased\]"):
        tools.prepare_release()
    assert 'version = "1.2.3"' in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_the_walk_answers_to_node_when_only_bun_is_installed(tmp_path, monkeypatch):
    """The npm tier installs through bun, but what bun *installs* is a
    launcher beginning `#!/usr/bin/env node`.

    bun stands in for node when bun itself runs a script; the extractor
    spawns the launcher as a subprocess, where the shebang is resolved by the
    OS with bun nowhere in the chain. A Linux box without node therefore read
    twelve cspell releases and eleven markdownlint releases as
    `No such file or directory` — and a CI runner has no node either, so the
    weekly matrix would have lost those tools on every leg, forever.
    """
    import os
    import shutil

    from livery.toolroom._machinery import _tasks as tools

    fake_bun = tmp_path / "bun"
    fake_bun.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(
        shutil, "which", lambda name: str(fake_bun) if name == "bun" else None
    )

    with tools._sandboxed(tmp_path / "scratch"):
        head = os.environ["PATH"].split(os.pathsep)[0]
        assert head.endswith("shims")
        written = list((tmp_path / "scratch" / "shims").iterdir())
        assert [p.name for p in written] == ["node.cmd" if tools._windows() else "node"]
        assert str(fake_bun) in written[0].read_text(encoding="utf-8")
        assert "--bun" in written[0].read_text(encoding="utf-8")

    # ...and it goes when the walk does — it lives inside scratch.
    assert os.environ["PATH"].split(os.pathsep)[0] != head


def test_a_machine_with_real_node_is_left_alone(tmp_path, monkeypatch):
    """A shim is for a gap, not a preference: where node exists, the tools
    run under the runtime they were published for.
    """
    import os
    import shutil

    from livery.toolroom._machinery import _tasks as tools

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    before = os.environ["PATH"]
    with tools._sandboxed(tmp_path / "scratch"):
        assert os.environ["PATH"] == before
        assert not (tmp_path / "scratch" / "shims").exists()


def test_no_bun_and_no_node_is_no_worse_than_before(tmp_path, monkeypatch):
    """Nothing to forward to, so nothing is written — the tier reports its
    holes as it would have anyway.
    """
    import os
    import shutil

    from livery.toolroom._machinery import _tasks as tools

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    before = os.environ["PATH"]
    with tools._sandboxed(tmp_path / "scratch"):
        assert os.environ["PATH"] == before


def test_a_platform_folding_into_an_older_release_agrees_with_what_is_stored():
    """The case a base-only test cannot reach, and the one a real matrix hits
    on its first run: every release below the newest is stored as a *step*,
    not a surface.

    Read that step as though it were a surface and every option the arriving
    platform saw looks like one nobody ever found — a first Linux fold tagged
    25,802 options as missing on macOS, across a store macOS itself had
    written. Two platforms that agree must record agreement: coverage widens,
    the sidecar stays empty, and the chain does not move.
    """
    surfaces = {
        "1.0.2": _with_flags("quiet", "fix"),
        "1.0.1": _with_flags("quiet", "fix"),
        "1.0.0": _with_flags("quiet"),
    }
    doc = _toolhistory.new(
        "ruff",
        version="1.0.2",
        date="2026-02-03",
        surface=surfaces["1.0.2"],
        platforms=["macOS"],
    )
    for version in ("1.0.1", "1.0.0"):
        _toolhistory.extend(
            doc,
            version=version,
            date="2026-02-01",
            surface=surfaces[version],
            platforms=["macOS"],
        )

    def steps(chain: dict[str, Any]) -> str:
        """The payloads alone — `platforms` is meant to widen."""
        return json.dumps(
            {
                v: {k: e[k] for k in e if k not in ("platforms", "absent")}
                for v, e in chain.items()
            },
            sort_keys=True,
        )

    before = steps(doc["deltas"])

    for version, surface in surfaces.items():  # Linux reads the same thing
        assert not _toolhistory.merge(
            doc, version=version, surface=surface, platforms=["Linux"]
        )

    assert steps(doc["deltas"]) == before  # the chain itself did not move
    for version, _expected in surfaces.items():
        entry = _toolhistory.entry_of(doc, version)
        assert entry is not None
        assert entry["platforms"] == ["Linux", "macOS"]
        assert not entry.get("absent")  # agreement is not an exception
        assert _toolhistory.at(doc, version) == surfaces[version]  # replay exact


def test_a_divergence_below_the_newest_release_restitches_two_entries():
    """A merge that genuinely widens an older release rewrites the two steps
    that describe it — the one keyed at it and the one below — and nothing
    else, however long the chain.
    """
    surfaces = {f"1.0.{n}": _with_flags("quiet") for n in range(5)}
    doc = _toolhistory.new(
        "ruff",
        version="1.0.4",
        date="2026-02-05",
        surface=surfaces["1.0.4"],
        platforms=["macOS"],
    )
    for n in (3, 2, 1, 0):
        _toolhistory.extend(
            doc,
            version=f"1.0.{n}",
            date=f"2026-02-0{n + 1}",
            surface=surfaces[f"1.0.{n}"],
            platforms=["macOS"],
        )
    untouched = {v: json.dumps(e, sort_keys=True) for v, e in doc["deltas"].items()}

    # Linux sees an extra flag at 1.0.2 — a real divergence, mid-chain.
    assert _toolhistory.merge(
        doc,
        version="1.0.2",
        surface=_with_flags("quiet", "linuxonly"),
        platforms=["Linux"],
    )

    moved = [
        v
        for v, was in untouched.items()
        if json.dumps(doc["deltas"][v], sort_keys=True) != was
    ]
    assert sorted(moved) == ["1.0.1", "1.0.2"]  # the step to it, and the step from it
    assert doc["deltas"]["1.0.2"]["absent"] == {"\tlinuxonly": ["macOS"]}
    for version, _expected in surfaces.items():
        replayed = _toolhistory.at(doc, version)
        assert replayed is not None
        names = {o for v in replayed["verbs"].values() for o in v["options"]}
        assert names == ({"quiet", "linuxonly"} if version == "1.0.2" else {"quiet"})


@pytest.mark.parametrize(
    ("help_text", "options", "verdict"),
    [
        ("Lint your spelling.", ("fix",), True),
        ("/usr/bin/env: 'node': No such file or directory", (), False),
        ("/usr/bin/env: 'node': No such file or directory", ("fix",), False),
        ("cspell: command not found", (), False),
        ("'foo' is not recognized as an internal or external command", (), False),
        ("Lint your spelling.", (), False),
    ],
)
def test_a_reading_must_describe_a_tool_to_count_as_one(help_text, options, verdict):
    """A launcher that cannot find its interpreter still prints prose and
    exits, and the extractor will faithfully turn that prose into a surface.

    The Linux box had no `node`; every npm-tier release read as one bare verb
    with no options and help text saying so. Stored, that claims the tool
    accepts nothing — which folds as 855 options "missing on Linux" for a
    tool that never ran. An observation has to be a description, not merely
    output.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery._toolspec import ToolSpec, Verb

    spec = ToolSpec(
        name="cspell",
        help=help_text,
        verbs=(
            Verb(
                name="",
                help=help_text,
                options=tuple(Option(o, (f"--{o}",)) for o in options),
            ),
        ),
    )
    assert tools._describes_itself(spec) is verdict


# --- a run must not report success while observing nothing --------------------


def _gathered(observed: int, missed: int, **over):
    from livery.toolroom._machinery._tasks import Gathered

    return Gathered(
        platform="Linux",
        observations={"ruff": {f"1.0.{n}": {} for n in range(observed)}},
        holes={"ruff": [f"9.0.{n}" for n in range(missed)]} if missed else {},
        **over,
    )


def test_a_run_whose_holes_outnumber_its_readings_fails(capsys):
    """`wrote obs-linux.json — 33 observations` was the line a *complete* run
    printed too, and the exit status was 0 with 330 of 363 releases unread.

    That is worse than failing: the document looks foldable, and folding it
    records a platform where the tools do not exist. Holes in the majority
    mean the machine, not the tools.
    """
    from livery.footman.context import Failed
    from livery.toolroom._machinery import _tasks as tools

    with pytest.raises(Failed) as failed:
        tools._report_gather(_gathered(observed=33, missed=330))
    assert failed.value.code == 75  # EX_TEMPFAIL: look again
    assert "picture of the machine" in failed.value.reason
    assert "33 observed, 330 holes" in capsys.readouterr().out


def test_an_ordinary_hole_is_not_a_failure(capsys):
    """One release whose asset has gone is ordinary. Failing on it would
    teach a weekly job's readers to ignore the exit code, which is the only
    way the majority case can go unnoticed.
    """
    from livery.toolroom._machinery import _tasks as tools

    tools._report_gather(_gathered(observed=300, missed=2))  # no raise
    out = capsys.readouterr().out
    assert "300 observed, 2 holes" in out
    assert "holes in ruff:" in out


def test_the_counts_are_stated_together(capsys):
    """Both numbers on one line: a truncated read of a long log still shows
    what was missed beside what was found.
    """
    from livery.toolroom._machinery import _tasks as tools

    tools._report_gather(_gathered(observed=5, missed=0))
    assert "Linux: 5 observed, 0 holes" in capsys.readouterr().out


def test_a_disk_with_no_room_stops_the_walk_instead_of_recording_holes(
    tmp_path, monkeypatch
):
    """A hole says *this release* could not be had. A full disk says nothing
    about any release, and every observation after it fails identically —
    recorded, that reads as a platform where the tools do not exist.

    The Linux box hit exactly this: a walk that exhausted the disk wrote 330
    holes, any one of which would have been folded as fact.
    """
    import shutil

    from livery.footman.context import Failed
    from livery.toolroom._machinery import _tasks as tools

    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(1, 1, 4 * 1024 * 1024))
    with pytest.raises(Failed) as failed:
        tools._refuse_a_broken_environment(tmp_path)
    assert failed.value.code == 75
    assert "4 MB free" in failed.value.reason

    # ...and room enough is simply a hole, as before.
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(1, 1, 40 * 1024**3))
    tools._refuse_a_broken_environment(tmp_path)


def test_a_skip_only_some_legs_reported_says_which():
    """Unattributed, one leg's skip reads as every leg's.

    Windows has no `man`, so it alone skips the tools read from their
    manuals — and `git (no man to read the pages with)` in a refresh PR
    then looks exactly like a tier nobody refreshes, when git's pages had
    been read on both Linux and macOS. A skip every leg reported needs no
    attribution: it is a fact about the tool, not about a box.
    """
    from livery.toolroom._machinery._tasks import _attributed

    lines = _attributed(
        {
            "bash (hand-written)": ["Linux", "macOS", "Windows"],
            "git (no man to read the pages with)": ["Windows"],
            "cspell (no bun to install with)": ["Windows", "macOS"],
        },
        legs=3,
    )
    assert "bash (hand-written)" in lines
    assert "git (no man to read the pages with) — Windows only" in lines
    assert "cspell (no bun to install with) — Windows and macOS only" in lines

    # One document has nothing to contrast with, so nothing is claimed.
    assert _attributed({"git (no man)": ["Windows"]}, legs=1) == ["git (no man)"]


def test_a_hand_written_stub_is_not_a_uv_tier_tool():
    """A `source="manual"` driver carries the *default* provision kind, so
    asking it names the `uv` tier for a shell nobody fetches — six of them in
    every document's skipped list.
    """
    from livery.toolroom._machinery import _toolfetch
    from livery.toolroom._machinery._tasks import _curated

    _, skipped = _curated("", _toolfetch)
    assert "bash (hand-written)" in skipped
    assert not any("uv tier" in line for line in skipped)


def test_a_reading_older_than_the_extractor_is_offered_again(monkeypatch):
    """`EXTRACTOR` was recorded against every observation from the start, and
    nothing ever read it — so an extractor that learned to see more had no
    way to say so.

    Three twine releases sat in the store with no options at all, recorded
    when the tool died before argparse ran under today's dependencies. The
    only thing that noticed was another platform reading them correctly and
    appearing to disagree, which is a divergence report for a bug. A reading
    is only as good as the extractor that took it.
    """
    from livery.toolroom._machinery import _tasks as tools
    from livery.toolroom._machinery import _toolfetch

    surface = _with_flags("quiet")
    doc = _toolhistory.new(
        "ruff",
        version="1.0.1",
        date="2026-02-02",
        surface=surface,
        platforms=[tools._platform()],
    )
    _toolhistory.extend(
        doc,
        version="1.0.0",
        date="2026-02-01",
        surface=surface,
        platforms=[tools._platform()],
    )
    listing = [_toolfetch.Release(version=v) for v in ("1.0.1", "1.0.0")]

    # Current generation, this platform has read both: nothing owed.
    assert tools._plan_gather(doc, listing, 0) == []

    # The extractor moves on, and both readings are owed again.
    monkeypatch.setattr(_toolhistory, "EXTRACTOR", _toolhistory.EXTRACTOR + 1)
    offered = [r.version for r in tools._plan_gather(doc, listing, 0)]
    assert offered == ["1.0.1", "1.0.0"]


def test_a_re_read_clears_a_claim_the_older_extractor_caused(monkeypatch):
    """The self-healing the mechanism is for: one platform's blind reading
    made the other look like a divergence, and reading it again with the
    better extractor settles it — no hand-editing of the store, which is the
    one thing a record of observations must never need.
    """
    from livery.toolroom._machinery import _tasks as tools

    here = tools._platform()
    blind = _toolhistory.surface_of(_spec(verbs=(Verb(name="", options=()),)))
    real = _with_flags("quiet", "fix")

    doc = _toolhistory.new(
        "twine", version="5.1.0", date="2026-02-01", surface=blind, platforms=[here]
    )
    # Another platform reads it properly: the options arrive tagged absent here.
    _toolhistory.merge(doc, version="5.1.0", surface=real, platforms=["Elsewhere"])
    assert doc["base"]["absent"], "the blind reading should read as an absence"
    assert here in next(iter(doc["base"]["absent"].values()))

    # This platform reads it again, now seeing what was always there.
    _toolhistory.merge(doc, version="5.1.0", surface=real, platforms=[here])
    assert not doc["base"].get("absent")  # the claim is withdrawn by evidence
    assert sorted(doc["base"]["platforms"]) == sorted([here, "Elsewhere"])


def test_bin_on_path_overlays_the_directory_itself(tmp_path):
    """A Windows venv keeps binaries in `Scripts` and uv's interpreter store
    keeps `python.exe` at the store root — the observation must overlay the
    directory the install actually returned, never a rebuilt `<parent>/bin`.
    """
    import os

    from livery.toolroom._machinery._tasks import _bin_on_path

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    with _bin_on_path(scripts):
        assert os.environ["PATH"].split(os.pathsep)[0] == str(scripts)


def test_observe_rejects_a_reading_of_the_wrong_binary(tmp_path, monkeypatch):
    """The help-path twin of `_from_click`'s guard. With the release's own
    directory missing from `PATH`, the extractor resolves some ambient
    binary and faithfully describes it under this release's label — on
    Windows a whole platform's uv tier read as one tool, no holes to show
    for it. A reading that names a different version must be a hole.
    """
    from livery.toolroom._machinery import _tasks as tools_tasks

    bindir = tmp_path / "release" / "bin"

    def fake_install(driver, release, into):
        bindir.mkdir(parents=True, exist_ok=True)
        return bindir

    monkeypatch.setattr("livery.toolroom._machinery._toolfetch.install", fake_install)
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: _spec(version="0.99.9")
    )
    assert tools_tasks.observe("ruff", "0.15.0", scratch=str(tmp_path)) is None

    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: _spec(version="0.15.0")
    )
    assert tools_tasks.observe("ruff", "0.15.0", scratch=str(tmp_path)) is not None


def test_npm_install_spawns_the_resolved_bun(tmp_path, monkeypatch):
    """`which` sees the task router's PATH overlay; Windows CreateProcess
    does not — its executable search reads the real process environment. So
    a bare `["bun", ...]` that `which` just found still failed to spawn, and
    every npm-tier release on the platform read as a hole. The spawn must
    use the path `which` resolved.
    """
    from livery.toolroom._machinery import _drivers, _toolfetch

    fake = tmp_path / "bun.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda name: str(fake))

    def fake_run(argv, env=None):
        calls.append(argv)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    driver = _drivers.find("cspell")
    assert driver is not None
    out = _toolfetch._install_npm(driver, "9.8.0", tmp_path / "into")
    assert out == tmp_path / "into" / "bin"
    assert calls[0][0] == str(fake)
    assert calls[0][1:] == ["add", "--global", "cspell@9.8.0"]


def test_observe_accepts_a_repack_wheel_version(tmp_path, monkeypatch):
    """PyPI's ninja 1.11.1.4 wraps a binary that answers `1.11.1` — the
    repack's own trailing component must not read as a wrong binary. Only a
    dotted prefix though, and never the reverse: a binary reporting *more*
    components than its release is some other binary.
    """
    from livery.toolroom._machinery import _tasks as tools_tasks

    bindir = tmp_path / "release" / "bin"

    def fake_install(driver, release, into):
        bindir.mkdir(parents=True, exist_ok=True)
        return bindir

    monkeypatch.setattr("livery.toolroom._machinery._toolfetch.install", fake_install)
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: _spec(version="1.11.1")
    )
    assert tools_tasks.observe("ninja", "1.11.1.4", scratch=str(tmp_path)) is not None
    assert tools_tasks.observe("ninja", "1.11.14", scratch=str(tmp_path)) is None
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: _spec(version="1.11.1.4")
    )
    assert tools_tasks.observe("ninja", "1.11.1", scratch=str(tmp_path)) is None


def test_python_find_ignores_the_cwd_project(monkeypatch, tmp_path):
    """`uv python find` consults the nearest pyproject, and the walk runs
    inside footman's own checkout — whose `requires-python` has opinions.
    `--no-project` keeps the answer about the version that was asked.
    """
    from livery.toolroom._machinery import _toolfetch

    calls: list[list[str]] = []

    def capture(argv, env=None):
        calls.append(argv)
        return ""

    monkeypatch.setattr(_toolfetch, "_run", lambda argv, env=None: True)
    monkeypatch.setattr(_toolfetch, "_capture", capture)
    assert _toolfetch._install_python("3.10.0", tmp_path) is None
    assert calls == [["uv", "python", "find", "--no-project", "3.10.0"]]


def test_python_installs_into_a_private_store(monkeypatch, tmp_path):
    """A shared uv store is one lock, and the walk is ten concurrent
    installs: uv queues the waiters, the walk's subprocess timeouts kill
    the queue, and every run scattered a different third of the python
    chain into holes. Each release installs into a store inside its own
    throwaway directory — discarded with the release, contended by nobody.
    """
    from livery.toolroom._machinery import _toolfetch

    envs: list[dict[str, str] | None] = []

    def fake_run(argv, env=None):
        envs.append(env)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    monkeypatch.setattr(_toolfetch, "_capture", lambda argv, env=None: "")
    _toolfetch._install_python("3.10.0", tmp_path)
    assert envs[0] is not None
    assert envs[0]["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "store")
