# The codec

One value has one encoding. The encoding is RFC 8949 CBOR under its
core deterministic encoding requirements (section 4.2.1), restricted
to the values below. Every structured object the store hashes, and
every envelope the fabric hashes, is encoded this way and named by the
digest of the result. A decoder refuses every byte sequence that is
not the deterministic encoding of a value in the subset, because a
lenient decoder turns two byte sequences into one value, and one value
with two byte sequences is one object with two names.

Vectors: `vectors/codec.json`.

## The subset

| Value | Encoding |
| --- | --- |
| an integer from 0 to 2^64 - 1 | major type 0, the integer as the argument |
| an integer from -2^64 to -1 | major type 1, `-1 - value` as the argument |
| a byte string | major type 2, the length as the argument, then the bytes |
| a text string, valid UTF-8 | major type 3, the length in bytes as the argument, then the UTF-8 |
| an array | major type 4, the count as the argument, then the items in order |
| a map with text-string keys | major type 5, the pair count as the argument, then each key and value, in key order |
| `false`, `true`, `null` | the heads `0xf4`, `0xf5`, `0xf6` |
| a float | major type 7 with additional information 25, 26 or 27, then the IEEE 754 value in 2, 4 or 8 bytes big-endian |

Nothing else exists: no tags, no indefinite lengths, no simple value
other than the three above, no integer past 64 bits either way.

## The head and the argument

Every item begins with one byte: three bits of major type, five bits
of additional information. An argument from 0 to 23 is the additional
information itself. Otherwise the additional information is 24, 25, 26
or 27 and the argument follows in 1, 2, 4 or 8 bytes, big-endian. The
shortest form that holds the argument is the only legal one: 24 to 255
in one byte, 256 to 65535 in two, 65536 to 2^32 - 1 in four, and
2^32 to 2^64 - 1 in eight. Additional information 28, 29 and 30 is
reserved and refused; 31 marks an indefinite length and is refused.

## Rules

1. Arguments use the shortest form, above.
2. Lengths are definite. An indefinite-length string, array or map is
   refused, and so is a break byte.
3. A text string is valid UTF-8. A surrogate code point, encoded or
   lone, is not valid UTF-8.
4. A map's keys are text strings, unique, and sorted in the bytewise
   lexicographic order of their encoded form, head included. For
   text-string keys that order is by length first, then by bytes: `"z"`
   sorts before `"aa"`.
5. Tags are refused.
6. Simple values other than `false`, `true` and `null` are refused,
   `undefined` included.
7. A float is encoded in the shortest of half, single and double
   precision that represents its value exactly; `1.0` is `f93c00`,
   `1.1` is `fb3ff199999999999a`. An infinity is half precision.
   Negative zero is `f98000` and is a different value from zero. The
   one NaN is `f97e00`; every other NaN bit pattern is refused.
8. An integer and a float are different values with different
   encodings: `1` is `01` and `1.0` is `f93c00`. A producer says
   which it means.
9. Containers nest at most 512 deep.
10. A decoder reads exactly one item and refuses bytes after it.

## Three hazards at the boundary

The rules make the encoding deterministic. Three places remain where
two values that look equal to a person or a language have two names,
and a producer must decide at its boundary:

- `1` and `1.0` are different names. A language with one number type
  must mark which it meant.
- `-0.0` and `0.0` are different names. A computation that produces a
  signed zero names its result differently from one that does not.
- A NaN carries a payload nowhere but in this encoding's refusal: a
  producer that emits any NaN encodes `f97e00`, and a consumer that
  needs a payload has no place to put it.

The store's own formats carry integers only, so none of the three
reaches them.

## CDDL

```cddl
value = uint / nint / bstr / tstr / [* value] / { * tstr => value }
      / false / true / null / float16 / float32 / float64
uint = 0..18446744073709551615
nint = -18446744073709551616..-1
```

The width of a float is not a schema choice: it is the shortest that
holds the value, by rule 7.

## Refusals

| Input | Rule |
| --- | --- |
| `1801`, `190001`, `1a00000001`, `1b0000000000000001`, `780161` | not the shortest form |
| `5fff`, `7fff`, `9fff`, `bfff`, `ff` | indefinite length |
| `1c`, `1d`, `1e`, `1f`, `fc` | reserved additional information |
| `c074…`, `d9d9f7…` | tags are refused |
| `f7`, `f0`, `f820` | simple value |
| `fa3f800000`, `fb3ff0000000000000` | float is not in its shortest form |
| `fa7fc00000`, `fb7ff8000000000000`, `f97e01` | NaN is not the canonical NaN |
| `a10102`, `a1416101` | map key is not a text string |
| `a26162016161 02` | map keys are not sorted |
| `a26161016161 02` | duplicate map key |
| `62c328`, `63eda080` | not valid UTF-8 |
| `6261`, `8201`, `1a0000`, an empty input | truncated |
| `0101` | trailing bytes |
| an integer past 2^64 - 1 or below -2^64, on encode | integer past 64 bits |
| a map key that is not text, on encode | map key is not a text string |
| a value of another type, on encode | no canonical form |
| a 513th level of nesting, either way | nesting deeper than 512 |
