"""Deterministic CBOR: one value, one encoding, one name.

RFC 8949's core deterministic encoding requirements over a restricted
subset, so that everything the store and the fabric hash has exactly
one byte sequence. `encode` produces it; `decode` accepts it and
refuses every other encoding of the same value. The subset, the rules
and the vectors are in the spec beside this package, `spec/cbor.md`.

Reach for [livery.strongroom.cbor.encode][] and
[livery.strongroom.cbor.decode][].
"""

from __future__ import annotations

from livery.strongroom.cbor._codec import MAX_DEPTH, CodecError, Value, decode, encode

__all__ = ["MAX_DEPTH", "CodecError", "Value", "decode", "encode"]
