"""A version's surface in its delta: the refusals first, then inheritance and the round trip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from livery.toolroom.store import (
    Artifact,
    Layout,
    Observation,
    Record,
    RecordDelta,
    RecordError,
    Surface,
    observations,
    surface_at,
)

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"


def _option(help_text: str = "The option.", **over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "flags": ["--opt"],
        "negation": "",
        "help": help_text,
        "type": "bool",
        "default": None,
        "choices": [],
    }
    fields.update(over)
    return fields


def _verb(*options: str, help_text: str = "The verb.", **over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "help": help_text,
        "wraps": False,
        "positional": "any",
        "lead": "",
        "options": {name: _option(flags=[f"--{name}"]) for name in options},
    }
    fields.update(over)
    return fields


def _surface(**over: Any) -> Surface:
    fields: dict[str, Any] = {
        "platforms": ("Linux",),
        "extractor": 4,
        "help": "A tool.",
        "verbs": {"": _verb("quiet"), "build": _verb("clean", "output")},
    }
    fields.update(over)
    return Surface(**fields)


def _delta(
    sequence: int, version: str, surface: Surface | None, **over: Any
) -> RecordDelta:
    return RecordDelta(
        sequence, version, f"2026-01-0{sequence}", surface=surface, **over
    )


def _record(*deltas: RecordDelta, **over: Any) -> Record:
    fields: dict[str, Any] = {"name": "tool", "kind": "uv-tool", "deltas": deltas}
    fields.update(over)
    return Record(**fields)


# --- the refusals ---------------------------------------------------------------


def test_a_surface_needs_a_platform_from_the_three_and_a_positive_extractor() -> None:
    with pytest.raises(RecordError, match=r"0001-1\.json surface: no platform read it"):
        _record(_delta(1, "1", _surface(platforms=())))
    with pytest.raises(RecordError, match=r"platform 'Plan 9' is not one of Linux"):
        _record(_delta(1, "1", _surface(platforms=("Plan 9",))))
    with pytest.raises(RecordError, match=r"a platform is listed twice"):
        _record(_delta(1, "1", _surface(platforms=("Linux", "Linux"))))
    with pytest.raises(RecordError, match=r"extractor is not a positive integer"):
        _record(_delta(1, "1", _surface(extractor=0)))


def test_the_first_surface_names_the_help_and_withdraws_nothing() -> None:
    with pytest.raises(RecordError, match=r"first surface names no help"):
        _record(_delta(1, "1", _surface(help=None)))
    with pytest.raises(
        RecordError, match=r"withdraws verb 'gone', which no earlier version has"
    ):
        _record(_delta(1, "1", _surface(verbs={"": _verb(), "gone": None})))


def test_a_restated_help_or_verb_is_refused_as_dead_data() -> None:
    first = _delta(1, "1", _surface())
    with pytest.raises(
        RecordError, match=r"0002-2\.json surface: restates help as it inherits it"
    ):
        _record(first, _delta(2, "2", _surface(help="A tool.", verbs={})))
    with pytest.raises(RecordError, match=r"restates verb 'build' as it inherits it"):
        _record(
            first,
            _delta(
                2, "2", _surface(help=None, verbs={"build": _verb("clean", "output")})
            ),
        )
    # Order inside a verb is layout, not content: the same options in
    # another order restate the verb.
    reordered = _verb("output", "clean")
    with pytest.raises(RecordError, match=r"restates verb 'build'"):
        _record(first, _delta(2, "2", _surface(help=None, verbs={"build": reordered})))


def test_a_verb_off_the_shape_is_refused_naming_the_field() -> None:
    with pytest.raises(RecordError, match=r"verb 'build': a verb carries exactly help"):
        _record(_delta(1, "1", _surface(verbs={"build": {"help": "x"}})))
    with pytest.raises(RecordError, match=r"verb 'build': wraps is not a boolean"):
        _record(_delta(1, "1", _surface(verbs={"build": _verb(wraps="no")})))
    with pytest.raises(RecordError, match=r"verb 'build': lead is not a string"):
        _record(_delta(1, "1", _surface(verbs={"build": _verb(lead=1)})))
    with pytest.raises(RecordError, match=r"verb 'build': options is not an object"):
        _record(_delta(1, "1", _surface(verbs={"build": _verb(options=[])})))
    with pytest.raises(RecordError, match=r"option 'x' carries exactly flags"):
        _record(_delta(1, "1", _surface(verbs={"build": _verb(options={"x": {}})})))
    with pytest.raises(RecordError, match=r"option 'x': help is not a string"):
        _record(
            _delta(
                1, "1", _surface(verbs={"": _verb(options={"x": _option(help=None)})})
            )
        )
    with pytest.raises(
        RecordError, match=r"option 'x': flags is not a list of strings"
    ):
        _record(
            _delta(1, "1", _surface(verbs={"": _verb(options={"x": _option(flags=1)})}))
        )


def test_an_absence_names_what_the_version_has_and_who_read_it() -> None:
    with pytest.raises(
        RecordError, match=r"absent names verb 'gone', which the version lacks"
    ):
        _record(_delta(1, "1", _surface(absent={"gone": {"": ("Linux",)}})))
    with pytest.raises(RecordError, match=r"absent names option 'x' of verb 'build'"):
        _record(_delta(1, "1", _surface(absent={"build": {"x": ("Linux",)}})))
    with pytest.raises(
        RecordError, match=r"absent names no platform for 'build' 'clean'"
    ):
        _record(_delta(1, "1", _surface(absent={"build": {"clean": ()}})))
    with pytest.raises(
        RecordError, match=r"absent names Windows, which did not read the version"
    ):
        _record(_delta(1, "1", _surface(absent={"build": {"clean": ("Windows",)}})))
    # A verb's whole absence is the option named "", and a verb inherited
    # from an earlier version is one the version has.
    record = _record(
        _delta(1, "1", _surface(platforms=("Linux", "Windows"))),
        _delta(
            2,
            "2",
            _surface(
                platforms=("Linux", "Windows"),
                help=None,
                verbs={"": _verb("quiet", "verbose")},
                absent={"build": {"": ("Windows",)}, "": {"verbose": ("Windows",)}},
            ),
        ),
    )
    assert surface_at(record, "2") is not None


def test_surface_json_off_the_shape_is_refused_naming_where() -> None:
    def load(surface: Any) -> RecordDelta:
        return RecordDelta.from_json(
            {"sequence": 1, "version": "1", "artifacts": {}, "surface": surface},
            where="d",
        )

    with pytest.raises(RecordError, match=r"d surface: not a JSON object"):
        load([])
    with pytest.raises(RecordError, match=r"d surface: unknown keys since"):
        load({"platforms": ["Linux"], "extractor": 1, "since": "1"})
    with pytest.raises(RecordError, match=r"d surface: no platforms"):
        load({"extractor": 1})
    with pytest.raises(RecordError, match=r"d surface: extractor is not a positive"):
        load({"platforms": ["Linux"], "extractor": True})
    with pytest.raises(RecordError, match=r"d surface: verbs is not an object"):
        load({"platforms": ["Linux"], "extractor": 1, "verbs": []})
    with pytest.raises(RecordError, match=r"verb 'x' is neither an object nor null"):
        load({"platforms": ["Linux"], "extractor": 1, "verbs": {"x": 1}})
    with pytest.raises(RecordError, match=r"d surface: absent is not an object"):
        load({"platforms": ["Linux"], "extractor": 1, "absent": []})
    with pytest.raises(RecordError, match=r"absent\[x\] is not an object"):
        load({"platforms": ["Linux"], "extractor": 1, "absent": {"x": []}})
    with pytest.raises(RecordError, match=r"absent\[x\]\[y\]: not a list of strings"):
        load({"platforms": ["Linux"], "extractor": 1, "absent": {"x": {"y": "Linux"}}})
    with pytest.raises(RecordError, match=r"d surface help: not a string"):
        load({"platforms": ["Linux"], "extractor": 1, "help": 1})


# --- inheritance ---------------------------------------------------------------


def test_a_version_inherits_every_verb_it_does_not_name_and_the_help() -> None:
    # Version 2 was never read: it carries an artifact alone, is left
    # out of the observations, and inherits nothing to version 3.
    record = Record(
        "tool",
        kind="archive",
        hosts=("linux-x64",),
        layout=Layout(entry_points=("tool",), paths=(".",)),
        deltas=(
            _delta(1, "1", _surface()),
            _delta(2, "2", None, artifacts={"linux-x64": Artifact("https://x/2", SHA)}),
            _delta(
                3,
                "3",
                _surface(
                    platforms=("macOS",),
                    extractor=5,
                    help=None,
                    verbs={"build": _verb("clean"), "run": _verb()},
                ),
            ),
            _delta(4, "4", _surface(help="A newer tool.", verbs={"run": None})),
        ),
    )
    seen = observations(record)
    assert [o.version for o in seen] == ["1", "3", "4"]
    assert surface_at(record, "2") is None
    one, three, four = seen
    assert isinstance(one, Observation)
    assert (one.help, sorted(one.verbs), one.platforms, one.extractor) == (
        "A tool.",
        ["", "build"],
        ("Linux",),
        4,
    )
    assert three.help == "A tool."  # inherited
    assert sorted(three.verbs) == ["", "build", "run"]
    assert three.verbs[""] == one.verbs[""]  # inherited whole
    assert list(three.verbs["build"]["options"]) == ["clean"]  # replaced whole
    assert (three.platforms, three.extractor, three.date) == (
        ("macOS",),
        5,
        "2026-01-03",
    )
    assert four.help == "A newer tool."
    assert sorted(four.verbs) == ["", "build"]  # run withdrawn
    assert four.verbs["build"] == three.verbs["build"]
    with pytest.raises(
        RecordError, match=r"no version '9'; the record tracks 1, 2, 3, 4"
    ):
        surface_at(record, "9")


def test_an_observation_orders_verbs_and_options_by_name() -> None:
    record = _record(
        _delta(
            1,
            "1",
            _surface(verbs={"run": _verb("z", "a"), "": _verb("m"), "build": _verb()}),
        )
    )
    (one,) = observations(record)
    assert list(one.verbs) == ["", "build", "run"]
    assert list(one.verbs["run"]["options"]) == ["a", "z"]
    assert list(one.verbs["run"]) == ["help", "wraps", "positional", "lead", "options"]
    assert list(one.verbs["run"]["options"]["a"]) == [
        "flags",
        "negation",
        "help",
        "type",
        "default",
        "choices",
    ]


# --- the round trip ---------------------------------------------------------


def test_a_surface_round_trips_through_save_and_load_sparse(tmp_path: Path) -> None:
    record = _record(
        _delta(1, "1", _surface(platforms=("Linux", "Windows"))),
        _delta(
            2,
            "2",
            _surface(
                platforms=("Windows", "Linux"),
                help=None,
                verbs={"build": None, "": _verb("quiet", "verbose")},
                absent={"": {"verbose": ("Windows",)}},
            ),
        ),
    )
    record.save(tmp_path / "tool")
    loaded = Record.load(tmp_path / "tool")
    assert loaded == record
    assert observations(loaded) == observations(record)
    written = json.loads(
        (tmp_path / "tool" / "deltas" / "0002-2.json").read_text("utf-8")
    )
    assert list(written) == ["sequence", "version", "date", "artifacts", "surface"]
    assert list(written["surface"]) == ["platforms", "extractor", "verbs", "absent"]
    assert written["surface"]["platforms"] == ["Windows", "Linux"]
    assert list(written["surface"]["verbs"]) == ["", "build"]  # name order
    assert written["surface"]["verbs"]["build"] is None
    assert written["surface"]["absent"] == {"": {"verbose": ["Windows"]}}
    first = json.loads(
        (tmp_path / "tool" / "deltas" / "0001-1.json").read_text("utf-8")
    )
    assert first["surface"]["help"] == "A tool."
    assert "absent" not in first["surface"]


def test_save_removes_a_delta_file_the_record_no_longer_names(tmp_path: Path) -> None:
    _record(
        _delta(1, "1", _surface()), _delta(2, "2", _surface(help="B", verbs={}))
    ).save(tmp_path / "tool")
    # The same versions renumbered, as an older version arriving does.
    _record(
        _delta(1, "0", _surface()),
        _delta(2, "1", _surface(help="A", verbs={})),
        _delta(3, "2", _surface(help="B", verbs={})),
    ).save(tmp_path / "tool")
    assert sorted(p.name for p in (tmp_path / "tool" / "deltas").iterdir()) == [
        "0001-0.json",
        "0002-1.json",
        "0003-2.json",
    ]
    assert Record.load(tmp_path / "tool").versions == ("0", "1", "2")
