# The ref record and the tombstone

Two records beside names. Neither is an object in the store: a ref
record lives beside its ref, a tombstone under the object's path.
Both are canonical JSON ([canonical.md](canonical.md)). Vectors:
`vectors/records.json`.

## The ref record

Written beside a ref on every update, so the provenance of a name is a
file and never a log to reconstruct.

```json
{
  "digest": "sha256:...",
  "previous": "sha256:..." | null,
  "by": {"kind": "person", "value": "..."},
  "at": "2026-09-11T12:00:00Z",
  "receipt": "sha256:..." | null,
  "meta": {}
}
```

| Field | Meaning |
| --- | --- |
| `digest` | what the ref names after the update |
| `previous` | what it named before; `null` on creation |
| `by` | who moved it, a subject ([version.md](version.md)) |
| `at` | when, an instant ([version.md](version.md)) |
| `receipt` | across a trust boundary, the signed receipt of the call that moved the ref; `null` under local custody |
| `meta` | an object the consumer owns: validators for a URL, a freshness window for a query. The store writes it and never reads it |

Updates are compare-and-swap on `previous`. Across a trust boundary a
reader trusts a ref only through its receipt; under local custody the
record makes an out-of-band edit visible, because the ref and the
record no longer agree.

## The tombstone

What an erased object becomes, in every tier. Erasure wins and
immutability keeps the fact: the bytes go, the name stays valid in
every tree that carries it, history still verifies structurally, and a
view of an affected version fails loudly on the erased entry instead
of silently serving content with holes.

```json
{
  "digest": "sha256:...",
  "at": "2026-09-11T12:00:00Z",
  "authority": {"kind": "person", "value": "..."},
  "receipt": "sha256:..." | null,
  "reason": "..."
}
```

| Field | Meaning |
| --- | --- |
| `digest` | the erased object's name |
| `at` | when |
| `authority` | who erased it, a subject |
| `receipt` | the receipt of the erasing call, or `null` |
| `reason` | free text, shown to a reader whose view meets the entry |

A tombstone is the third object state, beside present and absent.

## Refusals

| Input | Rule |
| --- | --- |
| `meta` as an array | not a JSON object |
| a missing or extra key | wrong keys |
| `at` that is not an instant | not an RFC 3339 UTC instant |
| `previous` or `receipt` that is not a digest string | the digest rule |
| `by` or `authority` as a string | not a JSON object |
| `reason` as `null` | not a string |
