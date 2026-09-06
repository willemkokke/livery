"""Structured returns: the return annotation as the output contract.

The mirror of the input story — `returned_spec` turns the annotation into a
native spec baked in the manifest, the envelope carries it per entry, the
producer-side check walks every declared value against it, and `--describe`
renders the whole tree's contract as JSON Schema. Golden-pair tests hold the
renderer to "the native shape never says anything JSON Schema cannot".
"""

from __future__ import annotations

import datetime
import decimal
import enum
import json
import typing
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict

from livery.footman import _manifest, markdown
from livery.footman._describe import (
    returned_mismatch,
    returns_json_schema,
    returns_phrase,
)
from livery.footman._executor import EX_USAGE
from livery.footman._manifest import returned_spec
from livery.footman.params import Stdout, suggest
from livery.footman.registry import Group
from livery.footman.testing import Runner


@dataclass
class Affected:
    tasks: list[str]
    reason: str
    since: str


@dataclass
class Nested:
    inner: Affected
    when: datetime.datetime | None


@dataclass
class Recursive:
    children: list[Recursive]


class Extras(TypedDict):
    kind: str
    detail: NotRequired[int]


class Point(NamedTuple):
    x: int
    y: int


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


AFFECTED_SPEC = {
    "kind": "object",
    "name": "Affected",
    "fields": {
        "tasks": {"kind": "list", "items": {"kind": "str"}},
        "reason": {"kind": "str"},
        "since": {"kind": "str"},
    },
}


def invoke(build, line, **kw):
    reg = Group("root")
    build(reg)
    return Runner().invoke(line, tasks=reg, **kw)


# --- the generator: annotation → native spec ----------------------------------


def test_dataclass_describes_recursively():
    assert returned_spec(Nested) == {
        "kind": "object",
        "name": "Nested",
        "fields": {
            "inner": AFFECTED_SPEC,
            "when": {"kind": "datetime", "nullable": True},
        },
    }


def test_typeddict_marks_notrequired_fields():
    assert returned_spec(Extras) == {
        "kind": "object",
        "name": "Extras",
        "fields": {
            "kind": {"kind": "str"},
            "detail": {"kind": "int", "required": False},
        },
    }


def test_namedtuple_is_a_row_not_an_object():
    # A NamedTuple is a tuple: json serialises it as an *array*, and the
    # spec says so instead of claiming an object that never appears.
    assert returned_spec(Point) == {
        "kind": "row",
        "name": "Point",
        "fields": {"x": {"kind": "int"}, "y": {"kind": "int"}},
    }


def test_the_scalar_bridge_types():
    assert returned_spec(Path) == {"kind": "path"}
    assert returned_spec(datetime.date) == {"kind": "date"}
    assert returned_spec(datetime.time) == {"kind": "time"}
    assert returned_spec(uuid.UUID) == {"kind": "uuid"}
    assert returned_spec(decimal.Decimal) == {"kind": "decimal"}
    assert returned_spec(bool) == {"kind": "bool"}


def test_containers_and_choices():
    assert returned_spec(set[str]) == {"kind": "list", "items": {"kind": "str"}}
    assert returned_spec(tuple[int, ...]) == {
        "kind": "list",
        "items": {"kind": "int"},
    }
    assert returned_spec(dict[str, int]) == {"kind": "map", "values": {"kind": "int"}}
    assert returned_spec(dict[str, Any]) == {"kind": "object"}
    assert returned_spec(Literal["pass", "fail", 3]) == {
        "kind": "enum",
        "values": ["pass", "fail", 3],
    }
    assert returned_spec(Colour) == {
        "kind": "enum",
        "name": "Colour",
        # `values` speaks tokens (what documents carry; what the JSON
        # Schema projection lists natively) and `members` carries the
        # declared pairs first-class for foreign frontends.
        "values": ["red", "blue"],
        "members": [
            {"token": "red", "value": "red"},
            {"token": "blue", "value": "blue"},
        ],
    }


def test_nullable_rides_the_member():
    spec = returned_spec(Affected | None)
    assert spec is not None
    assert spec["nullable"] is True
    assert spec["kind"] == "object"


def test_stdout_describes_the_inner_type():
    # One declaration, two doors: the document and the returned value.
    spec = returned_spec(Stdout[Affected])
    assert spec is not None
    assert spec["name"] == "Affected"


def test_outside_the_set_means_no_claims_never_an_error():
    for ann in (
        None,  # -> None: nothing to say
        int,  # the exit-code channel
        Stdout[int],
        Any,
        int | str,  # no json_default story for a wide union
        tuple[str, int],  # a heterogeneous tuple has no named positions
        dict[int, str],  # JSON object keys are strings
        Recursive,  # no finite spec to bake
    ):
        assert returned_spec(ann) is None, ann


def test_a_missing_or_broken_annotation_makes_no_claims(capsys):
    def missing():
        pass

    sig = _manifest.resolved_signature(missing)
    assert returned_spec(sig.return_annotation) is None

    def broken():
        pass

    broken.__annotations__ = {"return": "NoSuchType"}  # PEP-563 string
    _manifest._warned.clear()
    sig = _manifest.resolved_signature(broken)
    err = capsys.readouterr().err
    assert "<return>" in err and "did not resolve" in err
    assert "UserWarning" not in err
    assert returned_spec(sig.return_annotation) is None


def test_bare_int_is_the_exit_code_channel_even_nested_ints_describe():
    assert returned_spec(list[int]) == {"kind": "list", "items": {"kind": "int"}}
    assert returned_spec(int | None) is None  # still the exit-code channel


# --- golden pairs: native spec → JSON Schema ----------------------------------


def test_object_schema_golden_pair():
    spec = returned_spec(Affected)
    assert spec is not None
    assert returns_json_schema(spec) == {
        "title": "Affected",
        "type": "object",
        "properties": {
            "tasks": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "since": {"type": "string"},
        },
        "additionalProperties": False,
        "required": ["tasks", "reason", "since"],
    }


def test_typeddict_schema_requires_only_the_required():
    spec = returned_spec(Extras)
    assert spec is not None
    schema = returns_json_schema(spec)
    assert schema["required"] == ["kind"]
    assert schema["properties"]["detail"] == {"type": "integer"}


def test_row_schema_is_a_fixed_array():
    spec = returned_spec(Point)
    assert spec is not None
    assert returns_json_schema(spec) == {
        "title": "Point",
        "type": "array",
        "prefixItems": [{"type": "integer"}, {"type": "integer"}],
        "minItems": 2,
        "maxItems": 2,
    }


def test_scalar_and_nullable_schemas():
    assert returns_json_schema({"kind": "datetime"}) == {
        "type": "string",
        "format": "date-time",
    }
    assert returns_json_schema({"kind": "uuid"}) == {
        "type": "string",
        "format": "uuid",
    }
    assert returns_json_schema({"kind": "decimal"}) == {"type": "string"}
    assert returns_json_schema({"kind": "any"}) == {}
    assert returns_json_schema({"kind": "str", "nullable": True}) == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }
    assert returns_json_schema({"kind": "map", "values": {"kind": "float"}}) == {
        "type": "object",
        "additionalProperties": {"type": "number"},
    }
    assert returns_json_schema({"kind": "object"}) == {"type": "object"}
    assert returns_json_schema({"kind": "enum", "values": ["a", 1]}) == {
        "enum": ["a", 1]
    }


# --- the producer-side check --------------------------------------------------


def test_the_declared_instance_and_its_dict_twin_both_pass():
    value = Affected(tasks=["a"], reason="r", since="s")
    assert returned_mismatch(value, AFFECTED_SPEC) is None
    twin = {"tasks": [], "reason": "r", "since": "s"}
    assert returned_mismatch(twin, AFFECTED_SPEC) is None


def test_a_rename_is_a_missing_key_then_an_undeclared_one():
    renamed = {"tasks": [], "why": "r", "since": "s"}
    assert returned_mismatch(renamed, AFFECTED_SPEC) == "returned: missing key 'reason'"
    extra = {"tasks": [], "reason": "r", "since": "s", "why": "r"}
    assert returned_mismatch(extra, AFFECTED_SPEC) == "returned: undeclared key 'why'"


def test_the_first_break_names_its_path():
    value = {"tasks": ["ok", 3], "reason": "r", "since": "s"}
    note = returned_mismatch(value, AFFECTED_SPEC)
    assert note == "returned.tasks[1]: expected text, got int"


def test_scalar_families_follow_the_serialised_shape():
    # The check mirrors the JSON a consumer reads: a Path satisfies "path"
    # and so does the string it would serialise to; bool never passes int.
    assert returned_mismatch(Path("x"), {"kind": "path"}) is None
    assert returned_mismatch("x", {"kind": "path"}) is None
    assert returned_mismatch(True, {"kind": "int"}) is not None
    assert returned_mismatch(3, {"kind": "float"}) is None
    assert returned_mismatch(datetime.datetime.now(), {"kind": "date"}) is None
    assert returned_mismatch(decimal.Decimal("1.5"), {"kind": "decimal"}) is None
    assert returned_mismatch(1.5, {"kind": "decimal"}) is not None
    assert returned_mismatch(None, {"kind": "str", "nullable": True}) is None
    assert returned_mismatch(None, {"kind": "str"}) is not None


def test_enum_accepts_member_and_raw_value():
    spec = {"kind": "enum", "values": ["red", "blue"]}
    assert returned_mismatch(Colour.RED, spec) is None
    assert returned_mismatch("blue", spec) is None
    note = returned_mismatch("green", spec)
    assert note is not None and "'green'" in note


def test_rows_and_maps():
    row = returned_spec(Point)
    assert row is not None
    assert returned_mismatch(Point(1, 2), row) is None
    assert returned_mismatch((1, 2, 3), row) is not None
    note = returned_mismatch(Point(1, "y"), row)  # type: ignore[arg-type]
    assert note == "returned.y: expected an integer, got str"

    counts = {"kind": "map", "values": {"kind": "int"}}
    assert returned_mismatch({"a": 1}, counts) is None
    assert returned_mismatch({1: 1}, counts) == "returned: key 1 is not a string"
    assert returned_mismatch({"a": "x"}, counts) is not None


def test_optional_typeddict_fields_may_be_absent():
    spec = returned_spec(Extras)
    assert spec is not None
    assert returned_mismatch({"kind": "k"}, spec) is None
    assert returned_mismatch({"kind": "k", "detail": 3}, spec) is None
    assert returned_mismatch({"kind": "k", "detail": "x"}, spec) is not None


def test_a_different_dataclass_is_checked_by_its_fields():
    @dataclass
    class Wrong:
        tasks: list[str]
        why: str

    note = returned_mismatch(Wrong(tasks=[], why="r"), AFFECTED_SPEC)
    assert note == "returned: missing key 'reason'"


# --- the manifest bakes the contract ------------------------------------------


def build_tree(build) -> dict[str, Any]:
    reg = Group("root")
    build(reg)
    tree: dict[str, Any] = _manifest.build_manifest(reg)["tree"]
    return tree


def tasks_returning_reports(reg):
    @reg.task
    def affected() -> Affected:
        """The affected task set.

        Returns:
            Which tasks the change reaches, and why.
        """
        return Affected(tasks=["a", "b"], reason="lockfile", since="abc123")

    @reg.task
    def liar() -> Affected:
        """Claims Affected, returns a rename."""
        return {"tasks": ["a"], "why": "renamed", "since": "abc"}  # type: ignore[return-value]

    @reg.task
    def plain() -> int:
        """Exit-code channel."""
        return 0


def test_the_manifest_carries_returned_and_its_doc():
    tree = build_tree(tasks_returning_reports)
    node = tree["tasks"]["affected"]
    assert node["returned"] == AFFECTED_SPEC
    assert node["returned_doc"] == "Which tasks the change reaches, and why."
    assert "returned" not in tree["tasks"]["plain"]
    assert "returned_doc" not in tree["tasks"]["plain"]


# --- the envelope -------------------------------------------------------------


def test_the_envelope_carries_schema_beside_the_data():
    result = invoke(tasks_returning_reports, "--json affected")
    entry = json.loads(result.stdout)["items"][0]
    assert entry["returned"] == {
        "tasks": ["a", "b"],
        "reason": "lockfile",
        "since": "abc123",
    }
    assert entry["returned_schema"] == AFFECTED_SPEC
    assert "returned_mismatch" not in entry


def test_a_mismatch_is_a_note_never_an_exit_code():
    result = invoke(tasks_returning_reports, "--json liar")
    assert result.exit_code == 0
    entry = json.loads(result.stdout)["items"][0]
    assert entry["returned_mismatch"] == "returned: missing key 'reason'"
    assert entry["returned"]["why"] == "renamed"  # the value still serialises
    assert "missing key 'reason'" in result.stderr


def test_the_mismatch_warns_in_plain_runs_too():
    # The producer's own gate sees the rename go red without --json.
    result = invoke(tasks_returning_reports, "liar")
    assert result.exit_code == 0
    assert "liar: return value breaks its declared shape" in result.stderr


def test_an_undeclared_task_gets_no_schema_keys():
    result = invoke(tasks_returning_reports, "--json plain")
    entry = json.loads(result.stdout)["items"][0]
    assert "returned_schema" not in entry
    assert "returned_mismatch" not in entry


def test_a_prerequisite_entry_carries_its_own_schema():
    def tasks(reg):
        @reg.task
        def affected() -> Affected:
            return Affected(tasks=[], reason="r", since="s")

        @reg.task(pre=[affected])
        def check():
            pass

    result = invoke(tasks, "--json check")
    items = json.loads(result.stdout)["items"]
    by_task = {e["task"]: e for e in items}
    assert by_task["affected"]["returned_schema"]["name"] == "Affected"
    assert "returned_schema" not in by_task["check"]


# --- the describe door --------------------------------------------------------


def test_bare_describe_dumps_the_whole_contract_sorted():
    result = invoke(tasks_returning_reports, "--describe")
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["schema"] == 1
    addresses = [e["task"] for e in doc["tasks"]]
    assert addresses == sorted(addresses)
    by_task = {e["task"]: e for e in doc["tasks"]}
    schema = by_task["affected"]["returns"]["schema"]
    assert schema["title"] == "Affected"
    assert schema["additionalProperties"] is False
    assert by_task["affected"]["returns"]["doc"].startswith("Which tasks")
    assert "returns" not in by_task["plain"]


def grouped_tasks(reg):
    docs = reg.group("docs", "documentation")

    @docs.task
    def build() -> bool:
        return True

    @docs.task
    def serve():
        pass

    shots = docs.group("shots", "screenshots")

    @shots.task
    def cast() -> Literal["ok"]:
        return "ok"

    @reg.task
    def top():
        pass


def test_describe_a_group_answers_for_its_subtree():
    # The prefix-names-a-subtree rule, on the contract surface: a group
    # address is every task under it, nested groups included, sorted —
    # the wildcard-shaped ask without a glob in the grammar.
    result = invoke(grouped_tasks, "--describe=docs")
    doc = json.loads(result.stdout)
    assert [e["task"] for e in doc["tasks"]] == [
        "docs.build",
        "docs.serve",
        "docs.shots.cast",
    ]
    result = invoke(grouped_tasks, "--describe=docs.shots")
    doc = json.loads(result.stdout)
    assert [e["task"] for e in doc["tasks"]] == ["docs.shots.cast"]


def test_describe_a_runnable_group_is_its_subtree_default_included():
    def tasks(reg):
        lint = reg.group("lint", "linters")

        @lint.default
        def run_all() -> Literal["clean", "dirty"]:
            return "clean"

        @lint.task
        def markdown():
            pass

    # The group address answers for the namespace; the default rides in it
    # under its real child address, which also answers alone.
    result = invoke(tasks, "--describe=lint")
    doc = json.loads(result.stdout)
    assert [e["task"] for e in doc["tasks"]] == ["lint.default", "lint.markdown"]
    assert doc["tasks"][0]["returns"]["schema"] == {"enum": ["clean", "dirty"]}

    result = invoke(tasks, "--describe=lint.default")
    doc = json.loads(result.stdout)
    assert [e["task"] for e in doc["tasks"]] == ["lint.default"]
    assert doc["tasks"][0]["returns"]["schema"] == {"enum": ["clean", "dirty"]}


def test_describe_unknown_address_teaches_groups_too():
    result = invoke(tasks_returning_reports, "--describe=affceted")
    assert result.exit_code == EX_USAGE
    assert "unknown task or group 'affceted'" in result.stderr
    assert "did you mean 'affected'?" in result.stderr

    result = invoke(grouped_tasks, "--describe=dcos")
    assert result.exit_code == EX_USAGE
    assert "did you mean 'docs'?" in result.stderr


def test_bare_describe_with_a_trailing_task_teaches_the_spelling():
    result = invoke(tasks_returning_reports, "--describe affected")
    assert result.exit_code == EX_USAGE
    assert "--describe=<addr>" in result.stderr


def test_describe_drops_a_dynamic_completers_baked_choices():
    def tasks(reg):
        @reg.task
        def deploy(target: typing.Annotated[str, suggest(lambda: ["a", "b"])]):
            pass

    result = invoke(tasks, "--describe=deploy")
    param = json.loads(result.stdout)["tasks"][0]["params"][0]
    assert "choices" not in param  # runtime data, not contract
    assert param["dynamic"] == {"strict": True}


def test_describe_marks_hidden_tasks():
    def tasks(reg):
        @reg.task(hidden=True)
        def secret() -> bool:
            return True

    result = invoke(tasks, "--describe")
    entry = json.loads(result.stdout)["tasks"][0]
    assert entry["hidden"] is True


# --- help and docs ------------------------------------------------------------


def test_help_gains_a_returns_line():
    result = invoke(tasks_returning_reports, "affected --help")
    assert "returns:" in result.stdout
    assert "Which tasks the change reaches, and why." in result.stdout
    assert "Affected {tasks, reason, since}" in result.stdout


def test_help_without_a_declaration_stays_quiet():
    result = invoke(tasks_returning_reports, "plain --help")
    assert "returns:" not in result.stdout


def test_markdown_page_shows_the_fields():
    tree = build_tree(tasks_returning_reports)
    page = markdown.render_page(tree, path=("affected",))
    assert "**Returns:**" in page
    assert "Which tasks the change reaches, and why." in page
    assert "| `reason` | text |" in page


def test_phrases_read_like_words():
    assert returns_phrase({"kind": "list", "items": {"kind": "int"}}) == (
        "a list of integers"
    )
    assert returns_phrase({"kind": "map", "values": {"kind": "str"}}) == (
        "a mapping of text"
    )
    assert returns_phrase({"kind": "str", "nullable": True}) == "text (or null)"
    assert returns_phrase({"kind": "enum", "values": ["on", "off"]}) == "one of on|off"
    assert returns_phrase({"kind": "row", "name": "Point", "fields": {"x": {}}}) == (
        "Point (x)"
    )
