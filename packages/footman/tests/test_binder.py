"""The document binder: JSON on stdin into dataclasses, lists and dicts.

Structural rules under test: unknown keys are ignored, missing keys follow
the dataclass, recursion covers nested dataclasses / `list[T]` / `T | None`,
scalar leaves reuse the one coercion pipeline, and every refusal names the
JSON path. A dataclass parameter is boundary-only: no flag, no positional —
the pipe is its only source.
"""

from __future__ import annotations

import datetime
import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, TypedDict

import pytest

from livery.footman import _manifest, context
from livery.footman._describe import param_detail, usage_fragment
from livery.footman._executor import EX_USAGE, run_chain
from livery.footman._split import ChainError, split_chain
from livery.footman.params import stdin
from livery.footman.registry import Group
from livery.footman.testing import Runner


# Payload shapes live at module level, where `eval_str` can see them.
class Size(NamedTuple):
    width: int
    height: int
    label: str = "px"


class Opts(TypedDict):
    name: str
    port: int


@dataclass
class ToolInput:
    file_path: str = ""
    command: str = ""


@dataclass
class Event:
    tool_name: str
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False
    cwd: Path | None = None


@dataclass
class Config:
    name: str
    port: int = 8080


@dataclass
class Nest:
    inner: Config
    label: str = ""


@dataclass
class Node:
    label: str
    children: list[Node]


@dataclass
class Row:
    name: str
    score: float
    kind: Literal["unit", "functional"] = "unit"


@dataclass
class Stamped:
    when: datetime.datetime
    note: str | None = None


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


class Rank(enum.IntEnum):
    FIRST = 1
    SECOND = 2


class Flagged(enum.Enum):
    YES = True
    NO = False


@dataclass
class Report:
    level: Level
    rank: Rank = Rank.FIRST


@dataclass
class Switch:
    flag: Flagged


@dataclass
class Wider:
    x: int | str | None = None


def build_tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


def run(build, line):
    reg, tree = build_tree(build)
    _, segments = split_chain(tree, line.split())
    return run_chain(reg, segments)


@pytest.fixture
def piped(monkeypatch):
    def _set(data: bytes | str | None):
        payload = data.encode() if isinstance(data, str) else data
        monkeypatch.setattr(context, "_stdin_payload", payload)

    _set(None)
    return _set


# --- the dataclass shape ------------------------------------------------------


def test_a_document_binds_a_dataclass(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]):
            seen["event"] = event

    piped(
        '{"tool_name": "Edit", "tool_input": {"file_path": "a.py"},'
        ' "prompt_id": "ignored-unknown-key"}'
    )
    run(tasks, "hook")
    event = seen["event"]
    assert event.tool_name == "Edit"
    assert event.tool_input.file_path == "a.py"  # nested, no dotted paths
    assert event.tool_input.command == ""  # nested default filled
    assert event.stop_hook_active is False  # missing key follows the default
    assert event.cwd is None


def test_leaves_coerce_like_cli_tokens(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]):
            seen["event"] = event

    piped('{"tool_name": "x", "cwd": "/tmp/build", "stop_hook_active": true}')
    run(tasks, "hook")
    assert seen["event"].cwd == Path("/tmp/build")
    assert seen["event"].stop_hook_active is True


def test_a_missing_defaultless_field_names_its_path(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped('{"stop_hook_active": false}')
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "'tool_name'" in str(results[0].error)
    assert "Event.tool_name" in str(results[0].error)


def test_a_wrong_type_names_the_json_path(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped('{"tool_name": "x", "tool_input": {"file_path": 3}}')
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "event.tool_input.file_path" in str(results[0].error)


def test_an_array_where_an_object_belongs_refuses(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    piped("[1, 2]")
    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE and "expected an object" in str(results[0].error)


def test_datetime_and_null_leaves(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def stamp(s: Annotated[Stamped, stdin]):
            seen["s"] = s

    piped('{"when": "2026-07-26T12:00:00", "note": null}')
    run(tasks, "stamp")
    assert seen["s"].when == datetime.datetime(2026, 7, 26, 12, 0, 0)
    assert seen["s"].note is None


def test_a_bad_literal_choice_names_the_path(piped):
    def tasks(reg):
        @reg.task
        def rows(items: Annotated[list[Row], stdin] = ()):  # type: ignore[assignment]
            ...

    piped('[{"name": "a", "score": 1.5, "kind": "imaginary"}]')
    results = run(tasks, "rows")
    assert results[0].code == EX_USAGE
    assert "items[0].kind" in str(results[0].error)
    assert "unit|functional" in str(results[0].error)


def test_a_wider_optional_union_still_admits_null(piped):
    # `int | str | None` refused a JSON null: the optional walk recognised
    # exactly-two-member `T | None` and a third member cost the union its
    # nullability (audit L1). Any union carrying None admits null; the
    # remainder keeps union shape and binds through the same walk.
    seen = {}

    def tasks(reg):
        @reg.task
        def hook(payload: Annotated[Wider, stdin]):
            seen["p"] = payload

    for raw, expect in (("null", None), ("3", 3), ('"hi"', "hi")):
        piped(f'{{"x": {raw}}}')
        results = run(tasks, "hook")
        assert results[0].code == 0, results[0].error
        assert seen["p"].x == expect


def test_an_enum_document_round_trips_speaking_tokens(piped):
    """The document footman prints is one it can read back — and it speaks
    tokens now: the redact pre-walk carries every enum (IntEnum included,
    which rides json.dumps' int fast path and never meets the hook) to its
    canonical token, and the binder reads the token straight back."""
    from livery.footman._describe import redact

    seen = {}

    def tasks(reg):
        @reg.task
        def grade(report: Annotated[Report, stdin]):
            seen["report"] = report

    document = json.dumps(redact(Report(Level.HIGH, Rank.SECOND)))
    assert document == '{"level": "high", "rank": "second"}'
    piped(document)
    results = run(tasks, "grade")
    assert results[0].code == 0, results[0].error
    assert seen["report"].level is Level.HIGH
    assert seen["report"].rank is Rank.SECOND


def test_an_enum_binds_every_declared_face_and_only_those(piped):
    """The three-face input set at the document door: token, JSON-typed
    value (bool/int guard intact), and the argv-spelled value string. A raw
    member NAME is deliberately not a fourth face — its one legal spelling
    is the token."""
    seen = {}

    def tasks(reg):
        @reg.task
        def grade(report: Annotated[Report, stdin]):
            seen["report"] = report

    piped('{"level": "high"}')  # the token
    run(tasks, "grade")
    assert seen["report"].level is Level.HIGH
    piped('{"level": 2}')  # the JSON-typed value face — the foreign document
    run(tasks, "grade")
    assert seen["report"].level is Level.HIGH
    piped('{"level": "2"}')  # the stringified value face
    run(tasks, "grade")
    assert seen["report"].level is Level.HIGH
    seen.clear()
    piped('{"level": "HIGH"}')  # the raw NAME: not a face, refused
    results = run(tasks, "grade")
    assert results[0].code != 0
    assert "report" not in seen


def test_a_bool_does_not_answer_an_int_valued_member(piped):
    """`1 == True` in Python, so a member has to match on type as well."""

    def numeric(reg):
        @reg.task
        def grade(report: Annotated[Report, stdin]): ...

    def boolean(reg):
        @reg.task
        def flip(switch: Annotated[Switch, stdin]): ...

    piped('{"level": true}')
    results = run(numeric, "grade")
    assert results[0].code == EX_USAGE
    assert "report.level" in str(results[0].error)

    piped('{"flag": 1}')
    results = run(boolean, "flip")
    assert results[0].code == EX_USAGE
    assert "switch.flag" in str(results[0].error)


def test_a_list_of_dataclasses_binds(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def rows(items: Annotated[list[Row], stdin] = ()):  # type: ignore[assignment]
            seen["items"] = items

    piped('[{"name": "a", "score": 1}, {"name": "b", "score": 2.5}]')
    run(tasks, "rows")
    assert [r.name for r in seen["items"]] == ["a", "b"]
    assert seen["items"][0].score == 1.0  # a JSON int fits a float field


# --- dict and list shapes -----------------------------------------------------


def test_an_unshaped_dict_passes_the_document_through(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def raw(doc: Annotated[dict[str, Any], stdin] = {}):
            seen["doc"] = doc

    piped('{"anything": [1, {"nested": true}]}')
    run(tasks, "raw")
    assert seen["doc"] == {"anything": [1, {"nested": True}]}


def test_typed_dict_values_recurse(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def scores(by_name: Annotated[dict[str, int], stdin] = {}):
            seen["by_name"] = by_name

    piped('{"a": 1, "b": 2}')
    run(tasks, "scores")
    assert seen["by_name"] == {"a": 1, "b": 2}


def test_a_json_array_binds_a_list_parameter(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def audit(names: Annotated[list[str], stdin] = ()):  # type: ignore[assignment]
            seen["names"] = names

    piped('["a", "b"]')
    run(tasks, "audit")
    assert seen["names"] == ["a", "b"]


def test_cli_still_beats_a_json_list(piped):
    seen = {}

    def tasks(reg):
        @reg.task
        def audit(names: Annotated[list[str], stdin] = ()):  # type: ignore[assignment]
            seen["names"] = names

    piped('["piped"]')
    run(tasks, "audit --names=explicit")
    assert seen["names"] == ["explicit"]


# --- boundary-only: no CLI surface --------------------------------------------


def test_a_dataclass_parameter_is_not_a_flag(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    _, tree = build_tree(tasks)
    with pytest.raises(ChainError, match="--event"):
        split_chain(tree, ["hook", "--event", "x"])


def test_the_manifest_records_the_shape():
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    _, tree = build_tree(tasks)
    spec = tree["tasks"]["hook"]["params"][0]
    assert spec["kind"] == "stdin"
    assert spec["stdin"] == "json"
    assert usage_fragment(spec) == ""  # no token spelling in usage
    assert "reads stdin (JSON document → Event)" in param_detail(spec)
    # The shape is data a machine can build the document from, not a name it
    # has to already know the meaning of.
    assert spec["shape"]["name"] == "Event"
    fields = {f["name"]: f for f in spec["shape"]["fields"]}
    assert fields["tool_name"] == {
        "name": "tool_name",
        "types": ["str"],
        "required": True,
    }
    assert fields["stop_hook_active"] == {"name": "stop_hook_active", "types": ["bool"]}
    assert fields["cwd"] == {"name": "cwd", "types": ["path"]}
    # A nested record is described in turn, so a reader never has to already
    # know what a `ToolInput` is.
    assert fields["tool_input"]["shape"]["name"] == "ToolInput"
    assert fields["tool_input"]["shape"]["fields"]


def test_required_document_without_a_pipe_teaches_the_fixture(piped):
    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Event, stdin]): ...

    results = run(tasks, "hook")
    assert results[0].code == EX_USAGE
    assert "payload.json" in str(results[0].error)


def test_a_local_dataclass_is_a_taught_spec_error():
    @dataclass
    class Local:
        x: int = 0

    def tasks(reg):
        @reg.task
        def hook(event: Annotated[Local, stdin] = None): ...  # type: ignore[assignment]

    with pytest.raises(_manifest.SpecError, match="module-level"):
        build_tree(tasks)


# --- end to end ---------------------------------------------------------------


def test_runner_replays_a_fixture_payload():
    reg = Group("root")

    @reg.task
    def hook(event: Annotated[Event, stdin]):
        print("active" if event.stop_hook_active else "quiet")

    result = Runner().invoke(
        "hook", tasks=reg, stdin='{"tool_name": "Stop", "stop_hook_active": true}'
    )
    assert result.ok and "active" in result.stdout


def test_a_named_tuple_binds_from_a_json_object(piped):
    """A `NamedTuple` is a record like a dataclass, and binds like one.

    It used to fail every test in `is_document_target` — a `tuple`
    subclass, so `get_origin` is `None` — fall through to the text path,
    and hand the body a raw string with no warning at all.
    """
    seen = {}

    def tasks(reg):
        @reg.task
        def show(s: Annotated[Size, stdin]):
            seen["s"] = s

    piped('{"width": 800, "height": 600}')
    run(tasks, "show")
    assert seen["s"] == Size(800, 600, "px")
    assert isinstance(seen["s"], Size)


def test_a_typed_dict_binds_from_a_json_object(piped):
    """A `TypedDict` is a record too; called with keywords it is the dict
    it always was at runtime."""
    seen = {}

    def tasks(reg):
        @reg.task
        def show(o: Annotated[Opts, stdin]):
            seen["o"] = o

    piped('{"name": "web", "port": 8080}')
    run(tasks, "show")
    assert seen["o"] == {"name": "web", "port": 8080}


def test_a_record_still_refuses_a_missing_required_field(piped):
    """The defaulted field is optional, the others are not — the same rule
    dataclasses already followed, now read from one helper."""

    def tasks(reg):
        @reg.task
        def show(s: Annotated[Size, stdin]): ...

    piped('{"width": 800}')
    results = run(tasks, "show")
    assert results[0].code == EX_USAGE
    assert "no 'height' field" in str(results[0].error)


def test_the_shape_describes_every_record_the_same_way():
    """A dataclass, a NamedTuple and a TypedDict all bind the same JSON
    object, so they describe themselves the same way. They used not to: only
    a dataclass carried a shape at all, which is the manifest holding the
    opinion about records that the binder had already given up."""

    def tasks(reg):
        @reg.task
        def one(v: Annotated[Config, stdin]): ...

        @reg.task
        def two(v: Annotated[Size, stdin]): ...

        @reg.task
        def three(v: Annotated[Opts, stdin]): ...

    _, tree = build_tree(tasks)
    shapes = {
        name: tree["tasks"][name]["params"][0]["shape"]
        for name in ("one", "two", "three")
    }
    assert [f["name"] for f in shapes["one"]["fields"]] == ["name", "port"]
    assert [f["name"] for f in shapes["two"]["fields"]] == [
        "width",
        "height",
        "label",
    ]
    assert [f["name"] for f in shapes["three"]["fields"]] == ["name", "port"]
    # Required is per-field and comes from the shape's own rules: a default,
    # a `_field_defaults` entry, a TypedDict's `__required_keys__`.
    assert shapes["one"]["fields"][1].get("required") is None  # port defaults
    assert shapes["two"]["fields"][1]["required"] is True  # height has none
    assert shapes["two"]["fields"][2].get("required") is None  # label = "px"
    assert shapes["three"]["fields"][1]["required"] is True  # a total TypedDict


def test_a_shape_with_a_command_line_spelling_keeps_it():
    """A record whose slots are all scalars can be typed as well as piped,
    and the command line wins when both are given. A record holding another
    record cannot: no token can say where the inner one ends."""

    def tasks(reg):
        @reg.task
        def flat(v: Annotated[Config, stdin]): ...

        @reg.task
        def nested(v: Annotated[Nest, stdin]): ...

    _, tree = build_tree(tasks)
    flat = tree["tasks"]["flat"]["params"][0]
    nested = tree["tasks"]["nested"]["params"][0]
    assert flat["kind"] == "option"
    assert flat["group"]["label"] == "name,port"
    assert usage_fragment(flat) == "[--v=name,port]"
    assert nested["kind"] == "stdin"
    assert "group" not in nested
    assert usage_fragment(nested) == ""
    # Either way the document schema is there, because either way it reads
    # the pipe.
    assert flat["shape"]["name"] == "Config"
    assert nested["shape"]["fields"][0]["shape"]["name"] == "Config"


def test_a_recursive_shape_is_named_rather_than_expanded():
    """`Node.children` is a list of `Node`. There is no finite expansion, and
    the shape appears in full above, so the name is the description."""

    def tasks(reg):
        @reg.task
        def walk(v: Annotated[Node, stdin]): ...

    _, tree = build_tree(tasks)
    shape = tree["tasks"]["walk"]["params"][0]["shape"]
    children = shape["fields"][1]
    assert children["many"] == "list"
    assert children["shape"] == {"name": "Node"}  # named, not expanded
