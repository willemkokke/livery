# The strongroom redesign plan: livery-cbor, the spec rewritten, the freeze

Status: phase 1 landed 2026-09-25 (issue #690, PR #692) and
livery-cbor 0.0.0 shipped through the release train the same day;
phases 2 to 11 wait for Willem's go one at a time. The plan executes
the twelve rulings of
[the design record](20260925-strongroom-redesign.md). Phases land one
at a time, gate-green, each updating this note in the same change.
The freeze, phase 11, is the milestone after which compatibility
exists; before it, any ruling reopens at the cost of an edit and a
vector.

## The prompt (Willem)

Reviewed the on-disk format for scale, found it sound in its names and
costly in how it holds objects, and ruled the store pre-release: no
backward compatibility, no migrations, no legacy. The targets are
terabytes of binary, gigabytes of tiny text, a package registry, a
version control system and a shared filespace, all over one substrate.
Twelve rulings followed, recorded in the design note; this plan is
their sequence.

## Scope

In: `packages/cbor/`, distribution `livery-cbor`, import `livery.cbor`,
Python only for now. `packages/strongroom/` rewritten in place: the spec
under `packages/strongroom/spec/` replaced, the Python replaced module
by module, the conformance suite replaced, the docs rewritten. The
measurement harness as a script inside the strongroom package, not in
the wheel. The two consumers moved to the new API in one phase:
`packages/toolroom-store/`, `packages/toolroom-bench/` and the tools
module in `packages/workshop/`. The two live stores deleted and rebuilt
by their producers. The freeze and the first releases of both packages
through the train.

Out: the blake3 slice walk, encryption beyond its naming rule, the
mount, the Rust service, the OCI tier page, the registry and version
control consumers. Each is reserved a place by this plan and none is
built by it.

## Ground-truth contracts (do not violate)

1. **The spine.** An object's name is the digest of its plain bytes,
   no type prefix, one algorithm per address space. Objects above a
   threshold are plain whole files; below it, packs. Trees are Merkle,
   sorted, sized, with subtree aggregates, in one binary hashed form.
   Reachability, never age, with a write rooted before its bytes land.
   Every tier verifies and none is trusted.
2. **Vectors before persistence.** Every hashed format has a vector
   file with cases and refusals before the code that writes it. A
   registry entry has a vector before it is admitted. A change to a
   vectored format after the freeze is a migration and stops for the
   human; before the freeze it is an edit and a vector.
3. **The codec is `livery.cbor`** and nothing else: RFC 8949 core
   deterministic encoding over the subset the design note fixes, floats
   under the IETF deterministic rules, no tags, no indefinite lengths,
   a decoder that refuses every non-deterministic encoding. The store's
   own schemas are integer-only. No third-party CBOR library anywhere
   in the workspace.
4. **Dependencies point downward.** `livery.cbor` imports nothing
   first-party and has no dependencies. `livery.strongroom` imports
   `livery.cbor` and the standard library, plus `blake3` as an optional
   extra and `backports.zstd` below Python 3.14. Nothing in the
   household imports strongroom except its declared consumers. Never
   create `livery/__init__.py`.
5. **The Python floor is 3.11.** `compression.zstd` on 3.14,
   `backports.zstd` below it as a version-conditional dependency, one
   import name in the code.
6. **The registries stay small.** Digest: `sha256` mandatory, `blake3`
   optional. Chunker: fixed-size and FastCDC. Compression: `zstd`.
   Cipher: `aes-256-gcm` reserved and unimplemented. An optional entry
   is named by the implementation that supports it and its vectors run
   only there.
7. **Representation is the tier's business.** A reader asks by name
   and gets plain bytes; which representation answered is never
   visible above the tier. Two tiers are the same tier when they
   answer the same names with the same bytes and their heads name the
   same versions.
8. **Seal before ref.** A ref never names an object that is only in an
   unsealed pack. Ref moves and fills fsync; the hot landing path does
   not.
9. **Refs are versions.** A namespace's refs are a tree, a move is a
   version, one head per namespace moves by compare-and-swap, the
   mutation classes are merge rules, and a conflict keeps both
   candidates as data.
10. **Names.** Five format rules: valid UTF-8, not empty, not `.` or
    `..`, no `/`, no NUL. Profiles `portable` (the default) and
    `permissive`. A name a platform cannot hold is materialised
    escaped, reversibly, and reported; parked only when it cannot be
    made.
11. **Policies are the caller's, exposed in full**, in the API and in
    the standard, with the defaults the design note names until the
    measurements replace them. The store never reads a file's name or
    type to choose a representation.
12. **The store never prints.** Every fallback that costs is reported
    through the caller's sink.
13. **Fallbacks before happy paths.** Every refusal, every fallback and
    every rung's refusal is forced by a test before its success path.
14. **The gate is green at every phase cut** on every runner, Windows
    included. Old modules stay exported until the switch phase so the
    consumers keep working; new modules land beside them, tested
    alone, unexported.
15. **Strongroom knows no tool, no call, no package, no commit.** A
    test pins that nothing under `livery.strongroom` imports a
    consumer.
16. **General purpose.** Every default is measured on the public
    corpora of the measurement table, never on one tenant's data.

## Phases

Each phase lands alone, gate-green, with this note updated in the same
change. Exceptional cases are tested before happy paths in every phase.
`fm check` is the gate command throughout; `--fix` is the
iteration loop.

### Phase 1: livery-cbor

Deliverables:

- `fm new.package cbor --kind=package-python`, then the seeded surface
  replaced. `packages/cbor/workshop.toml` with `type = "python"`,
  `name = "livery-cbor"`, no `[[depends]]`, coverage floor 100.
  `pyproject.toml` with `dependencies = []`, `requires-python =
  ">=3.11"`, no scripts.
- `packages/cbor/spec/codec.md`: one page, the table of six major
  types, the argument rule, the eight rules, the float rules, the three
  boundary hazards. `packages/cbor/spec/vectors/codec.json`: cases at
  every boundary of the argument rule, every container and string edge,
  key ordering with shared prefixes and unequal lengths, UTF-8 above
  ASCII, every float width, NaN and negative zero; refusals for a tag,
  an indefinite length, a non-shortest integer and float, unsorted and
  duplicate keys, a byte-string key, invalid UTF-8, `undefined`, and
  every other simple value. A CDDL fragment for the subset.
- `livery.cbor`: `encode(value) -> bytes`, `decode(data) -> value`, a
  strict decoder that refuses every non-deterministic encoding naming
  the rule, typed values (`int`, `float`, `bytes`, `str`, `list`,
  `dict`, `bool`, `None`), `__all__` pinned.
- `packages/cbor/tests/test_cbor_vectors.py` parametrised over the
  vector file, one id per vector; `test_cbor_package.py` pinning no
  dependencies, no first-party import, no `livery/__init__.py`.
- `packages/cbor/docs/index.md`: what the codec is, the subset, the
  hazards, how a second implementation runs the vectors.

Refusals tested first: every refusal vector; a decoder handed the
non-canonical spelling of a case vector.

Acceptance:

- `uv run fm check` exits 0.
- `uv run fm typecomplete` exits 0 with `livery.cbor` at 100%.
- `uv run python -m pytest packages/cbor -q` exits 0 and lists one test
  per vector.
- `uv run python -c "import livery.cbor, sys; assert not [m for m in
  sys.modules if m.startswith('livery.') and m != 'livery.cbor']"`
  exits 0.

### Phase 2: the spec replaced, and the formats in Python

Deliverables:

- `packages/strongroom/spec/` emptied and rewritten. The old vectors,
  conformance cases and pages are deleted, not marked superseded.
  Pages: `README.md`, `digest.md` (both entries, aliases), `codec.md`
  (cites livery-cbor), `tree.md` (entries as arrays, aggregates, kind
  codes reserved for blob, executable blob, tree, symlink, version,
  large directory, block device, character device, FIFO, socket,
  whiteout; the aliases map; the size rule for large directories
  stated as pending phase 8), `version.md` (walked core: tree, parents,
  attachments, receipt; ordered headers; known headers with their
  encodings and order; unknown after in byte order), `recipe.md`
  (chunk list as a tree of recipe nodes, holes, the chunker entry
  carried), `chunkers.md` (fixed-size; FastCDC with the gear table by
  digest, minimum, average, maximum and normalisation fixed; a vector
  of a seeded input with its boundaries), `refs.md` (the namespace
  tree, the move version, the head, merge rules, the conflict entry),
  `records.md` (tombstone), `pack.md` (header, entry header with type
  hint, encoding and reference, alignment padding, trailer digest, the
  index with fanout, offsets, plain sizes and checksums, the listing
  file), `compression.md` (`zstd`, dictionaries as objects),
  `profiles.md` (`portable`, `permissive`, the escape table),
  `policy.md` (the representation policy document), `layout.md`
  (whole files, packs, the listing, heads, `store_id` in the manifest,
  scratch), `sources.md`, `lifecycle.md` (sweep from heads, seal before
  ref, compaction, retention as a future class), `materialiser.md`
  (profiles, escapes, budget, report).
- Vectors under `spec/vectors/`: `tree.json`, `version.json`,
  `recipe.json`, `refs.json`, `records.json`, `pack.json` (a pack and
  its index, byte for byte, with a plain, a compressed, a dictionary
  and a delta entry), `chunkers.json`, `escape.json`, `policy.json`,
  `manifest.json`.
- The Python for the formats, unexported until phase 10:
  `_formats/tree.py`, `version.py`, `recipe.py`, `refs.py`,
  `records.py`, `pack.py`, `chunkers.py` (fixed; FastCDC in pure Python
  with the gear table as a blob), `escape.py`, `policy.py`, each with
  `encode`, `decode` and validation, driven by the vectors.
- `packages/strongroom/tests/test_strongroom_vectors.py` parametrised
  over every vector file.

Refusals tested first: every refusal vector; a tree out of order; a
header out of the spec's order; a recipe whose chunker entry is unknown;
a pack whose trailer does not verify; an escape that does not
round-trip.

Acceptance:

- `uv run fm check` exits 0.
- `git ls-files packages/strongroom/spec | grep -c json` equals the
  count of vector files this phase names.
- `uv run python -m pytest packages/strongroom/tests/test_strongroom_vectors.py -q`
  lists one test per vector.
- `git diff --stat main -- packages/toolroom-store packages/toolroom-bench packages/workshop`
  is empty.

### Phase 3: the tier interface, the local tier, packs and the index

Deliverables:

- `_tier.py`: the interface, `get`, `get_range`, `get_ranges`, `head`,
  `list`, `put` with `if_none_match`, `delete`, `path`, and the
  capability record: range, vectored, list, conditional put, durable,
  local path, latency class. `_backends/local.py` with the
  network-filesystem flag; `_backends/fake.py` for tests, declaring any
  combination.
- `_index.py`: one SQLite file per store, rollback journal on a network
  home, tables for verified marks, digest to pack and offset, a
  representation per object; rebuilt from the layout by one scan.
- `_objects.py`: landing under a representation policy, whole above
  the threshold, packed at or below it, chunked above the chunking
  threshold with the recipe root returned for the entry; per-process
  packs sealed by fsync and rename to their digest, the index written
  at seal, sealing at a size bound and at process exit; the listing;
  `open()` with the whole-object fallback, reported through the sink
  above a caller's size, and the one refusal; `verify`, `scrub`,
  `representation(digest)`, `rechunk`; compaction under the maintenance
  lease with duplicates removed, on a pack-count threshold and at the
  sweep.
- The algorithm interface: `sha256` whole and streaming; `blake3` whole
  and streaming with the outboard written to the index, behind the
  optional extra, the slice walk left unimplemented and refused by
  name.
- The root manifest with `store_id`; a source whose manifest carries
  the store's own id is refused.

Refusals tested first: a landing whose bytes do not match `expected`;
an object present in two packs answered once; a policy naming an
unknown entry; `open` on a remote sha256 object too large to land;
`put` on a backend without conditional put for a head; an index lost
and rebuilt; a seal interrupted, leaving a `.part` the age rule
removes; a pack whose index is missing, rebuilt from the pack.

Acceptance:

- `uv run fm check` exits 0 on every runner.
- A test lands 10,000 200-byte objects and proves one pack, one index
  file and one SQLite file exist and every object reads back.
- A test proves `cp -r` of a store opens and answers every name after
  its index is deleted.

### Phase 4: refs as versions

Deliverables:

- `_refs.py`: a namespace's tree, the move version with the spec's
  headers, the head file per namespace under `refs/<namespace>`,
  compare-and-swap on the head through the backend's conditional put
  or local replace; single moves and groups as one version; the
  mutation classes as merge rules for two versions on one parent;
  the conflict entry and `resolve`; seal before ref, fsync on the move;
  history as the parent chain; `pins` and `pending` as namespaces of
  this shape.
- The sweep re-rooted: marks from every head, through versions,
  trees, recipes, attachments and receipts; the publish-in-flight case
  rooted by the pending namespace.
- `spec/conformance/` rewritten: `refs.json`, `groups.json`,
  `lifecycle.json`, `conflicts.json`, run by the harness.

Refusals tested first: a compare-and-swap loser; a write-once rewrite
naming a different digest; a monotone non-fast-forward; two versions on
one parent under volatile producing a conflict, not a loss; a commit
whose pack is unsealed refused by the invariant's test seam; a head
file torn by a simulated crash read as a digest failure, not as
tampering by guess.

Acceptance:

- `uv run fm check` exits 0.
- A two-process test moves refs in one namespace concurrently and
  proves every move landed or conflicted and none was lost.
- `uv run python -m pytest packages/strongroom/tests/test_conformance.py -q`
  runs the four case files green.

### Phase 5: sources and tiers over the interface

Deliverables:

- `_backends/http.py` (the listing file, pack indexes cached, ranges),
  folder sources with copy and reference policy over the local backend,
  origin hints; `fetch`, `fill`, `prefetch`, `shed`, `scrub` over the
  interface with packs as the unit of transfer; every hit verified on
  arrival; `offline`.
- Bucket support as a backend seam with the fake declaring its
  capabilities; no real bucket client in this plan.

Refusals tested first: a source serving wrong bytes; an unreachable
source; `offline` with the object only at an origin; a source whose
manifest is the store's own id; a listing that names a pack the tier
does not serve; a head move attempted on a backend without conditional
put.

Acceptance:

- `uv run fm check` exits 0.
- A test fills a folder from a store holding packs, opens the folder as
  a source, and proves every name answers and no pack was rewritten.
- A loopback `http.server` test serves the layout and a client fetches
  one pack whole and one object by range.

### Phase 6: the materialiser

Deliverables:

- The ladder unchanged; `path` on a packed object copies out to the
  index; profiles applied at publish and at view; the escape table in
  `_escape.py` used on every platform, the view record mapping both
  ways, the report of rewritten and over-long paths; the
  extended-length prefix on Windows; `collect` over a whole root with
  ignore rules and the stat cache in the index; `executable` as before.

Refusals tested first, one per rung as before, then: a name escaped on
Windows and collected back to its original; a case collision on a
case-insensitive filesystem parking nothing and escaping the second; a
component still over the limit after escaping, parked and named; a
path past 260 on Windows reported, not parked; a person's rename of an
escaped file collected as a rename.

Acceptance:

- `uv run fm check` exits 0 on every runner.
- A test views the design note's escape vectors on each platform and
  proves the file names match the vectors byte for byte.

### Phase 7: dictionaries, deltas and the policy surface

Deliverables:

- Compaction trains a dictionary per class the policy names and writes
  it as an object; small entries recompress against it. Deltas: for an
  object with a same-path predecessor in the previous version of a
  tree the compactor reaches, a frame against the base as prefix, kept
  when smaller, chain depth bounded by policy; the reference resolved
  before decode on read. `representation()` reports the dictionary or
  base.
- The policy surface complete: `Representation` values per call, per
  store and per namespace; `chunkers()`, `compressors()`,
  `algorithms()`, `ciphers()` listing; `rechunk`; the defaults from the
  design note.

Refusals tested first: a reference to an object outside the pack
refused at seal; a chain past the bound refused; a dictionary object
missing at read named, the entry unreadable and reported.

Acceptance:

- `uv run fm check` exits 0.
- A test compacts two versions of a text tree and proves the delta
  entries decode to the original bytes and the pack is smaller than the
  two whole trees.

### Phase 8: large directories

Deliverables:

- The prolly-tree node kind implemented: a directory above the size
  the spec fixes is split at content-defined boundaries over its sorted
  entries into a balanced Merkle tree of nodes; the same set gives one
  root whatever the order of insertion; lookups, walks, diffs and the
  sweep read it like a tree. `tree.md` gains the size rule as a format
  rule, with vectors: a directory at the threshold, one over it, and
  an insertion that moves a boundary.

Refusals tested first: a large directory encoded flat above the
threshold refused on decode; a node out of order refused.

Acceptance:

- `uv run fm check` exits 0.
- A test builds a directory of 100,000 entries, changes one, and
  proves fewer than 20 node objects differ.

### Phase 9: the measurement harness and the defaults

Deliverables:

- `packages/strongroom/scripts/measure.py`, not in the wheel: fetches
  or is pointed at each corpus of the design note's table, replays it
  into a store under each candidate policy, and emits one table. Rows:
  the registry corpus, replayed repositories with `audio2mesh_demo`
  by path, the public dataset families, the tool installs, the media
  and project-file edits, filer latency, rung timings.
- The results into the design note's measurements section with the
  date and the corpus versions, and the defaults in `policy.md` set
  from them.

Acceptance:

- `uv run python packages/strongroom/scripts/measure.py --corpus tools --out <path>`
  exits 0 and writes the table.
- The design note's table has a number in every row.

### Phase 10: the switch

Deliverables:

- `livery.strongroom.__init__` exports the new store; the old modules
  and their tests deleted; the contract test now pins imports of
  `livery.cbor` and the standard library only.
- `packages/toolroom-store/`, `packages/toolroom-bench/` and the
  workshop's tools module moved to the new API: namespaces declared
  with profiles, refs as versions, `collect` and `view` as before.
- `packages/strongroom/docs/` rewritten as the site: the standard, the
  API walk, the platforms, the policies.
- `workshop.toml` and the lock: `[[depends]]` from strongroom to cbor.
- The two live stores deleted and rebuilt: the tool cache by the next
  `fm` invocation, the tool record data by its replay.

Acceptance:

- `uv run fm check` exits 0 on every runner.
- `uv run fm docs.build` exits 0 with the cbor and strongroom pages.
- `uv run fm ci.e2e` exits 0 on the local loop.

### Phase 11: the freeze and the first releases

Deliverables:

- `spec/README.md` states the freeze: every vectored format is fixed
  from this release, a change is a migration, and the layout number is
  1. The design note's status line says the freeze happened.
- `fm workflow.release cbor` then `fm workflow.release strongroom`,
  the train's own acts, receipts `packages/cbor/v0.0.0` and
  `packages/strongroom/v0.1.0` (a break rides along before 1.0).

Acceptance:

- `git tag --list 'packages/cbor/v*'` and `'packages/strongroom/v*'`
  list the receipts and `git cat-file -t` prints `tag` for each.
- `uv pip download livery-cbor livery-strongroom --no-deps` succeeds
  from a clean directory.

## Temporary, replaced by

| Temporary piece | Replaced by |
| --- | --- |
| the old store modules, exported until phase 10 | deleted at the switch |
| the consumers on the old API | moved in phase 10 |
| FastCDC in pure Python | an accelerated chunker behind the same registry entry, when a corpus run measures it as the bottleneck |
| the blake3 slice walk refused by name | the walk, in whichever implementation gets the chunk-level binding first |
| `aes-256-gcm` reserved and unimplemented | the encrypted representation, when custody exists |
| the defaults in `policy.md` from the design note | the measured defaults of phase 9 |
| the bucket backend as a seam with the fake | a real client, with the service |
| retention as a future class | the class and the prune, when a consumer feels the growth |

## Decision record

- 2026-09-25, Willem: all twelve rulings closed in the design note;
  `livery-cbor`, Python only for now; profiles `portable` and
  `permissive`; large directories before the freeze; deltas in the
  first cut; the harness a script in the package, not in the wheel; no
  implementation until his go.
- 2026-09-25, this plan drafted.
- 2026-09-25, Willem: go for phase 1 alone, while a documentation
  modularisation runs in another session.
- 2026-09-25, phase 1 built (issue #690). Choices at the cut: the
  decoder keeps an explicit stack rather than recursing, so the
  nesting bound of 512 is the only limit a hostile input meets; the
  bound is stated in the spec as rule 9; a vector's `value` is present
  only where JSON carries the value exactly, and every case has a
  `diagnostic` string and round-trips its `hex`; the module is
  `livery.cbor._codec` with `encode`, `decode`, `CodecError`, `Value`
  and `MAX_DEPTH` re-exported. Found on the way and filed as
  livery#691: `fm check` from a shell that has not evaluated the
  entry contract fails with a misleading CommandNotFound for `ruff`,
  since the tools left the venv; the gate ran after
  `eval "$(fm --quiet env.emit posix)"`.

## Open

1. **The go for phase 2.** Phase 1's go was given 2026-09-25. Owner:
   Willem.
2. **The large-directory threshold**, fixed in phase 8 from the
   registry corpus's skew measured in phase 9; the two phases may swap
   if the number is needed first. Owner: the agent, at phase 8.
3. **Whether phase 10 splits**, the switch of three consumers in one
   change being the largest phase; split by consumer if it does not
   land in two days. Owner: the agent, at phase 10.
