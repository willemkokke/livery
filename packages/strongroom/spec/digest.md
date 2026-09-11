# Digest strings

Every object's name is a digest string. Vectors: `vectors/digest.json`.

## Grammar

The OCI digest grammar:

```text
digest    = algorithm ":" encoded
algorithm = [a-z0-9]+ ( [.+_-] [a-z0-9]+ )*
encoded   = [a-zA-Z0-9=_-]+
```

The grammar admits nothing on its own. A digest is valid only when its
algorithm is in the registry and its encoded part matches that
algorithm's form in full.

## The registry

| Algorithm | Encoded form | Status |
| --- | --- | --- |
| `sha256` | 64 lowercase hex characters | required, and the only entry |

The registry admits only cryptographic algorithms at full length. A
non-cryptographic hash (xxHash, CRC, CityHash) and a truncated digest
(BLAKE3-160) are refused as names, whatever their speed: a name anyone
can forge is not a name. An implementation may keep cheap checksums in
its local index; none of them is a format.

Named candidates, each added only when a tenant's measurement shows
the need: `blake3` for hash-bound single files, `sha512-256` for
post-quantum margin, `sha3-256` for construction diversity. Adding one
costs a dependency in every implementation, a second name for the same
bytes across address spaces, and the compliance answer.

## Rules

- One algorithm per address space, named in the root manifest
  ([layout.md](layout.md)). A store refuses to mix: the same bytes
  under two algorithms have two names and dedup silently fails.
- Uppercase hex is refused for `sha256`. One object has one name.
- A digest string carries its algorithm, so a successor is a registry
  entry and never a migration.

## Refusals

| Input | Rule |
| --- | --- |
| no colon, an empty encoded part, an uppercase or spaced algorithm | not in the OCI grammar |
| an algorithm the registry lacks (`sha1`, `sha512`, `blake3`, `md5`, `xxh3`) | not in the registry |
| uppercase hex, 63 or 65 characters, non-hex characters | not a full lowercase `sha256` digest |

## The object path

A digest `sha256:abcdef...` is stored at `objects/sha256/ab/cdef...`:
the algorithm, a two-character fan-out, the rest. About eighty
characters from the store root, inside Windows' 260 without long
paths.
