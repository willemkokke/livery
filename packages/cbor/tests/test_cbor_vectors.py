"""The golden vectors under spec/vectors drive these tests.

The vector file is data: a case pins the one encoding of a value, a
refusal pins the rule that rejects an input, by a phrase the error
must contain. A vector added to the file is a test added here, by
parametrisation, and a second implementation runs the same file.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from livery.cbor import CodecError, decode, encode

VECTORS = Path(__file__).resolve().parents[1] / "spec" / "vectors" / "codec.json"


def _load() -> dict[str, list[dict[str, Any]]]:
    loaded: dict[str, list[dict[str, Any]]] = json.loads(VECTORS.read_text("utf-8"))
    return loaded


def _params(key: str) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in _load()[key]]


def _hex(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


# Refusals first: a decoder that admits a second encoding of a value is
# the expensive bug, because it gives one object two names.


@pytest.mark.parametrize("case", _params("refusals"))
def test_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(CodecError, match=re.escape(case["reason"])):
        if "hex" in case:
            decode(_hex(case["hex"]))
        else:
            encode(case["value"])


@pytest.mark.parametrize("case", _params("cases"))
def test_round_trips_to_the_same_bytes(case: dict[str, Any]) -> None:
    data = _hex(case["hex"])
    assert encode(decode(data)) == data


@pytest.mark.parametrize(
    "case", [c for c in _params("cases") if "value" in c.values[0]]
)
def test_encodes_the_value_to_the_pinned_bytes(case: dict[str, Any]) -> None:
    assert encode(case["value"]).hex() == case["hex"].replace(" ", "")


@pytest.mark.parametrize(
    "case", [c for c in _params("cases") if "value" in c.values[0]]
)
def test_decodes_the_pinned_bytes_to_the_value(case: dict[str, Any]) -> None:
    decoded = decode(_hex(case["hex"]))
    assert decoded == case["value"]
    # A float and an integer are different values with the same
    # equality in Python; the type must round-trip too, and so must
    # the sign of a zero.
    _assert_same_shape(decoded, case["value"])


def _assert_same_shape(left: Any, right: Any) -> None:
    assert type(left) is type(right)
    if isinstance(left, float):
        assert math.copysign(1.0, left) == math.copysign(1.0, right)
    elif isinstance(left, list):
        for a, b in zip(left, right, strict=True):
            _assert_same_shape(a, b)
    elif isinstance(left, dict):
        assert list(left) == list(right) or set(left) == set(right)
        for key in left:
            _assert_same_shape(left[key], right[key])


def test_the_non_finite_vectors_decode_to_what_the_diagnostic_says() -> None:
    by_id = {case["id"]: case for case in _load()["cases"]}
    assert decode(_hex(by_id["float-infinity"]["hex"])) == math.inf
    assert decode(_hex(by_id["float-negative-infinity"]["hex"])) == -math.inf
    nan = decode(_hex(by_id["float-nan"]["hex"]))
    assert isinstance(nan, float) and math.isnan(nan)
    assert decode(_hex(by_id["bytes-4"]["hex"])) == b"\x01\x02\x03\x04"


def test_every_vector_has_an_id_and_a_rule_or_bytes() -> None:
    loaded = _load()
    ids = [case["id"] for key in ("cases", "refusals") for case in loaded[key]]
    assert len(ids) == len(set(ids))
    for case in loaded["cases"]:
        assert "hex" in case and "diagnostic" in case
    for case in loaded["refusals"]:
        assert "reason" in case and ("hex" in case) != ("value" in case)
