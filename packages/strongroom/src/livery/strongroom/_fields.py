"""Decoding helpers shared by the structured formats.

Each format decodes from a JSON value with the same discipline: the
value is an object, it has exactly the keys the format names, and each
field has the type the format says. A refusal names the format, the
field and the rule. The formats themselves are `_tree`, `_version` and
`_records`; this module is the part they share.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from livery.strongroom._canonical import FormatError, Value
from livery.strongroom._digest import Digest

SubjectKind = Literal["person", "call", "receipt", "code"]
"""Who or what may fill a subject slot."""

SUBJECT_KINDS: tuple[SubjectKind, ...] = ("person", "call", "receipt", "code")

# RFC 3339 in UTC with the `Z` designator, seconds required, up to nine
# fractional digits. One instant has one spelling: an offset is refused.
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")


@dataclass(frozen=True)
class Subject:
    """A subject slot: a person, a call key, a receipt digest, or code.

    A version's producer, a ref record's writer and a tombstone's
    authority are subjects. The slot is shaped so that attested code
    fits it from the first persisted byte.

    Attributes:
        kind: which of the four the subject is.
        value: the subject's identity, its spelling owned by the kind:
            a person's identifier, a call key, a receipt or code digest
            as a digest string.
    """

    kind: SubjectKind
    value: str

    def to_json(self) -> dict[str, Value]:
        """The subject as a JSON object."""
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_json(cls, value: Value, *, where: str) -> Subject:
        """Decode a subject, refusing an unknown kind or an empty value.

        Args:
            value: the JSON value to decode.
            where: the field being decoded, for the refusal's message.

        Returns:
            The subject.

        Raises:
            FormatError: when the shape or the kind is wrong.
        """
        fields = expect_object(value, ("kind", "value"), where=where)
        kind = expect_str(fields["kind"], where=f"{where}.kind")
        if kind not in SUBJECT_KINDS:
            known = ", ".join(SUBJECT_KINDS)
            raise FormatError(f"{where}.kind {kind!r} is not one of {known}")
        text = expect_str(fields["value"], where=f"{where}.value")
        if not text:
            raise FormatError(f"{where}.value is empty")
        if kind in ("receipt", "code"):
            Digest.parse(text)
        return cls(kind, text)


def expect_object(
    value: Value, keys: tuple[str, ...], *, where: str
) -> dict[str, Value]:
    """Return *value* as an object with exactly *keys*, or refuse."""
    if not isinstance(value, dict):
        raise FormatError(f"{where} is not a JSON object")
    present = set(value)
    wanted = set(keys)
    if present != wanted:
        missing = ", ".join(sorted(wanted - present)) or "none"
        extra = ", ".join(sorted(present - wanted)) or "none"
        raise FormatError(
            f"{where} has the wrong keys (missing: {missing}; unexpected: {extra})"
        )
    return value


def expect_str(value: Value, *, where: str) -> str:
    """Return *value* as a string, or refuse."""
    if not isinstance(value, str):
        raise FormatError(f"{where} is not a string")
    return value


def expect_int(value: Value, *, where: str) -> int:
    """Return *value* as a non-negative integer, or refuse.

    A boolean is refused although Python counts it as an integer, and
    a float is refused although it may be whole: the formats carry
    integers only.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise FormatError(f"{where} is not an integer")
    if value < 0:
        raise FormatError(f"{where} is negative")
    return value


def expect_bool(value: Value, *, where: str) -> bool:
    """Return *value* as a boolean, or refuse."""
    if not isinstance(value, bool):
        raise FormatError(f"{where} is not a boolean")
    return value


def expect_list(value: Value, *, where: str) -> list[Value]:
    """Return *value* as a list, or refuse."""
    if not isinstance(value, list):
        raise FormatError(f"{where} is not an array")
    return value


def expect_digest(value: Value, *, where: str) -> Digest:
    """Return *value* parsed as a digest string, or refuse."""
    return Digest.parse(expect_str(value, where=where))


def expect_optional_digest(value: Value, *, where: str) -> Digest | None:
    """Return *value* as a digest or None, or refuse."""
    if value is None:
        return None
    return expect_digest(value, where=where)


def check_timestamp(value: str, *, where: str) -> str:
    """Return *value* when it is an RFC 3339 UTC instant, or refuse.

    The form is `YYYY-MM-DDTHH:MM:SS[.fraction]Z`: seconds required,
    up to nine fractional digits, the `Z` designator and no offset, so
    one instant has one spelling.
    """
    if _TIMESTAMP.match(value) is None:
        raise FormatError(
            f"{where} {value!r} is not an RFC 3339 UTC instant of the form"
            " YYYY-MM-DDTHH:MM:SS[.fraction]Z"
        )
    try:
        datetime.fromisoformat(value[:19])
    except ValueError as error:
        raise FormatError(f"{where} {value!r} is not a real instant: {error}") from None
    return value


def nfc(value: str, *, where: str) -> str:
    """Return *value* when it is already in Unicode NFC, or refuse."""
    if unicodedata.normalize("NFC", value) != value:
        raise FormatError(f"{where} {value!r} is not in Unicode NFC")
    return value
