# The strongroom redesign: rulings, reservations and the shape of the spec

Status: a design record, written 2026-09-25 from the discussion of
2026-09-24 and 2026-09-25. It follows
[the on-disk review](20260924-strongroom-on-disk-review.md), which
measured the costs that started it. Rulings 1 to 3 are closed and
recorded below. Rulings 4 to 12 are open, each with its current lean.
The plan note, with phases and acceptance, is written after they close.
Nothing here is implemented.

## The stance

Strongroom is pre-release. Two stores exist, the tool cache and the tool
record data, and both are rebuilt by their producers. So there is no
backward compatibility, no layout 2, no migration code and no legacy
carried. Compatibility begins on the day the hashed formats freeze, which
is the first release of the rewritten spec, and the plan names that day
as a milestone. Until then any ruling below can be reopened at the cost
of an edit and a vector. After it, the name of every object ever landed
depends on them.

Stdlib-only stays a hygiene rule for the Python package where it costs
nothing. It is not a constraint on the spec. The spec picks the best
algorithm on each axis and every implementation pays its dependency once.

## The spine

Five things are not up for debate, because a standard needs a spine.
Everything else is a registry entry, a representation, a policy or an
API, and changes without touching a hashed byte.

1. An object's name is the digest of its plain bytes, with no type
   prefix, one algorithm per address space.
2. Objects above a threshold are plain whole files, so materialisation
   can be zero-copy. Below it, packs, which may compress and delta.
3. Trees are Merkle, sorted, sized, with subtree aggregates, in one
   fixed binary hashed form.
4. Reachability, never age, with a write rooted before its bytes land.
5. Every tier verifies and none is trusted.

## The consumers

The store serves five tenants and knows none of them:

- the tool store, rebuilt over the new spec;
- a package registry, PyPI first because its data is public and its
  sha256 names match object names, later the shape for the hundreds of
  registries that all store the same bytes in different places;
- a version control system with git's shape and better storage;
- large versioned data: corpora and checkpoints, terabytes of binary
  and gigabytes of tiny text; hse is one tenant of this kind, never the
  measure of it;
- a shared filespace with a mount, LucidLink's shape, the far-future
  tenant whose reservations are listed below.

## Rulings closed

### Ruling 1: the default digest algorithm

C. Both `sha256` and `blake3` are in the registry with vectors. `sha256`
is the default for a new address space. A tenant that wants sliceable
verification opens its address space in `blake3`. Moving the default to
`blake3` later changes only stores created afterwards; converting an
existing space is mechanical, a hashing pass over blobs and a bottom-up
rewrite of every structured object, with an old-to-new map for names
recorded outside the store.

The reasoning that carried it: sha256 matches the external identity of
PyPI, OCI, S3, crates, Go and conda, is FIPS-approved, and is hardware on
every CPU at about 3 GB/s a core. blake3 is a tree hash, so one file
uses every core and any slice verifies against the name through an
outboard tree, which is what a mount, a range read from an untrusted
tier, verified streaming and a sampling scrub need. Chunking large blobs
by default gives a sha256 space partial verified reads through the
recipe, vouched by the tree entry, so blake3's unique wins reduce to
parallel computation of the name itself, slicing with only a digest in
hand, and the browser without hash hardware.

Recorded with it: a blob entry may carry aliases, a map of algorithm to
digest of the same bytes, verified at landing when present, because the
registry consumer needs a digest table whatever the store's name is
(npm publishes sha512, Maven sha1 and md5). The Bao outboard is an
index-held representation for objects above a size a tier chooses, read
by range from any tier and verified node by node against the name, so it
needs no name of its own.

### Ruling 2: the codec for hashed formats

The store's own codec in every language, implementing RFC 8949's core
deterministic encoding requirements over a restricted subset, with the
codec's vectors as the conformance suite for each language package. No
third-party CBOR library, no DAG-CBOR, no CIDs, no IPLD interoperability.
The codec is shared by strongroom and the fabric: it replaces the
fabric's RFC 8785 normal form, so it is a package of its own below both,
with its own spec page and vector file. Schemas are written in CDDL
(RFC 8610).

The subset: unsigned and negative integers to 64 bits in shortest form;
byte strings and text strings, definite length, text valid UTF-8; arrays,
definite length; maps, definite length, text-string keys only, unique,
sorted bytewise over their encoded form; `true`, `false`, `null`; floats
under the IETF deterministic rules, shortest exact width, one NaN
pattern, negative zero distinct. Refused on encode and decode: tags,
indefinite lengths, every other simple value, non-shortest forms,
unsorted or duplicate keys, invalid UTF-8. The decoder refuses every
encoding that is not the deterministic one, because a lenient decoder
gives one value two byte sequences and so one object two names.

The store's own schemas are integer-only, one CDDL constraint each: every
number in a tree, a version, a recipe or a record is a count, a size or
an instant in integer nanoseconds. Floats exist in the codec for the
fabric's envelopes and derivation keys and for consumer metadata, with
three boundary hazards stated on the codec's page: `1` and `1.0` are
different names, `-0.0` and `0.0` are different names, and a language
with one number type must mark which it meant. Decimals and big integers
are schema conventions over integer pairs, never codec features.

The whole codec is one page: a table of six major types, the argument
rule that values 0 to 23 sit in the head and larger ones follow in the
shortest of 1, 2, 4 or 8 bytes, and eight rules. An encoder and a strict
decoder are about 150 lines each in any target language. Vectors: about
forty cases at every boundary of the argument rule, string and container
edge cases, key ordering with shared prefixes and unequal lengths, UTF-8
above ASCII, float widths; about thirty refusals.

### Ruling 3: tree names

C, with `portable` as the default profile. A name is valid UTF-8, not
empty, not `.` or `..`, and contains no `/` and no NUL. Those five rules
are the format's. Everything the format enforced before, Unicode NFC,
the 255-byte component limit, the forbidden characters, trailing dots
and spaces, Windows-reserved stems, case-fold uniqueness within a tree,
the path budget, becomes a named profile a namespace declares. The
`portable` profile is those rules exactly, and a namespace that declares
nothing gets it, so the surprise at checkout stays impossible unless a
consumer chose otherwise. The `permissive` profile, whose only rules
are the format's five, is declared by namespaces that carry data whose
producers are not ours: repositories, datasets, filespaces.

Raw non-UTF-8 names are foreclosed in the format; an importer that meets
one escapes it losslessly at its own layer.

A name a platform cannot hold is materialised under a reversible escaped
form, not parked. The escape is percent-encoding of the offending bytes
only: `foo:bar.txt` is `foo%3Abar.txt` on Windows, `x.` is `x%2E`, a
reserved stem escapes its first character so `aux` is `%61ux`, the
second of two names equal when case is ignored escapes its first
differing character on a filesystem that ignores case, the same for
normalisation on APFS, and a literal `%` that would be mistaken for an
escape in the same directory becomes `%25`. Every escaped name decodes by
plain percent-decoding. The view's record maps both ways and the view's
report names every rewritten entry with its original. `collect` restores
a recorded path's original name and reads a new file literally, so a
rename by a person collects as a rename. The escape table is spec with
vectors, since two implementations viewing one tree on one platform must
produce the same names. Parking remains only for what cannot be made: a
component past the platform's limit after escaping, a path past the
budget, a symlink whose target escapes the view.

The precedents, so the choice is placed: WSL, Cygwin, Samba's `catia`
and rclone map forbidden characters to private-use code points, which is
invisible and right inside a mount programs read and wrong in a
directory people look at; Mercurial's store escapes reserved names and
case with `~XX` and is the closest precedent for the shape; git,
Syncthing, Dropbox and Nextcloud refuse; extractors replace with `_` and
lose the name. If a mount is built, the private-use mapping is used
there and only there.

The path limit is not a non-developer problem. `MAX_PATH` is 260 for
every Windows process that is not long-path-aware, long paths need both
an administrator's policy and the program's manifest, Developer Mode does
not enable them, and Explorer, `cmd.exe` and many build tools still fail
on them. The materialiser uses the extended-length prefix for its own
operations so it never fails on length itself; the `portable` profile's
budget fits inside 260 with a reasonable root; a loose profile uses the
platform's real limits, and on Windows the view's report names every
path past 260 so the person knows which tools will choke.

## Requirements recorded

- Chunker configuration is the caller's, completely exposed in the API
  and in the standard, with sensible defaults. A representation policy
  is a value passed per call, defaulted per store and per namespace,
  nearest declaration winning; every parameter is a field, the gear
  table by digest; the registries are listable; `representation(digest)`
  reports how a tier holds an object, read from the object; an object
  can be re-represented under a new policy. Defaults: whole below the
  pack threshold, packed at or below it, above the chunking threshold
  FastCDC at a 64 KB average with the spec's public gear table. The
  store never reads a file's name or type to choose; a policy naming an
  entry the registry lacks is refused at the call, naming the entry.
- In the standard: the registries as tables with vectors; the policy as
  a portable document in the codec; the recipe carrying its chunker
  entry; what a tier's layout exposes; the operations as conformance
  cases. In an implementation: the language binding, the supported
  entries with the mandatory ones required for conformance, the
  defaults' storage.
- The store never prints. A fallback that costs, such as whole-object
  verification of a large sha256 object, is reported through the
  caller's progress sink with the object, its size and the reason.
- Seal before ref: a ref never names an object that is only in an
  unsealed pack. `set_ref` and a group commit seal the process's open
  pack, then fsync the record and the ref; `fill` fsyncs what it lands;
  the hot landing path stays unsynced.

## The remaining rulings, as presented

Numbered as the rulings list, each with the lean it was presented with.
Every one of them is now ruled; the decision record says how.

4. **Large directories.** Prolly trees, a sorted map split at
   content-defined boundaries into a balanced Merkle tree, so one change
   in a ten-million-entry directory rewrites logarithmically many nodes
   and the same set has one root whatever the insertion order. Lean:
   reserve the node kind, defer the shape; a consumer buckets a flat
   namespace by name prefix until skew is measured.
5. **The version.** A fixed core, tree and parents, plus an ordered list
   of header pairs, canonical by construction, known names specified
   (producer, created, message, receipt, author and committer where a
   consumer needs both), unknown names carried verbatim. A version entry
   kind in trees, walked by the sweep and viewed in place, for
   composition. Open: whether `created` is core.
6. **Refs.** A namespace's refs are a tree; a move is a version over it;
   the only mutable thing is one head file per namespace, moved by
   compare-and-swap. Groups become one version, history the parent
   chain under retention, the tampered check a digest check, and the
   mutation classes become merge rules for two versions on one parent:
   monotone takes the fast-forward, write-once and volatile flag the
   entry with both candidates kept. This is jj's operation log and
   Iceberg's metadata pointer in the store's own formats, and it removes
   the per-ref lock, the journal and the record beside the ref.
7. **Packs.** Git-shaped: written by one process, sealed by fsync and
   rename to the pack's own digest, immutable, with a fanout index
   beside it mapping sorted digests to offset, plain size and a
   checksum, derivable from the pack. Entries are zstd frames with a
   type hint, an encoding and an optional reference, which is an
   object's name: a trained dictionary or a base. A reference is in the
   same pack always, so a pack is self-contained. Duplicates across
   packs are tolerated and removed at compaction under the maintenance
   lease; no pack is modified in place. Whole-file threshold a writer's
   policy, default to be measured, 64 KiB the working figure.
8. **Compression.** zstd is the single registry entry; nothing else
   enters without a tenant's measurement. The Python floor stays at
   3.11: `compression.zstd` from the standard library on 3.14 and the
   standard-library backfill, `backports.zstd`, below it, declared as a
   dependency conditional on the interpreter version, the same API
   either way. The decision rests on dictionaries: tiny
   objects do not compress alone and a trained dictionary is the
   mechanism that makes them compress. Dictionaries are objects.
9. **Deltas.** zstd is the delta engine: a frame compressed against a
   base object as its prefix. Measured on 267 changed standard-library
   files between Python 3.13 and 3.14: delta sizes equal git's to three
   digits, 0.086 MB both, with zstd 8% ahead on the whole objects; on
   the `Python` dylib pair git stored no delta and zstd's patch cut the
   older version from 3.99 MB to 2.84 MB. Deltas apply to bases under
   2 GB, the window limit. Lean: reserve the reference field, ship
   after a measurement on wheel versions.
10. **Chunking.** The chunked representation: a recipe, an ordered list
    of chunk digests and lengths as a tree of recipe nodes for very
    large objects, plus chunks as ordinary objects, the whole-file
    digest unchanged as the name. It gives dedup between versions of a
    large file, partial verified reads one level deep, lazy fetch,
    resumable transfer and per-chunk scrubbing, and it packs the chunks.
    Two chunkers in the registry: fixed-size at one to four megabytes
    for in-place edits and appends, and FastCDC at a 64 KB average for
    insertions, with the gear table by digest, the minimum, average,
    maximum and normalisation level fixed in the spec and a vector of a
    seeded input with its boundaries. A public gear table makes chunk
    sizes a content fingerprint, so an encrypted space may use a
    per-key-domain table at the price of dedup across domains. Lean:
    chunk large blobs by default at publish, with the recipe root on
    the entry.
11. **The shared tier's write protocol.** Our own service, or OCI's
    distribution API used as a shared tier: blobs by sha256, packs as
    blobs, resumable uploads, cross-repository mount, referrers. A tag
    has no compare-and-swap, so ref moves go through the service in
    either case. Lean: a page of its own before the service is
    designed; not layout work.
12. **Retention.** Whether ref history and record chains need a
    retention class in the first cut or grow until a consumer feels it.

## The design as it stands

### Formats

Every structured object is the codec's bytes and its name is their
digest. The tree: entries as arrays, `[kind, size, digest, name]` and
`[kind, target, name]` for a symlink, sorted by name bytes, with a header
carrying the subtree's entry count and byte total; about 40 bytes plus
the name per entry. The version as in item 5. The recipe as in item 10.
The ref-namespace tree and its versions as in item 6. Records and
tombstones in the same codec. Every size and count is unsigned 64-bit;
the 2^53 rule is gone with the JSON.

### Objects and representations

A tier holds an object as a whole file, in a pack, as a recipe with
chunks, or encrypted, and may hold more than one representation, for
instance chunks for transfer and a reconstructed whole for the clone
rung. Which it holds is the tier's business and changes nothing a reader
sees. Two tiers are the same tier when every name one answers the other
answers with the same plain bytes and their heads name the same
versions; nothing about their files has to match, and equality is a set
difference over names read from each tier's listing, never a directory
diff. A tier's listing, loose objects plus its packs' indexes, is a
first-class thing in the layout: a static tier publishes a listing of its
packs. Representation changes what a tier can serve cheaply, not what it
holds; chunks, recipes, dictionaries and packs are extra names a tier
may hold or not, and the identity test is over the closure of the roots
a consumer asks for.

Compression at rest is allowed inside representations: the name is the
digest of the plain bytes, whole files are plain, and a pack entry or a
chunk may be compressed or a delta. A filesystem's own transparent
compression and transport compression are invisible and need no spec
sentence.

### Encryption, reserved

Chunk-wise by construction, because a partial read decrypts what it
touches. Three threat models want it: a tier not trusted with content,
zero knowledge across a team, and erasure by key destruction. The name
under encryption is a keyed digest of the plaintext, HMAC-SHA256 or
blake3's keyed mode by address space, so names mean nothing without the
key and a tier cannot confirm that a known file is present; the
representation carries an at-rest checksum of the ciphertext so a tier
scrubs what it holds without keys. Cipher registry with one entry,
AES-256-GCM, the chunk index and the object's name as associated data;
a key identifier in the representation header; custody behind an
interface the store calls and never implements, since custody is the
fabric's; rotation as re-encryption at compaction; structured objects
encrypted like any other under the private models. Compressing before
encrypting leaks sizes. Recorded, not built, until custody exists.

### The registries

Digest: `sha256` mandatory, `blake3` optional. Chunker: fixed-size,
FastCDC. Compression: `zstd`. Cipher: `aes-256-gcm`, reserved. Every
entry has vectors; the mandatory entries are the defaults and
conformance requires them; an optional entry is named by the
implementation that supports it and its vectors run only there, so a
store opened in an optional address space refuses on an implementation
that lacks it, naming the algorithm. A registry entry is a permanent obligation
on every implementation, so the mandatory sets stay this small.

### The index

One SQLite file per local store, a cache rebuilt from the layout: the
verified marks, digest to pack and offset, reference counts maintained
by tree diffs at ref moves so the sweep costs in proportion to the
change, reachability bitmaps and a commit graph when the consumers need
them, a decoded-tree cache, a stat cache for `collect`, the outboards for
large objects. A home on a network filesystem uses the rollback journal,
not WAL, and the store says so at open. Losing the index costs a
rebuild, never data.

### The read API

`open(digest_or_entry)` returns a file-like object with `seek` and
`read`. Local and landed: a size check and `pread`. On a reference tier
or a remote source with a blake3 name, or an entry carrying a recipe
root: verified slices. Otherwise the whole object is verified once and
served, which is today's behaviour, reported through the sink above a
size the caller sets. One refusal: a sha256 object on a remote source
too large to land, naming the object, the size and the two ways out. No
capability query; consumers see one call.

### The algorithm interface

A registry entry is an interface, not a name: hash whole bytes; hash a
stream producing the name and, where the algorithm has a tree, the
outboard in the same pass; verify a slice against a name given range
access, where supported. sha256 does the first two; blake3 all three.

### Tiers and the tier interface

A tier is a place that holds objects in the layout; a source is how a
store instance reaches a tier it does not own. The tiers: the local
tier; a folder with copy policy; a folder with reference policy; an HTTP
static tier; an origin hint; a bucket; the service; an OCI registry if
ruling 11 admits it. Presentations over tiers, which are not tiers: the
view, the mount, a browser client. Four rules hold them together: one
layout for every tier with packs as the unit of transfer; consulted in
order and never trusted; fill upward is publication; one authority per
namespace, so a ref read from a non-authoritative tier is a copy with a
freshness window and never a fact, where a write-once copy is a fact
once present, a monotone copy a lower bound, and a volatile copy a hint,
and a copied ref can also be forged, which is what the receipt on the
head's version is for.

Backends sit behind one small interface, the shape of Arrow's
`object_store` crate and Iceberg's `FileIO`, never fsspec's: `get`,
`get_range`, a vectored `get_ranges`, `head`, paged `list`, `put` with
`if_none_match`, `delete`, and `path` where the backend is a real
filesystem. Capabilities are a declared record, rclone's model: range,
vectored, list, conditional put, durable, local path, latency class. A
head move needs a conditional put or a local replace and is refused
elsewhere, naming the backend. A network filesystem is the local backend
with a flag that says locks are not to be trusted. Everything above the
interface, location, representation decode, verification, source order,
fill, prefetch, shed, scrub, compaction, never sees a transport, and a
fake backend drives all of it in tests. The Rust implementation uses
`object_store` directly, so naming our verbs to match costs nothing.
Kept out of the interface: directories, permissions, metadata beyond
size and tag.

### The service

An accelerator over the layout, never a requirement: the same server
exposes the layout over plain HTTP so a Python client, a browser, `curl`
and a `cp -r` mirror work without it. Over QUIC, pack-aware, in the
native implementation: negotiation, the client's roots and wants
answered with exactly the missing objects as one pack stream verified as
it arrives; `FindMissingBlobs` before an upload; leases; the
compare-and-swap on a namespace head; slices with their outboard path in
one stream; many streams per connection. The sequence: packs first,
which cut SMB's round trips on the same share; the NAS tier served over
HTTP/3 beside SMB, which nginx and Caddy do today; the service when
negotiation or writes across a trust boundary are needed. A measurement
owed before any of it: per-open latency to the filer from a Mac and from
a Windows box.

### The materialiser

The ladder is unchanged. Added: profiles from ruling 3, the escape
table, the path budget as a profile value, the report of rewritten and
over-long paths, and `collect` over a whole root with ignore rules and
the stat cache. A packed object cannot answer `path` with a path into
the tier, so `path` on one copies out to the index or the caller uses
`read`. If a mount is built, it uses the private-use mapping for names
and FUSE passthrough, FSKit or WinFsp for whole files, with packed small
objects served by the daemon.

## Reservations for the future

Each is a field, a code or a sentence in the spec now, so a later tenant
adds without touching a hashed byte.

- **Aliases** on a blob entry: a map of algorithm to digest.
- **The reference field** in a pack entry, for dictionaries and deltas.
- **The version entry kind** in trees.
- **The large-directory node kind.**
- **A store id** in the root manifest, so a source that is the store
  itself is refused; today `shed` on such a source would evict
  everything.
- **A filesystem tenant.** Room in the entry kind code space with block
  and character devices, FIFOs, sockets and whiteouts named and
  numbered; the recipe with holes, fixed chunks and in-place
  replacement; an attribute overlay, a parallel structure keyed by
  path hung on the version as an attachment, holding mode, owner,
  times, extended attributes and stable identity, so the content tree
  never carries metadata churn and identity never enters an entry, a
  rule stated so nobody adds an `id` field to the tree and breaks dedup
  for everyone; access time never stored; the executable bit stays in
  the entry. Everything else a filesystem needs, the write-back
  journal, checkpoints as versions, locking, case-insensitive lookup on
  the surface, a `.snapshots` directory, NFS export over overlay
  identities, is a tenant on top. This is Venti and Fossil, not btrfs:
  btrfs and bcachefs manage one machine's disks and compose underneath
  the local tier.
- **A block-device backend.** Behind the tier interface, for the
  service tier on an appliance, with its own arena and index, declaring
  no local path so the rungs refuse it correctly; BlueStore-class gains
  at the ingest end, and nothing before packs on XFS are the bottleneck.
  Haystack keeps its volumes as large files on XFS; the win is few large
  files plus an index, which packs are.
- **Fast local reads.** Vectored reads in the tier interface; aligned
  pack entries, padding allowed since offsets live in the index;
  block-wise frames for large entries, which chunking gives; a registry
  slot for a GPU-decodable compressor, GDeflate, if a game or ML
  pipeline ever loads from packs directly. DirectStorage, `io_uring` and
  `cuFile` are implementations of the local backend's vectored read,
  never a tier. The practical route for Unreal is a cook that emits
  IoStore containers as a derivation from the store.

## Deferred from the first cut

Encryption beyond the naming ruling, the blake3 slice walk in whichever
implementation gets the binding first, the OCI tier page, the mount.
Each enters afterwards without a layout change because the layout
reserved its place. Prolly trees and deltas are not deferred: both are
in the plan before the freeze.

## Lessons taken from other systems

| System | Lesson |
| --- | --- |
| git | packs, fanout index, delta chains, repack; reachability bitmaps; its type-prefixed names, compression as the only state, sizeless trees and age-based GC are the four decisions refused |
| jj | the repository's state versioned as an operation log, identity separate from content, conflicts as data; ruling 6 |
| Unison | every definition content-addressed, names a versioned map; its v1 one-file-per-definition codebase moved to SQLite for the same small-file cost measured here |
| Iceberg, Delta Lake | immutable manifests, one atomic pointer, snapshot expiry as retention |
| Xet | 64 KB content-defined chunks in 64 MB packs, global dedup by chunk query |
| kopia, restic, Borg | packs and compaction; kopia's lock-free concurrent writers to S3 by epoch-merged index blobs |
| Prolly trees, atproto's MST | the missing tree shape for flat namespaces at scale |
| REAPI | `FindMissingBlobs`; compression negotiated by capability |
| OSTree, composefs | whole files with hardlink checkouts; metadata in an image beside content |
| Nix | signed records beside names; substituters as tiers; one-NAR-per-package as the dedup flaw |
| crates.io, conda, apt | a registry index is a tree read by path, sharded and content-addressed, with one pointer swapped last |
| Go's sumdb, Certificate Transparency | a registry needs a tamper-evident log of its moves |
| Haystack, SeaweedFS | needles in volumes with an in-memory index |
| IPLD | separable from IPFS and not pursued; CAR files and the trustless gateway pattern are the two shapes borrowed, closure-as-pack and export |
| Venti and Fossil | a content-addressed archive with a filesystem on top, 2002 |
| iroh | blake3 with Bao over QUIC, and a team that left IPFS's stack for it |

## Measurements

Done, in the review note: 802 objects in the live store, 646 at or below
512 bytes, one mark file per object; 20,000 200-byte objects cost 164 MB
allocated as files with marks, 90 MB with marks in SQLite, 11.6 MB in
one SQLite file; an open costs 650 to 970 microseconds on this Mac with
Defender running against 5 for a stat; node has 2,058 files with 1,329
under 4 KiB, the uv tools directory 33,873 with 28,625. Done, above: the
delta measurement of item 9.

Owed, one corpus per tenant, public and reproducible wherever one
exists, run by one harness that replays each corpus into a prototype
store and emits one table, kept so a later change of a default re-runs
it. Each row names the default it decides.

| Tenant | Corpus | Measured | Decides |
| --- | --- | --- | --- |
| registry | every release of a few hundred of the most downloaded PyPI packages, wheels for one platform tag and sdists, extracted | whole-file dedup across versions; zstd delta size against the same path in the previous version; tree bytes per version; pack sizes with and without a trained dictionary for `.py` and JSON | deltas (item 9); the pack threshold; dictionary training |
| version control | public repositories of different shapes replayed commit by commit, a large single-language one such as CPython, a monorepo, a kernel subtree; and LFS repositories, `audio2mesh_demo` first: 442 commits, 2,147 LFS files at its head, 28 GB of LFS objects across its history, binaries per platform and model files among them | object counts and pack bytes against git's own pack; tree bytes; fixed against content-defined dedup on the LFS objects across their versions; commit-graph and sweep costs | the chunker defaults for checkpoints and binaries; the whole-file threshold; the pack count policy |
| large data | public dataset families with revisions, Parquet shards from a versioned Hugging Face dataset, successive fine-tunes of one public checkpoint family, a Zarr array with appended chunks | fixed against content-defined dedup between versions; chunk counts and recipe sizes at each average; hashing throughput per core and per file; outboard size at each group size | the FastCDC average; the fixed chunk size; the outboard group size |
| tool store | successive versions of node, uv, bun and CPython installs | file-level dedup across versions; small-file counts and sizes; the gain from a dictionary on the tiny files | the pack threshold; whether dictionaries ship in the first cut |
| filespace | public media and project files edited in reproducible steps: Blender demo files across their versions, an intra-frame and a long-GOP encode of one public source re-exported after an edit, a large layered image saved after each of a scripted series of edits | bytes uploaded per save under fixed and content-defined chunking; dedup across saves; write-path cost of re-chunking a modified region | fixed against content-defined for media and working files; the fixed chunk size |
| every tier | the studio filer from a Mac and a Windows box | per-open latency and per-range latency over SMB, against local and against HTTP/3 from the same host | the service's sequence |
| the materialiser | a real node and uv tree on each platform | time per rung per file size | the whole-file threshold |

`audio2mesh_demo` is private, so its numbers are reported and its corpus
is not redistributed; every other row is public so anyone can re-run
the table. A row's default is general purpose; a tenant that measures
differently tunes through the policy.

## Decision record

- 2026-09-24, Willem: the review requested and written down.
- 2026-09-24, Willem: pre-release; no backward compatibility, no layout
  2 language, no migrations; both live stores rebuilt by their
  producers; the targets are terabytes of binary, gigabytes of tiny
  text, and a package registry with deltas between versions.
- 2026-09-24, Willem: stdlib-only is aspirational hygiene, not a spec
  constraint; a language-agnostic spec limited to every language's
  standard library would be anaemic.
- 2026-09-24, Willem: zstd over zlib, by merit; defaults last forever,
  so the default is the right one, not the available one.
- 2026-09-25, Willem: ruling 1, C.
- 2026-09-25, Willem: IPLD not pursued.
- 2026-09-25, Willem: ruling 2, the store's own codec tracking RFC 8949
  exactly, floats in the codec, shared with the fabric.
- 2026-09-25, Willem: chunker configuration external, completely
  exposed, in the API and the standard.
- 2026-09-25, Willem: ruling 3, C with escaped materialisation.
- 2026-09-25, Willem: nothing forecloses a block-device backend; the
  QUIC service is the way past SMB once a native implementation exists.
- 2026-09-25, Willem: the Python floor stays at 3.11, with the
  standard-library backfill for zstd below 3.14.
- 2026-09-25, Willem: `blake3` is optional in conformance; `sha256` is
  the one mandatory digest.
- 2026-09-25, Willem: ruling 4, prolly trees deferred with the node kind
  reserved, on the condition that whether a directory above a fixed
  size must be one is decided by the freeze, since after it the same
  directory in two shapes has two names.
- 2026-09-25, Willem: ruling 7, packs as described; FastCDC at 64 KB is
  unworkable without them.
- 2026-09-25, Willem: ruling 11, the shared tier's write protocol gets
  a page of its own, on the condition that nothing is foreclosed.
- 2026-09-25, Willem: the measurement harness is a script inside the
  strongroom package, not shipped in the wheel for now.
- 2026-09-25, Willem: the fabric's superseded normal-form decision is
  recorded here; its own plan says so when next touched.
- 2026-09-25, Willem: ruling 5, B. A version is a core the sweep walks,
  tree, parents, attachments, receipt, plus an ordered list of header
  pairs for provenance, known headers named in the spec with their
  value encodings and their order, unknown headers after them in byte
  order, `created` a known optional header so a deterministic producer
  gets a reproducible digest.
- 2026-09-25, Willem: ruling 6, B with conflicts as data. A namespace's
  refs are a tree, a move is a version, one head per namespace moved by
  compare-and-swap, mutation classes as merge rules, and two moves of
  one ref on one parent under write-once or volatile keep both
  candidates as a conflict entry for a person to resolve.
- 2026-09-25, Willem: ruling 9, deltas ship in the first cut. The only
  benefit of postponing was scope, and dictionaries need the same
  reference field and the same resolve path, so a delta is the same
  code with a different reference; the measurement needs the
  implementation in any case.
- 2026-09-25, Willem: ruling 10, chunking is default-on above a
  threshold, with the recipe root on the entry.
- 2026-09-25, Willem: ruling 12, retention deferred; pruning is an
  ordinary version with its parents cut and the sweep removes the
  unreached, nothing in the format waits on it.
- 2026-09-25, Willem: the codec package is `livery-cbor`, import
  `livery.cbor`, Python only for now. Its spec page and vectors are
  language-neutral and live with the spec; the package has no
  dependencies and imports nothing first-party, pinned by its contract
  test, since strongroom and the fabric import it.
- 2026-09-25, Willem: large directories are safe to postpone within the
  plan and are implemented before the freeze, so the size rule is in
  the frozen format.
- 2026-09-25, Willem: the second name profile is `permissive`.
- 2026-09-25, this note written. The plan follows rulings 4 to 12.

## Open

1. **Resolved 2026-09-25.** Rulings 4 to 12 are closed; the decision
   record says how.
2. **Resolved 2026-09-25.** `blake3` is optional in conformance.
3. **Resolved 2026-09-25.** The codec package is `livery-cbor`; the
   name profiles are `portable` and `permissive`.
4. The measurement table above, each row before the default it decides
   freezes, run by a script inside the strongroom package that the
   wheel does not ship. Owner: the plan.
5. **Resolved 2026-09-25.** Rulings 5, 6, 9, 10 and 12 closed as the
   decision record says. The large-directory rule of ruling 4 is
   decided by the freeze. The plan note is next.
