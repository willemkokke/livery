<!-- Seeded from the template channel (package-base kind) at
     birth; this file is the workspace's own. Edit it directly:
     the template never rewrites it.
-->
# livery-strongroom

Content-addressed storage: one address space, every tenant.

The store names bytes by their digest, keeps trees and versions as
blobs in specified formats, moves refs by compare-and-swap with a
record beside each, and hands a real path to a program that needs
one. It knows no tool, no call and no dataset: a consumer composes
the formats and owns its namespaces. It imports only the standard
library, and takes no runtime dependency.

## The standard

The store is a standard implementable in any language, and the
Python package is its reference implementation. The standard is the
`spec/` directory beside the package: the digest grammar and its
registry, canonical JSON, the tree, the version, the ref record and
the tombstone, the on-disk layout, and the ref namespaces. Each format
has golden vectors under `spec/vectors/`, and the package's tests run
every one of them.

## What this release carries

The formats, as frozen dataclasses with `encode`, `decode` and
validation:

- `canonical` encodes a value as RFC 8785 canonical JSON, the one
  hashed form of every structured format.
- `Digest` parses and names; `digest_of` and `digest_stream` compute.
  The registry holds `sha256` alone.
- `Tree`, `Entry` and `Link` are a directory as a manifest, with the
  portable-name rules enforced when a tree is built or decoded.
- `Version` is provenance over a tree, with a subject-shaped producer.
- `RefRecord` and `Tombstone` are the two records beside names.

The store itself (objects, refs, tiers, the lifecycle, the
materialiser) builds on these formats in later releases.
