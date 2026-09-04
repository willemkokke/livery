# Random musings

No goal. Willem's passing thoughts, associations, and ideas, gathered so
they can be mined for inspiration when we are stuck. Entries are dated,
appended, and never tidied into a plan. Nothing here is a decision.

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
