"""The formats' behaviour the vectors cannot express as JSON."""

from __future__ import annotations

import io

import pytest

from livery.strongroom import (
    ALGORITHMS,
    SHA256,
    Digest,
    Entry,
    FormatError,
    Link,
    RefRecord,
    Subject,
    Tombstone,
    Tree,
    Version,
    canonical,
    check_name,
    check_target,
    check_timestamp,
    digest_of,
    digest_stream,
)

EMPTY = digest_of(b"")
ABC = digest_of(b"abc")
AT = "2026-09-11T12:00:00Z"
WILLEM = Subject("person", "willem")


# Refusals the vectors cannot spell, because JSON has no such value.


def test_canonical_refuses_a_non_string_key() -> None:
    with pytest.raises(FormatError, match="not a string"):
        canonical({1: "x"})  # type: ignore[dict-item]


def test_canonical_refuses_a_value_of_no_json_type() -> None:
    with pytest.raises(FormatError, match="no canonical form"):
        canonical(b"bytes")  # type: ignore[arg-type]


def test_canonical_refuses_a_tuple_where_an_array_is_meant() -> None:
    with pytest.raises(FormatError, match="no canonical form"):
        canonical((1, 2))  # type: ignore[arg-type]


def test_digest_of_refuses_an_unregistered_algorithm() -> None:
    with pytest.raises(FormatError, match="not in the registry"):
        digest_of(b"", algorithm="blake3")


def test_digest_stream_refuses_an_unregistered_algorithm() -> None:
    with pytest.raises(FormatError, match="not in the registry"):
        digest_stream(io.BytesIO(b""), algorithm="sha1")


def test_an_entry_refuses_a_kind_outside_blob_and_tree() -> None:
    with pytest.raises(FormatError, match="not blob or tree"):
        Entry("a", "symlink", EMPTY, 0)  # type: ignore[arg-type]


def test_an_entry_refuses_a_bad_name_at_construction() -> None:
    with pytest.raises(FormatError, match="Windows-reserved"):
        Entry("NUL", "blob", EMPTY, 0)


def test_a_link_refuses_a_bad_target_at_construction() -> None:
    with pytest.raises(FormatError, match="absolute"):
        Link("a", "/root")


def test_tree_of_refuses_a_case_clash_in_any_order() -> None:
    with pytest.raises(FormatError, match="same name when case is ignored"):
        Tree.of([Link("Readme", "a"), Entry("readme", "blob", EMPTY, 0)])


def test_tree_decode_refuses_bytes_that_are_not_json() -> None:
    with pytest.raises(FormatError, match="not JSON"):
        Tree.decode(b"{")


def test_tree_decode_refuses_non_canonical_bytes() -> None:
    # The same tree with a space is a second name for the same content.
    with pytest.raises(FormatError, match="not canonical"):
        Tree.decode(b'{"entries": []}')


def test_version_decode_refuses_bytes_that_are_not_json() -> None:
    with pytest.raises(FormatError, match="not JSON"):
        Version.decode(b"")


def test_version_decode_refuses_non_canonical_bytes() -> None:
    version = Version(EMPTY, (), WILLEM, AT, "m")
    padded = version.encode().replace(b",", b", ")
    with pytest.raises(FormatError, match="not canonical"):
        Version.decode(padded)


def test_version_refuses_a_bad_timestamp_at_construction() -> None:
    with pytest.raises(FormatError, match="RFC 3339 UTC"):
        Version(EMPTY, (), WILLEM, "2026-09-11", "m")


def test_version_refuses_a_bad_attachment_name_at_construction() -> None:
    with pytest.raises(FormatError, match="separator"):
        Version(EMPTY, (), WILLEM, AT, "m", attachments={"a/b": EMPTY})


def test_record_decode_refuses_bytes_that_are_not_json() -> None:
    with pytest.raises(FormatError, match="record bytes are not JSON"):
        RefRecord.decode(b"nope")


def test_record_decode_refuses_non_canonical_bytes() -> None:
    record = RefRecord(ABC, None, WILLEM, AT)
    with pytest.raises(FormatError, match="record bytes are not canonical"):
        RefRecord.decode(record.encode() + b"\n")


def test_record_refuses_a_bad_timestamp_at_construction() -> None:
    with pytest.raises(FormatError, match=r"record\.at"):
        RefRecord(ABC, None, WILLEM, "now")


def test_tombstone_decode_refuses_bytes_that_are_not_json() -> None:
    with pytest.raises(FormatError, match="tombstone bytes are not JSON"):
        Tombstone.decode(b"[")


def test_tombstone_decode_refuses_non_canonical_bytes() -> None:
    stone = Tombstone(ABC, AT, WILLEM, None, "gone")
    with pytest.raises(FormatError, match="tombstone bytes are not canonical"):
        Tombstone.decode(stone.encode().replace(b":", b": ", 1))


def test_tombstone_refuses_a_bad_timestamp_at_construction() -> None:
    with pytest.raises(FormatError, match=r"tombstone\.at"):
        Tombstone(ABC, "2026-09-11T12:00:00+01:00", WILLEM, None, "gone")


def test_check_timestamp_refuses_an_impossible_day() -> None:
    with pytest.raises(FormatError, match="not a real instant"):
        check_timestamp("2026-02-30T00:00:00Z", where="at")


def test_check_name_refuses_each_rule_by_name() -> None:
    for name, reason in [
        ("", "empty"),
        (".", "relative directory marker"),
        ("e\u0301", "not in Unicode NFC"),
        ("a" * 256, "longer than 255"),
        ("a|b", "separator"),
        ("a ", "ends in a dot or a space"),
        ("com1.log", "Windows-reserved"),
    ]:
        with pytest.raises(FormatError, match=reason):
            check_name(name)


def test_check_target_refuses_each_rule_by_name() -> None:
    for target, reason in [
        ("", "empty"),
        ("e\u0301", "not in Unicode NFC"),
        ("a\\b", "backslash"),
        ("a\x00b", "backslash or NUL"),
        ("/abs", "absolute"),
        ("D:\\x", "backslash"),
        ("d:/x", "absolute"),
    ]:
        with pytest.raises(FormatError, match=reason):
            check_target(target)


def test_subject_refuses_a_bad_shape() -> None:
    with pytest.raises(FormatError, match="not a JSON object"):
        Subject.from_json("willem", where="by")
    with pytest.raises(FormatError, match=r"by\.kind is not a string"):
        Subject.from_json({"kind": 1, "value": "x"}, where="by")


# Then the behaviour that holds.


def test_the_registry_holds_sha256_alone() -> None:
    assert list(ALGORITHMS) == ["sha256"]
    assert ALGORITHMS["sha256"] is SHA256


def test_digest_stream_matches_digest_of() -> None:
    data = b"x" * (3 * 1024 * 1024 + 7)
    assert digest_stream(io.BytesIO(data)) == digest_of(data)


def test_digests_order_and_compare_by_value() -> None:
    assert Digest.parse(str(ABC)) == ABC
    assert sorted([ABC, EMPTY]) == sorted([EMPTY, ABC])
    assert ABC != EMPTY


def test_tree_of_sorts_by_name_bytes_not_by_locale() -> None:
    tree = Tree.of(
        [
            Entry("b", "blob", EMPTY, 0),
            Entry("B2", "blob", EMPTY, 0),
            Link("a", "b"),
        ]
    )
    assert [entry.name for entry in tree.entries] == ["B2", "a", "b"]


def test_check_name_returns_the_name_and_check_target_the_target() -> None:
    assert check_name("ok.txt") == "ok.txt"
    assert check_target("../x/y") == "../x/y"
    assert check_timestamp(AT, where="at") == AT


def test_a_version_round_trips_through_json_and_bytes() -> None:
    version = Version(
        ABC,
        (EMPTY,),
        Subject("code", str(EMPTY)),
        "2026-09-11T12:00:00.123456789Z",
        "built",
        receipt=ABC,
        attachments={"spec.json": EMPTY},
    )
    assert Version.from_json(version.to_json()) == version
    assert Version.decode(version.encode()) == version
    assert version.digest() == digest_of(version.encode())


def test_a_record_and_a_tombstone_round_trip() -> None:
    record = RefRecord(
        ABC, EMPTY, Subject("call", "k"), AT, meta={"n": [1, {"m": None}]}
    )
    stone = Tombstone(ABC, AT, Subject("receipt", str(ABC)), ABC, "asked")
    assert RefRecord.decode(record.encode()) == record
    assert Tombstone.decode(stone.encode()) == stone


def test_a_subject_round_trips_each_kind() -> None:
    for kind, value in [
        ("person", "willem"),
        ("call", "fetch:v1"),
        ("receipt", str(ABC)),
        ("code", str(EMPTY)),
    ]:
        subject = Subject(kind, value)  # type: ignore[arg-type]
        assert Subject.from_json(subject.to_json(), where="x") == subject
