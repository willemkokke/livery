"""The surfaces in a tool's record: the refusals first, then placing, merging and the union.

Three properties carry the whole design, and each has a way of failing
silently: a lossy round trip degrades stubs without erroring, a wrong
step reads as a plausible surface nobody notices is wrong, and a version
read that changed nothing must not read as a version never looked at, or
a release job redoes work it already did.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from typing import Any

import pytest

from livery.toolroom.bench import _surfaces
from livery.toolroom.store import Option, RecordError, ToolSpec, Verb
from toolroom_bench_readings import (
    chain_of,
    described,
    elsewhere,
    history,
    isolate,
    load,
    platform,
    reading,
    release_surface,
    save,
    serve,
    spec_of,
    tools_run,
    with_flags,
)

# --- a reading, refused or stored ----------------------------------------------


def test_a_reading_that_lost_bytes_is_refused():
    """U+FFFD in a surface is the decoder admitting it could not read
    something. Storing it manufactures an event, because help text is state
    and nothing downstream can tell a mangled byte from a real edit: djLint
    printed its banner separator as one cp1252 byte on Windows, UTF-8 turned
    it into a replacement character, and the store credited 1.43.2 with a
    description change that never happened.
    """
    lossy = spec_of(help="djLint � HTML template linter and formatter.")
    with pytest.raises(_surfaces.LossyReading, match=r"U\+FFFD"):
        _surfaces.surface_of(lossy)

    # Named where it is, so the report says which reading to distrust.
    verbs = spec_of().verbs
    deep = spec_of(verbs=(verbs[0], Verb(name="build", help="Build it � fast.")))
    with pytest.raises(_surfaces.LossyReading, match="build"):
        _surfaces.surface_of(deep)

    # A clean reading is untouched: the guard is a tripwire, not a filter.
    assert _surfaces.surface_of(spec_of())["help"] == "A demo tool."


def test_a_surface_round_trips_without_losing_a_field():
    """Every field the stub renderer reads must survive the record, or a
    regenerated stub quietly loses a negation, a default or a Literal.
    """
    spec = spec_of()
    back = _surfaces.spec_from(
        _surfaces.surface_of(spec),
        name=spec.name,
        version=spec.version,
        in_process=spec.in_process,
    )
    assert back == spec
    # ...and through the record, which keeps the options in name order.
    surface = _surfaces.surface_of(spec)
    record = history("demo", ("1.0.0", "2026-01-01", surface))
    assert _surfaces.at(record, "1.0.0") == {
        "help": surface["help"],
        "verbs": {
            name: {**verb, "options": dict(sorted(verb["options"].items()))}
            for name, verb in surface["verbs"].items()
        },
    }


def test_the_surface_leaves_out_what_is_not_the_release():
    """`version` keys the release and `in_process` is a fact about the machine
    that looked; neither describes what the tool accepts.
    """
    surface = _surfaces.surface_of(spec_of(version="9.9.9", in_process=True))
    blob = json.dumps(surface)
    assert "9.9.9" not in blob
    assert "in_process" not in blob


def test_a_step_names_what_moved_rather_than_restating_the_surface():
    """The changelog's whole claim: a step between two surfaces says what
    moved, option for option, verb for verb.
    """
    new = _surfaces.surface_of(spec_of())
    older = _surfaces.surface_of(
        spec_of(
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
    step = _surfaces.delta(new, older)
    assert "\tquiet" in step["drop"]  # the newer release added these
    assert "build\tclean" in step["drop"]
    assert "add" not in step  # ...and withdrew nothing the older one had
    assert "build\toutput" in step["revert"]  # reworded
    assert step["verbs"]["build"] == {  # the verb's own fields that moved
        "help": "Build it, once.",
        "positional": "any",
        "lead": "",
    }
    assert "help" in step and step["help"] == "An older demo tool."

    # A verb the newer release added steps back as None.
    bare = _surfaces.surface_of(
        spec_of(verbs=(Verb(name="", help="The tool itself.", options=()),))
    )
    assert _surfaces.delta(new, bare)["verbs"]["build"] is None


def test_an_unchanged_release_is_recorded_and_changes_nothing():
    """Observed and changed nothing is not the same as never looked at. The
    first is a version whose delta names no verb, the second is absent, and
    a release job reads the difference to decide whether to work.
    """
    surface = _surfaces.surface_of(spec_of())
    assert _surfaces.delta(surface, surface) == {}

    record = history(
        "demo", ("1.0.0", "2026-01-02", surface), ("0.9.0", "2026-01-01", surface)
    )
    assert _surfaces.at(record, "1.0.0") == _surfaces.at(record, "0.9.0")
    assert _surfaces.versions(record) == ["1.0.0", "0.9.0"]
    assert _surfaces.changed(record, "1.0.0") is False  # read, and the same
    assert _surfaces.changed(record, "0.9.0") is False  # the floor
    newer = record.delta_for("1.0.0").surface
    assert newer is not None and newer.help is None and newer.verbs == {}
    assert _surfaces.at(record, "0.5.0") is None  # never observed
    assert _surfaces.observation(record, "0.5.0") is None


def test_every_version_resolves_however_the_readings_arrived():
    """Placed newest first, the way a prime walks, and every version still
    resolves to the surface it was built from.
    """
    surfaces = {
        f"1.{n}.0": _surfaces.surface_of(
            spec_of(
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
    order = sorted(surfaces, reverse=True)
    record = history(
        "demo", *((v, f"2026-01-0{5 - n}", surfaces[v]) for n, v in enumerate(order))
    )
    for version, expected in surfaces.items():
        assert _surfaces.at(record, version) == expected, version
    # Sparse by construction: each delta names one verb, the one that moved.
    for delta in record.deltas[1:]:
        assert delta.surface is not None
        assert list(delta.surface.verbs) == ["build"]


def test_a_missing_record_is_none_and_a_broken_one_is_refused(tmp_path):
    """No record yet is an answer; a record that will not load is a fault
    to correct, never read as a tool with no readings.
    """
    assert _surfaces.load(tmp_path / "nope.jsonl") is None
    (tmp_path / "broken.jsonl").write_text("{not json")
    with pytest.raises(RecordError, match=r"not JSON"):
        _surfaces.load(tmp_path / "broken.jsonl")


def test_save_writes_one_file_of_lines_and_load_reads_it_back(tmp_path):
    import json

    record = history(
        "demo",
        ("1.0.0", "2026-01-02", _surfaces.surface_of(spec_of())),
        ("0.9.0", "2026-01-01", with_flags("quiet")),
    )
    save(record, tmp_path)
    assert load(tmp_path, "demo") == record
    lines = [
        json.loads(line) for line in (tmp_path / "demo.jsonl").read_text().splitlines()
    ]
    assert lines[0]["name"] == "demo"
    assert [line["version"] for line in lines if "version" in line] == [
        "0.9.0",
        "1.0.0",
    ]


# --- the checked-in records -----------------------------------------------------


def _records_dir() -> pathlib.Path:
    from livery.toolroom.bench import _tasks as tools_tasks

    found = pathlib.Path(tools_tasks._records_dir())
    if not found.is_dir():
        pytest.skip("the checked-in records are a checkout fact")
    return found


def _curated_keys() -> list[str]:
    from livery.toolroom.bench import _drivers

    return [d.key for d in _drivers.DRIVERS if d.source != "manual"]


def test_every_checked_in_record_reads_whole_and_names_who_looked():
    """What is checked in must resolve end to end, and every version read
    must say where the reading came from: a later multi-platform refresh
    reads that to decide what is an exclusion and what was never looked at.
    """
    for key in _curated_keys():
        record = _surfaces.load(_records_dir() / f"{key}.jsonl")
        assert record is not None, key
        chain = _surfaces.versions(record)
        assert chain, f"{key} was never read"
        assert _surfaces.floor(record) == chain[-1]
        for version in chain:
            assert _surfaces.at(record, version) is not None, (key, version)
            assert _surfaces.platforms_of(record, version), (key, version)
    prek = _surfaces.load(_records_dir() / "prek.jsonl")
    assert prek is not None
    assert len(_surfaces.versions(prek)) > 1, "prek was primed; it carries deltas"


def test_priming_changes_the_stub_the_record_renders_to():
    """A deeper record changes what a stub may say: an option that looked
    original at the old floor may turn out to have arrived. The stub is a
    rendering of the record, so extending the record changes the rendering
    with no further step.
    """
    import re

    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools_tasks

    record = _surfaces.load(_records_dir() / "prek.jsonl")
    assert record is not None
    chain = _surfaces.versions(record)
    assert len(chain) > 5, "prek is the primed tool; this test needs its chain"

    driver = _drivers.find("prek")
    assert driver is not None
    stub = tools_tasks._stub_from(driver, record)
    assert "Added in" in stub, "a primed tool's stub carries what the record proved"
    # ...and only versions the record actually holds.
    for claimed in set(re.findall(r"Added in ([0-9][^.\s]*(?:\.[^.\s]+)*)\.", stub)):
        assert claimed in chain, claimed


# --- placing a version --------------------------------------------------------------


def test_placing_below_the_floor_deepens_and_a_second_pass_adds_nothing():
    """The prime's whole write pattern: an older version lands below the
    floor, and a version already read is left alone, which is what makes an
    interrupted run resumable rather than duplicative.
    """
    surface = _surfaces.surface_of(spec_of())
    record = history("demo", ("1.2.0", "2026-02-01", surface))

    older = _surfaces.surface_of(
        spec_of(verbs=(Verb(name="build", help="Older.", options=()),))
    )
    placed = _surfaces.place(
        record, version="1.1.0", date="2026-01-01", surface=older, platforms=["Linux"]
    )
    assert placed is not None
    assert _surfaces.floor(placed) == "1.1.0"
    assert _surfaces.at(placed, "1.1.0") == older
    assert _surfaces.at(placed, "1.2.0") == surface  # the head did not move
    assert _surfaces.versions(placed) == ["1.2.0", "1.1.0"]

    # A second pass over the same release adds nothing.
    assert (
        _surfaces.place(
            placed,
            version="1.1.0",
            date="2026-01-01",
            surface=older,
            platforms=["Linux"],
        )
        is None
    )


def test_a_release_can_arrive_at_any_position_and_every_version_still_resolves():
    """The property the record was shaped for, and the one that lets
    gathering be unordered: releases placed in a shuffled order build the
    same record as releases placed newest first.

    Every version is resolved and compared against the surface it was built
    from: a delta computed against the wrong neighbour reconstructs
    something plausible, so only checking all of them catches it.
    """
    surfaces = {f"1.0.{n}": release_surface(n) for n in range(8)}
    dates = {v: f"2026-01-{n + 1:02d}" for n, v in enumerate(surfaces)}

    # Deliberately not in order: newest, oldest, then the middle scattered.
    arrival = ["1.0.7", "1.0.0", "1.0.4", "1.0.2", "1.0.6", "1.0.1", "1.0.5", "1.0.3"]
    shuffled = history("demo", *((v, dates[v], surfaces[v]) for v in arrival))
    ordered = history(
        "demo", *((v, dates[v], surfaces[v]) for v in sorted(surfaces, reverse=True))
    )
    assert _surfaces.versions(shuffled) == sorted(surfaces, reverse=True)
    for version, expected in surfaces.items():
        assert _surfaces.at(shuffled, version) == expected, version
    assert shuffled == ordered  # the same record, delta for delta


def test_placing_between_undated_patchlevels_of_one_base():
    """OpenSSH's shape: `version_tuple` reads 9.9p1 and 9.9p2 as the same
    base, and the portable listing carries no dates to break the tie. The
    patchlevel itself must place the release.
    """
    record = history(
        "ssh",
        ("9.9p3", "", release_surface(3)),
        ("9.9p1", "", release_surface(1)),
        ("9.9p2", "", release_surface(2)),
    )
    assert _surfaces.versions(record) == ["9.9p3", "9.9p2", "9.9p1"]
    for version, n in (("9.9p1", 1), ("9.9p2", 2), ("9.9p3", 3)):
        assert _surfaces.at(record, version) == release_surface(n), version


def test_a_version_that_ties_on_every_component_has_no_honest_place():
    record = history("demo", ("1.0.0", "2026-01-01", release_surface(0)))
    with pytest.raises(ValueError, match=r"1\.0\.0-wk\.2 ties a version"):
        _surfaces.place(
            record,
            version="1.0.0-wk.2",
            date="2026-01-01",
            surface=release_surface(1),
            platforms=["Linux"],
        )


def test_a_midfill_re_anchors_only_the_version_after_it():
    """Local, never cascading: filling a gap in a long record leaves every
    other version's own delta as it was, except the one just above the
    gap, which inherited from the version below the gap and is re-anchored
    on what it now inherits. Nothing any version resolves to moves.
    """
    # From 1.0.3 on the tool did not move, except at 1.0.4, the gap.
    surfaces = {f"1.0.{n}": release_surface(min(n, 3)) for n in range(8)}
    surfaces["1.0.4"] = release_surface(4)
    record = history(
        "demo",
        *(
            (f"1.0.{n}", f"2026-01-{n + 1:02d}", surfaces[f"1.0.{n}"])
            for n in (7, 6, 5, 3, 2, 1, 0)  # 1.0.4 deliberately missing
        ),
    )
    before = {d.version: d.surface for d in record.deltas}
    assert before["1.0.5"] is not None and before["1.0.5"].verbs == {}  # inherited

    filled = _surfaces.place(
        record,
        version="1.0.4",
        date="2026-01-05",
        surface=surfaces["1.0.4"],
        platforms=[platform()],
    )
    assert filled is not None
    after = {d.version: d.surface for d in filled.deltas}
    moved = [v for v, was in before.items() if after[v] != was]
    assert moved == ["1.0.5"]  # the version after the gap, and only it
    assert [d.sequence for d in filled.deltas] == list(range(1, 9))  # renumbered
    for version, expected in surfaces.items():
        assert _surfaces.at(filled, version) == expected, version


def test_a_gap_costs_precision_and_not_correctness():
    """Until a missing release is filled, an option it introduced reads as
    arriving at the next release actually read, the same honest imprecision
    the record already carries where an index has no build to offer.
    """
    record = history(
        "demo",
        ("1.0.2", "2026-01-03", release_surface(2)),
        ("1.0.0", "2026-01-01", release_surface(0)),
    )
    spec = _surfaces.union(record, name="demo")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["opt2"] == "1.0.2"  # 1.0.1 unread: attributed to what was seen

    filled = _surfaces.place(
        record,
        version="1.0.1",
        date="2026-01-02",
        surface=release_surface(1),
        platforms=[platform()],
    )
    assert filled is not None
    spec = _surfaces.union(filled, name="demo")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["opt1"] == "1.0.1"  # filled, and now attributed exactly


def test_a_version_tracked_but_never_read_takes_its_first_reading():
    """A version the record carries for its artifact alone is not a version
    read. Its first reading is placed on it, and a merge into it is the
    same first reading, never a fold into a surface it does not have.
    """
    from livery.toolroom.store import Artifact, Layout, Record, RecordDelta

    sha = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
    record = Record(
        "demo",
        kind="download",
        hosts=("linux-x64",),
        layout=Layout(entry_points=("demo",), paths=(".",)),
        deltas=(
            RecordDelta(
                1, "1.0.0", "", {"linux-x64": Artifact("https://x/1.zip", sha)}
            ),
        ),
    )
    assert _surfaces.versions(record) == []
    assert _surfaces.observation(record, "1.0.0") is None

    placed = _surfaces.place(
        record,
        version="1.0.0",
        date="2026-01-01",
        surface=with_flags("quiet"),
        platforms=["Linux"],
    )
    assert placed is not None
    assert _surfaces.versions(placed) == ["1.0.0"]
    assert placed.delta_for("1.0.0").artifacts == record.delta_for("1.0.0").artifacts

    merged, moved = _surfaces.merge(
        record, version="1.0.0", surface=with_flags("quiet"), platforms=["Linux"]
    )
    assert moved is True
    assert _surfaces.at(merged, "1.0.0") == with_flags("quiet")
    # ...and a version the record does not track is left alone.
    assert _surfaces.merge(
        record, version="9.9.9", surface=with_flags("quiet"), platforms=["Linux"]
    ) == (record, False)


# --- the union: what a stub may claim ----------------------------------------------


def test_the_union_carries_intervals_the_record_can_prove():
    """What a stub may say about an option's life, and what it may not.

    An option already present at the oldest release read has no `since`:
    the record never looked far enough back to claim one, and "at or before
    the floor" is not a `since`. An option the tool has dropped keeps its
    entry and gains an `until`, because a reader may be running a version
    that still has it.
    """
    old = _surfaces.surface_of(
        spec_of(
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
    middle = _surfaces.surface_of(
        spec_of(
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
    newest = _surfaces.surface_of(
        spec_of(
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
    record = history(
        "demo",
        ("3.0.0", "2026-03-01", newest),
        ("2.0.0", "2026-02-01", middle),
        ("1.0.0", "2026-01-01", old),
    )

    options = {
        o.name: o for v in _surfaces.union(record, name="demo").verbs for o in v.options
    }
    assert set(options) == {"ancient", "doomed", "fresh"}  # every option ever
    assert options["ancient"].since == ""  # there at the floor: nothing provable
    assert options["fresh"].since == "2.0.0"  # arrived, and the record saw it
    assert options["doomed"].until == "3.0.0"  # the release it stopped appearing in
    assert options["doomed"].since == ""


def test_a_record_of_one_release_claims_nothing():
    """The seeded state: one version, so no interval is provable and the
    stub says only what the tool says.
    """
    record = history("demo", ("1.0.0", "2026-01-01", _surfaces.surface_of(spec_of())))
    spec = _surfaces.union(record, name="demo")
    assert spec.verbs, "the union of one release is that release"
    assert not any(o.since or o.until for v in spec.verbs for o in v.options)


def test_an_observation_records_which_platforms_read_it():
    """A fact about the observation, like its date, and the groundwork for
    exclusions: "absent on Windows, and Windows was read" is an exclusion,
    while "absent on Windows, which never ran" is silence.

    One list, because a release read on three platforms is one observation
    of a merged surface.
    """
    surface = _surfaces.surface_of(spec_of())
    record = history(
        "demo",
        ("2.0.0", "2026-02-01", surface, ["macOS", "Linux"]),
        ("1.0.0", "2026-01-01", surface, ["Windows"]),
    )
    assert _surfaces.platforms_of(record, "2.0.0") == ["Linux", "macOS"]  # sorted
    assert _surfaces.platforms_of(record, "1.0.0") == ["Windows"]
    assert _surfaces.platforms_of(record, "0.0.1") == []


# --- what a sync writes --------------------------------------------------------------


def test_an_older_reading_never_becomes_the_head(tmp_path, monkeypatch):
    """A machine with a stale tool must not lift a newer release out of its
    place. Placed at its own position, an older reading is history and the
    newest version stays what it is.
    """
    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools_tasks

    monkeypatch.setattr(tools_tasks, "_RECORDS", tmp_path)
    driver = _drivers.find("prek")
    assert driver is not None

    def spec_at(version: str) -> ToolSpec:
        return ToolSpec(name="prek", version=version, verbs=spec_of().verbs)

    tools_tasks._observe(driver, spec_at("0.5.0"))
    record = tools_tasks._observe(driver, spec_at("0.4.0"))  # a laggard machine
    assert _surfaces.versions(record) == ["0.5.0", "0.4.0"]  # the head stands
    assert record.kind == driver.provision.record_kind

    record = tools_tasks._observe(driver, spec_at("0.6.0"))  # a newer release
    assert _surfaces.versions(record) == ["0.6.0", "0.5.0", "0.4.0"]
    assert load(tmp_path, "prek") == record  # written as returned


def test_a_tie_the_comparator_cannot_break_leaves_the_record_alone(
    tmp_path, monkeypatch
):
    """`0.6.0-wk.3` and `0.6.0-wk.5` are two builds of one base, and the
    comparator reduces both to `(0, 6, 0)`: a build tail says nothing about
    which flags exist. A fresh reading is stamped today whatever build it
    holds, so the sync has nothing to break the tie with, and must decline.
    Treating the tie as "not older" is what let a stale checkout promote
    `wk.3` over the recorded `wk.5`.
    """
    from livery.toolroom.bench import _drivers
    from livery.toolroom.bench import _tasks as tools

    surface = _surfaces.surface_of(
        spec_of(verbs=(Verb(name="", options=(Option("fix", ("--fix",)),)),))
    )
    record = history("eclint", ("0.6.0-wk.5", "2026-07-01", surface))
    monkeypatch.setattr(tools, "_RECORDS", tmp_path)
    save(record, tmp_path)

    driver = _drivers.find("eclint")
    assert driver is not None
    spec = _surfaces.spec_from(surface, name="eclint", version="0.6.0-wk.3")
    with pytest.raises(tools._Ambiguous) as raised:
        tools._observe(driver, spec)
    assert raised.value.reading == "0.6.0-wk.3"
    assert raised.value.base == "0.6.0-wk.5"
    assert load(tmp_path, "eclint") == record  # untouched


# --- the CHANGELOG entry the events write ---------------------------------------


def test_an_entry_names_a_flag_once_however_many_verbs_carry_it():
    """The same flag on the bare command and on one of its verbs is two keys
    in the surface and one thing to tell a reader about.
    """
    from livery.toolroom.bench import _tasks as tools

    def both(*names: str) -> dict[str, Any]:
        return _surfaces.surface_of(
            spec_of(
                verbs=tuple(
                    Verb(
                        name=verb,
                        options=tuple(Option(n, (f"--{n}",)) for n in names),
                    )
                    for verb in ("", "run")
                )
            )
        )

    record, versions = chain_of(both("quiet"), both("quiet", "glob"))
    entry = tools._entry_for("prek", record, [versions[1]])
    assert entry.count("`--glob`") == 1


def test_an_entry_tells_commands_apart_from_their_descriptions():
    """Three things hide in one step. A verb the newer release added steps
    back as `None`; one it withdrew steps back as a whole verb; one whose
    description merely moved steps back as a couple of fields. Only the
    first two are news: the third is a rewording, and counting it as a lost
    command would be a lie in both directions.
    """
    from livery.toolroom.bench import _tasks as tools

    def tree(*verbs: tuple[str, str]) -> dict[str, Any]:
        return _surfaces.surface_of(
            spec_of(
                verbs=tuple(
                    Verb(name=name, help=help_, options=()) for name, help_ in verbs
                )
            )
        )

    record, versions = chain_of(
        tree(("", "The tool."), ("run", "Run it."), ("clean", "Clean up.")),
        tree(
            ("", "The tool."),
            ("run", "Run the hooks."),  # reworded, not lost
            ("autoupdate", "Update."),  # gained
        ),  # `clean` withdrawn
    )
    entry = tools._entry_for("prek", record, versions[1:])

    assert "gains the `autoupdate` command" in entry
    assert "withdraws the `clean` command" in entry
    assert "rewords 1 description" in entry
    assert "`run`" not in entry  # a reworded verb is not a lost one


# --- the walks, driven through footman's own runner --------------------------
#
# The gather runs releases in parallel and leans on the run infrastructure,
# the environ router and the per-call env copy at the task boundary, so these
# tests drive the real thing: `footman.testing.Runner` is an in-process run,
# routers installed, and the result rows include every `observe` the engine
# fanned out.


def _release(version: str, date: str = "") -> Any:
    from livery.toolroom.bench import _toolfetch

    return _toolfetch.Release(version=version, date=date)


def test_a_refresh_reads_every_release_it_missed_not_just_the_newest(
    tmp_path, monkeypatch
):
    """Attribution is the whole point of the record: three releases behind
    means three observations, and the flag that arrived in 1.0.1 is recorded
    there, not at 1.0.3. The observations run in parallel and land in
    whatever order the pool finishes; the record assembles the same either
    way.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release(f"1.0.{n}", f"2026-02-0{n + 1}") for n in (3, 2, 1, 0)]
    }
    surfaces = {
        ("ruff", "1.0.1"): with_flags("quiet", "fix"),  # the event
        ("ruff", "1.0.2"): with_flags("quiet", "fix"),
        ("ruff", "1.0.3"): with_flags("quiet", "fix"),
    }
    installed = serve(monkeypatch, listings, surfaces)

    result = tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr

    assert sorted(installed) == [("ruff", f"1.0.{n}") for n in (1, 2, 3)]
    stored = load(root, "ruff")
    assert _surfaces.versions(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    newest = _surfaces.observation(stored, "1.0.3")
    assert newest is not None and newest.date == "2026-02-04"  # the index's date
    assert "adds `--fix`" not in result.stdout  # changelog was off
    assert "release warranted: yes" in result.stdout
    # every observation is a row in the run's own report: the audit trail
    assert sum(1 for row in result.results if row.task == "observe") == 3


def test_a_release_that_will_not_install_is_a_hole_not_a_dead_walk(
    tmp_path, monkeypatch
):
    """The releases beyond a failed install are still observed, the hole is
    named in the report, and a later run fills it, at which point its
    changes are attributed exactly.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release(f"1.0.{n}", f"2026-02-0{n + 1}") for n in (3, 2, 1, 0)]
    }
    surfaces: dict[tuple[str, str], dict[str, Any] | None] = {
        ("ruff", "1.0.1"): None,  # will not install
        ("ruff", "1.0.2"): with_flags("quiet", "fix"),
        ("ruff", "1.0.3"): with_flags("quiet", "fix"),
    }
    serve(monkeypatch, listings, surfaces)

    result = tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    assert "holes in ruff: 1.0.1" in result.stdout

    stored = load(root, "ruff")
    assert _surfaces.versions(stored) == ["1.0.3", "1.0.2", "1.0.0"]
    # the gap costs precision, not correctness: --fix reads as arriving at
    # the release actually read...
    spec = _surfaces.union(stored, name="ruff")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["fix"] == "1.0.2"

    # ...and the next run fills the hole and sharpens the claim.
    surfaces[("ruff", "1.0.1")] = with_flags("quiet", "fix")
    result = tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    stored = load(root, "ruff")
    assert _surfaces.versions(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    spec = _surfaces.union(stored, name="ruff")
    arrived = {o.name: o.since for v in spec.verbs for o in v.options}
    assert arrived["fix"] == "1.0.1"


def test_a_refresh_with_nothing_new_warrants_no_release(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.3", "2026-02-04", with_flags("quiet"))), root)
    listings = {"ruff": [_release("1.0.3", "2026-02-04")]}
    installed = serve(monkeypatch, listings, {})

    result = tools_run("refresh --only=ruff")
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
    from livery.toolroom.bench import _tasks as tools
    from livery.toolroom.bench import _toolfetch

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.3", "2026-02-04", with_flags("quiet"))), root)

    def throttled(_driver: Any) -> None:
        raise _toolfetch.Unreachable("https://pypi.org/pypi/ruff/json", "429")

    monkeypatch.setattr(_toolfetch, "releases", throttled)

    result = tools_run("refresh --only=ruff")
    assert result.exit_code == 75
    assert "ruff" in result.stderr


def test_a_refresh_writes_its_own_events_into_the_changelog(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release("1.0.1", "2026-02-02"), _release("1.0.0", "2026-02-01")]
    }
    serve(monkeypatch, listings, {("ruff", "1.0.1"): with_flags("quiet", "fix")})

    result = tools_run("refresh --only=ruff")
    assert result.ok, result.stderr
    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "adds `--fix`" in written


def test_the_pre_pass_answers_without_installing_anything(tmp_path, monkeypatch):
    """A gather provisions every tool and then discovers there is nothing
    to observe, which is most weeks. Listing is network and nothing else,
    so the question can be answered before the work is prepared for.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release("1.0.1", "2026-02-02"), _release("1.0.0", "2026-02-01")]
    }
    installed = serve(monkeypatch, listings, {})

    result = tools_run("owed --only=ruff")
    assert result.ok, result.stderr
    answer = result.results[0].returned
    assert answer.releases == {"ruff": ["1.0.1"]}
    assert answer.total == 1
    assert installed == []  # nothing fetched to find that out
    assert "owed: 1" in result.stdout


def test_an_unreadable_index_is_not_nothing_to_do(tmp_path, monkeypatch):
    """A walk that cannot see an index cannot say the index has nothing new,
    so the caller is told separately rather than reading a total of zero as
    "all quiet".
    """
    from livery.toolroom.bench import _tasks as tools
    from livery.toolroom.bench import _toolfetch

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)

    def blocked(driver: Any) -> None:
        raise _toolfetch.Unreachable("the index", "429")

    monkeypatch.setattr(_toolfetch, "releases", blocked)
    result = tools_run("owed --only=ruff")
    assert result.ok, result.stderr
    answer = result.results[0].returned
    assert answer.total == 0
    assert "ruff" in answer.unreachable
    assert "unreachable: ruff" in result.stdout


def test_a_tool_with_no_record_is_skipped_by_name(tmp_path, monkeypatch):
    """No record is no floor to walk from: the tool is named and left for
    a `sync`, never read as a tool with nothing owed.
    """
    from livery.toolroom.bench import _tasks as tools

    isolate(tools, monkeypatch, tmp_path)
    serve(monkeypatch, {"ruff": [_release("1.0.1", "2026-02-02")]}, {})
    result = tools_run("owed --only=ruff")
    assert result.ok, result.stderr
    assert "ruff (no record — run `sync` first)" in result.results[0].returned.skipped


def test_a_backfill_is_recorded_but_never_announced(tmp_path, monkeypatch):
    """A walk that reaches backwards changes the surface at every step it
    takes, and every one of those steps is a change the tool made years
    ago. The releases are still read, still folded, still stubbed: a
    changelog reports a release nobody had seen before, not one footman had
    not got around to reading.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.3", "2026-02-04", with_flags("quiet", "fix"))), root)
    listings = {
        "ruff": [_release(f"1.0.{n}", f"2026-02-0{n + 1}") for n in (3, 2, 1, 0)]
    }
    # Each older release really does differ: the backfill is not a no-op.
    surfaces = {
        ("ruff", "1.0.2"): with_flags("quiet", "fix"),
        ("ruff", "1.0.1"): with_flags("quiet"),
        ("ruff", "1.0.0"): with_flags("quiet"),
    }
    serve(monkeypatch, listings, surfaces)

    out = tmp_path / "obs.json"
    assert tools_run(["gather", "--only=ruff", "--count=3", f"--out={out}"]).ok
    result = tools_run(["assemble", str(out)])
    assert result.ok, result.stderr

    stored = load(root, "ruff")
    assert _surfaces.versions(stored) == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]
    assert "release warranted: no" in result.stdout
    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "`--fix`" not in written


def test_a_release_above_the_record_is_still_announced_beside_a_backfill(
    tmp_path, monkeypatch
):
    """The rule is about direction, not about how much a run read: one run
    can do both, and only the release nobody had seen is news.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.2", "2026-02-03", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release(f"1.0.{n}", f"2026-02-0{n + 1}") for n in (3, 2, 1, 0)]
    }
    surfaces = {
        ("ruff", "1.0.3"): with_flags("quiet", "fix"),  # above: news
        ("ruff", "1.0.1"): with_flags("quiet", "cache"),  # below: history
        ("ruff", "1.0.0"): with_flags("quiet"),
    }
    serve(monkeypatch, listings, surfaces)

    out = tmp_path / "obs.json"
    assert tools_run(["gather", "--only=ruff", "--count=2", f"--out={out}"]).ok
    result = tools_run(["assemble", str(out)])
    assert result.ok, result.stderr

    written = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "`--fix`" in written  # 1.0.3, newer than anything seen
    assert "`--cache`" not in written  # 1.0.1, filled in behind it


def test_a_refresh_with_no_events_writes_no_note(tmp_path, monkeypatch):
    """A new release that changed nothing is recorded, a delta naming no
    verb, and warrants neither a release nor a line about one.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release("1.0.1", "2026-02-02"), _release("1.0.0", "2026-02-01")]
    }
    serve(monkeypatch, listings, {("ruff", "1.0.1"): with_flags("quiet")})

    result = tools_run("refresh --only=ruff")
    assert result.ok, result.stderr
    assert "release warranted: no" in result.stdout
    assert "###" not in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    stored = load(root, "ruff")
    assert _surfaces.versions(stored) == ["1.0.1", "1.0.0"]  # observed, unchanged


def test_a_prime_reaches_below_the_floor_and_only_below_it(tmp_path, monkeypatch):
    """The backward walk: newer releases are the refresh's business, and a
    prime must never lift the head, only deepen the tail, up to its count.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.2", "2026-02-03", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release(f"1.0.{n}", f"2026-02-0{n + 1}") for n in (4, 3, 2, 1, 0)]
    }
    surfaces = {
        ("ruff", "1.0.0"): with_flags("quiet"),
        ("ruff", "1.0.1"): with_flags("quiet"),
    }
    installed = serve(monkeypatch, listings, surfaces)

    result = tools_run("prime --only=ruff --count=1")
    assert result.ok, result.stderr
    assert installed == [("ruff", "1.0.1")]  # one below the floor; never 1.0.3+

    stored = load(root, "ruff")
    assert _surfaces.versions(stored)[0] == "1.0.2"  # the head did not move
    assert _surfaces.floor(stored) == "1.0.1"
    assert "ruff +1 (from 1.0.1)" in result.stdout


def test_a_floor_the_index_cannot_place_refuses_the_tool(tmp_path, monkeypatch):
    """A stub synced from an outdated binary leaves a floor no listing holds;
    priming from the top would file the newest release as the oldest.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("0.9.9", "2026-01-01", with_flags("quiet"))), root)
    listings = {"ruff": [_release("1.0.3", "2026-02-04")]}
    installed = serve(monkeypatch, listings, {})

    result = tools_run("prime --only=ruff")
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

    from livery.toolroom.bench import _drivers, _toolfetch
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [
            _release("1.0.2", "2026-02-03"),
            _release("1.0.1", "2026-02-02"),
            _release("1.0.0", "2026-02-01"),
        ]
    }
    surface = with_flags("quiet", "fix")
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: list(listings[driver.key])
    )

    def install(driver: Any, release: Any, into: pathlib.Path) -> pathlib.Path:
        (into / "bin").mkdir(parents=True, exist_ok=True)
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)

    both_inside = threading.Barrier(2)
    seen: list[tuple[str, str]] = []

    def extract(driver: Any) -> ToolSpec:
        with contextlib.suppress(threading.BrokenBarrierError):
            both_inside.wait(timeout=10)  # overlap for real, or say so
        head = os.environ.get("PATH", "").split(os.pathsep)[0]
        seen.append((head, os.environ.get("PATH", "")))
        return _surfaces.spec_from(surface, name=driver.name)

    monkeypatch.setattr(_drivers, "extract", extract)

    result = tools_run("refresh --only=ruff --no-changelog")
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
    from livery.toolroom.bench import _drivers, _toolfetch
    from livery.toolroom.bench import _tasks as tools

    isolate(tools, monkeypatch, tmp_path)
    installed: list[str] = []

    def install(driver: Any, release: Any, into: pathlib.Path) -> pathlib.Path:
        installed.append(release.version)
        (into / "bin").mkdir(parents=True, exist_ok=True)
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)
    monkeypatch.setattr(
        _drivers,
        "extract",
        lambda driver: _surfaces.spec_from(with_flags("quiet"), name=driver.name),
    )

    demo = Group("demo")

    @demo.task
    def twice() -> None:
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


# --- cross-platform observations ---------------------------------------------
#
# One observation comes from one platform. Observations merge; the record
# stores only what a platform SAW (the sidecar beside the surface, never
# inside it), and every standing claim, who lacks an option now, since,
# until, is derived at render time from those verdicts.


def test_folding_keeps_every_option_and_names_who_missed_it():
    """A matrix run folds before it touches the record. Otherwise an option
    only Linux has would be placed, dropped by macOS, resurrected by
    Windows: three real steps for one release nobody changed.
    """
    surface, absent = _surfaces.fold(
        {
            "Linux": reading("quiet", "fork"),
            "macOS": reading("quiet", "fork"),
            "Windows": reading("quiet"),
        }
    )
    assert sorted(surface["verbs"][""]["options"]) == ["fork", "quiet"]
    assert absent == {"\tfork": ["Windows"]}  # the exception, and only it


def test_a_verb_missing_whole_is_said_once_not_per_option():
    """`docker compose` absent on a platform is one fact about the command,
    not forty about its flags: smaller, and what a reader wants.
    """
    surface, absent = _surfaces.fold(
        {
            "Linux": {
                "help": "",
                "verbs": {"up": reading("build", "detach", verb="up")["verbs"]["up"]},
            },
            "Windows": {"help": "", "verbs": {}},
        }
    )
    assert absent == {"up\t": ["Windows"]}
    assert sorted(surface["verbs"]["up"]["options"]) == ["build", "detach"]


def test_a_re_read_can_say_a_tool_has_no_description():
    """`markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)` is a signature, not
    a description, and the tool has none to give. Settling the tool's own
    line on truthiness made that unsayable: an empty reading looked like a
    platform that had failed to look, so the banner survived every re-read
    and no better extractor could ever correct it.
    """
    record = history(
        "markdownlint",
        (
            "0.23.2",
            "2026-01-01",
            described("markdownlint-cli2 v0.23.2 (markdownlint v0.41.1)"),
            ["Linux", "Windows", "macOS"],
        ),
    )
    # The platform whose words these are reads again and finds none.
    record, _moved = _surfaces.merge(
        record, version="0.23.2", surface=described(""), platforms=["Linux"]
    )
    assert (_surfaces.at(record, "0.23.2") or {})["help"] == ""


def test_a_quieter_platform_still_cannot_erase_a_description():
    """The other half of the same rule, and the reason truthiness was there:
    a platform that reads nothing must not blank a line another platform
    read properly.
    """
    record = history(
        "demo", ("1.0.0", "2026-01-01", described("A real description"), ["Linux"])
    )
    record, _moved = _surfaces.merge(
        record, version="1.0.0", surface=described(""), platforms=["macOS"]
    )
    assert (_surfaces.at(record, "1.0.0") or {})["help"] == "A real description"


def test_a_better_reading_replaces_a_signature_with_the_real_line():
    """git-cliff opens `git-cliff 2.13.1` and describes itself on the next
    line. The extractor that learned to skip the signature must be able to
    put the real description in its place.
    """
    record = history(
        "git_cliff", ("2.13.1", "2026-01-01", described("git-cliff 2.13.1"), ["Linux"])
    )
    record, _moved = _surfaces.merge(
        record,
        version="2.13.1",
        surface=described("A highly customizable changelog generator"),
        platforms=["Linux"],
    )
    assert (_surfaces.at(record, "2.13.1") or {})["help"] == (
        "A highly customizable changelog generator"
    )


def test_a_merge_widening_coverage_costs_the_neighbours_nothing():
    """The sidecar never enters a step, so a platform looking for the first
    time cannot make the tool look like it changed: no recompute, no event,
    no changelog line.
    """
    record = history(
        "demo",
        ("2.0.0", "2026-01-02", reading("quiet", "fork"), ["macOS"]),
        ("1.0.0", "2026-01-01", reading("quiet"), ["macOS"]),
    )
    before = {
        d.version: (d.surface.help, d.surface.verbs) for d in record.deltas if d.surface
    }

    merged, moved = _surfaces.merge(
        record,
        version="2.0.0",
        surface=reading("quiet"),  # Windows lacks --fork
        platforms=["Windows"],
    )
    assert moved is False  # the surface did not change; nothing to recompute
    after = {
        d.version: (d.surface.help, d.surface.verbs) for d in merged.deltas if d.surface
    }
    assert after == before
    assert _surfaces.platforms_of(merged, "2.0.0") == ["Windows", "macOS"]
    assert _surfaces.absent_of(merged, "2.0.0") == {"\tfork": ["Windows"]}
    assert _surfaces.absent_of(merged, "1.0.0") == {}  # never the neighbour's
    assert _surfaces.changed(merged, "2.0.0") is True  # the tool did change here


def test_a_merge_bringing_an_option_records_who_had_looked_without_it():
    """An option only the newcomer sees was, by construction, missing for
    everyone who looked before: that is an observed absence, and it is the
    only reason the record may tag them.
    """
    record = history("demo", ("2.0.0", "2026-01-02", reading("quiet"), ["macOS"]))
    record, moved = _surfaces.merge(
        record,
        version="2.0.0",
        surface=reading("quiet", "winonly"),
        platforms=["Windows"],
    )
    assert moved is True
    assert _surfaces.absent_of(record, "2.0.0") == {"\twinonly": ["macOS"]}
    assert "winonly" in (_surfaces.at(record, "2.0.0") or {})["verbs"][""]["options"]
    # ...and never the merging platform itself, which is what saw it.
    assert "Windows" not in json.dumps(_surfaces.absent_of(record, "2.0.0"))


def test_the_record_holds_only_absences_that_were_observed():
    """The invariant the whole design rests on: `absent` names only
    platforms that read the version. A claim about a platform that never
    looked is derived at render time, where a later sighting revises it,
    never written down, where it would harden into a fact nobody rechecks.
    """
    record = history(
        "demo", ("2.0.0", "2026-01-02", reading("quiet", "fork"), ["macOS"])
    )
    record, _moved = _surfaces.merge(
        record, version="2.0.0", surface=reading("quiet"), platforms=["Windows"]
    )
    for version in _surfaces.versions(record):
        looked = set(_surfaces.platforms_of(record, version))
        for key, who in _surfaces.absent_of(record, version).items():
            assert set(who) <= looked, f"{version} {key} claims an unobserved absence"
    # ...and the record refuses one written by hand.
    with pytest.raises(RecordError, match=r"absent names Linux, which did not read"):
        history("demo", ("1.0.0", "2026-01-01", reading("quiet"), ["macOS"])).__class__(
            "demo",
            kind="uv-tool",
            deltas=(
                _surfaces.RecordDelta(
                    1,
                    "1.0.0",
                    "",
                    surface=_surfaces.Surface(
                        ("macOS",),
                        1,
                        "",
                        reading("quiet")["verbs"],
                        {"": {"quiet": ("Linux",)}},
                    ),
                ),
            ),
        )


def test_a_sighting_on_a_platform_clears_its_standing_absence():
    """Nothing means cross-platform. Windows lacked `--fork` at 1.0.0 and
    has it at 2.0.0, so the claim is dropped, derived from the newest
    verdict rather than chased back through the record.
    """
    record = history(
        "demo",
        ("2.0.0", "2026-01-02", reading("quiet", "fork"), ["Windows", "macOS"]),
    )
    placed = _surfaces.place(
        record,
        version="1.0.0",
        date="2026-01-01",
        surface=reading("quiet", "fork"),
        platforms=["Windows", "macOS"],
        absent={"\tfork": ["Windows"]},  # the old verdict
    )
    assert placed is not None
    record = placed
    options = {
        o.name: o for v in _surfaces.union(record, name="demo").verbs for o in v.options
    }
    assert options["fork"].not_on == ()  # the newer sighting wins
    assert options["quiet"].not_on == ()


def test_an_absence_stands_until_that_platform_looks_again():
    """A Linux-only week can neither set nor clear a Windows claim: the
    verdict is per platform, and silence is not evidence.
    """
    record = history(
        "demo",
        ("2.0.0", "2026-01-02", reading("quiet", "fork"), ["Linux"]),  # Linux alone
    )
    placed = _surfaces.place(
        record,
        version="1.0.0",
        date="2026-01-01",
        surface=reading("quiet", "fork"),
        platforms=["Linux", "Windows"],
        absent={"\tfork": ["Windows"]},
    )
    assert placed is not None
    record = placed
    options = {
        o.name: o for v in _surfaces.union(record, name="demo").verbs for o in v.options
    }
    assert options["fork"].not_on == ("Windows",)  # still standing, still honest


def test_a_platforms_own_floor_is_not_a_since():
    """An option first seen where only one platform's coverage reaches was
    not "added" there: the older releases were never read on that platform.
    The record's floor rule, one level down.
    """
    record = history(
        "demo",
        ("1.0.0", "2026-01-01", reading("quiet"), ["macOS"]),  # Windows never read it
    )
    placed = _surfaces.place(
        record,
        version="2.0.0",
        date="2026-01-02",
        surface=reading("quiet", "winonly"),
        platforms=["Windows", "macOS"],
        absent={"\twinonly": ["macOS"]},
    )
    assert placed is not None
    record = placed
    options = {
        o.name: o for v in _surfaces.union(record, name="demo").verbs for o in v.options
    }
    assert options["winonly"].since == ""  # Windows' floor is 2.0.0, not a since
    assert options["winonly"].not_on == ("macOS",)


def test_divergent_words_settle_the_same_whichever_leg_arrives_first():
    """One copy of the text is stored, so the pick must not depend on merge
    order, or two legs would flip a divergent help string every week, each
    flip a step in a record whose question is "did anything change".
    """
    words = {"Linux": {"quiet": "Hush, penguin."}, "Windows": {"quiet": "Hush, PC."}}

    def built(order: tuple[str, str]) -> str:
        record = history(
            "demo",
            (
                "1.0.0",
                "2026-01-01",
                reading("quiet", help_of=words[order[0]]),
                [order[0]],
            ),
        )
        record, _moved = _surfaces.merge(
            record,
            version="1.0.0",
            surface=reading("quiet", help_of=words[order[1]]),
            platforms=[order[1]],
        )
        surface = _surfaces.at(record, "1.0.0") or {}
        return str(surface["verbs"][""]["options"]["quiet"]["help"])

    assert built(("Linux", "Windows")) == built(("Windows", "Linux"))
    assert built(("Linux", "Windows")) == "Hush, penguin."  # priority, not order


def test_a_merge_that_widens_the_newest_version_touches_its_delta_alone():
    """A merge that widens the surface is local, like a midfill: the
    version itself is rewritten, the one after it is re-anchored, and
    nothing before it moves. At the newest version there is nothing after.
    """
    record = history(
        "demo",
        ("3.0.0", "2026-01-03", reading("quiet"), ["macOS"]),
        ("2.0.0", "2026-01-02", reading("quiet"), ["macOS"]),
        ("1.0.0", "2026-01-01", reading("quiet"), ["macOS"]),
    )
    untouched = {d.version: d for d in record.deltas}

    merged, moved = _surfaces.merge(
        record,
        version="3.0.0",
        surface=reading("quiet", "winonly"),
        platforms=["Windows"],
    )
    assert moved is True  # the surface grew, so the record must be told
    assert merged.delta_for("1.0.0") == untouched["1.0.0"]
    assert merged.delta_for("2.0.0") == untouched["2.0.0"]
    assert merged.delta_for("3.0.0") != untouched["3.0.0"]
    for version in _surfaces.versions(merged):
        assert _surfaces.at(merged, version) is not None, version


def test_gather_writes_a_document_another_machine_can_fold(tmp_path, monkeypatch):
    """The two halves are split because a Linux box cannot tell you what a
    tool's `--help` says on Windows. So the observation travels as a
    self-describing document, copied off that machine by hand if that is
    how the week goes, and the assembler folds it wherever the records are.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(history("ruff", ("1.0.0", "2026-02-01", with_flags("quiet"))), root)
    listings = {
        "ruff": [_release("1.0.1", "2026-02-02"), _release("1.0.0", "2026-02-01")]
    }
    serve(monkeypatch, listings, {("ruff", "1.0.1"): with_flags("quiet", "fix")})

    out = tmp_path / "obs.json"
    result = tools_run(["gather", "--only=ruff", f"--out={out}"])
    assert result.ok, result.stderr

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema"] == tools.OBSERVATION_SCHEMA
    assert document["platform"] == platform()
    assert list(document["observations"]["ruff"]) == ["1.0.1"]
    # the record is untouched: gathering writes nothing but the document
    assert _surfaces.versions(load(root, "ruff")) == ["1.0.0"]

    result = tools_run(["assemble", str(out), "--no-changelog"])
    assert result.ok, result.stderr
    assert _surfaces.versions(load(root, "ruff")) == ["1.0.1", "1.0.0"]


def test_a_correction_below_the_head_is_saved_and_stamped():
    """A better extractor corrects an older version, and the fold must
    notice: the correction is written, not computed and dropped, and the
    version is stamped as read by today's extractor, or `_plan_gather`
    offers it again every run and the walk that heals the record never
    records that it healed it.
    """
    from livery.toolroom.bench import _tasks as tools

    record = history(
        "ssh",
        ("2.0", "2026-02-01", described("OpenSSH remote login client"), ["Linux"]),
        (
            "1.0",
            "2026-01-01",
            described("SSH(1)      General Commands Manual      SSH(1)"),
            ["Linux"],
        ),
    )
    # That older reading came from the previous generation.
    record = _surfaces._set(
        record,
        "1.0",
        surface=_surfaces.at(record, "1.0") or {},
        platforms=["Linux"],
        absent={},
        extractor=_surfaces.EXTRACTOR - 1,
    )
    assert (_surfaces.at(record, "1.0") or {})["help"].startswith("SSH(1)")

    record, fresh, touched = tools._fold_into(
        record,
        {"1.0": {"Linux": described("OpenSSH remote login client")}},
        {"1.0": {"date": "2026-01-01", "tag": ""}},
    )
    assert fresh == [], "correcting a release the record has is not a new release"
    assert touched, "a correction below the head must be saved"
    assert (_surfaces.at(record, "1.0") or {})["help"] == "OpenSSH remote login client"
    seen = _surfaces.observation(record, "1.0")
    assert seen is not None and seen.extractor == _surfaces.EXTRACTOR
    # The newer version now inherits the description it used to set.
    newer = record.delta_for("2.0").surface
    assert newer is not None and newer.help is None


def test_two_platforms_fold_into_one_release_with_the_exception_named(
    tmp_path, monkeypatch
):
    """The whole point: one release, two witnesses, one record, and the
    option only one of them has is the exception the record keeps.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    save(
        history(
            "ruff", ("1.0.0", "2026-02-01", with_flags("quiet"), ["Linux", "Windows"])
        ),
        root,
    )

    def document(platform_: str, *options: str) -> pathlib.Path:
        path = pathlib.Path(tmp_path) / f"obs-{platform_}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": tools.OBSERVATION_SCHEMA,
                    "platform": platform_,
                    "observations": {
                        "ruff": {
                            "1.0.1": {
                                "date": "2026-02-02",
                                "tag": "",
                                "surface": with_flags(*options),
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

    result = tools_run(["assemble", str(linux), str(windows), "--no-changelog"])
    assert result.ok, result.stderr

    stored = load(root, "ruff")
    assert _surfaces.platforms_of(stored, "1.0.1") == ["Linux", "Windows"]
    assert _surfaces.absent_of(stored, "1.0.1") == {"\tfork": ["Windows"]}
    # folded once: the option is in the surface, and no drop/resurrect churn
    assert "fork" in (_surfaces.at(stored, "1.0.1") or {})["verbs"][""]["options"]
    assert _surfaces.absent_of(stored, "1.0.0") == {}

    spec = _surfaces.union(stored, name="ruff")
    options = {o.name: o for v in spec.verbs for o in v.options}
    assert options["fork"].not_on == ("Windows",)
    assert options["quiet"].not_on == ()


def test_a_platform_new_to_a_tool_backfills_the_version_people_run(
    tmp_path, monkeypatch
):
    """A platform that has never looked at a tool starts with the newest
    version, otherwise its coverage would begin at whatever ships next, and
    the version everyone is actually running would stay unaccounted for.
    """
    from livery.toolroom.bench import _tasks as tools

    root = isolate(tools, monkeypatch, tmp_path)
    # Somebody, and never this machine: the test is about a platform's
    # first look, so the fixture must not name the runner it runs on.
    save(
        history(
            "ruff", ("1.0.0", "2026-02-01", with_flags("quiet", "fork"), [elsewhere()])
        ),
        root,
    )
    listings = {"ruff": [_release("1.0.0", "2026-02-01")]}
    installed = serve(monkeypatch, listings, {("ruff", "1.0.0"): with_flags("quiet")})

    result = tools_run("refresh --only=ruff --no-changelog")
    assert result.ok, result.stderr
    assert installed == [("ruff", "1.0.0")]  # the newest, backfilled

    stored = load(root, "ruff")
    assert _surfaces.platforms_of(stored, "1.0.0") == sorted([elsewhere(), platform()])
    assert _surfaces.absent_of(stored, "1.0.0") == {"\tfork": [platform()]}
    assert "release warranted: no" in result.stdout  # coverage is not an event


def test_a_platform_folding_into_an_older_release_agrees_with_what_is_stored():
    """Two platforms that agree must record agreement: coverage widens, the
    sidecar stays empty, and no version's surface moves.
    """
    surfaces = {
        "1.0.2": with_flags("quiet", "fix"),
        "1.0.1": with_flags("quiet", "fix"),
        "1.0.0": with_flags("quiet"),
    }
    record = history(
        "ruff",
        *((v, "2026-02-01", s, ["macOS"]) for v, s in surfaces.items()),
    )
    steps = {
        d.version: (d.surface.help, d.surface.verbs) for d in record.deltas if d.surface
    }

    for version, surface in surfaces.items():  # Linux reads the same thing
        record, moved = _surfaces.merge(
            record, version=version, surface=surface, platforms=["Linux"]
        )
        assert not moved

    assert {
        d.version: (d.surface.help, d.surface.verbs) for d in record.deltas if d.surface
    } == steps  # the steps themselves did not move
    for version in surfaces:
        assert _surfaces.platforms_of(record, version) == ["Linux", "macOS"]
        assert not _surfaces.absent_of(record, version)  # agreement is not an exception
        assert _surfaces.at(record, version) == surfaces[version]  # resolves exact


def test_a_divergence_below_the_newest_release_re_anchors_the_version_after_it():
    """A merge that widens an older release rewrites the version itself and
    re-anchors the one after it, which now inherits from a different
    surface, and nothing else, however long the record.
    """
    surfaces = {f"1.0.{n}": with_flags("quiet") for n in range(5)}
    record = history(
        "ruff",
        *(
            (f"1.0.{n}", f"2026-02-0{n + 1}", surfaces[f"1.0.{n}"], ["macOS"])
            for n in range(5)
        ),
    )
    untouched = {d.version: d for d in record.deltas}

    # Linux sees an extra flag at 1.0.2: a real divergence, mid-record.
    record, moved = _surfaces.merge(
        record,
        version="1.0.2",
        surface=with_flags("quiet", "linuxonly"),
        platforms=["Linux"],
    )
    assert moved

    changed = [v for v, was in untouched.items() if record.delta_for(v) != was]
    assert sorted(changed) == ["1.0.2", "1.0.3"]  # the version, and the one after
    assert _surfaces.absent_of(record, "1.0.2") == {"\tlinuxonly": ["macOS"]}
    for version in surfaces:
        replayed = _surfaces.at(record, version)
        assert replayed is not None
        names = {o for v in replayed["verbs"].values() for o in v["options"]}
        assert names == ({"quiet", "linuxonly"} if version == "1.0.2" else {"quiet"})


def test_a_reading_older_than_the_extractor_is_offered_again(monkeypatch):
    """`EXTRACTOR` is recorded against every observation, and an extractor
    that learned to see more says so through it: a reading is only as good
    as the extractor that took it, and this is where that is acted on.
    """
    from livery.toolroom.bench import _tasks as tools

    surface = with_flags("quiet")
    record = history(
        "ruff",
        ("1.0.1", "2026-02-02", surface),
        ("1.0.0", "2026-02-01", surface),
    )
    listing = [_release(v) for v in ("1.0.1", "1.0.0")]

    # Current generation, this platform has read both: nothing owed.
    assert tools._plan_gather(record, listing, 0) == []

    # The extractor moves on, and both readings are owed again.
    monkeypatch.setattr(_surfaces, "EXTRACTOR", _surfaces.EXTRACTOR + 1)
    offered = [r.version for r in tools._plan_gather(record, listing, 0)]
    assert offered == ["1.0.1", "1.0.0"]


def test_a_re_read_clears_a_claim_the_older_extractor_caused():
    """The self-healing the mechanism is for: one platform's blind reading
    made the other look like a divergence, and reading it again with the
    better extractor settles it. No hand-editing of the record, which is
    the one thing a record of observations must never need.
    """
    here = platform()
    blind = _surfaces.surface_of(spec_of(verbs=(Verb(name="", options=()),)))
    real = with_flags("quiet", "fix")

    record = history("twine", ("5.1.0", "2026-02-01", blind, [here]))
    # Another platform reads it properly: the options arrive tagged absent here.
    record, _moved = _surfaces.merge(
        record, version="5.1.0", surface=real, platforms=[elsewhere()]
    )
    absent = _surfaces.absent_of(record, "5.1.0")
    assert absent, "the blind reading should read as an absence"
    assert here in next(iter(absent.values()))

    # This platform reads it again, now seeing what was always there.
    record, _moved = _surfaces.merge(
        record, version="5.1.0", surface=real, platforms=[here]
    )
    assert not _surfaces.absent_of(record, "5.1.0")  # withdrawn by evidence
    assert _surfaces.platforms_of(record, "5.1.0") == sorted([here, elsewhere()])
