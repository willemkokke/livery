# A global address space for data

Status: EXPLORATORY — 2026-08-17. **Nothing built, nothing ruled.** Opened
while ruling the service scope in
[20260816-services-and-sinks.md](20260816-services-and-sinks.md), when the
question "could this help with distributed processing" turned out to be
pointing at something larger.

Neighbours, and this note leans on both rather than restating them:

- [20260816-services-and-sinks.md](20260816-services-and-sinks.md) — the
  transports frame: footman is already an RPC framework whose IDL is the type
  annotation, and the CLI is one transport.
- [20260727-incremental-caching.md](20260727-incremental-caching.md) — *"What
  has to be in the key"*, and the ruling that the skip and the value must not
  be merged. Both still hold; this note extends them, it does not revise them.

## The goal (Willem)

> Could this be a big part of my goal to have distributed processing offloaded
> between machines as well?

and, when told which slice was close and which was not:

> Yes, that's really getting to what I have in my mind. **A global addressing
> space for data.**

## What is already close, and what is not

A distributed runner is a DAG, addressable units, typed payloads, a scheduler
and receipts. footman has all five *locally*: dotted addresses, the once-cell
keyed on (task, bound args), `_schedule.py`'s graph with fail-fast and
keep-going, the `--json` record model, and `--jobs` as a budget. Plus a manifest
that is already a fetchable, cacheable description of what is callable.

So **remote invocation** — "run the integration suite on the big box", "build
the container on the Linux host" — is genuinely near. It is a task that happens
to execute elsewhere, and Part 3's transport frame covers most of it.

**Automatic distribution of a DAG across a fleet, with data movement, is not
near**, and the gap is not transport. It is data locality: `run()`, `cwd=`,
`rel=` and the process-globals routers all assume one filesystem. Offload a task
and it sees a different disk, so either you assume a shared mount or you ship
inputs — and shipping inputs means **declaring what a task reads and writes**,
which footman deliberately does not require today.

That is the same blocker the caching note already found from the other side:

> **Content-keyed early-out.** The big one. Needs input/output declarations, a
> store, staleness rules, and an answer for every item in the key below.

**One missing thing gates three goals**: content-keyed caching, remote
execution, and this note. And it pays for itself locally before any second
machine exists, which makes it the rare prerequisite that is worth building on
its own merits.

## The observation: the data address is the call

footman **already has a global address space.** It is for tasks, not data:
dotted addresses, namespaced, composable through `plugin()`/`include()`,
discoverable through the manifest.

And the once-cell already keys on `(task, bound arguments)`. That pair is
*already a name for a result* — it is what lets `pre=[build]` and a body call to
`build()` be one execution. Today it lives for one run. The caching note lifts
it to a persisted key; the services note's project rung lifts it across
processes; a fleet rung lifts it across machines. **Same name, wider store.**

So there is probably no separate naming scheme to design. The name of a datum is

    task address + arguments in normal form + input fingerprint

which is how Nix names a derivation and how Bazel names an action. It falls out
of a model footman already has rather than being bolted beside it.

## What a cache key is not: an address

This is where the reframe costs something, and it is the part worth having on
paper before anyone builds a key.

| | a cache key | an address |
|---|---|---|
| must be | consistent with itself | **canonical** — the same everywhere |
| scope | private to one machine | shared between machines |
| comparison | equal or not | **verifiable** — bytes can be checked against the name |
| wrong answer costs | a needless rebuild | a wrong result imported from elsewhere |

Three consequences:

**1. A reach-address is project-relative, and that is not good enough.** The
caching note's likely answer to task identity is *"record the address the task
was reached through, at reach time"* — correct for a key, since comparisons only
happen within one project. But `plugin(…, into=…)` grafts a provider's tree
under a consumer-chosen prefix, so the *same* task is `lint.check` in one
project and `acme.lint.check` in another. Two machines would disagree about the
name of identical work. A global address needs the **provider's** canonical
identity — distribution plus dotted path — with the mount point as a local alias
on top.

**2. Content addressing is what lets you accept bytes from a machine you do not
fully trust.** Part 3 leaves the remote rung with an authorization story and an
untrusted-manifest problem. A content-addressed artifact sidesteps half of it:
you hash what you were given and check it against the name you asked for. That
does not authenticate the *sender*, but it removes the need to — which is a much
smaller security surface than "trust this server's build outputs".

**3. Determinism becomes load-bearing**, where for a local cache it was merely
useful. Two machines must agree, so a task that is not a function of its inputs
cannot be addressed at all.

## Purity already has a marker

`shared=False` documents itself as *"work whose whole point is to happen again,
like a notification or a timestamp"* (`registry.py:1570`). That is impurity,
described from the scheduling side.

So the predicate this design needs already exists and already has a spelling: a
`shared=False` task is **not addressable**, and asking for its result by address
should be a refusal rather than a surprise. Worth checking whether the two
really coincide before leaning on it — but if they do, the vocabulary is
already there, which is the third time in this thread that a needed distinction
turned out to be half-built under another name.

## Three tiers of result, not two

The caching note ruled that the skip and the value must not merge, because
*"merging them taxes every cacheable task with a serialiser it does not need"*.
That ruling holds. But a global data space needs a third tier the note did not
have a reason to name:

| tier | what it is | what it needs |
|---|---|---|
| **skip** | permission not to run | a key. No serialiser. |
| **value** | the typed return | already serialisable — `Stdout[T]`, the `--json` envelope |
| **artifact** | bytes: a wheel, an image, a dataset | declared outputs, and a store |

Only the third needs machinery that does not exist, and only tasks that opt in
should pay for it. That keeps the ruling intact: the skip stays serialiser-free,
the value keeps riding the transport it already has, and a store appears only
where an author asked for one.

## What I would not do

- **Build a store before the declaration.** Inputs and outputs are the blocker;
  a store without them has nothing trustworthy to key on.
- **Chase Bazel remote execution or Ray.** Different category, different team
  size, and footman's differentiators — 30 ms completion, zero dependencies,
  typed signatures — do not transfer into that fight. Remote invocation plus a
  content-addressed skip is where nearly all the felt value is for this
  audience.
- **Merge the skip and the value.** Already ruled; the third tier is an
  addition, not a revision.

## Where this went next

Pulling the transports frame away from footman entirely — what the general
system is, its security decomposition, and where it could lead as a product —
became its own thread, kept **outside the repository** with the launch
material rather than in `notes/`: it is exploratory product thinking, not
footman design. Nothing in it revises this note's rulings; anything from it
that ever lands in footman arrives through the existing threads (services,
incremental caching, this note) on their own merits.

## Open

1. **Is a global address the provider's canonical path or the mount point?**
   Leaning provider-canonical with the mount as a local alias, per consequence 1
   above. This is the first thing to settle — every other question inherits it.
2. **Where does an artifact store live, and does `_gc.py` own it?** The sweep
   already reaps by age; artifacts want reference-counting or a root set, which
   is a different collector.
3. **What happens to a non-deterministic task** — refuse to address it, or
   address it by *output* hash after the fact? The second is what content-
   addressed build systems do for "fixed-output" derivations, and it is a real
   escape hatch rather than a hole.
4. **Does a fleet rung imply trust between machines, or verification only?**
   Consequence 2 argues verification gets you most of the way, which would keep
   Part 3's local-only line drawn further out than it looks.
5. **Do `shared=False` and "not addressable" actually coincide?** Cheap to
   check, and it decides whether purity needs its own marker or already has one.
