# The standard

The codec is a standard implementable in any language, and this
package is its reference implementation, not its definition. What has
to be byte-identical is on one page, and the vectors beside it check
an implementation byte for byte.

## Where it lives

The normative text is `spec/codec.md` beside this package in the
repository: the subset, the head and argument rule, ten rules, the
three boundary hazards, a CDDL fragment and the refusal table. The
vectors are `spec/vectors/codec.json`.

## Vectors

The vector file has cases and refusals. A case has an `id`, the `hex`
of the one encoding, a `diagnostic` string in RFC 8949's diagnostic
notation for a reader, and, where JSON can carry the value exactly, a
`value`. A refusal has an `id`, either the `hex` of an input a decoder
must refuse or a `value` an encoder must refuse, and a `reason`, a
phrase the implementation's error must contain.

An implementation proves itself by running every vector:

- every case's `hex` decodes and re-encodes to the same bytes;
- every case with a `value` encodes to its `hex` and decodes to its
  `value`, with the type and the sign of a zero preserved;
- every refusal is refused with an error containing its `reason`.

Cases without a `value` are the ones JSON cannot spell: byte strings,
infinities, NaN and a nested map with bytes inside. Their `diagnostic`
says what they decode to, and the round trip pins the bytes.

## What a second implementation needs

Two hundred lines and this vector file. The encoder is a table of six
major types and one rule for the argument's width; the decoder is the
same table read backwards with every rule enforced. Nothing here
depends on a library, and a general CBOR library's canonical mode is
not a substitute: its determinism is a claim until a vector checks it.
