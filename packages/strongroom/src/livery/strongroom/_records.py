"""The ref record and the tombstone: the two records beside names.

A ref record is written beside a ref on every update, so the
provenance of a name is a file and never a log to reconstruct. A
tombstone is what an erased object becomes: the name stays, the bytes
go, and the fact is kept in every tier. Both are canonical JSON, and
neither is an object in the store: a record lives beside the ref, a
tombstone under the object's path.

Reach for [livery.strongroom.RefRecord][] and
[livery.strongroom.Tombstone][].
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import Digest
from livery.strongroom._fields import (
    Subject,
    check_timestamp,
    expect_digest,
    expect_object,
    expect_optional_digest,
    expect_str,
)


@dataclass(frozen=True)
class RefRecord:
    """What a ref update writes beside the ref.

    Attributes:
        digest: what the ref names after the update.
        previous: what it named before, or None on creation.
        by: who moved it.
        at: when, as an RFC 3339 UTC instant.
        receipt: across a trust boundary, the signed receipt of the
            call that moved the ref; None under local custody.
        meta: an opaque object a consumer may use, such as validators
            for a URL or a freshness window for a query. Strongroom
            stores it and never reads it.
    """

    digest: Digest
    previous: Digest | None
    by: Subject
    at: str
    receipt: Digest | None = None
    meta: dict[str, Value] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check_timestamp(self.at, where="record.at")

    def to_json(self) -> dict[str, Value]:
        """The record as a JSON object."""
        return {
            "digest": str(self.digest),
            "previous": None if self.previous is None else str(self.previous),
            "by": self.by.to_json(),
            "at": self.at,
            "receipt": None if self.receipt is None else str(self.receipt),
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, value: Value) -> RefRecord:
        """Decode a record from its JSON value.

        Args:
            value: the JSON value.

        Returns:
            The record.

        Raises:
            FormatError: when the shape or a field is wrong.
        """
        fields = expect_object(
            value, ("digest", "previous", "by", "at", "receipt", "meta"), where="record"
        )
        meta = fields["meta"]
        if not isinstance(meta, dict):
            raise FormatError("record.meta is not a JSON object")
        return cls(
            digest=expect_digest(fields["digest"], where="record.digest"),
            previous=expect_optional_digest(
                fields["previous"], where="record.previous"
            ),
            by=Subject.from_json(fields["by"], where="record.by"),
            at=expect_str(fields["at"], where="record.at"),
            receipt=expect_optional_digest(fields["receipt"], where="record.receipt"),
            meta=meta,
        )

    def encode(self) -> bytes:
        """The record's canonical JSON bytes."""
        return canonical(self.to_json())

    @classmethod
    def decode(cls, data: bytes) -> RefRecord:
        """Decode a record from its stored bytes.

        Args:
            data: the record file's bytes.

        Returns:
            The record.

        Raises:
            FormatError: when the bytes are not JSON, not a record, or
                not canonical.
        """
        return cls.from_json(_loads(data, what="record"))


@dataclass(frozen=True)
class Tombstone:
    """What an erased object becomes, in every tier.

    Attributes:
        digest: the erased object's name, which stays valid in every
            tree that names it.
        at: when, as an RFC 3339 UTC instant.
        authority: who erased it.
        receipt: the receipt of the erasing call, or None under local
            custody.
        reason: free text, shown to a reader whose view meets the
            erased entry.
    """

    digest: Digest
    at: str
    authority: Subject
    receipt: Digest | None
    reason: str

    def __post_init__(self) -> None:
        check_timestamp(self.at, where="tombstone.at")

    def to_json(self) -> dict[str, Value]:
        """The tombstone as a JSON object."""
        return {
            "digest": str(self.digest),
            "at": self.at,
            "authority": self.authority.to_json(),
            "receipt": None if self.receipt is None else str(self.receipt),
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, value: Value) -> Tombstone:
        """Decode a tombstone from its JSON value.

        Args:
            value: the JSON value.

        Returns:
            The tombstone.

        Raises:
            FormatError: when the shape or a field is wrong.
        """
        fields = expect_object(
            value, ("digest", "at", "authority", "receipt", "reason"), where="tombstone"
        )
        return cls(
            digest=expect_digest(fields["digest"], where="tombstone.digest"),
            at=expect_str(fields["at"], where="tombstone.at"),
            authority=Subject.from_json(
                fields["authority"], where="tombstone.authority"
            ),
            receipt=expect_optional_digest(
                fields["receipt"], where="tombstone.receipt"
            ),
            reason=expect_str(fields["reason"], where="tombstone.reason"),
        )

    def encode(self) -> bytes:
        """The tombstone's canonical JSON bytes."""
        return canonical(self.to_json())

    @classmethod
    def decode(cls, data: bytes) -> Tombstone:
        """Decode a tombstone from its stored bytes.

        Args:
            data: the tombstone file's bytes.

        Returns:
            The tombstone.

        Raises:
            FormatError: when the bytes are not JSON, not a tombstone,
                or not canonical.
        """
        return cls.from_json(_loads(data, what="tombstone"))


def _loads(data: bytes, *, what: str) -> Value:
    try:
        value: Value = json.loads(data)
    except ValueError as error:
        raise FormatError(f"{what} bytes are not JSON: {error}") from None
    if canonical(value) != data:
        raise FormatError(f"{what} bytes are not canonical JSON")
    return value
