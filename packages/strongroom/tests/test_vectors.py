"""The golden vectors under spec/vectors drive these tests.

Each vector file is data: a case pins the canonical bytes, typed by
hand, and the digest of those bytes; a refusal pins the rule's name.
A vector added to a file is a test added here, by parametrisation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import (
    Digest,
    FormatError,
    RefRecord,
    Tombstone,
    Tree,
    Version,
    canonical,
    digest_of,
)

VECTORS = Path(__file__).resolve().parents[1] / "spec" / "vectors"


def _load(name: str) -> dict[str, list[dict[str, Any]]]:
    loaded: dict[str, list[dict[str, Any]]] = json.loads(
        (VECTORS / name).read_text("utf-8")
    )
    return loaded


def _cases(name: str, key: str) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in _load(name)[key]]


# Refusals first: a format that admits what it should refuse is the
# expensive bug, and every rule is pinned by its name.


@pytest.mark.parametrize("case", _cases("canonical.json", "refusals"))
def test_canonical_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        canonical(case["input"])


@pytest.mark.parametrize("case", _cases("digest.json", "refusals"))
def test_digest_parse_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        Digest.parse(case["text"])


@pytest.mark.parametrize("case", _cases("tree.json", "refusals"))
def test_tree_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        Tree.from_json(case["tree"])


@pytest.mark.parametrize("case", _cases("version.json", "refusals"))
def test_version_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        Version.from_json(case["version"])


@pytest.mark.parametrize("case", _cases("records.json", "record_refusals"))
def test_record_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        RefRecord.from_json(case["record"])


@pytest.mark.parametrize("case", _cases("records.json", "tombstone_refusals"))
def test_tombstone_refuses(case: dict[str, Any]) -> None:
    with pytest.raises(FormatError, match=re.escape(case["reason"])):
        Tombstone.from_json(case["tombstone"])


# Then the bytes: the canonical form typed by hand, and the digest of it.


@pytest.mark.parametrize("case", _cases("canonical.json", "cases"))
def test_canonical_bytes(case: dict[str, Any]) -> None:
    assert canonical(case["input"]) == case["canonical"].encode("utf-8")


@pytest.mark.parametrize("case", _cases("digest.json", "bytes"))
def test_digest_of_bytes(case: dict[str, Any]) -> None:
    assert str(digest_of(case["data"].encode("utf-8"))) == case["digest"]


@pytest.mark.parametrize("case", _cases("digest.json", "parse"))
def test_digest_parse(case: dict[str, Any]) -> None:
    digest = Digest.parse(case["text"])
    assert digest.algorithm == case["algorithm"]
    assert digest.encoded == case["encoded"]
    assert list(digest.path_parts) == case["path"]
    assert str(digest) == case["text"]


@pytest.mark.parametrize("case", _cases("tree.json", "cases"))
def test_tree_bytes_and_digest(case: dict[str, Any]) -> None:
    data = case["canonical"].encode("utf-8")
    tree = Tree.from_json(case["tree"])
    assert tree.encode() == data
    assert str(tree.digest()) == case["digest"]
    assert Tree.decode(data) == tree


@pytest.mark.parametrize("case", _cases("version.json", "cases"))
def test_version_bytes_and_digest(case: dict[str, Any]) -> None:
    data = case["canonical"].encode("utf-8")
    version = Version.from_json(case["version"])
    assert version.encode() == data
    assert str(version.digest()) == case["digest"]
    assert Version.decode(data) == version


@pytest.mark.parametrize("case", _cases("records.json", "records"))
def test_record_bytes(case: dict[str, Any]) -> None:
    data = case["canonical"].encode("utf-8")
    record = RefRecord.from_json(case["record"])
    assert record.encode() == data
    assert RefRecord.decode(data) == record


@pytest.mark.parametrize("case", _cases("records.json", "tombstones"))
def test_tombstone_bytes(case: dict[str, Any]) -> None:
    data = case["canonical"].encode("utf-8")
    tombstone = Tombstone.from_json(case["tombstone"])
    assert tombstone.encode() == data
    assert Tombstone.decode(data) == tombstone


def test_every_vector_file_is_read() -> None:
    # A vector file nobody parametrises over is a vector nobody runs.
    assert sorted(path.name for path in VECTORS.glob("*.json")) == [
        "canonical.json",
        "digest.json",
        "records.json",
        "tree.json",
        "version.json",
    ]
