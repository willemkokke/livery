# The version

Provenance over a tree: git's commit with a subject-shaped producer.
Vectors: `vectors/version.json`.

## Shape

```json
{
  "tree": "sha256:...",
  "parents": ["sha256:...", ...],
  "producer": {"kind": "person", "value": "..."},
  "created": "2026-09-11T12:00:00Z",
  "message": "...",
  "receipt": "sha256:..." | null,
  "attachments": {"name": "sha256:...", ...}
}
```

Exactly those keys. A version's bytes are its canonical JSON
([canonical.md](canonical.md)) and its digest is the digest of those
bytes. A version is a blob in the store; a ref record or a parent
entry says to read it as a version.

| Field | Meaning |
| --- | --- |
| `tree` | the content, a tree digest |
| `parents` | the versions this one continues, in order; empty for a root |
| `producer` | who or what produced it, a subject |
| `created` | when, an instant |
| `message` | free text, may be empty |
| `receipt` | the receipt of the producing call, or `null` |
| `attachments` | name to blob digest: a consumer's own metadata (a tool spec, a dataset card) hung on the version without touching the tree; names obey the portable-name rules of [tree.md](tree.md) |

Parents make history, diff and common ancestry ordinary tree walks.
An appended dataset version is a version with one parent whose tree
shares every unchanged subtree with it. There is no merge machinery:
a branch is a ref, a tag is a ref, and history is a version's parents.

## Subjects

A subject slot is `{"kind": ..., "value": ...}` with exactly those
keys:

| Kind | Value |
| --- | --- |
| `person` | a person's identifier, its spelling the consumer's |
| `call` | a call key |
| `receipt` | a receipt digest, a digest string |
| `code` | a code digest, a digest string |

The value is never empty. For `receipt` and `code` it must parse as a
digest string. The slot is shaped so that attested code fits it from
the first persisted byte: a grant can go to code, and a version
produced by attested code needs the same subject shape.

## Instants

An RFC 3339 instant in UTC: `YYYY-MM-DDTHH:MM:SS[.fraction]Z`.
Seconds are required, the fraction has one to nine digits, the `Z`
designator is required and an offset is refused, so one instant has
one spelling. The calendar date must exist.

## Refusals

| Input | Rule |
| --- | --- |
| `created` with an offset, without seconds, or on 2026-13-01 | not an RFC 3339 UTC instant; not a real instant |
| producer kind `robot`; an empty value; a `code` value that is not a digest | not one of the four kinds; empty; the digest rule |
| an attachment named `CON`; `attachments` as an array | Windows-reserved; not a JSON object |
| `parents` not an array; a parent or `receipt` not a digest string | not an array; the digest rule |
| a missing or extra key; a non-string message | wrong keys; not a string |
