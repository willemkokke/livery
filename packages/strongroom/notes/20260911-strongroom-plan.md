# Strongroom in a vacuum: the store standard and its core

Status: executing; Willem's go 2026-09-11. Phase 1 landed 2026-09-11
(issue #434, PR #436): the package, the spec's formats with their
vectors, and the Python formats. Phase 2 landed 2026-09-11 (issue
#440, PR #443): the local store, objects and refs. Phase 3 built
2026-09-11 (issue #444): sources and tiers, fill and offline; see the
decision record.
Scoped by Willem the same day: strongroom alone, no change to
toolroom, footman, or the workshop. This note lives in
`packages/strongroom/notes/`, beside the package it plans. This plan executes steps 0, 2
and 3 of the content-addressed store design note
(`livery-planning/docs/20260828-content-addressed-store.md`, the
"store note" below): the spec with its vectors, the core, and the
materialiser. The tool store rework over strongroom is a later plan
of toolroom's, and adds the first dependency edge then.

## Scope

In: `packages/strongroom/`, distribution `livery-strongroom`, import
`livery.strongroom`, with `packages/strongroom/spec/` beside its
source. The spec's formats and golden vectors; blobs, refs, sources
and tiers, landing and verification, the lifecycle, and the
materialiser with its whole strategy ladder. The behaviour cases of
the conformance suite, as data plus a harness. A first release through
the train, so the name and the train are proven before a consumer
exists.

Out: every consumer. No tool spec, no `tools/` namespace, no PATH
emission, no dataset, no call. No fabric host, so the verb tree
(`publish.begin`, `publish.commit`, `ref.set`, `erase`, `pin`,
`lease.read`) exists as plain typed functions called in-process and
nothing mounts them. No versions-over-datasets features beyond the
version format itself: incremental publish, diff and partial views
are step 4 of the store note. No shared tier, no S3, no service, no
encryption, no signature verification. No layer entry in
`workshop.toml`: strongroom mounts no verbs.

The store note's rule that two consumers exercise the core at birth
is not met here. The vectors and the conformance cases stand in for
them, and the toolroom rework is where a real consumer arrives.

## Ground-truth contracts (do not violate)

1. **Strongroom knows no tool, no call and no dataset.** It owns
   `pins/` and `pending/`, publishes namespace conventions in the
   spec's appendix, and interprets no other namespace. It ships no
   consumer. A test pins that no module under `livery.strongroom`
   imports `livery.toolroom`, `livery.footman`, `livery.forge` or
   `livery.workshop`.
2. **Stdlib-only at runtime.** `dependencies = []`, pinned by test.
   Extras add reach, never features, and this plan declares none.
3. **Nothing outside `packages/strongroom/` changes**, beyond the
   workspace wiring `fm new.package` performs: the root answers file,
   the project render, the lock. Toolroom's, footman's and the
   workshop's files are untouched, and no `[[depends]]` edge points at
   strongroom.
4. **The spec is the product; vectors before persistence.** Every
   format that reaches disk has a golden vector under
   `packages/strongroom/spec/` before the code that writes it. A
   change to a persisted format after its vector lands is a migration
   and stops for the human.
5. **sha256 only, one algorithm per address space.** Every stored name
   is `<algorithm>:<hex>` in the OCI grammar and carries its
   algorithm. The registry admits no non-cryptographic and no
   truncated digest. The root manifest names the algorithm and a store
   refuses to mix.
6. **Canonical JSON (RFC 8785) is the only hashed form** of a tree, a
   version, a ref record and a tombstone. `size` is an integer; a
   float is refused at publish.
7. **Objects are immutable, landing is idempotent, never compress at
   rest, representation is never in the name.** Whole-file is the only
   representation this plan ships.
8. **Refs are the only mutable thing.** Every update is
   compare-and-swap on the previous digest and writes a record beside
   the ref. Each namespace declares a mutation class (write-once,
   monotone, volatile) and strongroom enforces the class without
   reading the namespace's meaning.
9. **Reachability, not age.** Only orphaned scratch keeps a clock.
10. **Every source is verified and none is trusted.** `offline` means
    never an origin; a miss fails closed naming the digest and the
    origin that would have satisfied it.
11. **The materialiser removes only what it can prove it created.** A
    directory a person made is never touched. A view whose tier is
    gone is parked, never left pointing at empty disk.
12. **Fallbacks before happy paths.** Every rung's refusal and every
    fallback is forced by a test before the rung's success path is
    tested. A rung whose refusal has no test is untested code.
13. **The gate is green at every phase cut** on the workspace's
    runners. Windows is designed in and not gated (Willem,
    2026-09-11): the Windows-only paths are exercised through their
    seams with fakes, and the plan says so wherever it applies.
14. **Dependencies point downward.** `livery.strongroom` imports
    nothing first-party. Nothing in this plan imports it.

## Phases

Each phase lands alone, gate-green, with this note updated in the
same change. Exceptional cases are tested before happy paths in every
phase.

### Phase 1: the package is born, and the formats have vectors

The store note's step 0.

Deliverables:

- `fm new.package strongroom --kind=package-python`, then the seeded
  `hello` surface replaced. This note moves to
  `packages/strongroom/notes/20260911-strongroom-plan.md` in the
  same change, with `git mv`, and every later phase updates it
  there. `packages/strongroom/workshop.toml` with
  `type = "python"`, `name = "livery-strongroom"`, no `[[depends]]`,
  and the seeded coverage floor of 100. `pyproject.toml` with
  `dependencies = []`, no scripts, no entry points.
- `packages/strongroom/tests/test_package_contract.py`: dependencies
  empty; no `livery/__init__.py`; no first-party import anywhere under
  `livery.strongroom` (contracts 1, 2, 14); `__all__` pinned.
- `packages/strongroom/spec/`, language-neutral data:
  - `README.md`: what the spec covers, what the conformance suite
    covers, what is an implementation's business.
  - `digest.md` and `vectors/digest.json`: the grammar
    `algorithm ":" encoded`, the registry with `sha256` required and
    sole, refusals for unknown, truncated and non-cryptographic
    names.
  - `canonical.md` and `vectors/canonical.json`: RFC 8785 cases,
    including number serialisation, string escaping and key order.
  - `tree.md` and `vectors/tree.json`: entries
    `(name, kind, digest, size, executable)` for blob and tree,
    `(name, kind="symlink", target)` for links; sorted by name bytes;
    the portable-name rules (UTF-8 NFC, no case-insensitive
    duplicates, no Windows-reserved names, no trailing dot or space,
    a per-component and a total-path budget); symlink targets
    relative, forward-slashed, never absolute; each vector is the
    tree, its canonical bytes and its digest, and each refusal names
    its reason.
  - `version.md` and `vectors/version.json`:
    `(tree, parents, producer, created, message, receipt,
    attachments)`, the producer as a subject slot.
  - `records.md` and `vectors/records.json`: the ref record
    `(digest, previous, by, at, receipt, meta)` and the tombstone
    `(digest, at, authority, receipt, reason)`.
  - `layout.md`: `objects/<algo>/<xx>/<rest>`, `refs/<namespace>/…`
    with the record beside each ref, `pending/`, `pins/`, the root
    manifest naming the layout version and the algorithm, the `.part`
    scratch rule, and the HTTP mapping, which is the layout served as
    files.
  - `namespaces.md`: the two strongroom owns and the conventions it
    publishes (`urls/`, `tools/`, `datasets/`, `derivations/`,
    `queries/`, `contracts/`) with each one's mutation class.
- The Python for the formats: canonical encoding, digest parsing and
  computation, tree, version, ref record and tombstone as frozen
  dataclasses with `encode`, `decode` and validation. The vector
  files drive the tests: one parametrised test per vector file, so a
  vector added later is a test added.

Acceptance:

- `uv run fm check --affected` exits 0.
- `uv run fm typecomplete` exits 0 with `livery.strongroom` at 100%.
- `uv run pytest packages/strongroom -q` exits 0 and the vector tests
  are listed by parametrisation, one id per vector.
- `uv run python -c "import livery.strongroom"` exits 0 with no
  first-party module in `sys.modules` beyond itself, proven by the
  contract test.
- `git diff --stat main -- packages/toolroom packages/footman
  packages/workshop packages/forge` is empty.

### Phase 2: the local store, objects and refs

The store note's step 2, first half.

Deliverables:

- The root manifest: written by `Store.create(root)`, read by
  `Store.open(root)`, refused when the algorithm or layout version
  differs from what the implementation speaks.
- Landing: bytes stream through the digest into a `.part` scratch
  beside the destination and `os.replace` into the digest path. A
  mismatch removes the scratch and raises naming expected and actual.
  Landing an object that already exists with the right digest
  succeeds without writing. A replace that fails because a reader
  holds the destination open, the Windows case, is success when the
  destination verifies. The last rule is exercised through the
  replace seam with a fake, not on a Windows runner (contract 13).
- Landed and verified as two facts: a per-tier verified mark, size
  checked on every access, the full digest on fill, on demand, and
  in `Store.scrub`.
- `Store.path(digest)`: a read-only path into the local objects
  directory, the first materialiser call. Raises when the object is
  absent or tombstoned, naming which.
- Refs: `Store.ref(namespace, path)`, `Store.set_ref(..., previous=)`
  compare-and-swap under a per-ref `O_EXCL` lock with a staleness
  break, writing the record beside the ref through scratch and
  replace. Mutation classes declared per namespace at `open`:
  write-once (create if absent, a second write naming a different
  digest is refused), monotone (fast-forward: the new version's
  parents must include the current target), volatile (last writer
  wins, still under compare-and-swap). `pins/` and `pending/` are
  declared by strongroom; every other namespace is declared by the
  caller.
- An out-of-band edit of a ref file is visible: the record no longer
  matches, and reading the ref names the mismatch.

Refusals tested first: a digest mismatch on landing, a corrupt object
found by size on access and by digest on scrub, a compare-and-swap
loser, a write-once rewrite, a monotone non-fast-forward, a lock
broken only on provable staleness with the compare-and-swap still
deciding afterwards, a manifest of another algorithm, a namespace with
no declared class.

Acceptance:

- `uv run fm check --affected` exits 0.
- The lock-break rule has a conformance vector under
  `spec/conformance/` and a test that forces the break.
- Two processes landing the same digest concurrently (a subprocess
  test) both exit 0 and one object is on disk.

### Phase 3: sources, tiers, fill and offline

The store note's step 2, second half.

Deliverables:

- A store instance is the local objects directory plus an ordered
  list of sources: a local folder in the same layout; a read-only
  folder with a reference-only fill policy (no local copy is made,
  `path` answers into the folder); an HTTP base URL serving the
  layout, read with `urllib` from the stdlib; an origin hint, a URL
  with an expected digest.
- `Store.fetch(digest)` consults sources in order, verifies every hit
  on arrival, lands under the source's fill policy, skips an
  unreachable source after a short connect timeout separate from the
  transfer timeout, and reports each skip through a progress seam
  that never prints.
- `offline=True`: never an origin. A miss after every source names
  the digest and the origin hint that would have satisfied it.
- `Store.fill(target, digests)`: land a set of objects into a folder
  tier, so a mirror is built by the store rather than by hand.
- The HTTP tier is tested against `http.server` from the stdlib on a
  loopback port; the origin hint against a fake opener through the
  seam.

Refusals tested first: a mirror serving wrong bytes for a digest
(refused, the next source consulted, the corrupt hit never lands), an
unreachable mirror (skipped, reported, the origin reached), `offline`
with the object only at the origin (fails closed, names both), a
source whose layout version differs.

Acceptance:

- `uv run fm check --affected` exits 0.
- A test proves the offline failure message contains the digest and
  the origin URL verbatim.
- A test proves `fill` into an empty folder yields a folder that
  `Store.open` accepts as a source and that serves every filled
  digest.

### Phase 4: lifecycle: pending publish, reachability, tombstones

The rest of the store note's step 2.

Deliverables:

- The verb functions, plain and in-process: `publish_begin(tree)`
  requires the tree object present and writes `refs/pending/<id>`
  with a lease record; `publish_commit(pending, namespace, path)`
  moves the real ref under its mutation class and drops the pending
  ref; `ref_set`, `erase`, `pin`, `lease_read`. Each returns a typed
  result; none prints.
- Format-aware walkers, one per structured format: a tree walker
  yields its entries, a version walker its tree and parents.
- `Store.sweep()`: mark from the roots (every ref, pins, pending refs,
  live views) through the walkers; re-scan pending refs immediately
  before deleting and treat any created during the sweep as a root;
  delete the unreached; orphaned `.part` scratch older than a
  configured age is the only age rule. The sweep runs under an
  exclusive maintenance lease; dropping a pin and pruning under a
  retention class are sweep-time operations under the same lease.
- Three object states, present, absent, erased. `erase(digest)`
  writes the tombstone under the object's path and then deletes the
  bytes. A tree still names the digest; `path` on an erased object
  raises naming the tombstone's reason.
- A crashed publish leaves its pending ref, which `Store.retire(id)`
  removes deliberately; nothing removes it on a clock.

Refusals tested first: `publish_begin` on an absent tree, a commit
whose pending ref is gone, a sweep racing a publish that began after
the roots were snapshotted (the object survives), an erase of an
object a live view holds (the tombstone lands, the bytes go on the
next sweep), a pending ref of another store.

Acceptance:

- `uv run fm check --affected` exits 0.
- Conformance cases under `spec/conformance/` for the publish
  sequence, the sweep's re-scan, the mutation classes and the three
  object states, each run by the harness of phase 6.

### Phase 5: the materialiser, the whole ladder

The store note's step 3, on the local store only. Willem's ruling
2026-09-11: the whole rung, implemented and tested, not copy alone.

Deliverables:

- `Store.view(tree, at, *, writable=False)` fills a directory from a
  tree, `Store.collect(at, declared)` reads declared outputs back into
  a tree, and `Store.path(blob)` stays the single-object form. The
  caller states whether it will write; the materialiser picks the
  cheapest safe strategy per entry and records which one it used.
- The strategy ladder, cheapest first, each with its eligibility rule
  and a probe that decides it once per view:
  1. reference: a path into a read-only tier. Eligible only when the
     tier is read-only at the server, or the store directory is
     owner-only and the view is not writable.
  2. clone: copy-on-write. APFS `clonefile` through `ctypes`; Linux
     `FICLONE` through `fcntl.ioctl`; ReFS block cloning through
     `DeviceIoControl`. Always safe. The probe clones one file into
     the view's directory and falls to the next rung on any error.
  3. hardlink: into a directory the materialiser owns and marks
     read-only; refused into a writable view unless the caller passes
     the explicit allowance.
  4. link: a relative symlink; a junction for a directory on
     Windows; per-file fallback to the next rung. Presentation only;
     the target must be a read-only tier.
  5. copy: always available.
- Symlink entries: a real symlink where the platform allows; where it
  does not, the within-view target's content as a marked copy; a
  target that escapes the view is a parked refusal.
- The view record: a `views/<id>` entry in the local index listing
  every path the view created and the rung used, a GC root while the
  view lives.
- The removal doctrine: `Store.drop_view(id)` removes only what the
  record lists; a path in the view the record does not list is left
  and named in a warning; a view whose tier is gone is parked; a view
  whose directory is gone is retired by the next sweep.
- Portable names enforced at publish, so a view never meets a name
  the platform cannot represent.

Platform coverage, stated so it is decided: the gate runs
ubuntu-latest and macos-latest. The clone rung's success path runs on
macOS (APFS). On the Linux runner the filesystem does not support
`FICLONE`, so the clone refusal and the fall to hardlink run there,
and the clone success path is proven on Linux only where a test
detects a supporting filesystem and otherwise records the rung as
untested on that leg by name, never as a silent skip. Junctions and
ReFS cloning are exercised through their seams with fakes (contract
13). The e2e proof against a real NAS as a read-only tier is hse's,
not this plan's.

Refusals tested first, one per rung: reference into a writable view
refused; clone on an unsupporting filesystem falls through; hardlink
into a writable view refused without the allowance; symlink refused
where the platform refuses it and the per-file fallback taken; copy
into a directory that is not empty refused. Then the doctrine: a
hand-made file inside a view survives `drop_view` and is named; a
view over a vanished tier is parked; an escaping symlink target is
parked. Then the success path of each rung.

Acceptance:

- `uv run fm check --affected` exits 0 on both runners.
- A test proves that for one tree, `view` then `collect` round-trips
  to the same tree digest on every rung the leg can reach.
- A test proves the view record lists every created path and
  `drop_view` leaves the hand-made file.

### Phase 6: the conformance harness, the docs, the first release

Deliverables:

- `livery.strongroom.conformance`: a harness that takes an
  implementation through a small protocol (create, open, land, ref,
  publish, sweep, view) and runs every case under `spec/conformance/`
  against it. The Python implementation is its first subject; the
  suite is data so a second implementation runs the same cases.
- `packages/strongroom/docs/`: the standard rendered as the package's
  site, with the spec files mounted, the namespace conventions, the
  platform checklist for Windows, and the stdlib-only statement.
  Written as the site, never as scratch.
- `CHANGELOG.md` with the first entry, then the release:
  `fm workflow.release` on the strongroom branch, the receipt tag
  `packages/strongroom/v0.1.0`, `livery-strongroom` on PyPI.

Acceptance:

- `uv run fm check` exits 0.
- `uv run fm docs.build` exits 0 with the strongroom pages present.
- `git tag --list 'packages/strongroom/v*'` lists `v0.1.0` and
  `git cat-file -t` on it prints `tag`.
- `uv pip download livery-strongroom==0.1.0 --no-deps` succeeds from
  a clean directory.

## Temporary, replaced by

| Temporary piece | Replaced by |
| --- | --- |
| the verb functions called in-process, no host | the fabric host mounts them, grant-gated and receipted (the store note's step 6) |
| no consumer; vectors and conformance cases stand in | toolroom's tool store over strongroom, its own plan, adding the first `[[depends]]` edge |
| refs read from a non-authoritative tier are hints confirmed nowhere | the `[verify]` extra checking the signed receipt beside a ref |
| the local index is files only | SQLite per local store once GC needs a reverse index |
| the spec and vectors live in the repository only | package data in the wheel, if open item 1 rules so |
| Windows paths proven through seams with fakes | the suite on windows-latest when livery#357 brings the runner back |

## Decision record

- 2026-08-28, Willem (store note): sha256 only, registry open;
  mutability only through calls, adopted; the tool store is
  toolroom's over strongroom; strongroom knows no tool, call or
  dataset; the names `livery.strongroom` and `livery-strongroom`.
- 2026-08-31, Willem (store note): canonical JSON is the tree's only
  hashed form; a binary tree is a representation, never a name.
- 2026-09-01, Willem (store note): mutation classes per namespace;
  the sweep's rules over append-only namespaces.
- 2026-09-03, Willem (store note): the symlink entry kind in the v1
  tree format.
- 2026-09-05, Willem (migration note): strongroom is born
  `livery.strongroom`; the arrows read `livery.toolroom ->
  livery.strongroom` and `livery.footman -> livery.fabric ->
  livery.strongroom`.
- 2026-09-11, Willem: strongroom in a vacuum. No change to toolroom
  yet; the tool store rework is a later plan.
- 2026-09-11, Willem: the spec lives at `packages/strongroom/spec/`.
- 2026-09-11, Willem: Windows is designed in and not gated; the plan
  names the deferral where it applies.
- 2026-09-11, phase 1 built (issue #434). Choices made at the cut,
  each the agent's and open to reversal while nothing persists: a
  tree's JSON is `{"entries": [...]}` with no format tag, because
  the referrer names the format; a subject is `{"kind", "value"}`
  with kinds person, call, receipt and code, and a receipt or code
  value must parse as a digest; an instant is RFC 3339 in UTC with
  the `Z` designator and seconds required, so one instant has one
  spelling; the canonical JSON profile refuses every float and any
  integer past 2^53; the version's producer, the record's writer
  and the tombstone's authority share the subject shape. The
  template's pyproject omits `dependencies`, so the package spells
  `dependencies = []` for the contract test to pin. The total-path
  budget is stated in the spec and enforced in phase 2, where a
  whole tree is first published.
- 2026-09-11, phase 2 built (issue #440). Choices at the cut: the
  verified mark lives in the local index as a size per object, so an
  access checks size and an object landed by copy is hashed in full
  once; a scratch name carries the writer's process id and a counter,
  because two processes landing the same digest through one scratch
  name interleave their writes (found by the concurrent-landing test);
  a monotone ref's creation is unchecked and only a move must
  fast-forward; the write-once class treats a repeat of the same
  digest as done and returns the existing record; a ref whose record
  disagrees is reported as tampered whether the cause was an edit or
  an update that died between its two replaces; the lock's staleness
  proof is a dead holder, an age past the bound, or a torn lock file,
  and no liveness probe is sent on Windows because `os.kill` there
  terminates. Behaviour cases live under `spec/conformance/refs.json`,
  run by a harness in the tests until phase 6 moves it into the
  package.
- 2026-09-11, phase 3 built (issue #444). Choices at the cut: sources
  are declared at open, not per fetch, so a store instance is one
  read path; a folder source's manifest is checked at open and an
  HTTP source's on first use, and a mismatch is an error rather than
  a skip, because a misconfigured tier is not a transient; a
  reference-policy hit is hashed in full once and trusted by size
  afterwards through a mark in the local index, the same two facts
  as a landed object; network reads go through `http.client` so the
  connect timeout and the transfer timeout are two settings, with
  one seam (`fetch_url`) that tests fake; an HTTP 404 is the source
  lacking the object, reported like an unreachable source; `fill`
  reuses a folder that is already a store of the same algorithm and
  refuses one of another.
- 2026-09-11, Willem: the materialiser's whole strategy ladder is
  implemented and tested in this plan, not the copy rung alone. The
  agent's reading of "implement and test the whole rung"; correct it
  here if the intent was narrower.

## Open

1. **Whether the wheel ships the spec and vectors.** The spec lives at
   `packages/strongroom/spec/`, outside the import package. A second
   implementation reads them from the repository, or the build ships
   them as package data. Decide by phase 6. Owner: Willem.
2. **The Linux clone rung in the gate.** ubuntu-latest's filesystem
   does not support `FICLONE`. The success path is proven on macOS;
   whether a Linux leg on a btrfs or XFS loop mount is worth its cost
   is decided when phase 5 lands. Owner: the agent, at phase 5.
3. **`livery-strongroom` on PyPI.** The store note checked
   `strongroom` free on 2026-08-28; the distribution name is checked
   at the first release, and a taken name is recorded as debt the
   same day per the workshop's release rule. Owner: the agent, at
   phase 6.
4. **The toolroom note amendment.** `packages/toolroom/notes/
   20260827-pinned-tool-store.md` still puts the bytes engine in
   `toolroom.store`. The store note asks for the amendment; it waits
   for the toolroom rework plan, out of this plan's scope by ruling.
   Owner: Willem, when that plan is written.
5. **The two measurements** (dedup whole-file versus chunked, hashing
   from disk) belong to hse's tenant and are not this plan's.
6. **The store note is uncommitted.** In `livery-planning`, the store
   note and seven other notes since 2026-08-21 are untracked, and
   `tokens.txt` sits beside them with no `.gitignore`. This plan cites
   the note by path; committing it, with the tokens file ignored, is
   Willem's. Owner: Willem.
