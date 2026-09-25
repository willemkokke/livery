# Seeded from the template channel (package-python kind) at
# birth; this file is the workspace's own. Edit it directly:
# the template never rewrites it.
"""Deterministic CBOR: one value, one encoding, one name.

RFC 8949's core deterministic encoding requirements over a restricted
subset, so that everything the store and the fabric hash has exactly
one byte sequence. `encode` produces it; `decode` accepts it and
refuses every other encoding of the same value. The subset, the rules
and the vectors are in the spec page beside this package.

Reach for [livery.cbor.encode][] and [livery.cbor.decode][].
"""

from __future__ import annotations

from livery.cbor._codec import MAX_DEPTH, CodecError, Value, decode, encode

__all__ = ["MAX_DEPTH", "CodecError", "Value", "__version__", "decode", "encode"]

__version__ = "0.0.0"
