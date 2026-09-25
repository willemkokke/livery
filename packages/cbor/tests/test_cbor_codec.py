"""The refusals a vector file cannot spell, then the behaviour around them.

JSON cannot carry a tuple, a set, a non-text key, a lone surrogate or a
nesting of five hundred levels, so those refusals are pinned here, in
the order the codec's rules list them, before any success path.
"""

from __future__ import annotations

import math

import pytest

from livery.cbor import MAX_DEPTH, CodecError, decode, encode


def test_refuses_a_type_outside_the_subset() -> None:
    with pytest.raises(CodecError, match="no canonical form for tuple"):
        encode((1, 2))  # type: ignore[arg-type]
    with pytest.raises(CodecError, match="no canonical form for set"):
        encode({1})  # type: ignore[arg-type]


def test_refuses_a_map_key_that_is_not_text() -> None:
    with pytest.raises(CodecError, match="map key is not a text string"):
        encode({1: "a"})  # type: ignore[dict-item]


def test_refuses_a_lone_surrogate_in_text() -> None:
    with pytest.raises(CodecError, match="not valid UTF-8"):
        encode("\ud800")
    with pytest.raises(CodecError, match="not valid UTF-8"):
        encode({"\udfff": 1})


def test_refuses_nesting_past_the_bound_on_encode() -> None:
    nested: list[object] = []
    for _ in range(MAX_DEPTH):
        nested = [nested]
    with pytest.raises(CodecError, match=f"nesting deeper than {MAX_DEPTH}"):
        encode(nested)
    mapped: dict[str, object] = {}
    for _ in range(MAX_DEPTH):
        mapped = {"a": mapped}
    with pytest.raises(CodecError, match=f"nesting deeper than {MAX_DEPTH}"):
        encode(mapped)


def test_refuses_nesting_past_the_bound_on_decode() -> None:
    with pytest.raises(CodecError, match=f"nesting deeper than {MAX_DEPTH}"):
        decode(b"\x81" * (MAX_DEPTH + 1) + b"\x80")
    with pytest.raises(CodecError, match=f"nesting deeper than {MAX_DEPTH}"):
        decode(b"\xa1\x61\x61" * (MAX_DEPTH + 1) + b"\xa0")


def test_accepts_nesting_at_the_bound() -> None:
    nested: list[object] = []
    for _ in range(MAX_DEPTH - 1):
        nested = [nested]
    assert decode(encode(nested)) == nested


def test_the_error_names_the_offset_of_a_bad_argument() -> None:
    with pytest.raises(CodecError, match="offset 1"):
        decode(b"\x81\x18\x01")
    with pytest.raises(CodecError, match="offset 2"):
        decode(b"\x82\x01\x1f")


def test_a_bool_is_never_an_integer() -> None:
    assert encode(True) == b"\xf5"
    assert encode(1) == b"\x01"
    assert decode(b"\xf5") is True
    assert decode(b"\x01") == 1
    assert type(decode(b"\x01")) is int


def test_the_negative_boundary_is_exact() -> None:
    assert encode(-(2**64)) == b"\x3b" + b"\xff" * 8
    with pytest.raises(CodecError, match="integer past 64 bits"):
        encode(-(2**64) - 1)
    assert encode(2**64 - 1) == b"\x1b" + b"\xff" * 8
    with pytest.raises(CodecError, match="integer past 64 bits"):
        encode(2**64)


def test_floats_take_the_shortest_exact_width() -> None:
    assert encode(0.5) == b"\xf9\x38\x00"
    assert encode(0.1) == b"\xfb\x3f\xb9\x99\x99\x99\x99\x99\x9a"
    assert encode(2.0**-24) == b"\xf9\x00\x01"  # the smallest half subnormal
    assert encode(2.0**-30) == b"\xfa\x30\x80\x00\x00"  # below half, exact in single
    assert encode(-0.0) == b"\xf9\x80\x00"
    assert encode(math.inf) == b"\xf9\x7c\x00"
    assert encode(float("-inf")) == b"\xf9\xfc\x00"
    assert encode(float("nan")) == b"\xf9\x7e\x00"
    assert encode(-float("nan")) == b"\xf9\x7e\x00"


def test_a_signed_zero_decodes_with_its_sign() -> None:
    zero = decode(b"\xf9\x80\x00")
    assert isinstance(zero, float)
    assert math.copysign(1.0, zero) == -1.0


def test_map_keys_sort_by_their_encoded_bytes_whatever_the_input_order() -> None:
    one = encode({"b": 1, "aa": 2, "z": 3, "": 4})
    two = encode({"": 4, "z": 3, "aa": 2, "b": 1})
    assert one == two == b"\xa4\x60\x04\x61\x62\x01\x61\x7a\x03\x62\x61\x61\x02"


def test_decode_returns_plain_python_types() -> None:
    value = decode(encode({"a": [1, b"\x00", "x", None, False, 1.5]}))
    assert value == {"a": [1, b"\x00", "x", None, False, 1.5]}
    assert isinstance(value, dict)
    assert type(value["a"][1]) is bytes


def test_a_second_encoding_of_one_value_is_refused_where_it_matters() -> None:
    # The hazard the strict decoder exists for: `1` in two bytes is the
    # same integer and would be a second name for the same tree.
    assert decode(b"\x01") == 1
    with pytest.raises(CodecError, match="not the shortest form"):
        decode(b"\x18\x01")
