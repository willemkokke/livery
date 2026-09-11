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

## The local store

`Store.create` writes the root manifest and `Store.open` reads it,
refusing another layout version or algorithm. `land` streams bytes
through the digest into a scratch file and moves them into place,
verifying against an expected digest when one is given; landing is
idempotent, and two processes landing the same bytes both succeed.
`path` hands over a read-only path, checking size on every access and
hashing in full an object this store never verified; `scrub` hashes
everything and removes what does not match its name.

`set_ref` moves a ref by compare-and-swap under a per-ref lock and
writes the record beside it; `ref` reads it back and reports a ref
whose record disagrees as tampered. Every namespace is declared at
open with its mutation class, and the store enforces the class
without reading the namespace's meaning: write-once, monotone
(fast-forward over versions), or volatile. `pins` and `pending` are
the store's own.

## Sources and tiers

A store opens with an ordered list of sources: a folder in the same
layout (a mirror, or a read-only share under a reference policy that
lands nothing), an HTTP base URL serving the layout, or an origin
hint, one URL with the digest it yields. `fetch` answers locally when
it can and otherwise consults the sources in order, verifying every
hit on arrival, skipping an unreachable source after its connect
timeout and refusing one that serves wrong bytes, each reported
through a progress sink that never prints. `offline` never consults
an origin, and a miss names the URL that would have satisfied it.
`fill` lands a set of objects into a folder, which then serves as a
source: a mirror built by the store.

## The lifecycle

`publish_begin` roots a target under `pending/<id>` before anything
else, `publish_commit` moves the real ref under its class and drops
the pending ref, and `retire` drops one deliberately; nothing removes
a pending ref on a clock. `sweep` marks from every ref on disk, pins
and pending refs included, reading each object by shape (a version,
a tree, or a leaf) rather than by namespace, reads the pending refs
again immediately before deleting, and removes the unreached and old
scratch under an exclusive maintenance lease that `unpin` shares.
`erase` writes a tombstone under the object's path and deletes the
bytes: the name stays valid in every tree that carries it, a path to
it fails naming the reason, and landing it again is refused.

## The materialiser

`view` fills a directory from a tree by the cheapest safe rung per
entry: clone (APFS and Linux copy-on-write), hardlink (refused into a
writable view unless allowed), link (refused from a writable view),
copy. An executable entry never shares an inode or a target's mode. A
symlink entry becomes a real symlink, the within-view target's content
as a marked copy where the platform refuses, or a parked refusal when
the target escapes the view. The record lists every path and rung and
is a root while the view's directory exists; `drop_view` removes only
what the record lists and names what it leaves. `collect` reads
declared outputs back into a tree, landing every object. `prefetch`
warms everything one digest reaches, and `shed` evicts local copies a
named source holds, except what a live view depends on.
