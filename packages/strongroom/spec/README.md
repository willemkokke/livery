# The strongroom standard

The store is a standard implementable in any language. The Python
package beside this directory is its reference implementation, not
its definition. The line: the standard covers what has to be
byte-identical, the conformance suite covers what has to be
behaviour-identical, and everything else is an implementation's
business.

## What this directory holds

Bytes, each with golden vectors under `vectors/`:

| File | Specifies |
| --- | --- |
| [digest.md](digest.md) | the digest string grammar and the algorithm registry |
| [canonical.md](canonical.md) | the canonical JSON every structured format hashes |
| [tree.md](tree.md) | the tree: a directory as a manifest, with the portable-name rules |
| [version.md](version.md) | the version: provenance over a tree |
| [records.md](records.md) | the ref record and the tombstone |
| [layout.md](layout.md) | the on-disk layout, the root manifest, and the HTTP mapping |
| [namespaces.md](namespaces.md) | the two ref namespaces the store owns and the conventions it publishes |

A vector file has cases and refusals. A case pins the canonical bytes
of an input, typed by hand, and the digest of those bytes. A refusal
pins the rule that rejects an input, by a phrase the implementation's
error must contain. An implementation runs every vector.

## What a vector proves

Everything persisted is one of these formats or a raw blob. A change to
a format after its vector lands is a migration, priced as one. The
vectors exist so that a second implementation, in another language or
in wasm, can be written from this directory alone and be checked
against the first byte for byte.

## Out of the standard

The local index an implementation keeps, the strategy it picks within
the materialiser's ladder, a service's framework, encryption mechanics,
and anything a consumer adds above the formats. Behaviour (landing,
verification, compare-and-swap, the lifecycle, the materialiser's
safety rules) is conformance material and arrives with the code that
implements it, as cases under `conformance/`.
