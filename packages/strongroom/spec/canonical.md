# Canonical JSON

The one hashed form of every structured format: the tree, the version,
the ref record and the tombstone. Vectors: `vectors/canonical.json`.

## The encoding

RFC 8785, the JSON Canonicalization Scheme, restricted to the values
the formats carry:

| Value | Encoding |
| --- | --- |
| `null`, `true`, `false` | the literal |
| integer | decimal digits, a leading `-` when negative, no `+`, no leading zeros, no exponent, no fraction |
| string | see below |
| array | `[`, the elements in order separated by `,`, `]`; no whitespace |
| object | `{`, the members sorted by key separated by `,`, `}`; each member `"key":value`; no whitespace |

The output is UTF-8.

## Strings

Only these characters are escaped, exactly as RFC 8785 section 3.2.2.2
says:

| Character | Escape |
| --- | --- |
| U+0008 backspace | `\b` |
| U+0009 tab | `\t` |
| U+000A line feed | `\n` |
| U+000C form feed | `\f` |
| U+000D carriage return | `\r` |
| U+0022 quotation mark | `\"` |
| U+005C reverse solidus | `\\` |
| any other character below U+0020 | `\u` and four lowercase hex digits |

Every other character is literal: the solidus, U+007F, U+2028 and
U+2029, and every non-ASCII character.

## Key order

Object members sort by their key's UTF-16 code units, compared
numerically. This is RFC 8785 section 3.2.3, and it differs from
code-point order above the Basic Multilingual Plane: `😀` (U+1F600,
the units D83D DE00) sorts before `＀` (U+FF00). The vector
`key-order-utf16` pins it.

## Numbers

The formats carry integers only. A float is refused, whole or not,
because a tree's `size` and every other number in the formats is a
count. Refusing the class keeps number serialisation to digits every
language prints alike. An integer past 2^53 in magnitude is refused
too: RFC 8785 serialises numbers as IEEE 754 doubles, which cannot
name it exactly. 2^53 bytes is past any file the store will ever
name.

## Why one canonicaliser

A tree's digest is the digest of its canonical JSON bytes,
permanently. A second hashed encoding would give one logical tree two
names, the same silent dedup loss as mixing hash algorithms. A binary
tree encoding is a representation behind the one digest, never a
second name. The same canonical form serves the fabric's normal form,
so one canonicaliser and one set of vectors serve both.

## Refusals

| Input | Rule |
| --- | --- |
| a float, whole or not, anywhere in the value | no canonical form |
| an integer past 2^53 in magnitude | past 2^53 |
| a non-string key, a value of a type outside the table | not a string; no canonical form |
