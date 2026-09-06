# Incremental caching: content-keyed skip and resume-from-failure

Status: EXPLORATORY — 2026-07-27, reviewed against the code the same day by
the agent implementing phase 3/4 (verdicts recorded at the bottom; three of
its findings amended this note, and one of its justifications is corrected
there). The feature itself is undecided. Willem's idea, opened
while phase 3 (task futures) sat in PR #93 and phase 4 (plugin lifecycle)
was unbuilt. Written down because the analysis turned up two findings that
outlive the feature, and one cheap decision that expires when phase 4
publishes its hook contract.

## The idea (Willem)

> I do a lot of data processing, and I would love for footman to carve out
> a niche there too. With this memoizing DAG we're implementing right now,
> if we can affect the memoization key on hashes of filenames or
> combinations thereof, we can early out on second runs if needed, and
> incrementally resume on failure.

Prior art in the neighbourhood: make, Snakemake, Nextflow, Luigi, DVC,
Bazel. All of them key on file targets; footman's DAG keys on tasks. The
question is what a task-keyed runner can do that a file-target runner
can't, and whether the answer is big enough to be a niche.

## First cut: these are two features, not one

Phase 3's memo is **run-scoped and value-carrying** — `work_key` → a
`Future` → the live returned object. Its job is "same work, one execution,
share the result" inside one `fm` line.

What this note is about is **cross-run and skip-carrying** — a persisted
key → permission not to run. It need not carry a value at all.

They should share a key derivation and nothing else. A persistent cache
hit generally *cannot* serve a body-call, because the caller wants the
return object and most returns are not serialisable. So: the persistent
cache serves the skip, the run-scoped memo serves the value. Merging them
taxes every cacheable task with a serialiser it does not need.

Within the cross-run half there are two further features with very
different costs, and they should not ship as one thing:

- **Resume from failure.** Needs a run journal of which nodes went green,
  not content hashing. footman already has the distinction that makes it
  sound: `cancelled` (fail-fast killed it mid-write) is not `failed` is not
  `ok`. A cancelled node must never count as done. Small correctness
  surface; for a forty-minute pipeline step it delivers most of the felt
  benefit.
- **Content-keyed early-out.** The big one. Needs input/output
  declarations, a store, staleness rules, and an answer for every item in
  the key below.

Resume first, if either.

## What has to be in the key

A cache that is fast and subtly wrong is worse than no cache, so this is
where the design actually lives.

1. **Bound arguments in normal form.** Half exists. The cell key in
   `_futures` is `(registry.work_key(task), frozen-args)` — the args half is
   stable and reusable, but `work_key` is `(id(base), frozenset(cwd/rel
   overrides))` (registry.py:186) and `id()` is process-local, meaningless
   the moment it is journaled. `schedule._dep_key` is the same. **A stable
   task identity does not exist yet** and every cross-run feature needs one.
   Compounding it: a bare prerequisite is labelled with its leaf name only,
   so two `build` tasks in different groups both report as `build`, and a
   pulled task function can live at two addresses. Likely answer: record the
   address the task was *reached through*, at reach time. This is a design
   decision, not plumbing.
2. **The matched path set, not only the contents.** Hashing the contents of
   `src/**/*.py` without the sorted list of matched paths means adding a
   file does not invalidate. Cheap to get right, silent when wrong.
3. **Content, with mtime+size as the fast path.** mtime alone is wrong the
   moment you switch git branches; content alone is slow on a big tree.
   Everyone lands on the hybrid.
4. **The environment the task read.** The environ router already
   virtualises every `os.environ` alias through `_globals._merged`, gated
   on `in_task` — footman could record *which vars were actually read*.
   make cannot do this. Nobody who declares `inputs=` remembers
   `PYTHONHASHSEED`.
5. **Tool versions.** `tools.*` already carries scraped version stubs. A
   cached `lint` that ignores which ruff ran will skip a lint the new ruff
   would fail.
6. **The task's own source.** `registry.task_source` (registry.py:376,
   phase 3) makes the body hashable. Transitive helpers are not covered —
   a heuristic, same hole make has, closed only by hashing a whole rule
   closure the way Bazel does. State it, do not paper over it.
7. **The prereqs' keys.** A task's key folds in its dependencies' keys or
   resume hands you a stale downstream. A Merkle chain over the DAG — the
   shape where having a real dependency graph pays.

**Item 1 now gates three features, not one** (added 2026-08-17). Stable task
identity is needed by this cache, by remote execution, and by
[20260817-global-address-space.md](20260817-global-address-space.md). That note
argues the answer proposed here — *record the address the task was reached
through* — is correct for a **key** and insufficient for an **address**:
`plugin(…, into=…)` grafts a provider's tree under a consumer-chosen prefix, so
the same task is `lint.check` in one project and `acme.lint.check` in another,
and two machines would disagree about the name of identical work. A global name
wants the provider's canonical identity with the mount point as a local alias.
Deciding that here costs nothing and cannot be retrofitted cheaply.

Items 4 and 5 are the argument that this would be footman's niche rather
than a Snakemake reimplementation: footman sits between the task and the
process, so it can observe things a file-target tool must be told.

## The hazard

The failure mode of every declarative build cache is the **silent stale
skip** — the glob was wrong, footman skips, the old artifact ships and
nothing says a word. That is the one failure class footman's whole
personality is built against. Consequences if it is ever built:

- Off by default, opt-in per task. Never inferred.
- Outputs verified at hit time, not merely recorded — inputs unchanged but
  someone deleted `dist/` must be a miss.
- Nondeterministic outputs (a timestamp in the file) never re-verify;
  needs a recorded-but-not-verified mode or existence-only checking.
- The escape hatch wants sharing's shape. Phase 3 made it a tree-propagating
  property of the *request* with the ladder `.opts(shared=)` → declaration →
  inherited → shared. "Never shared" and "never cached" want that identical
  propagation. **Settled: two words, one shared resolver** — the flag landed
  as `shared=` (see below), leaving `cached` free for the across-runs axis.

## Two things not to do

- **Do not derive DAG edges from files.** The tempting step is: A declares
  `outputs=["data.parquet"]`, B declares `inputs=["data.parquet"]`,
  therefore B depends on A. That is the make/Snakemake model and it is what
  makes those tools mysterious to debug — the graph stops being readable
  from the declarations. Keep `pre=` the only edge; inputs and outputs are
  for keying and verification, never graph construction. It also keeps
  `--dry-run`, `--where` and completion honest, since they read
  declarations.
- **Do not become an artifact store.** Bazel's real win is not skipping but
  *restoring* outputs from a content-addressed store. That is a CAS, a GC,
  size limits, eventually a remote protocol — a different product.

## Could it be a plugin? — mostly yes, and that is the interesting part

Walked against the phase 4/5 hook surface as planned:

| Needs | Available? |
|---|---|
| Read bound args to build a key | yes — `pre_task` (post-bind), read-only |
| Record outputs after the body | yes — `post_task` + ResultView |
| Write a cross-run journal | yes — `post_tasks` (`inv.results` + `inv.skipped`) |
| Read it back next run | yes — `pre_tasks` |
| `--no-cache`, cache dir config | yes — phase 5 `GlobalOption`, `[tool.footman.<name>]` (D14) |
| Escape hatch per task/subtree | yes — the `shared=` ladder |
| **Skip the body, supply the result** | **no — the one missing primitive** |
| Which env vars the task actually read | no — the router observes it, nothing exposes it |
| Stable hash of the task's source | partly — `task_source` exists but is core-internal |

**Resume-from-failure and content-keyed skip bottom out on the same missing
power** — the skip. But resume is *not* otherwise a pure plugin today: it
is missing **two** things, the skip and a stable task identity (key item 1).
Journalling the greens in `post_tasks` and reading them back in `pre_tasks`
is the easy part; naming which task went green in a way that survives to the
next process is not solved. Key item 7's Merkle chain over prereq keys needs
that identity stable *and* composable. Resume-first is still the right
order; budget the identity decision into it rather than treating it as
plumbing.

**Where the short-circuit belongs:** in `run_bound` (executor.py), not in
the scheduler. `_futures._run_now` also routes through `run_bound`, so one
implementation there covers declared and dynamic runs alike. A
scheduler-level skip would let a body call bypass the cache — reintroducing
exactly the declared-versus-dynamic divergence Willem ruled out during
phase 3.

The plan doc already reserves this in spirit
(`notes/plugin-architecture.md`, the per-task-moments section: "Explicitly
future: a memoization plugin's short-circuit the body with a cached result
— a bigger power than observation, its own design pass"). Deferring the
feature is right. The live question is narrower and expires: **phase 4b is
about to publish a hook contract in which a pre's only outputs are
`task.state` and an exception.** Retrofitting "a pre may also supply a
result and skip the body" into a settled public surface is a breaking
change to the thing plugins are written against. Reserving the moment costs
approximately nothing now.

Recommended split if it is ever built: **mechanism core, policy plugin.**
Hashing choices, store location, GC, size limits, remote stores — all
opinionated, all exactly what the plugin architecture exists to absorb.

## Findings that outlive the feature

Two things turned up while checking whether a plugin could declare its own
parameter markers. Both are true of main today and are worth fixing (or at
least knowing) regardless of whether caching happens.

### 1. Plugin markers in `Annotated` work — and a callable one is silently eaten

`coerce.peel`'s marker loop (coerce.py:135-168) has **no `else`**. Unknown
metadata is silently ignored, which is exactly what makes plugin-defined
markers possible at all. But the final branch is:

```python
elif callable(mark) and not isinstance(mark, type):
    completer = suggest(mark)  # a bare callable == suggest(fn)
```

So a plugin marker that is a plain function, or an instance with
`__call__`, is silently registered as a **shell completion function**. The
marker vanishes; a mystery completer appears. A non-callable instance — the
house pattern, `nosplit = _NoSplitMarker()`, `exists =
_PathRequirement(...)` — works, as does a bare class. Nothing teaches you
which you picked.

Consequences: two plugins dropping objects into `Annotated` get no
provenance, no collision detection, no `--help` entry, and one wrong shape
becomes a completer. That is a hole in the architecture's own "provenance
everywhere, collisions loud" rule. If parameter markers ever become a
blessed plugin surface they want `GlobalOption`'s treatment — a registered
marker base, ownership stamped from the defining module, and the callable
catch-all becoming a taught error instead of a guess.

Note also that `peel` *discards* unknown metadata rather than retaining it,
so a plugin reading its own marker must re-derive type hints from the
signature itself (`get_type_hints(..., include_extras=True)`). Workable,
plugin-side, no core help needed — but a `markers` passthrough on the
peeled spec would make it pleasant.

### 2. The phase-3 handle forwards attribute reads — so stacked decorators work

`registry.py:348-355` plus `functools.update_wrapper`: the task handle
forwards attribute access to the wrapped function. A plugin decorator
applied *below* `@task` stamps the raw function and the plugin's hook reads
it back off the handle — exactly how `@requires` puts `_footman_checks`
there. So this needs nothing from core:

```python
@task
@cached(inputs=["data/raw/**/*.csv"], outputs=["data/clean.parquet"])
def clean(): ...
```

This is the surface to lead with over parameter annotations, because it is
the only one that can express **outputs** — which have no parameter to hang
off, and which a correct hit must verify. Parameter markers are the
composable *addition*: `Annotated[Path, exists, hashed]` means footman
guarantees the file is there before the plugin hashes it.

(Task code importing `hashed` or `cached` from a plugin is ordinary — the
plugin owns the vocabulary, footman never learns the word. That is the
architecture working, not coupling to apologise for. See
[[dont-launder-user-level-coupling]].)

## If first-party, not yet

`footman.env_files` is a good funnel plugin because being wrong is cheap. A
caching plugin being wrong is a silent stale skip. Prove it out-of-tree
first — let the key derivation be wrong a few times and get fixed — before
it moves in beside `footman.docs` and inherits the project's reputation for
not lying to you.

## Open questions, in order of how much they constrain the rest

1. Does phase 4b **reserve** the body short-circuit (even as a documented,
   unimplemented, taught refusal)? This is the one that expires.
   → *Being done.* Structure was already right (`_call` has one caller pair
   in `run_bound`); only the reserving wording is needed.
2. Does `volatile` absorb "do not cache", or are they separate words?
   → **Settled: separate words, one shared resolver.** The flag is `shared=`
   and the in-run report state is `shared`; `cached` stays unspent for the
   across-runs axis. The original argument, for the record: the axes differ
   ("never shared" is within a run, "never cached" is across runs) and
   shared-but-never-cached is an ordinary combination — expensive and
   nondeterministic. Good argument, not yet a decision.
3. Does resume-from-failure ship as its own small feature ahead of any
   hashing? → Agreed in principle, with the stable-identity caveat in key
   item 1 budgeted into it.
4. Is the `Annotated` callable catch-all (coerce.py:166) tightened into a
   taught error? → **Superseded: delete the branch instead.** The bare
   callable is an undocumented-in-prose second spelling for `suggest(fn)`,
   which already exists; deleting closes the hazard completely rather than
   shrinking it, and unknown callables then fall through to "ignored" like
   every other unknown marker — which is what plugin markers need.
   **Correction to the agent's stated justification:** it is *not*
   undocumented. `params.py:74`, inside the public `suggest` docstring,
   says "A bare callable in `Annotated` is treated as `suggest(fn)`." So
   this is the removal of a documented behaviour — the docstring sentence
   goes with it and it earns a CHANGELOG line, rather than being a silent
   tidy-up. The verdict still holds on one-spelling-per-concept grounds.

## Verdicts from the implementing agent (2026-07-27)

All seven handoff asks accepted, none conflicting with a settled decision.
Beyond the four above:

- **Env vars read** — better shape than the note proposed: record the read
  keys **and** whether the task spawned a subprocess, so the observation
  describes its own completeness (total for an in-process body, explicitly
  partial once a child inherits the whole environment). A cache can then
  decline to key on it rather than silently under-keying. Reads already
  rebuild a merged dict per lookup, so a set-add is free by comparison.
- **Source hash accessor** — accepted, with two caveats that must ship in
  the docstring: it returns `None` when source is unreadable (C functions,
  REPL definitions), and the hash is **shallow** — the task's own source
  including decorator lines, never its helpers or imports.
- **Result state** — accepted as a *derived* reported field; `ok`/`code`
  stay the internal exit-code channel. Adding a field keeps `schema: 1`
  compatible where changing `ok` would not. Docs must tell consumers to
  tolerate unknown state values, or "open" is only true on our side.
- **Sharing resolver** — was *not* already extracted: inlined at
  schedule.py:150 (dependencies) and schedule.py:213 (chain segments), and
  the two spellings differed — the segment site dropped the inherited rung,
  correctly but implicitly. **Done:** `schedule.resolve_inherited(declared,
  inherited)`, named for the mechanism rather than either axis, so the
  across-runs property reuses it unchanged. Extracting it also made the
  segment case explicit (a chain segment is a root, so nothing asked for
  it).
- **`markers` passthrough on the peeled spec** — must be runtime-only and
  provably absent from the manifest, which is JSON on disk read by the
  import-free hot path; arbitrary marker objects are not serialisable.
  Re-deriving `get_type_hints(..., include_extras=True)` plugin-side stays
  the safer answer.

## Second review pass (2026-07-27, supersedes parts of the above)

- **Key item 1 has an answer: identity is the address reached through, plus
  an author-bumped `version` on the plugin's own decorator.** A source hash
  is both too sensitive (this repo's gate runs `ruff format`, so a
  formatting pass busts every entry) and not sensitive enough (a helper
  changes and it does not move). Too sensitive costs the cache, not
  sensitive enough costs correctness — so it cannot be the key.
  `@cached(..., version=2)` is caching vocabulary, works today via the
  handle's attribute forwarding, and core never learns the word.
- **The source hash survives as a tripwire, not an identity** — recorded
  and compared, so "the body changed since the cached entry, version is
  still 2" is a nag you dismiss rather than a stale skip you never learn
  about. Hash the **AST**, not the text (`ast.parse` + normalised dump
  drops comments and formatting; footman already parses task source this
  way in `_empty_body`), so a format pass does not trip it.
- **Address-as-identity fails towards a miss.** Move `build` into a
  `release` group and the address changes, so you rebuild; pull one
  function in at two addresses and you get two entries — wasteful, never
  wrong. A content hash fails towards a *hit* when it under-captures. For a
  cache, a key whose errors are misses is the only kind worth having.
- **The Merkle chain makes manual bumping cheap** — a task's key folds in
  its prerequisites' keys, so bumping one leaf invalidates everything
  downstream. You bump where the change happened, not everywhere it
  matters.
- **`peel`'s callable branch shipped as a taught refusal, not a deletion** —
  callable `Annotated` metadata now raises `SpecError` (reported as a taught
  refusal), so a plugin author whose marker is a function learns
  immediately instead of getting a mystery completer. Unrecognised
  **non-callable** metadata is still silently ignored and now pinned by a
  test, so `Annotated[Path, hashed]` remains a plugin's business. Better
  than either "narrow it" or "delete it".
- **The best input declaration may be the type annotation footman already
  has.** `def clean(src: Annotated[Path, isfile])` already means "a real
  file this task reads" — typed, validated to exist before the body runs,
  hashable, with no `inputs=` glob to get wrong. A second `hashed` marker
  asks the author to say it twice. Globs then appear only where genuinely
  needed, and per-argument caching is already free from phase 3's args key
  (`fm process --date=2026-07-01` gets per-date entries and mid-sweep
  resume with no extra work).
- **Core can refuse to cache what it can see is unsafe.** The environ
  router records every `os.environ` write in `ctx.env`, attributed to the
  task (_globals.py:140) — so a task whose effect is "set `DATABASE_URL`
  for everything downstream" is computably unskippable, not
  trusted-to-a-human. Same from flags core already carries: `interactive=`
  has no result to cache, `infinite=` has no completion, `serial=`/
  `exclusive=` means shared state. This is the anti-stale-skip property
  made structural, and no file-target tool can compute it.
- Also raised, worth keeping: the timing history makes caching
  *discoverable* (point at the 11-minute task that declares no inputs
  rather than relying on a docs page); a **taught hit** that says which
  components matched, with `--explain-cache` as the same data from the
  other side; a zero-match `inputs=` glob refused loudly at declaration
  time. **Cut:** negative caching — a transient failure cached is a trap;
  report "failed on identical inputs last run" instead. **Not core:**
  filesystem tracing — platform-specific, and unsound as a key because it
  fails towards a hit.

## Naming: two axes, and the report word collides too

**Decided by Willem since (relevant here):** a request satisfied by an
earlier execution is **recorded, with a cached indicator** — not silently
absent as today. That is exactly the journal signal a resume plugin wants,
and it makes the open state enum load-bearing immediately ("cached" is one
of its values). It composes with phase 3's ordering rule: an entry that
never began sits directly after whatever prevented it, so a cached entry
lands right after the execution that satisfied it.

**But that indicator collides, and the collision is user-facing.** Two
different things now want the word:

- a request satisfied by an earlier execution **in this run** (phase 3's
  memo), and
- a request satisfied by a **previous run** (this note's feature).

Both would read as `cached` in the same report field. Worse, footman
already spends "cache" on the completion manifest — `completion.md` and
`comparison.md` say "cached manifest", "cached answer" throughout — so a
task-result `cached` invites "cached where?".

**Two axes, one word each, used for both the flag and the report state:**

| Axis | Question | Flag | Report state |
|---|---|---|---|
| Within one invocation | may two requests share one execution? | `shared` | `shared` |
| Across invocations | may this run reuse a previous run's result? | `cached` (plugin-owned) | `cached` |

Which makes `volatile` the word to retire — see below. The report state is
the more urgent half: the flag is one API surface, the state string is what
every user reads on every run.

## `volatile` is the mis-named one — LANDED as `shared=`

Its own documentation never uses it to explain itself
(`docs/orchestration.md`, the futures branch): the section header says
"Asking for **fresh** work", the body says "the task is **never shared**",
the rule says "**Sharing** is a property of the *request*", the admonition
says it "**unshares** its whole dependency subtree", and the pin
`volatile=False` is explained as "anything that genuinely is **reusable**".
Commit 20aae22 is titled *"sharing is a property of the request, not of the
task."* One concept, three words (volatile, fresh, shared), and the flag is
the only place the first appears — the pattern `=`-only and dotted
addressing were both decided against.

`shared=` is the word the prose already picked. `@task(shared=False)`,
`.opts(shared=False)`, and "sharing is a property of the request" becomes
literally true of the API instead of a translation of it. The pin improves
most: `volatile=False` → `shared=True` — "pin anything that genuinely is
reusable with `shared=True`" says what it does instead of double-negating.
Honest cost: polarity flips, so the notable case becomes a negation — but
the concept as documented *is* the negation ("never shared"), and the
tri-state (`None` = whoever asks decides) makes the framing arbitrary
anyway.

It also clears the caching axis. `volatile`'s canonical opposite —
non-volatile, durable storage — is precisely about surviving a restart,
which is the *other* axis; two words from one family for two orthogonal
axes reads worse than two unrelated words.

**Timing:** taken while `volatile` was still unmerged in PR #93, so nothing
published ever carried the word. Renaming after the merge would have been a
second breaking change to a flag introduced one release earlier.

**Done** (in #93, before merge): `@task(shared=…)`, `.opts(shared=…)`,
`registry.sharing()` for the tri-state read, `ctx.shared` / `_Node.shared`
carrying the resolved answer (defaulting to `True` now that the positive
reading is the default), `_futures.unshared()` for the per-call ladder, and
the report state `shared`. The docs section is retitled "Work that is never
shared: `shared=False`" and the admonition "Unsharing propagates down the
subtree", so the prose stopped translating.

One point of evidence the proposal did not use, and the strongest of them:
`shared` was **already** the codebase's word for this concept, in core
docstrings written long before the futures branch — `schedule._dep_key` says
"a shared prerequisite still runs once" and `docs/orchestration.md` says
"deduping shared deps". So this was a reunification rather than a rename;
`volatile` was the newcomer that did not fit the vocabulary already in use.
`docs/testing.md`'s "filter the volatile fields" is the English word about
values that vary between runs, and was deliberately left alone.
