"""The deterministic encoder and the strict decoder.

RFC 8949's core deterministic encoding requirements, over the subset the
spec page names: integers to 64 bits, byte strings, text strings,
arrays, maps with text-string keys, `true`, `false`, `null`, and
floats under the IETF deterministic rules. Every value has one
encoding, and the decoder refuses every other encoding of it, because
a lenient decoder gives one value two byte sequences and so gives one
object two names.

Reach for [livery.cbor.encode][] and [livery.cbor.decode][]; a refusal
raises [livery.cbor.CodecError][] with the rule in its message.
"""

from __future__ import annotations

import math
import struct
from typing import Any, TypeAlias

Value: TypeAlias = (
    "bool | int | float | bytes | str | list[Any] | dict[str, Any] | None"
)
"""A value the codec encodes: the subset, nested the same way."""


class CodecError(ValueError):
    """A value with no deterministic encoding, or bytes that are not one.

    The message names the rule: the phrase a vector's refusal quotes.
    """


# One address space's integers: an unsigned argument fits in eight
# bytes, so the range is -2**64 to 2**64 - 1 and nothing past it.
_UNSIGNED_MAX = 2**64 - 1
_NEGATIVE_MIN = -(2**64)

# The one NaN: a half-precision quiet NaN with an empty payload.
_CANONICAL_NAN = b"\xf9\x7e\x00"

# Nesting is bounded so a hostile input cannot exhaust the stack; the
# bound is generous for every format the store and the fabric hash.
MAX_DEPTH = 512
"""Containers may nest this deep and no deeper."""

_MAJOR_UNSIGNED = 0
_MAJOR_NEGATIVE = 1
_MAJOR_BYTES = 2
_MAJOR_TEXT = 3
_MAJOR_ARRAY = 4
_MAJOR_MAP = 5
_MAJOR_TAG = 6
_MAJOR_SIMPLE = 7

# Argument widths by additional information: 24 to 27 carry one, two,
# four or eight bytes; each width is legal only above the previous one.
_WIDTHS = {24: 1, 25: 2, 26: 4, 27: 8}
_FLOORS = {24: 24, 25: 1 << 8, 26: 1 << 16, 27: 1 << 32}


def encode(value: Value) -> bytes:
    """Encode *value* deterministically.

    Args:
        value: an integer within 64 bits, a float, bytes, text, a list,
            a dict with text keys, a boolean, or None, nested at most
            [livery.cbor.MAX_DEPTH][] deep.

    Returns:
        The one encoding of *value*.

    Raises:
        CodecError: when *value* is outside the subset: an integer past
            64 bits, a map key that is not text, text that is not valid
            UTF-8, a type the subset lacks, or nesting past the bound.
    """
    out = bytearray()
    _encode_into(value, out, 0)
    return bytes(out)


def _head(major: int, argument: int) -> bytes:
    if argument < 24:
        return bytes(((major << 5) | argument,))
    for info, width in _WIDTHS.items():
        if argument < 1 << (8 * width):
            return bytes(((major << 5) | info,)) + argument.to_bytes(width, "big")
    raise CodecError(f"integer past 64 bits: {argument}")  # pragma: no cover


def _encode_into(value: Value, out: bytearray, depth: int) -> None:
    # bool before int: True is an int in Python and 0xf5 in CBOR.
    if value is True:
        out.append(0xF5)
    elif value is False:
        out.append(0xF4)
    elif value is None:
        out.append(0xF6)
    elif isinstance(value, int):
        if value >= 0:
            if value > _UNSIGNED_MAX:
                raise CodecError(f"integer past 64 bits: {value}")
            out += _head(_MAJOR_UNSIGNED, value)
        else:
            if value < _NEGATIVE_MIN:
                raise CodecError(f"integer past 64 bits: {value}")
            out += _head(_MAJOR_NEGATIVE, -1 - value)
    elif isinstance(value, float):
        out += _encode_float(value)
    elif isinstance(value, bytes):
        out += _head(_MAJOR_BYTES, len(value))
        out += value
    elif isinstance(value, str):
        encoded = _utf8(value)
        out += _head(_MAJOR_TEXT, len(encoded))
        out += encoded
    elif isinstance(value, list):
        if depth >= MAX_DEPTH:
            raise CodecError(f"nesting deeper than {MAX_DEPTH}")
        out += _head(_MAJOR_ARRAY, len(value))
        for item in value:
            _encode_into(item, out, depth + 1)
    elif isinstance(value, dict):
        if depth >= MAX_DEPTH:
            raise CodecError(f"nesting deeper than {MAX_DEPTH}")
        entries: list[tuple[bytes, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise CodecError(f"map key is not a text string: {key!r}")
            encoded = _utf8(key)
            entries.append((_head(_MAJOR_TEXT, len(encoded)) + encoded, item))
        # Bytewise over the encoded key, RFC 8949 section 4.2.1; two
        # distinct text keys never encode alike, so no duplicate exists.
        entries.sort(key=lambda entry: entry[0])
        out += _head(_MAJOR_MAP, len(entries))
        for encoded, item in entries:
            out += encoded
            _encode_into(item, out, depth + 1)
    else:
        raise CodecError(f"no canonical form for {type(value).__name__}")


def _utf8(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CodecError(f"not valid UTF-8: {error.reason}") from None


def _encode_float(value: float) -> bytes:
    if math.isnan(value):
        return _CANONICAL_NAN
    # The shortest of half, single and double that holds the value
    # exactly; packing carries the sign, so -0.0 stays -0.0.
    for info, code in ((25, ">e"), (26, ">f")):
        try:
            packed = struct.pack(code, value)
        except OverflowError:
            continue
        if struct.unpack(code, packed)[0] == value:
            return bytes(((_MAJOR_SIMPLE << 5) | info,)) + packed
    return bytes(((_MAJOR_SIMPLE << 5) | 27,)) + struct.pack(">d", value)


def decode(data: bytes) -> Value:
    """Decode *data*, refusing anything that is not a deterministic encoding.

    Args:
        data: exactly one encoded item.

    Returns:
        The value.

    Raises:
        CodecError: when the bytes are not the deterministic encoding of
            a value in the subset: a non-shortest argument, an indefinite
            length, a tag, a simple value the subset lacks, a float not in
            its shortest form or a NaN other than the canonical one, a map
            key that is not text, keys out of order or repeated, text that
            is not valid UTF-8, nesting past the bound, truncated input,
            or bytes after the item.
    """
    decoder = _Decoder(data)
    value = decoder.item()
    if decoder.position != len(data):
        raise CodecError(f"trailing bytes after the item at offset {decoder.position}")
    return value


class _Frame:
    """A container being filled: its kind, the items so far, what is owed."""

    __slots__ = ("container", "pending", "previous", "remaining")

    def __init__(self, container: list[Any] | dict[str, Any], count: int) -> None:
        self.container = container
        self.remaining = count
        # A map frame reads its next key before its next value; the
        # encoded key is kept to enforce order and uniqueness.
        self.previous: bytes | None = None
        self.pending = ""


class _Decoder:
    # The decoder keeps its own stack instead of recursing, so the
    # nesting bound is the only limit and a hostile input never reaches
    # the interpreter's.

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def take(self, count: int) -> bytes:
        end = self.position + count
        if end > len(self.data):
            raise CodecError(
                f"truncated: {count} bytes wanted at offset {self.position},"
                f" {len(self.data) - self.position} left"
            )
        chunk = self.data[self.position : end]
        self.position = end
        return chunk

    def argument(self, major: int, info: int) -> int:
        if info < 24:
            return info
        if info in _WIDTHS:
            width = _WIDTHS[info]
            argument = int.from_bytes(self.take(width), "big")
            if argument < _FLOORS[info]:
                raise CodecError(
                    f"not the shortest form: {argument} in {width} bytes at"
                    f" offset {self.position - width - 1}"
                )
            return argument
        if info == 31 and major in (
            _MAJOR_BYTES,
            _MAJOR_TEXT,
            _MAJOR_ARRAY,
            _MAJOR_MAP,
            _MAJOR_SIMPLE,
        ):
            raise CodecError(f"indefinite length at offset {self.position - 1}")
        raise CodecError(
            f"reserved additional information {info} at offset {self.position - 1}"
        )

    def item(self) -> Value:
        stack: list[_Frame] = []
        while True:
            head = self.take(1)[0]
            major, info = head >> 5, head & 0x1F
            value: Value
            if major == _MAJOR_SIMPLE:
                value = self.simple(info)
            else:
                argument = self.argument(major, info)
                if major == _MAJOR_UNSIGNED:
                    value = argument
                elif major == _MAJOR_NEGATIVE:
                    value = -1 - argument
                elif major == _MAJOR_BYTES:
                    value = self.take(argument)
                elif major == _MAJOR_TEXT:
                    value = self.text(argument)
                elif major == _MAJOR_TAG:
                    raise CodecError(f"tag {argument}: tags are refused")
                else:
                    if len(stack) >= MAX_DEPTH:
                        raise CodecError(f"nesting deeper than {MAX_DEPTH}")
                    if argument:
                        frame = _Frame([] if major == _MAJOR_ARRAY else {}, argument)
                        stack.append(frame)
                        if major == _MAJOR_MAP:
                            self.key(frame)
                        continue
                    value = [] if major == _MAJOR_ARRAY else {}
            # A finished value belongs to the innermost open container;
            # a container that is thereby completed is itself a value.
            while stack:
                frame = stack[-1]
                if isinstance(frame.container, list):
                    frame.container.append(value)
                else:
                    frame.container[frame.pending] = value
                frame.remaining -= 1
                if frame.remaining:
                    if isinstance(frame.container, dict):
                        self.key(frame)
                    break
                value = stack.pop().container
            else:
                return value

    def key(self, frame: _Frame) -> None:
        start = self.position
        head = self.take(1)[0]
        major, info = head >> 5, head & 0x1F
        if major != _MAJOR_TEXT:
            raise CodecError(f"map key is not a text string at offset {start}")
        text = self.text(self.argument(major, info))
        encoded = self.data[start : self.position]
        if frame.previous is not None:
            if encoded == frame.previous:
                raise CodecError(f"duplicate map key {text!r}")
            if encoded < frame.previous:
                raise CodecError(
                    f"map keys are not sorted: {text!r} follows a later key"
                )
        frame.previous = encoded
        frame.pending = text

    def text(self, length: int) -> str:
        raw = self.take(length)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CodecError(f"not valid UTF-8: {error.reason}") from None

    def simple(self, info: int) -> Value:
        if info == 20:
            return False
        if info == 21:
            return True
        if info == 22:
            return None
        if info in (25, 26, 27):
            return self.floating(info)
        if info == 31:
            raise CodecError(f"indefinite length at offset {self.position - 1}")
        if info in (28, 29, 30):
            raise CodecError(
                f"reserved additional information {info} at offset {self.position - 1}"
            )
        if info == 24:
            number = self.take(1)[0]
            raise CodecError(f"simple value {number} is refused")
        raise CodecError(f"simple value {info} is refused")

    def floating(self, info: int) -> float:
        width = _WIDTHS[info]
        raw = self.take(width)
        value: float = struct.unpack({25: ">e", 26: ">f", 27: ">d"}[info], raw)[0]
        if math.isnan(value):
            if bytes((0xE0 | info,)) + raw != _CANONICAL_NAN:
                raise CodecError("NaN is not the canonical NaN f97e00")
            return value
        if _encode_float(value)[0] != (0xE0 | info):
            raise CodecError(
                f"float is not in its shortest form: {value!r} in {width} bytes"
            )
        return value
