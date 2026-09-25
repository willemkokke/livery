# The strongroom on-disk format, reviewed for scale

Status: a review, written 2026-09-24 at Willem's request. Its
measurements stand and are cited by
[the design record](20260925-strongroom-redesign.md), which superseded
its four proposals on 2026-09-25 with the twelve rulings of the
redesign, and by [the plan](20260925-strongroom-redesign-plan.md),
which owns the measurements it left open. The proposals below are kept
as the reasoning of the day, not as live proposals. The spec under
review was `packages/strongroom/spec/` at layout 1, as released in
`livery-strongroom 0.0.0`.

## What this is

The question was whether the strongroom's on-disk format is as future
and scale proof as it can be: whether a better representation exists,
whether canonical JSON is the best hashed form, whether a database
built for content addressing exists, and whether the disk block cost
of many small files can be amortised.

The verdict, first. The hashed formats (digest strings, canonical JSON
trees, versions, ref records, tombstones) are sound and no hashed byte
should change. The weak point is how objects are held on disk, not how
they are named: one whole file plus one mark file per object, never
synced, with no packed representation. The spec already reserves the
fix in `layout.md`: how bytes are held is a backend property and never
in the name, and this layout version stores whole files only. A packed
representation is therefore a priced migration under a new layout
number, not a redesign.

## What was measured

The live store at `~/.local/share/footman/toolroom/store` on this Mac
(APFS, 4 KiB allocation blocks, macOS 26.5.1), measured 2026-09-24:

| Part | Files | Apparent | Allocated |
| --- | --- | --- | --- |
| `objects/` | 802, of which 646 are at or below 512 bytes | 108.1 MB | 110.9 MB |
| `index/verified/` marks | 804 | 3 KB | 3.3 MB |
| `refs/` | 4 (two refs, two records) | | |

The tiny objects are the tool index's trees (576) and lists (33), not
archive contents. The two tool views (`ty@0.0.62`, `uv@0.11.26`) are
three files, cloned, no hardlinks.

Real tool trees are the near-term small-file case:

| Install | Files | At or below 4 KiB |
| --- | --- | --- |
| `/opt/homebrew/Cellar/node/26.5.1` | 2,058 | 1,329 |
| `~/.local/share/footman/toolroom/uv/tools` | 33,873 | 28,625 |

A synthetic run on the same volume, in the session's scratch
directory, 20,000 objects of 200 random bytes (4 MB of payload),
written one at a time as the store writes them (scratch file, then
replace), then read back in random order:

| Holding | Inodes | Allocated | Write | Read all |
| --- | --- | --- | --- | --- |
| whole files plus mark files (today's layout) | 40,000 | 164 MB | 12.3 s | 66.9 s |
| whole files, marks in one SQLite file | 20,000 | 90 MB | 6.8 s | 53.3 s |
| one SQLite file holding the objects | 1 | 11.6 MB | 0.35 s | 0.05 s |

The same at 20,000 objects of 3,000 bytes (60 MB of payload):

| Holding | Allocated | Write | Read all |
| --- | --- | --- | --- |
| whole files plus mark files | 164 MB | 12.3 s | 66.4 s |
| whole files, marks in SQLite | 91 MB | 6.6 s | 49.2 s |
| SQLite holding the objects | 88 MB | 0.8 s | 0.07 s |

At 3 KB the allocation gap closes and the read gap stays. The cause
is the per-file open, not the block: an `open`, `read`, `close` of one
small file costs 650 to 970 µs on this machine against 5 µs for a
`stat`. Microsoft Defender's daemons are running and scan every open.
That is the condition on every managed developer machine. No
block-size trick removes it; only fewer opens do. The SQLite reads
above are a page-cache measurement and say what a pack buys, not what
a cold disk gives.

Run inside and outside the agent's sandboxed shell, the numbers are
the same, so the sandbox is not the cause. The benchmark script is
not kept; it is twenty lines over `sqlite3` and `os.replace`, and the
next measurement writes its own.

## The formats, one by one

**Digest strings.** Sound. One algorithm per address space, the
algorithm in every name, lowercase hex only. Nothing to change.

**Canonical JSON as the hashed form.** Keep, for the reason ruled on
2026-08-31: a second hashed encoding is a second name for the same
tree, the same silent dedup loss as mixing algorithms. The cost is
real and bounded: a tree entry runs 150 to 170 bytes, three times
git's binary entry, most of it the 71-byte digest string. That is
about 35 MB of tree objects for a 200,000-entry version, parsed in
well under a second, and only on a cold full walk. The efficiency the
question reaches for lives in the representation behind the name and
in the local index. A parsed tree cache in the index is already legal.
A binary tree at rest is a representation behind the same digest,
also already legal, and it changes nothing about the count of objects:
a binary tree is still one object per directory.

Two reservations in the tree and group formats, both layout-2 items
and neither urgent:

- A tree carries no cumulative entry count or byte total for its
  subtree, so planning a view, showing progress, or answering "how
  big is this version" needs a full walk. Git lacks it too.
- A group's manifest names its moves as four decimal digits
  (`_groups.py`, `f"{index:04d}"`), so a group past 9,999 moves
  sorts out of order. The journal in the pending record's `meta`
  carries the truth and nothing reads the manifest's order, so this
  is a limit to state, not a fault.

**The version, the ref record, the tombstone.** Sound. The subject
slot's four kinds are validation, so a fifth kind is a spec change
and not a format break.

**The root manifest.** `Manifest.decode` refuses unknown keys
(`expect_object` in `_fields.py` wants exactly the named keys). That
is right for hashed formats, whose bytes are their name. For the
unhashed root manifest it means every addition, a representation flag
included, is a layout bump. Accept that: the layout number exists for
exactly this, and a layout-1 reader that refuses a store with packs
is refusing correctly.

**The object layout.** `objects/<algorithm>/<xx>/<rest>` is git's
loose-object stage with a 256-way fanout. At 10^6 objects that is
about 4,000 entries per directory; at 10^7, 39,000. ext4, APFS and
NTFS handle both; the sweep's `objects()` listing and a listing over
SMB are the only things that walk it. Fine as it stands, and moot
once small objects pack.

**The verified marks.** One file per object under
`index/verified/`, holding the size. That doubles the inode count and
the allocated bytes of every small object, and doubles the opens on
landing. The index is out of spec by design, and the strongroom plan's
"temporary, replaced by" table already schedules SQLite per local
store for the reverse index. The measurements above say to pull it
forward.

**Durability.** There is no `fsync` anywhere in `livery.strongroom`.
On the object side this self-heals: a torn object after power loss
fails the size check against its mark, or has no mark and is hashed
in full, and either way is evicted and landed again. On the ref side
it does not: a ref and its record are two replaces, unsynced, and
after power loss they can land torn or out of order and then read as
tampered until a person fixes them by hand. `fill` into a shared tier
is unsynced too, and a mirror on a NAS is the copy that must survive
a crash.

**Ref namespaces.** The published conventions `urls/<sha256>` and
`derivations/<call key>` put every ref flat in one directory, at two
files per ref and a third while locked. At 10^5 to 10^6 refs a flat
directory hurts on NTFS and over SMB, and the sweep reads every ref
as a root. The store's live `urls/` namespace holds no refs yet.

**The sweep.** A full mark from every ref through every tree, with
no reverse index. Seconds at 10^6 objects, minutes at 10^7. The
reverse index is planned and SQLite in the index is where it goes.

**Compression.** Never at rest, by rule, and the rule is right for
names. Two things are invisible to the format and worth knowing: a
filesystem's own transparent compression (btrfs zstd, ZFS lz4, APFS
decmpfs as Homebrew uses it) is a backend property, and an HTTP tier
may compress in transit. Neither needs a spec sentence.

## Better representations, and what they share

Every mature store converges on one shape: whole files where
zero-copy matters, packs for the small, a key-value index for
metadata.

| Store | Small objects | Large objects | Metadata |
| --- | --- | --- | --- |
| git | packs, multi-pack index | packs | packed refs |
| restic, kopia, borg | packs of 16 to 128 MB | chunked into packs | index blobs |
| Hugging Face xet | 64 MB xorbs | chunked into xorbs | shards |
| Unreal Zen | `.ucas` chunk files | `.ucas` | RocksDB |
| Facebook Haystack, SeaweedFS | needles in a volume file | same | in-memory index |
| Nix | whole files | whole files | SQLite |
| ostree, composefs | whole files, hardlinks | whole files | an EROFS image |
| strongroom layout 1 | whole files | whole files | files |

Strongroom's layout is git's loose-object stage, with sizes in the
tree and a record beside each ref. That is the right stage to have
shipped first. It lacks the pack stage.

## A database for content addressing

No product is a content-addressed database as such. The embedded
candidates:

- **SQLite.** In Python's standard library. SQLite's own "faster than
  the filesystem" study puts reads of blobs under 10 KB about 35%
  ahead of one file per blob with about 20% less disk; the run above
  is far past that because of the per-open cost on this machine. Its
  file format is documented and stable. Unsafe over SMB and NFS,
  which the planning store note already states.
- **LMDB.** Memory-mapped, read-fast, one writer at a time. A
  dependency in every language.
- **RocksDB with BlobDB.** Key-value separation for large values, the
  choice of Zen and Ceph BlueStore. A heavy dependency.

For a stdlib-only standard, SQLite is the only one that costs nothing
in Python, and it can only ever be a local representation and an
index. The shared tier stays the layout served as files: a static
HTTP server and an object bucket read files, and SQLite does not
survive a network filesystem.

## Amortising the small-file cost

The filesystems that matter give no help. APFS, XFS and default ext4
charge a full block plus an inode per object. btrfs inlines files to
2 KiB in its metadata tree, NTFS keeps files under about 700 bytes
resident in the MFT record, ZFS sizes the record to the file; none of
the three is a developer's default on a Mac or a Linux runner, and
the per-open cost under an endpoint agent is the same on every one.

The producer-side ruling for hse's datasets stands: a corpus of
hundreds of thousands of small files is a sharding derivation's
problem first, and a few large files dedup and materialise for free.
It does not cover the store's own small files: tool trees, the tool
index's trees and lists, and the one tree object per directory that
is intrinsic to the Merkle shape.

The amortisation is a pack for objects at or below a threshold, held
under `objects/<algorithm>/packs/` beside the whole files, under a
new layout number. The threshold that loses nothing is the block
size:

- above it, the clone, hardlink and link rungs need one file per
  object, so the object stays a whole file;
- at or below it, a copy costs the same inode a clone would, so
  packing leaves every rung's economics unchanged;
- names stay per file, so dedup across tool versions is unchanged:
  the npm tree that does not change between two node releases is
  still the same objects.

Two consequences to state in the design: a packed object cannot
answer `path` with a path into the tier, so `path` on one copies out
to the index or the caller uses `read`; and the shared tier may stay
whole-file, since a NAS runs no endpoint agent, with `fill` reading
from the local pack and writing files.

## Proposals, in order

1. **Sync ref moves and fills.** `fsync` (`F_FULLFSYNC` on macOS) the
   record and the ref replaces in `set_ref` and the group commit, and
   every file `fill` lands in a folder tier. Never the hot landing
   path: verify-on-access already covers a torn object. No format
   change. Its own issue.
2. **Move the verified marks into one SQLite file per local store.**
   The index is out of spec. It halves the inode count and the
   allocated bytes for small objects now, and it is where the reverse
   index for the sweep goes later. One caveat: a home on a network
   filesystem needs the rollback journal, not WAL, and the store says
   so when it opens one. Its own issue.
3. **Fan out the published ref conventions.** `urls/<xx>/<rest>` and
   `derivations/<xx>/<rest>` in `namespaces.md`, while the only live
   `urls/` namespace is empty. A spec change to a convention, not to
   a format. Its own issue.
4. **Price the pack as layout 2.** The root manifest's strictness
   makes it a layout bump anyway. Decide then whether the pack is a
   specified format, implementable from the spec alone as git's is,
   or SQLite, a file format that is not ours with atomicity and
   concurrency for free. The lean here: SQLite for the local tier,
   whole files for shared tiers. A plan note of its own, on a
   measurement from a real tool tree rather than random bytes.

Everything else scales as it stands to about 10^6 objects: the
fanout, the sweep's full mark, the path budget on Windows.

## Decision record

- 2026-09-24, Willem: asked for the review and for it to be written
  down. No proposal is ruled.

## Open

1. **Superseded 2026-09-25.** The proposals were replaced by the twelve
   rulings of the design record: fsync on ref moves and fills, the
   SQLite index, refs as versions per namespace, packs with the
   whole-file threshold as a writer's policy.
2. **Superseded 2026-09-25.** The pack threshold is decided by the
   plan's measurement phase, from view rung timings on real node and
   uv trees.
