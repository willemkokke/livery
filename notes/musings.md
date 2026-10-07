# Musings

Raw material, dated, never a plan: a musing becomes a plan entry
only when it is ruled.

## 2026-09-03: the name family

The definition Willem is circling: livery contains a group of loosely
related libraries and utilities (livery companies) in a monorepo, all
forged in the workshop according to uniform rules and opinions that
produce good software. That makes them all wear the same livery.

The map of associations, as explored:

- **footman**: a footman is a liveried servant. The taskrunner wears
  the livery. The strongest pun in the family and it was there before
  the frame was.
- **Etymology of livery**: Old French *livrée*, "that which is
  delivered", the allowance of clothes and provisions handed to
  servants. Livery and delivery share a root. The monorepo whose point
  is a release train is named "the thing delivered".
- **fabric**: lands four times. The cloth a livery is cut from (and
  fabric was originally going to be called livery, so the rename made
  the component the material the whole is tailored from). The fabric
  of a hall: the structure a fabric fund maintains. Latin *fabrica*,
  the workshop of a *faber* (smith): fabric, forge, and workshop are
  one word family. And switching/service fabric in the trade register.
- **strongroom**: the room in a hall where the plate is kept.
  Immutable inventoried items, verified on deposit and re-assayed
  later, admitted by grant, removals recorded (tombstones as inventory
  lines). Fits the CAS exactly: everything else makes or moves,
  strongroom alone stores.
- **hallmark**: the word comes from the Goldsmiths' Company assaying
  plate at Goldsmiths' Hall. Reserved: if anything ever needs the
  name, it belongs to strongroom's verification (assay on landing,
  scrub as periodic re-assay), not to the gate.
- **Guild ranks**: apprentice, freeman, then liveryman; the admission
  ceremony is being *clothed* in the livery. The `archive/setup` tag
  "cut at graduation" already speaks this vocabulary. A repo adopted
  into the workshop's rules is a repo being clothed.
- **charter**: livery companies exist by royal charter; `livery.toml`
  is the workspace's charter.
- **The stable sense**: a horse "kept at livery" is boarded and cared
  for by the stable on the owner's behalf. Describes the workshop
  maintaining many repos.
- **The modern sense**: vehicle and aircraft livery, one paint scheme
  across a fleet. Readers who know nothing of guilds land here and the
  metaphor still works.
- **toolroom**: machine-shop vocabulary, one register younger than the
  guild layer. The room where the tools that make the tools are made
  and issued, to tighter tolerance than the production floor. A tool
  cache handing one pinned toolset to every agent is a tool crib.
- **Two strata**: household/guild (livery, footman, strongroom,
  charter, forge-as-smithy) and shop floor (workshop, toolroom), with
  workshop as the bridge and fabric's etymology tying both to forge.
- **Free fact**: the Worshipful Company of Weavers, who make fabric,
  is the oldest London livery company, chartered 1155.
- **Structural point behind the fabric rename**: the ecosystem's name
  should not also be a component's name, or every sentence about the
  part is ambiguous with the whole.
- **The rule that contains all of this**: the names are identifiers.
  Nothing in the system, and no published sentence, depends on the
  metaphor. The joke works harder when nothing explains it.

## 2026-09-03: genesis of the family

The order the pieces emerged, as Willem tells it: footman came first.
toolroom split off from it. Taking footman to its absolute basic
principle produced fabric. fabric necessitated strongroom, which then
turned out to serve footman and toolroom as caches and datastore too.
Thinking about that put the focus on workshop first, to make sure all
parts were self-consistent, and workshop wanted forge, out of a dislike
of being tied to a particular provider.

Read backwards it is nearly the dependency order. The genesis is
reduction (runner to principle to store) and the architecture is the
rebuild on top of what the reduction found.

## 2026-09-03: distribution stance

- Nobody clones livery to work on it. Consumers install the workshop
  globally: today `uv tool install footman --with livery-workshop`;
  once footman moves into the monorepo, a workshop extra so it is
  `footman[workshop]`.
- Workshop gets launcher scripts like hse's, and loses the `uv run`
  prefix once installed globally.
- The repo is public but not for use: "I don't want anyone to use it,
  but I don't want to develop in a private repo either." Development in
  the open as a value, with a do-not-use disclaimer doing the gating.

## 2026-09-03: toolroom installs the forge itself

Once toolroom is part of the repo, it can also install gitea and
gitea-runner automatically, without docker: they are just pinned
binaries, which is exactly what toolroom stores. That makes a 100%
local development environment possible, running in a sandbox. The
whole loop (repo, issues, pull requests, CI runs) with no network, no
docker daemon, no external provider: the forge stops being
infrastructure you stand up and becomes a tool you provision.

And then: why not 100% on a usb stick? "I don't see why not." The
stick would carry the mirror (toolroom's fetch already fills a folder
that is a mirror by construction, every host's binaries included), the
strongroom tiers, and the forge's data directory: a whole development
world that plugs into any machine. Two configurations fall out: exFAT
for a stick that travels between platforms (dumb tier, copy rung), or
the platform's own filesystem (APFS, btrfs) for a single-platform
stick that carries store and worktrees together, where CoW clones make
it a complete world at native semantics. And if the FUSE strongroom
filesystem ever happens, the split dissolves, and Willem means the
full version: store and worktrees both on the stick, the mount
supplying the semantics the stick's filesystem lacks. Worktrees are
the mount's CoW-with-explicit-commit mode (overlay upper on the stick,
collect on commit), so even an exFAT stick that travels between
platforms is a complete world, and committing a worktree publishes a
version into the store riding beside it: carrying your work is
carrying its history. The stick may be the strongest concrete case for
the mount yet, since it is the copy rung at its most dominant and has
no server to improve.

Then the same stick as backup: a very efficient backup/sync protocol
on top of strongroom, carried in a pocket. Plug it in and it fills
itself with the encrypted history of your entire environment. Sync is
set difference of digests, incremental by construction, safe to
interrupt (objects are present or absent, there is no backup state to
corrupt), and the encrypted-at-rest representation means the stick
holds ciphertext under plaintext names, so dedup and verification
still work while a lost stick leaks only sizes and shape. Working
world and backup stop being two copies: one object set, two kinds of
refs pointing into it.

On how the mount would be built, per platform: one daemon (resolve,
fetch, verify, view records) with a thin presentation adapter per OS.
Linux: FUSE with passthrough for lazy fetch, and daemonless composefs
(EROFS + overlayfs + fs-verity) for static read-only views. macOS:
FSKit, never macFUSE (kext); NFSv3 loopback as the proven fallback
(EdenFS's move). Windows: ProjFS for the lazy worktree (hydrated files
become real NTFS files, so tooling behaves), WinFsp for a true mount,
CfAPI placeholders for the backup/sync surface. The read-only Linux
rung is nearly free and proves the format first.

How full-featured can the filesystem be: everything whose truth is
content maps fully and gains properties (snapshots, verify-on-read,
dedup, lazy fetch, the CoW worktree with explicit commit). Everything
whose truth is live state is buffered in the overlay or refused: no
mtime (needs a synthesis policy; make cares), no uid/gid/xattrs, no
hardlinks, no live in-place write or locks, no distributed read-write.
One decision waiting: the tree has no symlink kind, and real worktrees
contain symlinks, so the stick musing eventually forces a symlink
entry kind or a documented refusal. (Actioned the same day: the CAS
note's eleventh pass rules a symlink entry kind into the v1 tree
format.)

Most of those weaknesses engineer around without dilution, under one
rule: state lives beside the store (per-view metadata tables for
mtime/ownership/xattrs, git-index style; the overlay upper is a real
filesystem so the working set gets native write semantics; synthesized
inodes; prefetch from trees), never inside the name. A background
collect into a volatile wip ref gives near-continuous capture of live
worktrees without cheapening deliberate versions. The two refusals
that must stay: nothing non-content ever enters the hash, and no
distributed live read-write, since engineering around one-authority-
per-namespace is building the distributed filesystem the design
refused.

Is strongroom defined enough for a distributed filesystem: as a
distributed store with filesystem presentations (mount anywhere,
overlay locally, share by publish), yes in design, no in bytes: the
spec with golden vectors, verb schemas, and the conformance suite are
the missing artifact, and a watch/notify verb is the one additive gap
if remote ref changes should feel live. As a textbook DFS with shared
live writes, no by ruling: one authority per namespace is the asset,
not the limitation.

## 2026-09-04: distributed locking for AI datasets

Distributed file locking on top of strongroom would still be very
useful for large AI datasets. The design half-contains it: leases
already exist (publish.begin mints one, lease.read is a verb), the
namespace authority is the lock server, so the hard part of a DLM
(agreeing who decides) is a design axiom rather than a protocol.
Hierarchical namespaces scope a lock per subtree. Three uses,
strongest first: long read leases pinning a version for the length of
a training run; work claims on derivation keys so two GPUs never
compute the same expensive transform; advisory writer coordination on
a branch (correctness already comes from fast-forward compare-and-swap
and the loser was already cheap). The guarding line: the lock advises,
the ref decides. The moment a lock is load-bearing for correctness it
has become POSIX byte-range locking and the refused DFS returns.

## 2026-09-04: customising the docs render, after 17

Discussed the day the docs toolchain shipped, parked until phase 17
settles the layer-registry question.

- **Hierarchical properties**: knobs like whether the API reference
  includes private members, read package over workspace over
  built-in: a `[docs]` key in the workspace's `workshop.toml` sets
  the default, the same key in a package's own `workshop.toml`
  overrides it there. The two-level contract read already exists for
  layering and floors; the emitter just joins it.
- **Layered styling**: layers ship `content/docs/` beside their
  fragments. `theme.toml` fragments merge key-by-key in layer order
  into the rendered config; `assets/` composes same-name-topmost-
  wins with `extra_css` listed in layer order, so the CSS cascade
  agrees with layer precedence by construction; `overrides/` the
  same for theme templates, wholesale replacement carrying the
  overlay's declared-reason discipline. The workspace's own
  committed tree composes last. A brand restyle is one layer
  release through the gradient.
- The line neither crosses: replacing the generators themselves (a
  different release view, a different API page shape) wants 17's
  registry opened to layers, not a docs-only extension mechanism.

## 2026-09-09: the nanobind kind as its own layer

Willem, on the wheel build test costing every pull request 77 s:
could the nanobind kind and its build proof live in a separate layer
on PyPI, not for now. The registry already takes a kind from a layer
(`register_kind`), so `livery-workshop-nanobind` could ship the
backend, its seed templates, and the build proof in its own suite.
The catch is where it lives: inside the monorepo it would depend on
workshop, and the affected rule runs dependents, so a workshop change
would still run its build; in its own repository the build runs only
on that package's changes, and the loop keeps proving the kind end to
end through `loop-native`, from the layer's released wheel rather
than a dev wheel. Meanwhile the build runs at the nightly point only
(ruled the same evening).

## 2026-09-16: the runner image from the tool record

Willem, on the dev rig's one runner image serving both Gitea's
act_runner and GitLab's shell executor: if the tool record carried
each tool's installation package, the image could be derived to hold
only what the workspaces require. The image's list has two halves.
Runner plumbing (node for `actions/checkout`, bash for run steps, curl
for the uv installer, `gitlab-runner` for the GitLab service) belongs
to the rig and stays fixed. Workspace tools (git, docker, cmake, the
C++ toolchain) are what the kinds and seams declare today, so the
image could be the plumbing plus the union of the packages each
declared tool names, rendered from the record and tagged by the
record's hash so a change rebuilds it. The record would carry a
package name per platform per tool: apk for the image, and apt, brew
and winget if the same fact is to feed `fm doctor` and `fm sync` on a
developer's machine and top up a hosted runner missing a tool, which
is the larger win; the image alone is eight packages that move with
the kinds. Two things to settle first: the rig serves every workspace
on the machine, so the list is the union over the workspaces it is up
for or the image goes per workspace; and apk installs the Alpine
release's version where the record pins one, so either the image
installs the pinned build or the record says the image's version is
the distribution's. Not now; raw material for the tool record plan's
open questions.

## 2026-09-28: a native strongroom, and only the tests the change needs

Willem, on two pieces that stack. First, an in-memory, file-backed
implementation of strongroom, memory-mapped, written in native code,
most likely Rust, reached from Python through an extension. Second, on
top of it, a Rust library extracted from pants2 that gives most of what
pants2 gives: running exactly the tests a change requires and no
others. Cross platform and cross forge, and robust enough to leave on
in CI all the time rather than as an opt-in experiment.

What makes the second part attractive here is that the selection today
is package-level: the gate runs the packages a change touched plus
everyone declaring an edge on them, so a change to one package runs
whole suites that share nothing with it, and a package whose tests
alone use a sibling is not selected at all. pants2 avoids both by
making the file the unit of the graph and inferring the edges from the
sources. The pieces it does that with are a per-file parse, a module
map built from source roots, longest-match resolution with ambiguity
refused, ancestor `conftest.py` counted as a dependency of the tests
below it, and memoization keyed by content digests.

Three facts that bear on it, none of them a decision:

- The input digests are free. Git already names every file's bytes,
  and the gate record already keys on tree ids, so a memo table stores
  derived values and never the sources.
- The keys are not one kind. Parsing a file is a function of that
  file's bytes; resolving an import depends on the whole module map;
  selecting depends on the diff and the resolved graph. Three layers,
  three keys, which is why pants2 has a rule graph rather than a
  cache.
- The measured prize is small today. The whole workspace parses in
  about a second inside one test. A file-level graph makes that more
  and the suites it would skip are minutes, so the case rests on the
  skipping, not on the parsing.

A Rust core with a Python extension is a kind this workspace does not
have: the nanobind kind is C++ over conan. `livery-cbor` wants the
same shape for its own native implementation, so the two would share
whatever kind answers it.

## 2026-09-28: the base and the house, and what has to compose

Willem: workshop's defaults and livery's house convention need
rigidly separating. The house should be a thin layer on top of a
simpler base, and that layer is where anything worth customising
happens, so nobody has to fork the templates. Four type checkers is
the house's extremity, not a default.

### What the layer system does today

A layer is an ordinary distribution named in the root contract's
`[workspace] layers`, in precedence order, workshop first and the
instance implicitly last. Discovery is that list alone. It carries
four things: verbs through its footman plugin, a template overlay
under `<module>/templates/` with an `overlay.toml`, content under
`<module>/content/`, and the kinds it registers at mount.

Content is pushed by `fm sync` in layer order: fragments into
`.workshop/fragments/`, skills and hooks into `.claude/` through the
materialiser, and the managed `CLAUDE.md` stub whose last import is
the repository's own `CLAUDE.project.md`. Templates are composed at
render, bottom to top, into one source tree, from each layer's own
tree in the installed wheel, or the member tree when the layer is
self-hosting.

An overlay may add a file, replace a base file wholesale with a
declared reason, or contribute defaulted questions. It may not edit a
base file, and a declared replace ends inheritance for that file.

### The findings behind the discomfort

- The base layer ships house convention today, by its own
  definition. `CLAUDE.workshop.md` says the base fragment carries
  "only the rules the workshop itself enforces", and beside it sit
  `interaction-voice.md` and `documentation-standards.md`, both of
  which open by saying they are imported from hse's guidance.
- Wholesale replacement is the only escape hatch an overlay has, so
  every customisation of an existing file is a fork in all but name:
  base improvements stop arriving and nothing says so.
- Python check configuration is root-only and managed. Every
  `[tool.*]` table lives in the workspace's root `pyproject.toml`, so
  one configuration serves every python package and a kind cannot
  differ.
- Native check configuration is the mirror image: `.clang-tidy` is
  per package and declares itself a seed the template never rewrites,
  so a kind differs freely and an improvement never arrives.
- `requirements()` gathers tools from three sites, the kinds present,
  each package's contract and the root's. A layer is not one of them,
  so a layer's verb needing a tool has nowhere to say so.
- The managed-union that kinds use is a union of which files the
  drift gate judges, not of their contents. Composing contents is
  what the gate plan's phase 4 proposes to build.

### What was ruled while talking

- Templates aim to be generic enough that customisation is a layer's
  job and forking is never the answer.
- The small default set is the base's; four type checkers are the
  house layer's choice.
- Enabling a check the workshop provides generates its
  configuration; disabling removes it, and only when the file that
  exists is the one we generated.
- Tools should be declarable in a layer.
- A check's configuration varies by kind, clang-tidy under unreal
  against conan-cpp against nanobind, so it resolves down the kind
  chain rather than belonging to the check alone.
- Where a format composes itself, use that rather than inventing a
  mechanism: git reads a `.gitignore` per directory, clang-tidy
  searches up the tree and can inherit its parent, CMakePresets has
  `include`, the editor's settings already split into managed and
  local.
- Where a format forces one file, a managed file may carry a region
  the repository owns, preserved across renders. The root
  `.gitignore` is the case that needs it, since its own rules cannot
  be split into parts.

### Open

Whether a check's configuration lives as fragments inside a rendered
file or as a file per check. Both work; the file per check makes
withdrawal a deletion rather than a diff, and the withdrawal
semantics already exist in the materialiser, which keeps a local
override and names it. ruff, mypy, pyright, pytest and coverage all
support a standalone file; ty and pyrefly want checking.

Whether the region a repository owns is written by the instance or
contributed by a layer. Both are wanted and they are not the same
feature: a layer's section composes at render, an instance's edit is
state that must survive one.

A test for whether the separation is real: a plain workshop project,
with the house layer absent, gates green with the small set and
keeps its voice rules nowhere.

## 2026-10-03: an in-memory strongroom, and the core it implies

Willem, during the extensions refactor, wanting something to think
about: "lets assume the strongroom redesign plan holds up. I feel
there is room for an in memory database variant, optionally
filesystem backed. It would have to be super quick, with a great
multithreading story." It picks up the 2026-09-28 musing (a native
strongroom under a pants2-style test selector) and went much further.
Nothing below is ruled; the one lean is marked as a lean.

### Why it is the same store

The redesign's concurrency rules were written for processes and
remote tiers, and they are also the rules of lock-free memory.
Immutable objects need no read lock, and two threads landing the same
object race harmlessly. One writer per pack becomes one per thread.
The one head per namespace is an atomic pointer, and a loser applies
the merge rule. Seal before ref becomes a release store before the
compare-and-swap. A write rooted before its bytes land lets the sweep
run beside writers. A snapshot is a version digest. So the variant is
a tier under contract 7, not a fork of the spec. The file backing is
also the multi-process answer: footman's threads conduct and its
processes work, and a mapped file is how pytest, a compiler or a
DataLoader worker reads the same store without a daemon.

Measured on the Mac, Python 3.12, 10,000 random reads of 200-byte
objects, warm, best of five: a file per object 92 µs (the on-disk
review measured 650 to 970 µs per open under Defender); a pack with a
SQLite index and `pread`, phase 3's shape, 5.2 µs; a mapped pack with
the index in memory, 0.08 µs.

### What it opens, strongest fit first

1. The mount's daemon, and the stick: lookups from many kernel
   threads at stat latency. The backing file is what the stick
   carries; what remains is an adapter per OS.
2. Unreal's derived data on the same store. `FIoHash` is BLAKE3-160,
   the first 20 bytes of BLAKE3-256, and a DDC value is named by the
   IoHash of its raw bytes, so in a blake3 space Unreal's name is a
   prefix of ours: an argument for blake3 that ruling 1's record does
   not list. The DDC is an ordered hierarchy of stores (memory, file
   system, pak, Zen, HTTP, S3), its build definition is the fabric's
   call, its memory tier is two `TMap`s behind one `FRWLock`, and
   Epic's own answer to the workload is a native daemon, `zenserver`.
3. Training data from a pinned version. Entry counts make "the i-th
   sample" a walk and a seeded shuffle needs no file list; the
   receipt names version, seed and sampler, so the data order is
   attested though the weights never replay. A pin is the 2026-09-04
   read lease, in memory. Contamination as a digest intersection runs
   at memory speed.
4. The fabric's evaluator, per file. Livery alone does not need it
   (215 source and 235 test files, about 5,400 keys, about 28 ms at
   phase 3's cost); the work monorepo with conan C++ and Unreal
   plugins does. Pants keeps its local store in LMDB split 16 ways;
   Bazel and Buck2 rebuild their graph after a restart; a mapped file
   keeps it across `fm` runs and shares it between worktrees and
   agent sessions.
5. A second implementation before the freeze: no layout and no
   materialiser, only formats, refs and the sweep, which is what the
   vectors test, and fast enough to drive the merge rules with
   millions of concurrent moves.
6. No backing file: the same core in a browser through WebAssembly,
   reading packs from a static tier.

Two forks decide which are cheap. A library over a mapped file
(LMDB's shape) or a resident daemon (Zen's shape), one core under
both. And the GIL: a small read costs less than a GIL handoff, so the
threading pays for native callers, on 3.14t, and in batch calls, which
puts `get_many` and `put_many` first in the Python API.

### Small and fast

Measured on the workspace's `site-packages` (27,679 files, 168 MB,
Python 3.14.6, zstd level 3, a dictionary trained on half the small
objects and measured on the other half): 47% of distinct objects at
or below 512 bytes, 86% at or below 4 KiB. Ratio 3.22 one frame per
object, 3.76 with a 110 KiB dictionary, 3.88 as one frame with no
random access; below 512 bytes, 1.44 without the dictionary and 2.35
with it. One decompression, 3.6 µs and 3.7 µs. Canonical trees
through the codec: 55.3 bytes per entry, 58% of it digests. Names:
0.44 MB plain, 0.34 MB front-coded.

What followed:
- Do not ask one representation to be both. Hot objects plain and
  decoded; warm ones compressed, one frame each with a trained
  dictionary (97% of the one-frame ratio, still random access); cold
  ones mapped and left to the page cache. Large objects are never
  compressed in memory. Evict with CLOCK or S3-FIFO, so a hot read
  writes no shared state.
- Digests do not compress, so intern them: a dense 4-byte id per
  digest, ids in trees, refcounts and bitmaps, about 21 bytes per
  decoded entry against 55.3 encoded. The strict codec makes it safe:
  decoding and re-encoding give the same bytes, so the encoded tree
  need not stay resident. Interning names saves nothing within a tree
  (22,109 distinct over 29,349 entries), and inlining tiny objects is
  not worth it (137 of 25,779 are 32 bytes or less).
- The index stores only what the position does not imply. The name
  is its own hash: a 65,536-entry fanout, then 4-byte fingerprints
  and ids, about 20 bytes per object against 50 to 70 for a hash map
  of full digests. The full-digest check stays, beside the data: at
  ten million objects there is a one-in-six chance that two share 48
  bits. Sealed runs plus a sharded mutable table: an LSM. A sorted
  index answers Unreal's 20-byte prefix lookup with the same search.
  An untrusted writer can aim at a bucket in about 2^16 tries, so the
  service keys bucket selection.
- Arenas per thread, compaction as a copying collector, batch calls
  that prefetch across the batch and hash small objects in SIMD lanes.
- None of it freezes: every item is a representation, and a version
  mismatch rebuilds from the packs.

### Reclamation, and retention first

Every namespace keeps its history and the sweep walks parents, so
without retention nothing a namespace pointed at becomes unreachable.
The tool store's comment, "the old tree is unreached once the ref has
moved", stops being true at the switch. Once retention exists: drop
whole dead arenas; free dead page runs in place (`madvise`,
`F_PUNCHHOLE`, private arenas only, 16 KiB pages on this Mac); copy
mostly-dead ones, emptiest first, at u/(1-u) bytes copied per byte
freed, as restic (`--max-unused`, 5%) and Borg (`--threshold`, 10%)
do. Land together what dies together. Compaction needs no write
barrier, because objects never change, and the pending root is the
"allocate black" rule of a concurrent collector. Erasure turns
compaction into a deadline.

Four decisions came out of it:
1. Retention. B: a "no history" class, moves written as parentless
   versions, for caches. D: cuts recorded as a "pruned" tombstone
   kind, per tier, for windows. Willem is "strongly leaning" towards
   this, with the requirement "All those things should be possible
   (fill history from mirror, keep latest N, etcetera)". Git's
   shallow clone is the shape: the boundary belongs to a repository,
   the commits are unchanged, and the server keeps everything. Today
   `drop_ref` (volatile only) frees its target; after the redesign a
   drop is a move and frees nothing until history is cut. Erasing a
   version cuts history below it today (reached, not walked), at the
   price of a permanent tombstone that refuses re-landing.
2. Which packs a compaction copies: a dead-space budget, emptiest
   first, beside geometric pack-count merges.
3. Where the lifetime class lives: inferred from the namespace,
   learned from survival otherwise, nothing in `policy.md`, which
   keeps fields it does not know.
4. Found on the way: ruling 6 sends two concurrent moves of one
   volatile ref to a person, and the tool store declares `tools`
   volatile, so two simultaneous `fm sync` runs with different
   layouts would ask a person. A cache wants a merge rule that never
   asks.

### Strongroom and git

Plumbing, not porcelain. Strongroom maps closely onto git's plumbing:
`hash-object`, `cat-file`, `mktree`, `commit-tree`, `update-ref` with
an old value, `rev-list --objects`, packs, `gc`, `fsck`,
`update-server-info`, worktrees, alternates, promisor remotes. It
refuses four of git's decisions: type-prefixed names, sizeless trees,
compression as the only state, age-based collection. Missing
plumbing: history walks, tree diff, symbolic refs, content merge,
shallow history. The porcelain is the version control tenant, with jj
as its precedent.

### The core, and a btrfs-like filesystem on top

Willem: "strong room has to be the core that allows all these things
to be implemented on top of it as cleanly as possible, so it
naturally will need to have various policies, retention schemes and
settings that can be combined to provide the most optimal backend for
those use cases. Ideally I would love it to be a core that is
flexible enough to implement a btrfs like filesystem on top of."

Read as btrfs's features offered to programs. That extends the design
note's "Venti and Fossil, not btrfs" rather than reversing it; raw
disks stay below, in the block-device backend. Subvolumes are
namespaces, snapshots are versions, copy-on-write and reflinks are
native, a commit is a version behind a compare-and-swap, the log tree
is the write-back journal, send and receive are a set difference, and
the checksums are stronger. Missing: exclusive space accounting and
redundancy.

The rule for where a setting binds: what changes a name's meaning or
a move's promise belongs to the address space or the namespace and is
the same everywhere; what changes only what one tier holds or costs
belongs to the tier. Digest and encryption: address space. Profile,
mutation class, history, eviction, placement: namespace. Depth,
verification cadence, compaction, lifetime grouping: tier.
Representation: requested per call, applied per tier.

What this asks of phase 2: the pruned tombstone kind; subtree counts
on the entry, not only in the header; `policy.md` and namespace
declarations keeping fields they do not know; and whether redundancy
is placement only or also an erasure-coded representation.

### nodatacow and the ZFS way

`nodatacow` writes data in place, implies `nodatasum`, drops
compression, allows torn writes, and copies once after every
snapshot. The ZFS way is regarded as the better design: always
copy-on-write, `recordsize` at the database page, an intent log, and
the database drops its own torn-page protection
(`innodb_doublewrite=0`, `full_page_writes=off`). For strongroom, the
plain overlay file is the btrfs way; the ZFS way is a checksummed
append-only journal folded into page-sized chunks at each checkpoint.
It is open without a format change if phase 2 keeps the fixed
chunker's size free down to a page and keeps in-place replacement in
recipes. Not built: an edit call, a policy per path, and incremental
naming, which is microseconds in a blake3 space with its outboard and
a rehash to the end of the file in sha256 (about 3 s per 10 GB per
core). That is a third reason for blake3, after slicing and IoHash.

### LSM trees, LMDB, and what sets strongroom apart

LSM trees: a write-ahead log and a memtable, immutable sorted files,
compaction leveled or tiered, the RUM trade. LMDB: a copy-on-write
B+tree in one mapped file, two meta pages, readers that never block,
one writer, a stuck reader that grows the file, a format tied to byte
order. Pants: 16 LMDB shards by digest bits, leases, and a
target-size collection under an exclusive lock. Packs are an LSM
without shadowing, because a name never changes value.

Set apart: verification by name across tiers, dedup, permanent
snapshots, replication by name, reachability and erasure with stable
names, a portable spec, representation per object and tier, storage
that reads its own structure, semantics per namespace. Where the
engines stay better: mutable data under arbitrary keys, range scans
over keys with meaning, maturity. Additions that would widen the gap:
proofs, a versioned sorted map, reconciliation in proportion to the
difference, verified slices, encryption a tier can scrub without
keys.

The versioned sorted map, expanded. Prolly trees (phase 8) are
B-trees whose node boundaries come from content. Dolt targets 4 KiB
nodes and chooses a boundary from the key and the current node size,
never the value, so a value change never moves a boundary. The same
set gives the same root, and one change rewrites one path. The map
adds get, put, range, a commit as a version, a diff that skips equal
subtrees, a three-way merge per key with conflicts as data, rank and
count, proofs and sync. A commit hashes about 16 KiB per change at
four levels, so puts batch through a memtable: an LSM whose runs are
versions. It is close to free: a namespace's refs are already a tree
and a large one a prolly tree, so `derivations/` is already a
versioned map whose values are digests. The phase 8 question: a
general key-to-value node with directories as one schema, or a
directory-only node. Users: the lineage note's mutable index keyed by
digest, the state store's series, a registry index, training-sample
indexes.

## 2026-10-07: a minimal CI image from the contract

A job that installs system packages pays `apt-get install` on every run
(the "System packages" step, from a job's `installs`). That is worth
revisiting when the workshop generates a minimal docker image from the
contract. Two ways in: store the install instructions with each tool
record, `apt-get` lines and their kin, so an image assembles from the
records; or let toolroom itself build the minimal image, since it
already knows every tool the lock pins for a host.
