"""Canonical JSON: the one hashed form of every structured format.

RFC 8785 (JSON Canonicalization Scheme) restricted to the values the
store's formats use: objects with string keys, arrays, strings,
integers, booleans and null. A float is refused, because a tree's
`size` is an integer and no format carries a fraction; refusing the
whole class keeps the number serialisation to plain digits, which
every language prints the same way. The same canonicaliser serves
every format, so one set of vectors proves it.

Reach for [livery.strongroom.canonical][] to produce bytes and
[livery.strongroom.FormatError][] to catch a refusal.
"""

from __future__ import annotations

from typing import Any, TypeAlias

Value: TypeAlias = "bool | int | str | list[Any] | dict[str, Any] | None"
"""A JSON value the store's formats may carry: no float, nested the same way."""

# ES6 serialises an integer past 2^53 with lost digits, so RFC 8785
# cannot name it; the store refuses it before it is ever written.
_LARGEST_EXACT = 2**53

# RFC 8785 section 3.2.2.2: these five control characters use their
# short escapes, every other one below U+0020 is `\u00xx` in lowercase
# hex, and nothing else is escaped except the quote and the backslash.
_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


class FormatError(ValueError):
    """A value does not satisfy one of the store's formats.

    The message names the rule that refused it. Every format refusal
    raises this one class, so a caller validating a whole tree catches
    one thing.
    """


def canonical(value: Value) -> bytes:
    """Encode *value* as RFC 8785 canonical JSON, UTF-8.

    Object keys sort by their UTF-16 code units, which differs from
    code-point order above the Basic Multilingual Plane. Strings keep
    every character above U+001F literal.

    Args:
        value: the value to encode. Nested values are encoded the same
            way.

    Returns:
        The canonical bytes. Two equal values encode to equal bytes.

    Raises:
        FormatError: on a float, an integer past 2^53 in magnitude, a
            non-string key, or a value of a type no format carries.
    """
    parts: list[str] = []
    _write(value, parts)
    return "".join(parts).encode("utf-8")


def _write(value: Value, parts: list[str]) -> None:
    if value is None:
        parts.append("null")
    elif value is True:
        parts.append("true")
    elif value is False:
        parts.append("false")
    elif isinstance(value, int):
        if abs(value) > _LARGEST_EXACT:
            raise FormatError(
                f"integer {value} is past 2^53 in magnitude, which"
                " canonical JSON cannot name exactly"
            )
        parts.append(str(value))
    elif isinstance(value, str):
        _write_string(value, parts)
    elif isinstance(value, list):
        parts.append("[")
        for index, item in enumerate(value):
            if index:
                parts.append(",")
            _write(item, parts)
        parts.append("]")
    elif isinstance(value, dict):
        _write_object(value, parts)
    else:
        raise FormatError(
            f"a value of type {type(value).__name__} has no canonical"
            " form; the formats carry null, booleans, integers, strings,"
            " arrays and objects"
        )


def _write_object(value: dict[str, Value], parts: list[str]) -> None:
    for key in value:
        if not isinstance(key, str):
            raise FormatError(
                f"object key {key!r} is not a string; canonical JSON keys are strings"
            )
    parts.append("{")
    first = True
    for key in sorted(value, key=_utf16_units):
        if not first:
            parts.append(",")
        first = False
        _write_string(key, parts)
        parts.append(":")
        _write(value[key], parts)
    parts.append("}")


def _utf16_units(key: str) -> bytes:
    # Big-endian UTF-16 bytes compare as the code-unit sequence does,
    # which is the order RFC 8785 section 3.2.3 requires.
    return key.encode("utf-16-be", "surrogatepass")


def _write_string(value: str, parts: list[str]) -> None:
    parts.append('"')
    for char in value:
        escaped = _SHORT_ESCAPES.get(char)
        if escaped is not None:
            parts.append(escaped)
        elif char < " ":
            parts.append(f"\\u{ord(char):04x}")
        else:
            parts.append(char)
    parts.append('"')
