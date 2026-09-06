"""Enum spellings: names, values, and the token — the ruled behaviour.

An enum member is a declared pair (name, value), optionally with aliases
(Python's duplicate-value bindings, collapse adopted verbatim). Input
honours the enumerated three-face set at every door; identity and every
developer-visible output speak exactly one face: the token, the member
name projected at declaration time to the neutral identifier grammar.
"""

from __future__ import annotations

import enum
import json

import pytest

from livery.footman import _coerce, _manifest
from livery.footman._describe import redact, returns_json_schema
from livery.footman._futures import _freeze
from livery.footman._manifest import returned_spec
from livery.footman.registry import Group


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


class Priority(enum.Enum):
    LOW_PRIORITY = 1
    MAX = 2
    HIGH = 2  # a compat rename: HIGH is now an alias of MAX


class MixedFaces(enum.Enum):
    # Module-level (eval_str resolves annotations here): the argv-door
    # collision under test — A and B both spell "1" on a string transport.
    A = 1
    B = "1"


def _tree(build):
    reg = Group("root")
    build(reg)
    return reg, _manifest.build_manifest(reg)["tree"]


# --- the token and its projection ---------------------------------------------


def test_the_token_is_the_projected_name():
    assert _coerce.token_of(Level.LOW) == "low"
    assert _coerce.token_of(Priority.LOW_PRIORITY) == "low-priority"
    # An alias-accessed member has no independent identity, so its token
    # is the canonical binding's.
    assert _coerce.token_of(Priority.HIGH) == "max"


def test_choices_speak_tokens_never_payload_values():
    # `Level.LOW = 1` shows low|high — the author named the numbers. The
    # 1|2 surface read as payload and completed nothing worth completing.
    choices, cls, _ = _coerce.element_choices(Level)
    assert choices == ["low", "high"]
    assert cls is Level


def test_aliases_are_accepted_not_taught():
    choices, _, _ = _coerce.element_choices(Priority)
    assert choices is not None
    assert choices == ["low-priority", "max"]  # canonical only
    accepts = _coerce.enum_accepts(Priority)
    assert "high" in accepts  # the alias keeps working…
    assert "high" not in choices  # …but is taught nowhere
    assert _coerce.enum_member_for(Priority, "high") is Priority.MAX


def test_the_three_faces_and_only_those():
    for text, member in (("low", Level.LOW), ("1", Level.LOW), ("2", Level.HIGH)):
        assert _coerce.enum_member_for(Level, text) is member
    # A raw member NAME is not a fourth face: its one spelling is the token.
    assert _coerce.enum_member_for(Level, "LOW") is None
    assert _coerce.enum_member_for(Level, "Low") is None


# --- declaration-time refusals ------------------------------------------------


def test_two_members_projecting_to_one_token_are_refused():
    class Clash(enum.Enum):
        LOW = 1
        Low = 2

    with pytest.raises(_coerce.EnumContractError, match="both project to"):
        _coerce.enum_faces(Clash)


def test_cross_member_face_collision_is_refused_not_adjudicated():
    # A=1 and B="1" collide on the argv door (both spell "1") though not
    # on the document door (JSON 1 and "1" are distinct) — any-door
    # intersection refuses, loudly, instead of picking a winner.
    class Mixed(enum.Enum):
        A = 1
        B = "1"

    with pytest.raises(_coerce.EnumContractError, match="ambiguously"):
        _coerce.enum_faces(Mixed)


def test_a_name_projecting_outside_the_grammar_is_refused():
    class Weird(enum.Enum):
        GOOD = 1
        BÖSE = 2

    with pytest.raises(_coerce.EnumContractError, match="token grammar"):
        _coerce.enum_faces(Weird)


def test_declaration_refusal_reaches_the_manifest_as_taught():
    def tasks(reg):
        @reg.task
        def go(x: MixedFaces = MixedFaces.A): ...

    with pytest.raises(_manifest.ManifestError, match=r"'x'.*ambiguously"):
        _tree(tasks)


# --- output: the wire face ----------------------------------------------------


def test_the_walk_carries_values_and_dict_keys_to_tokens():
    class Rank(enum.IntEnum):
        FIRST = 1
        SECOND = 2

    # IntEnum rides json.dumps' int fast path and dict keys never consult
    # the default hook for any type — the pre-walk is the one reliable
    # interception, and these are the two holes only this test catches.
    payload = {Rank.SECOND: [Level.HIGH], "plain": Rank.FIRST}
    assert json.dumps(redact(payload)) == '{"second": ["high"], "plain": "first"}'


def test_returns_side_moves_in_lockstep():
    spec = returned_spec(Level)
    assert spec is not None
    assert spec["values"] == ["low", "high"]
    assert spec["members"] == [
        {"token": "low", "value": 1},
        {"token": "high", "value": 2},
    ]
    # The JSON Schema projection is native over tokens — strings by
    # construction, no bolt-on.
    assert returns_json_schema(spec)["enum"] == ["low", "high"]


def test_alias_metadata_rides_the_members():
    spec = returned_spec(Priority)
    assert spec is not None
    by_token = {m["token"]: m for m in spec["members"]}
    assert by_token["max"]["aliases"] == ["high"]


# --- identity -----------------------------------------------------------------


def test_identity_is_the_token_so_renumbering_does_not_churn():
    class Before(enum.Enum):
        LOW = 1

    class After(enum.Enum):
        LOW = 10  # renumbered: the member is the meaning

    a = _freeze(Before.LOW)
    b = _freeze(After.LOW)
    assert a[2] == b[2] == "low"  # same token, value out of the key

    # And an IntEnum member no longer freezes as its int, so it cannot
    # collide with a plain 2 argument meaning something else.
    class Rank(enum.IntEnum):
        SECOND = 2

    assert _freeze(Rank.SECOND) != _freeze(2)
