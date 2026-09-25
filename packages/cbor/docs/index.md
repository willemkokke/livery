<!-- Seeded from the template channel (package-base kind) at
     birth; this file is the workspace's own. Edit it directly:
     the template never rewrites it.
-->
# livery-cbor

Deterministic CBOR: one value, one encoding, one name.

`livery.cbor` encodes a value to the one byte sequence RFC 8949's
core deterministic encoding requirements allow, over a restricted
subset, and decodes only that byte sequence. Everything the store
hashes and everything the fabric hashes goes through it, so an object
has exactly one name and a call has exactly one key. The package has
no dependencies and imports nothing first-party, because every other
package imports it.

## Using it

```python
from livery.cbor import CodecError, decode, encode

data = encode({"name": "bun", "size": 41_943_040, "parts": [b"\x00", 1.5]})
value = decode(data)
try:
    decode(b"\x18\x01")  # the integer 1 in two bytes
except CodecError as error:
    print(error)  # not the shortest form: 1 in 1 bytes at offset 0
```

`encode` takes an integer within 64 bits, a float, `bytes`, `str`, a
`list`, a `dict` with `str` keys, a `bool` or `None`, nested at most
`MAX_DEPTH` deep, and raises `CodecError` for anything else. `decode`
returns the same Python types and raises `CodecError` for any input
that is not a deterministic encoding, naming the rule and, where it
helps, the offset.

## The subset

| Value | Encoded as |
| --- | --- |
| integer, -2^64 to 2^64 - 1 | major type 0 or 1, shortest argument |
| bytes | major type 2, definite length |
| text, valid UTF-8 | major type 3, definite length |
| list | major type 4, definite count |
| dict with text keys | major type 5, keys sorted by their encoded bytes |
| `False`, `True`, `None` | `f4`, `f5`, `f6` |
| float | the shortest of half, single and double that is exact; one NaN, `f97e00` |

No tags, no indefinite lengths, no other simple values. The full rules
and the refusals are on the standard's page.

## Three hazards at the boundary

The encoding is deterministic; these are the places where two values
that look equal have two names, and a producer decides at its edge:

- `1` and `1.0` are different names. A language with one number type
  must say which it meant.
- `-0.0` and `0.0` are different names.
- Every NaN encodes to the one canonical NaN; a payload has nowhere to
  go.

The store's own formats carry integers only, so none of the three
reaches them.
