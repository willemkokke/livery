# The execution model — the spec (move 1: core and derivation ledger)

**Status: CALLED, and BUILT — the model shipped in 0.28.0 (2026-08-01).**
The normative core distilled from
[the thinking record](20260731-execution-model-record.md), which holds the
evidence, the register, and the history. This note is the other
direction: definitions and invariants first, and every feature either
derives or gets cut. The build that implemented it is
[20260731-execution-model-build.md](20260731-execution-model-build.md).

The ⚠ marks below read as *"incoherence hiding here"* in the draft; every
one was worked through in this same note before the build started — the
walks resolve them in place ("then the ⚠ rows fall — two at once, to one
principle"), and the five moves closed on 2026-07-31. Read them as the
map of where the thinking was hardest, not as remaining work.

## Definitions

All terms per the settled register (see the thinking record). One-liners
only; the register carries nuance.

- **work item** — the one substrate: a piece of managed work with a
  record. (Notes-word; users meet only tasks and steps.)
- **task / step** — the two default bundles of the substrate. Task:
  named at import, CLI-addressable, dedups by declaration, full policy.
  Step: anonymous, unique per mention, inherits policy.
- **the ladder** — foreign (just code) → observed (a step made from body
  code) → owned (declared; schedulable). Readings: schedulability,
  placement (local-only → placeable → shippable), generator-shaped or
  not.
- **the record** — what is kept about an item: title, verdict/code,
  shown output, duration, provenance. Its rendered form is the
  *receipt*; its draft is a **`ResultView`**; committed, it is a
  **`Result`**.
- **off the record** (`recorded=False`) — executed under full
  management, no record. How a task learns something.
- **the audit** — the record's verdict provenance: every lifecycle
  moment that acted on (or failed) the grain, as (moment, actor,
  code-or-None) entries in execution order. The body/capture entry is
  always present and carries the work's own raw code; a failing moment
  always enters, declared or not; quiet undeclared moments are
  skipped; involvement without a verdict write records code None —
  and nothing else (no title-diff log). `failed_at` and the
  pre-failure code are derived readings of the audit, not stored
  twins. (Ruled 2026-07-31, name Willem's; supersedes the provisional
  `work_code` field.)
- **address** — a node's tree-derived name (parent-path + label +
  ordinal). Universal, referential, line-number-stable. Ordinals count
  same-labelled siblings in request order AS WRITTEN — never completion
  order — so addresses are deterministic across runs and hosts (move 4:
  cross-run duration history and the horizon need nothing racier, and a
  parent's requests are made from its own control flow, so written
  order is well-defined even under a parallel pool).
- **shareable identity** — (declaration, frozen overrides, bound
  arguments in normal form — defaults applied); declared items only;
  what dedup keys on. Unkeyable arguments (no frozen form) mean unique
  work — graceful degradation, never a guess. "Prereqs run defaulted"
  is the degenerate case, not a separate rule. (Verified in
  `_futures._key`, 2026-07-31: the runtime already does exactly this;
  the spec was behind the code.)
- **(resource) lane** — a serialised claim on a named resource (process
  globals; custom user resources by ruling).
- **lifting** — changing an item's grain at the use site (`@step`,
  `with step():`, `step(fn)`, `@task`…).

## Invariants

- **I1 — One substrate.** Every piece of managed work footman performs
  is a work item; task and step are default bundles, not kinds.
- **I2 — A Result is its exit code.** The committed record IS its code;
  `ResultView` is its only draft, writes phase-gated by the item's
  lifecycle.
- **I3 — No record without work.** Every record belongs to a work item
  that executed (or was dry-run-declared, or was skipped — states of
  real items). The forged receipt is unspellable, not discouraged.
- **I4 — Separable concerns, composed by default.** Execution and record
  are distinct; `run()` composes both; `recorded=False` is execution
  alone; a record without execution violates I3.
- **I5 — One record, committed once.** Review (`pre_record`, the
  generator's view, the block handle) happens before commit; after
  commit the record is immutable and reported exactly once. Review is
  per EXECUTION (a shared row: one review, observer events per
  request); a maker's hook reviews the record its maker makes, never
  its children's. Observation is read-only BY TYPE (ruled 2026-07-31):
  after the review window closes there is no view — `post_task` holds
  the immutable `Result`, so observers see, never judge, unspellably;
  `set_returned` is a review-window write only. A raising hook at any
  moment is an error like any other error: the grain fails, and every
  failure records WHERE in the lifecycle it happened (`failed_at`:
  bind / enter / body / review / observe) — the machinery tags the
  moment; no hook rewrites a verdict. An observer may VETO —
  `fail(reason, code)` rides this same channel, loud and attributed to
  the observe moment (on a shared re-request: to the requesting
  reference row's) — but never FORGE: the work's own verdict is not
  rewritten; I2 makes the veto's code the grain's final int, and the
  work-was-green story survives in `failed_at` plus the reason.
  `fail(code=0)` is a taught error EVERYWHERE (ruled, move-4
  follow-up): fail is the failure verb and code 0 is success — the
  pass-branch spelling was already rejected once in the task-failure
  design; the executor's verbatim honouring of it in a body was
  drift, not intent. At a hook moment the stakes are higher — the
  verbatim honouring would have let an observer "fail" a grain green
  (move 4, verified in source) — but the rule is one rule, uniform.
  And every grain carries its audit (see definitions): the verdict's
  full provenance in execution order, so a green build vetoed at
  observe shows its raw code, its reviewed 0, and the veto — each
  attributed. `failed_at` and the pre-failure code are derived
  readings of the audit, not stored twins.
- **I6 — One identity rule, everywhere.** Address is universal; sharing
  exists only where a declaration does (I13), and the key is uniform at
  plan and execution: **(declaration, frozen overrides, resolved
  normal-form arguments)** — defaults applied, forwarding resolved,
  unkeyable → unique. A node and a body call that resolve to the same
  arguments name one piece of work; requests that resolve differently
  are different work, silently and correctly, on every path. Ruled
  2026-07-31 (Willem: "if declared means dedup by default, arguments
  passed in must be part of the key"), overruling the shipped
  divergent-forwarding `ChainError`: divergence now makes two nodes —
  what the user meant — not a refusal; the build carries that as a
  CHANGELOG-visible change. Consequence parked at decisions 1/4:
  same-label different-args rows are distinct by address ordinal, and
  whether resolved arguments surface on receipts is display policy.
  (Both paths verified in source, 2026-07-31: `run_bound` computes the
  same `work_of`/`_key` over bound-including-forwarded arguments and
  joins the same cell protocol body calls use — "sharing means the same
  thing whichever way a task was reached." Also found and adopted: the
  key is computed before `ctx` joins the arguments, so the context can
  never become part of the work's identity.) A shared item's subtree
  rides with it.
- **I7 — Yields are never scheduling points.** Bare `yield` =
  checkpoint; every yield evaluates to the item's `ResultView`; yielding
  a value is a taught error. Concurrency stays threads and processes.
- **I8 — Lane acquisition is boundary-atomic.** All resource claims at
  the item's boundary; mid-body escalation does not exist.
- **I9 — Manifest presence only at import.** Runtime lifting can grant a
  report label, never a CLI address; the completion hot path reads the
  manifest and nothing else.
- **I10 — The record is an interface, not a blob.** Fields absent by
  circumstance (a `None` return) belong on the shared view; surfaces
  absent by kind (plugin `state`, CLI binding) stay off it.
- **I11 — Projections, not privileged shapes.** The report tree
  (who-requested-what) and the dependency DAG (what-needs-what) are both
  derivable views over one item set; committing to a storage shape is
  open decision 1, and whichever is chosen must keep the other cheap.
- **I13 — Declaration is the commitment boundary.** Shareable identity,
  boundary policy (confirm, gates, shared, forward), and the guarantee
  of a record exist exactly where a declaration does; execution policy
  (cwd, env, timeout, lanes, capture, recorded) is item-general —
  except `recorded`, whose task-grain value is pinned True by this same
  invariant (declared ⟹ recorded, walk 4): the keyword exists only at
  undeclared grain. (Carve-out found by move 3 — the two clauses
  collided the moment the policy groups became types.) Every wall in
  "defaults, not walls" is an instance of this one.
- **I12 — Footman holds no ungraded code.** (Ruled GO 2026-07-31 —
  decision 8.) Everything handed to footman has a chosen grain; the
  foreign rung exists only inside bodies.

## Derivation ledger

*derives* = follows from the invariants; *axiom* = is one (or a ruling);
*⚠ doesn't fit yet* = the model cannot yet say it cleanly — the open work.

| feature / claim | derivation | status |
| --- | --- | --- |
| `pre_record` (per-tool `.opts()`) | I5: review before commit; attachment-as-dispatch from I1 (a tool's items are just items) | derives |
| `@pre_record(fn)` stacked on makers | I1 (one substrate → stacks on `@step` and `@task` alike) + I5; the `requires=`→stacked-`@requires` history as precedent; `.opts()` form remains for def-less attachments; phase rule reviewed → observed → committed | derives (ruled 2026-07-31) |
| hse's djlint fix | `pre_record` + I2 (override the code, verdict follows); walk 2 walked it concrete — one line plus a reviewer, both old lies (fake duration, hidden work) die, `nofail` subsumed. Plus two derived rules: review sees what was captured; a raising reviewer fails the item. | derives (walk 2, concrete) |
| `@step` three-position grammar | lifting (definition) + I1; the decorator mark mitigates build-not-run by convention, not invariant | derives (ergonomics ruled, 2026-07-31) |
| yield contract | I7 verbatim | axiom |
| `recorded=False` | I4 verbatim | axiom (spelling ruled) |
| forged receipt refused | I3 verbatim | axiom |
| cwd lane (and custom resources) | I8 + lanes definition; the in-process-only scope derives from `run()` injecting `cwd=` for subprocesses | derives |
| `parallel()` takes work items only | I12 (ruled GO 2026-07-31); refusal structural — a bare callable has no `.opts`, so neither maker protocol matches | derives |
| `p.also` retires | I12 + deferability of makers | derives — decision 8 ruled GO |
| no step-grain observer | I6 (steps carry no shareable identity; address is parent-derived — out of context they are storyless) + the dispatch refusal + I5's enforced windows; liveness routes via the Status units today and record-stream export under the horizon | derives (opinion ruled 2026-07-31) — scope sharpened same day: this refuses the GLOBAL stream; handle-attached per-maker observation (`post_step`) is not this, and was ruled in (move-5 addendum) |
| dry-run semantics | Walk 3 falsified the old row ("skips observed steps" — PEP 377 makes with-bodies unskippable, and dry-run never skipped body code anyway). Correct rule derives from the ladder: dry-run fakes what footman owns the execution of (run payloads, deferred makers, generator steps); inline body code always runs, with-form records without an execution boundary. | derives (walk 3, corrected) |
| per-step duration history | address (universal, cross-run-matchable) | derives — future work, nothing blocks it |
| display policy (collapse green) | receipt = rendered record (record family); pure presentation over I5's committed records | derives — scope still open (decision 4) |
| **`recorded=False` at task grain** | Walk 4: refused — sharing IS record-reuse, and declared items are the run's accounting. Declared ⟹ recorded (I13). Taught error, not a capability. | resolved (walk 4) |
| **`parallel()`'s return shape** | Walk 1: nothing wants codes over Results; I2 makes Results backward-readable. Rides decision 1 only. | derives — decision 1 called 2026-07-31 (flat items): `list[Result]` it is |
| **`confirm=`/gates on steps** | Walk 4: refused via I13 — boundary policy (confirm, gates, shared, forward) requires a declaration to resolve at; execution policy (cwd, env, timeout, lanes, capture, recorded) is item-general. The policy table splits on this line. | resolved (walk 4) |
| **The generator pump vs lanes** | Walk 5: suspended-at-yield is exactly what close() cancels — GeneratorExit unwinds, finally releases (boundary-atomic = one release site). Between yields: uninterruptible, bounded by the longest inter-yield stretch, documented. Lanes never force-stripped. | resolved (walk 5) |
| **`Fanout` in the model** | Walk 1: both parallel() forms are an anonymous grouping item whose children are the fanned items — not special, just an anonymous parent. Shape known; rendering rides decision 1. | derives — decision 1 called 2026-07-31: a flat item with children by address |
| **I6 and bound arguments** | Walk 1 found the gap; `_futures._key` verified. The Forward question exposed the plan layer's arg-excluding dedup + divergence refusal — and Willem's ruling collapsed the layers: ONE key, (declaration, overrides, resolved args), everywhere; the shipped ChainError retires (divergence = two nodes). Display of same-label/different-args rows parked at decisions 1/4. | resolved — ruled, uniform |
| **partial-of-a-task defeats interception** | Walk 1: real today (footman's own tasks.py) — silent grain demotion. The double-count worry died in source: the unit-claim protocol anticipated "a task call in disguise" by name (unit_pending handed down, claimed by the first request). The footgun narrows to interception/queueing loss alone. Under I12: taught refusal. | resolved into decision 8's case |
| **post_task observes the committed record** | Ruling 2026-07-31 (the phase-gate counter, move 3): observation holds the immutable `Result` — the phase gate is the ResultView/Result type split, static; `set_returned` review-window-only; a raising observer fails the grain; every failure carries `failed_at` lifecycle provenance (I5) | resolved — ruled, typed in the loom |
| **the empty `with step():` block** | Not even new rope (Willem, 2026-07-31): an empty `@task` body with no `pre=`/`post=` has always minted a named green row — the with-form is the same spelling at step grain. Named work that does nothing was never footman's to police; I3 polices records unmoored from EXECUTION (the machinery attesting to work it never performed), and works by motive-removal plus attribution — the honest `pre_record` line is cheaper than any lie, which sits in reviewed code with a real duration. | non-issue (move 4, corrected by ruling) |
| **hook raises carry no success** | `fail(code=0)` is honoured verbatim in a body (the author's own record — verified in `_executor.py`); at a hook moment raising IS failure and the code is never 0 (taught error), else the veto asymmetry is false | derives (move 4, hse) — guard added to I5 |
| **a generator abusing GeneratorExit** | Swallowing GeneratorExit and yielding again is Python's own RuntimeError; the item fails; lane release is machinery-owned at the boundary (I8) — the abuse cannot hold a lane | derives (move 4) |
| **completion hot path and Windows under the model** | Every move-3 surface is execution-side: anonymous steps never touch the manifest (I9), runtime lifting grants report labels only, `Phase`/`failed_at` live in `--json`, the ban changes no CLI grammar; observation runs on the runner after child reaping, so walk 5's kill discipline is untouched | confirmed clean (move 4) |
| **per-task hooks on the handle** | Attachment-as-dispatch extended to the lifecycle moments; handles-are-the-registry (the lanes precedent, identically argued); knowledge lives where it belongs; zero invariants moved — the mark of something the model already implied | ruled 2026-07-31 (the contract page's first finding) |

## Move 2 — the payload walks

### Walk 1: footman's own `fm check` (2026-07-31)

The payload: `check` fans out `parallel(functools.partial(format,
check=True), lint, typecheck, typecomplete, covered)`; `typecheck`
itself fans `parallel(based, mypy_linux, mypy_darwin, mypy_win32,
run_ty, run_pyrefly)` — all local defs. Mixed grains in one expression,
twice over.

**Today's items, walked:** `check` is a row. `lint`/`typecheck`/
`typecomplete` are owned references → intercepted → rows (deduped
identity). `partial(format, check=True)` is a *bare callable wrapping an
owned task* — footman doesn't own `partial.__call__`, so interception is
silently defeated; it executes as foreign, and only the inner
`format(check=True)` body-call re-enters as a request. `covered` and the
four typecheck defs are foreign; their `run()` steps fold onto their
parents' step lists. The report renders every row FLAT and chronological
(`results[]`); the nesting that obviously exists (`typecheck`'s four
checkers "under" it) is expressed only by steps-riding-rows.

**Findings:**

1. **NEW ⚠ — I6 is incomplete: where do bound arguments live in
   shareable identity?** The definition says (declaration, frozen
   overrides). But `format(check=True)` and a bare `format` are
   different work; historically deps never exposed this (pre=/post=
   run defaulted, so shared nodes never differed in args), and chain
   mentions are each their own node. Body-call dedup vs arguments needs
   verifying in `_futures.call` — and whatever the answer, I6 must
   *state* it. Candidate: shareable identity = (declaration, overrides,
   bound args), with "runs defaulted" the degenerate case. VERIFY IN
   SOURCE before promoting.
2. **The partial footgun strengthens the ban (decision 8).** A partial
   of a task defeating interception is not hypothetical — footman's own
   tasks.py does it (it works only because the body-call re-intercepts;
   whether the status line double-counts that unit — parallel child AND
   inner request — needs an empirical check). Under I12 this is a
   taught refusal instead of a silent demotion. The walk found a
   today-bug-class, not just tomorrow's ergonomics.
3. **`parallel()`'s return shape: promoted toward derives.** `check`
   ignores the codes entirely (it relies on the raise); nothing in this
   payload wants `list[int]` over `list[Result]`, and I2 makes Results
   backward-readable as codes. Remaining tie to decision 1 only.
4. **`Fanout`-as-item: sharpened.** Walked, the bare-call and block
   forms produce identical children; the block adds only an addressable
   parent. Uniform answer: BOTH forms are an anonymous grouping item
   (the fan-out) whose children are the fanned items — under the tree
   projection `typecheck`'s checkers finally nest where they belong;
   under today's flat report the grouping item is invisible. So the row
   rides entirely on decision 1, but its shape is now known: not
   special, just an anonymous parent.
5. **Under the model + ban, the payload reads better than it does
   today:** every foreign def lifts (`@step` on `covered` and the four
   checker defs — each gaining a receipt, which `typecheck`'s live line
   already fakes via `__name__` assignment today: the lift replaces a
   convention with a record), and `format(check=True)` moves into the
   block form where owned calls carry arguments naturally. The
   `__name__ = "..."` idiom in tasks.py is a hand-rolled title= — more
   evidence the record surface was always being improvised.

**Promotions:** finding 3 (conditional-on-1 only), finding 4 (shape
known, rides 1). **New work:** finding 1 (I6 gap → verify + restate),
finding 2 (empirical double-count check).

### Walk 2: the djlint gate (hse's payload)

Before, five lines and two lies (`run_titled`): real work `recorded`-off
with `nofail=True`, output read, then a forged receipt lambda carrying
the title (0.0s duration, no provenance) — plus the `cwd` workaround the
forged callable dragged in. After, one line and a reviewer:

    djlint.opts(pre_record=dj_outcome).reformat(...)

Walked moment by moment: the tool call makes one step; it executes
(capture on); code finalises at 1; `pre_record` fires once for this
execution with the draft — reads `view.stdout + view.stderr`, and
either sets `view.title` and `view.code = 0` (reformatted / no files),
or sets the failure title and leaves the code. Commit follows; the
raise-on-nonzero decision reads the post-review code, so the caller
writes no `nofail` — "fail by this tool's definition of failure" comes
free. The receipt shows the REAL duration and the real command in
`.raw`; both of the old pattern's lies die, and hse deletes
`run_titled`, its twin, both lambdas, and the cwd workaround with its
caveat comments.

Invariants touched, all clean: I2 (the code override IS the verdict),
I3 (no second item exists to forge), I4 (the work is recorded again —
the report gains a step it had lost to `recorded=False` hiding), I5
(one review, one commit).

Two derived rules the walk forces into words:

- **Review sees what was captured.** The draft exposes the streams the
  run kept; an uncaptured (`capture=False`) run reviews code alone. Not
  a limitation to fix — a consequence of what a record is.
- **A raising reviewer fails the item with the hook's error** — the
  same shape as every other hook error (the enter-hook precedent).

### Walk 3: dry-run

The walk starts by falsifying a ledger row I wrote: "dry-run skips
observed steps" is wrong twice over. First, mechanically: a `with`
block's body cannot be skipped by its context manager (PEP 377 was
rejected); no CM can fake its block. Second, semantically: dry-run has
NEVER skipped body code — task bodies run under `--dry-run` today; it
is `run()` payloads that fake (including `run(callable)`, whose dry
branch returns before the callable executes).

The correct rule falls straight out of the ladder:

- **Dry-run fakes what footman owns the execution of** — subprocess
  payloads, callables handed to `run()`, deferred `@step` makers,
  generator steps (never pumped). Their records are declared-not-
  executed (I3's dry-run state), titles from the maker/entry.
- **Inline body code always runs** — lifted by a `with step():` or not.
  The with-form is the observed rung: a record-only lift with no
  execution boundary, so there is nothing for dry-run to fake; its
  inner `run()`s fake individually, exactly as the same statements
  would bare. No regression against today, and the asymmetry is not a
  wart: **dry-run capability is a property of the rung** — fakeable is
  what ownable means at dry-run time.

Consequence for the docs when they come: the with-form's sentence must
say "records the block; does not create an execution boundary" — the
one honest difference from the deferred forms, now load-bearing in two
places (dry-run and deferability).

### Walk 4: `recording()` — and the declaration principle

Payload: a test drives a task through `recording()` (built on dry-run +
quiet) whose body has recorded steps, an off-the-record read, a
reviewer, and body-called sub-tasks.

**First, a correction walk 4 forces onto walk 3.** Shipped doctrine:
off-the-record calls EXECUTE under recording and dry-run ("a value read
is not the story being recorded, and faking it would corrupt the story
that is" — the read feeds the real steps downstream). So walk 3's rule
refines: **dry-run fakes what is owned AND recorded.** Off-the-record
work executes always — it feeds the story, it isn't in it. The faked
set and the recorded-owned set are the same set; that symmetry is the
rule.

**Then the ⚠ rows fall — two at once, to one principle:**

- **`recorded=False` at task grain: refused, and now we know why.** A
  shared answer IS the record reused — sharing mechanically requires a
  record (I6 meets I3). And a declared item is a public commitment: the
  run's rows are its accounting (`--json` completeness, the exit
  summary). So: declared ⟹ recorded, definitionally. The taught error
  for `some_task.opts(recorded=False)`: a task is part of the story by
  declaration — make the read a step, or a helper function.
- **`confirm=` on steps: refused by the same principle.** Confirm (and
  the availability gates, sharing, forwarding) resolve at the REQUEST
  BOUNDARY — before work, answerable as a denied row. A mid-execution
  anonymous confirm has no boundary to resolve at. Split the policy
  table on this line: **boundary policy** (confirm, gates, shared,
  forward) requires declaration; **execution policy** (cwd, env,
  timeout, lanes, capture, recorded) is item-general.

Both walls, and walk 1's identity wall, have one root — promoted to an
invariant:

- **I13 — Declaration is the commitment boundary.** Shareable identity,
  boundary policy, and the guarantee of a record exist exactly where a
  declaration does. "Defaults, not walls" holds everywhere else; the
  walls that remain are all this one wall.

### Walk 5: Ctrl-C mid-run — lanes and the generator

Payload: full abort with children in flight, one generator step
suspended at a yield holding the cwd lane, one running between yields.

The mechanism designed for cancellation IS the answer walked: a
SUSPENDED generator is exactly what `close()` works on — fail-fast
closes it at its yield, `GeneratorExit` unwinds, `finally` releases the
lane (boundary-atomic acquisition means release is one place). A
generator RUNNING between yields is uninterruptible until its next
yield — the cooperative contract's cost, bounded by the longest
inter-yield stretch, documented rather than mechanized. Lanes are never
force-stripped from a live holder: correctness over liveness, same as
today's serial lane. Children reap per the latch exactly as shipped
(and as the 2026-07-31 flake taught us to test). The ⚠ resolves into
three sentences for the lanes design, no new mechanism.

### Walk 6: a horizon sketch (mini-ETL)

`fetch → parse → aggregate` as declared tasks with typed returns; two
consumers of `parse` in one run. Walked: I6-with-arguments already
gives run-scoped memoization (both consumers share the one `parse`
execution — the model is dataflow-shaped WITHIN a run today); placement
reads off the ladder (fetch's subprocess spec shippable; an in-process
aggregate local-only); nothing in I1–I13 blocks the horizon. What the
walk confirms as the two rendezvous, both already noted: durable
(cross-run) identity — the cache — and serializable typed returns (the
parked structured-results thread) for placement-ready hand-off. The
horizon needs those two threads, not a different model.

**Move 2 complete: every ⚠ resolved or promoted-conditional-on-1.**

## What move 2 does with this

The six payload walks (djlint gate; footman's own `check`; dry-run;
`recording()`; Ctrl-C mid-run; one horizon dataflow case) run against
these invariants specifically to attack the ⚠ rows: each walk must
either turn a ⚠ into *derives* (with the derivation written down) or
sharpen it into a named open decision. No walk, no promotion.

## Move 3 — the loom (2026-07-31)

The skeleton exists and the four-checker gate weaves it:
`tests/typecheck_workitem.py` (the stub-only spec surface plus the
walks retyped as consumer exercises; self-contained, imports nothing
from footman, never executed; in ty's and pyrefly's scope by name, the
`typecheck_api.py` pattern) and `tests/typecheck_workitem_negative.py`
(the taught errors the skeleton makes structural, each line a policed
`type: ignore` — the ignore is the assertion; mypy + basedpyright only,
the `typecheck_api_negative.py` pattern). `fm check` green with both
wired in.

What the types forced — the findings, numbered for the record:

1. **`ok` must derive from `code`.** Stored, `code = 1` with
   `ok = True` is spellable; as a read-only property the verdict
   follows the code by construction (I2 typed). Consequence for walk
   2's hook: a reviewer writes `view.code`, never `view.ok` — the
   negative file pins the write as an error.
2. **The phase gate went static after all — Willem's counter, ruled
   same day.** First drafted as runtime-only ("one nominal type cannot
   carry per-object phase"). The counter: the one-view ruling is one
   type across GRAINS, never across PHASES — and the phase axis
   already has two types. Ruled: `post_task` is purely read-only
   observability; an observer holds the immutable `Result`, so
   "observers see, never judge" is unspellable, not enforced (the
   negative file pins the write). Three consequences, walked before
   the ruling:
   - **`set_returned` loses its observer-phase home.** The audit
     killed the counter-case: the "global redaction plugin" was never
     sound as an observer write — it rewrote `returned` while the same
     secret sat in captured stdout, in receipts, in what dependents
     and `recording()` already held (the pristine value is handed over
     before any hook fires). Contract-aware shaping of a reported
     value is per-maker review-window work (`pre_record`, and
     `set_returned` on the draft); contract-free scrubbing is
     emission-time display policy over committed records (decision 4's
     lane) — the sound version of what the observer write only
     pretended to do. Decision 2's observer-writable residue: settled,
     none.
   - **A raising observer is an error like any other — the grain
     fails.** Ruled with a generalisation: EVERY failure records where
     in the grain's lifecycle it happened — `failed_at`: bind / enter
     / body / review / observe — subsuming walk 2's "a raising
     reviewer fails the item" and the enter-hook precedent as
     instances of one rule. The machinery tags the moment; no hook
     rewrites a verdict. Commit therefore stays after observation (the
     machinery can still fail the grain at the observe moment) — the
     static gate never needed commit-first, only the type split.
   - **Shared rows unify.** Every observer event holds an immutable
     record; the first-request/shared-request asymmetry disappears.
   - **`fail()` in an observer is the veto, and needs no special case
     (ruled follow-up, same day).** It rides the error channel — loud,
     attributed: the grain fails at "observe" with the hook's reason —
     while forging (rewriting title/code/returned as the work's own
     words) stays unspellable. The line: observers may veto, never
     forge; that is what "never judge" always meant. Two consequences,
     both clean: I2 decides the committed int (a green-work-vetoed
     grain reads as the failure code — a Result reading 0 on a failed
     row would lie to `if result:`; the work-was-green story is
     display over `failed_at` + the reason, no second code field
     [superseded same day, move-4 follow-up: Willem wants the work's
     own code VISIBLE — the record keeps it, provisional spelling
     `work_code`; see I5]), and
     shared rows get a coherent late veto (observer events fire per
     request — on a re-request the execution's record is long
     committed, so the veto lands on the requesting reference row's
     observe moment, which under the old writable view had nowhere
     sound to go).

   Typed: `Result` is all read-only properties plus `failed_at:
   Phase | None`; `ObserverHook` holds one; I5 amended above; breaking
   for the shipped `post_task` surface (`set_returned` moves into the
   review window), CHANGELOG-visible when built.
3. **`step(fn)` is always the maker.** Decorator position and
   expression position are the same expression — Python cannot tell
   them apart — so both return the lifted `StepFn`, never a built item.
   Decision 8's cheap spelling therefore hands `parallel()` a *maker*,
   and `parallel()`'s payload union must say so: (work item | step
   maker | task ref). Bonus: the ban is structural for free — both
   maker protocols demand `.opts`, which a bare lambda lacks, so
   `parallel(lambda: 0)` already fails overload resolution.
4. **I13's `recorded` carve-out** (amended in the invariant above): the
   execution-policy list said item-general; walk 4 said declared ⟹
   recorded. As prose both read fine; as types one keyword cannot be in
   `TaskOpts` and refused there too. Resolved by omission — `StepOpts`
   carries `recorded`, `TaskOpts` does not — and the invariant text now
   carries the carve-out.
5. **The yield contract is statically enforceable.** `StepBody =
   Generator[None, ResultView, R]`: the `None` yield-type makes
   yielding a value a *type error*, `result = yield` types as the view,
   and bare `yield` checks. I7's taught error is structural.
6. **Decision 2's typing residue is answered.** `pre_record(hook)`
   types as the identity `Callable[[F], F]` — exactly the gates'
   shape — so it reads order-free above or below the lifter and one
   spelling covers a plain function, a `StepFn`, and a `TaskFn`. The
   exercises pin both stacking orders on `@step` and the `@task` form.
7. **Address keeps I11's projections cheap by itself.** An address
   encodes its own parent chain (parent-path + label + ordinal), so a
   flat creation-order list of records derives the report tree with no
   extra storage — whichever container decision 1 picks, the other
   projection is a fold over addresses. Typed as `Address.parent:
   Address | None`.
8. **`Result(int)` does walk 1's compatibility work.** `parallel()`
   returning `list[Result]` and `Fanout(list[Result])` keep every
   code-reader working because the record IS its code — I2 is what
   makes the return-shape promotion non-breaking.
9. **The build/run asymmetry is now stated in types.** `StepFn.__call__
   → WorkItem[R]` (builds); `TaskFn.__call__ → R` (a body call is a
   request that runs). The generator-call footgun's mitigation — "the
   expression's static type says work-item-not-result" — is verified,
   `assert_type`-pinned.
10. **I13 as two TypedDicts.** `ExecutionOpts` (cwd, env, timeout,
    lanes, capture) / `BoundaryOpts` (confirm, shared, keep_going) —
    `StepOpts` extends the former (+ `recorded`, `pre_record`),
    `TaskOpts` composes both (+ `pre_record`). The policy-table split
    from walk 4 is now a pair of keyword surfaces, and `confirm=` on a
    step is a type error the negative file pins.

Drift note, on purpose: the skeleton restates shipped shapes
(`TaskFn`, run's `Result`) rather than importing them, so it CAN drift
from `src/` — that is the point pre-build (the spec must be free to
lead the code), and the files' docstrings say which shapes are
restatements. When the build lands, each restated stub either becomes
the real import or dies; a skeleton line the build contradicts is a
decision to surface, not silently reconcile.

What the loom deliberately did not weave: identity/dedup (I6 is a
runtime key, not a signature), lanes beyond their declaration surface,
dry-run/`recording()` (no new static surface), and everything riding
decision 1's container shape beyond what finding 7 dissolves.

## Move 4 — the adversarial pass (2026-07-31)

Five fixed personas, run against the spec as written (I1–I13 plus the
loom's typed surface), not against the chat. Every attack either
bounced off a named invariant (a confirmation), sharpened an open
decision, or opened a new one. The ledger rows above marked "(move 4)"
carry the promotions; the sharpenings landed on the open-decisions
list in the thinking record (decisions 1, 9, 10).

### hse-the-abuser: the next forgeable primitive

1. **The empty with-block survives as the residual forgery spelling**
   (`with step("deployed"): pass`) — and is undetectable by
   construction: no context manager can veto or inspect its block
   (PEP 377 again), and "a block of real work" includes the empty
   block. The defense is not detection but economics plus
   attribution: the 2026-07-30 forgery existed because
   titling-after-the-fact HAD no honest spelling; now the honest line
   (`pre_record`) is strictly cheaper than the lie, and the lie sits
   in reviewable code carrying a real (suspicious ~0s) duration. I3's
   role restated honestly: it removes the *reasons* to forge and the
   blessed spellings of forgery — it cannot remove all rope. Same
   answer for the reconstruction attack (`recorded=False` on the real
   work + an empty titled block): three dishonest lines against one
   honest one. Corrected by ruling (Willem, same day): this is not
   even NEW rope — an empty `@task` body with no `pre=`/`post=` has
   always minted a named green row; the with-form is the same
   spelling at step grain, and named work that does nothing was never
   footman's to police. The attack demoted from accepted-residue to
   non-issue.
2. **The greenwash hole — real, and verified in source.** The
   executor honours `Failed.code` verbatim (`except Failed: return
   exc.code, …`), so `fail("looks fine", code=0)` from a post_task
   observer would fail a grain *green* — the veto asymmetry
   ("observers can only make things worse, never better") would be
   false as spelled. Closed in I5, widened by ruling (same day):
   `fail(code=0)` is a taught error EVERYWHERE, body included — fail
   is the failure verb, the pass-branch spelling was rejected once
   already in the task-failure design, and the executor's verbatim
   honouring was drift, not intent. One rule, uniform; a shipped-
   behaviour change, CHANGELOG-visible when built. And the same
   ruling answers what the failure keeps: a grain failed by a
   post-work moment preserves the code it carried when that moment
   began (`work_code`, name provisional) — the vetoed green's 0 is on
   the record, not reconstructed from the reason.
3. **Greenwashing via `pre_record` itself** (attach `view.code = 0`
   reviewers everywhere) is sanctioned per-maker interpretation — that
   is literally the djlint walk — and the defense is attribution. But
   the record currently would not SAY it was amended: nothing on
   today's row names a hook (verified: `TaskResult` has no such
   field), so a reader cannot tell a reviewed green from a native
   green. Opened as decision 10 (review provenance).
4. **Lane hostage via GeneratorExit** bounced off I8: a generator that
   swallows the close and yields again is Python's own RuntimeError,
   the item fails, and release is machinery-owned at the boundary —
   the abuse cannot hold what it never owned.

### The report reader

All three findings sharpen decision 1 (appended there): a lookup
contract under I6's same-label multiplicity (by name → a list, by
address → unique); the `state` × `failed_at` pair codified as two
axes with one word each (the shipped `state` docstring's own rule,
extended); and reference-row accounting (duration and steps live on
the execution row, references link — aggregation must never
double-count a shared subtree). One confirmation: a veto row reads
fully from code ≠ 0 + `failed_at="observe"` + the reason; work-was-
green display is decision 4's, as ruled.

### The distributed future

One amendment, folded into the address definition: ordinals count
same-labelled siblings in request order AS WRITTEN, never completion
order — cross-run matching (duration history) and any future
placement need addresses deterministic across runs and hosts, and a
parent's requests come from its own control flow, so written order is
well-defined even under the pool. The rest bounced: unkeyable-means-
unique composes with declared-opt-in cacheability (Bazel lesson 1);
generator items are local-only by the ladder's placement reading,
already stated.

### The completion hot path

Clean pass, no findings: every move-3 surface is execution-side.
Anonymous steps never enter the manifest (I9), runtime lifting grants
report labels never CLI addresses, `Phase`/`failed_at` are report
vocabulary, and the ban changes runtime coercion, not CLI grammar.
The TAB path gains zero imports and zero schema changes.

### Windows

Clean pass: observation happens on the runner after children are
reaped, so the phase-gate ruling never meets taskkill/killpg
semantics; walk 5's kill discipline is untouched; `timeout` keeps its
124 convention inside `failed_at="body"`; addresses are made of
labels, not paths, so no separator or drive-letter semantics leak in.

**Move 4 complete. New opens: decision 9 (reviewer composition),
decision 10 (review provenance). Everything else bounced or
sharpened decision 1.**

## Move 5 — decisions (started 2026-07-31)

Called in the agreed dependency order, report shape first — decision
1's brief is below, awaiting the call.

### Decision 9 — reviewer composition: RULED

Chained draft, bottom-up: the reviewer written closest to the `def`
runs first, each further-out reviewer sees the accumulated edits, and
`.opts(pre_record=…)` runs last. Uniform wherever the hooks sit
relative to the lifter (attachment stays order-free; execution order
is always bottom-up). The docs phrasing, per Willem's ask — carry
this wording to the docs page when it comes:

> Reviewers run from the inside out: the hook written closest to your
> function sees the draft first, and each one above it sees what the
> previous reviewers left. A per-use `.opts(pre_record=…)` runs last —
> the use site gets the final word. (This is the same order decorators
> themselves apply: bottom to top.)

### Decision 10 — review provenance: RULED as the audit

Willem's generalisation and his name (2026-07-31): not a reviewer
list — the record carries every lifecycle moment involved with the
verdict, as (moment, actor, code-or-None) entries in execution order.
Full shape in the definitions ("the audit"). Pinned rules: the
body/capture entry always enters with the work's own raw code; a
failing moment always enters, declared or not; quiet undeclared
moments are skipped; involvement without a verdict write records code
None and nothing more (verdict-scope only — no title-diff log). One
structure subsumes three fields: `failed_at` and the pre-failure code
(`work_code`, whose naming question dissolves) become derived
readings. A bonus that fell out: the audit's execution order IS
decision 9's order, documented by the data itself.

### The decision-1 brief (the shadow-emitter spike, 2026-07-31)

Method: the real `fm --json check` payload (schema 1, green run,
22.8s), projected into the model's item set — foreign defs lifted per
walk 1, fan-outs as anonymous grouping items — with parentage
hand-threaded from tasks.py, because the runtime records no requester
identity (gap 1 below). Both candidate shapes built from the same
items; every query run against both.

**Exhibit 1 — today's 5 rows are 24 items.** The real structure the
flat report hides: `covered`'s 22.8s pytest run rides `check`'s row
unnamed; the six checker runs are anonymous command strings on
`typecheck` (their `__name__` labels reach the live line but are LOST
in `--json`); the nesting exists only as pre-rendered text inside
`check`'s `output` field. Abridged, as the model records it:

    check                                  22.8s  [task]
      (fanout)
        format → ruff format . --check      0.1s
        lint   → ruff check .               0.1s
        typecheck                           5.5s  [task]
          (fanout)
            basedpyright → basedpyright…    5.5s  [lifted step]
            mypy_linux / _darwin / _win32   0.6s ×3
            ty / pyrefly                    0.3s / 0.5s
        typecomplete → verifytypes          1.1s
        covered → pytest --cov …           22.8s  [lifted step]

**Exhibit 2 — the queries, measured.** Identical answers from both
shapes; the difference is reader ergonomics:

| question | flat + address | nested tree |
| --- | --- | --- |
| did lint pass | one `select(.address == …)` | recursive descent (`.. \| objects`) |
| all failures | one `select(.ok \| not)` | recursive descent |
| typecheck subtree wall-time | address PREFIX match (prefix = subtree, free) | two-stage recursion (find node, then descend) |
| render the human tree | ~10 lines, group by parent (finding 7, now run) | native |

**Exhibit 3 — a failing item under the model** (simulated fields; the
green payload has no failures to show). The hse djlint step: raw exit
1, reviewed green, then vetoed at observe by a duration budget:

    { "address": "check/fanout/djlint",
      "label": "djlint",
      "code": 1, "ok": false,
      "title": "djlint: reformatted",
      "state": "failed",
      "failed_at": "observe",         // derived
      "work_code": 0,                 // derived
      "audit": [
        ["body",    "djlint reformat …", 1],
        ["review",  "dj_outcome",        0],
        ["observe", "budget",            1]
      ] }

One field tells the whole story — raw 1, reviewed 0, vetoed 1 — each
attributed; the derived readings answer the common questions without
scanning.

**The gaps today's runtime must close, whatever shape is chosen:**
requester identity (rows do not know who asked for them); report
labels for lifted work (the `__name__` hack reaches the live line
only); cross-grain creation order (steps carry no start times relative
to child rows); the fan-out grouping item (invisible today).

**Recommendation: flat creation-order list, parent by address.** Every
reader query is a one-line select; address prefix gives subtrees for
free; the human tree derives in ~10 lines (finding 7 confirmed by
running it); the machine surface stays simple while receipts own the
human rendering; and the information content is identical — measured,
not asserted. The tree candidate's only win is native nesting, which
is a renderer's need, not a reader's.

### Decision 1 — the report shape: RULED (2026-07-31)

Flat creation-order list of items, parent by address — the brief's
recommendation, called by Willem with one amendment: **the schema
field stays 1.** Nobody consumes `--json` yet (the cost-sign
inversion the thinking record noted), so the new shape replaces the
old under the same number — footman launches with it rather than
migrating to it. The reader contract rides the call as briefed:
name-lookup multiplicity (by name → a list, by address → unique); the
failure story is `state` plus the audit (move 4's `state` ×
`failed_at` pair collapses into this); reference-row accounting
(execution rows own duration and steps, references link, aggregation
never double-counts). The ledger rows conditional on this call
promote: `parallel()` returns Results, and the fan-out grouping item
has a place to render.

**Streaming corollary (Willem's, parked at the post-1.0 horizon):**
the flat list is jsonl-shaped — one committed item per line, appended
as commits happen. This is the FILE FORM of the record-stream export
the observer opinion already anticipated ("presentation over
committed records; stream consumers cannot judge by construction"):
only committed records stream, so I5 holds structurally, and liveness
stays the Status lane. Two properties fall out free: a child's line
is self-describing before its parent exists in the stream (parents
commit AFTER their children, and the address carries parentage as a
string — no parent-id join), and the batch file stays
creation-ordered while the stream arrives in commit order (lines
carry `started`; consumers re-sort if they care). Nothing designed
now beyond not foreclosing it — which the flat shape just did.

### Decisions 4, 5, 6, 8 — ruled (2026-07-31, one sitting)

- **8, the bare-callable ban: GO** ("hell yeah"). I12 stops being
  conditional; the two conditional ledger rows promote; the loom's
  typed refusal (no `.opts` on a lambda → no overload match) is the
  enforcement, already in place. Migration is mechanical: `step(fn)`
  around every thunk, each gaining a receipt.
- **6: dissolved into the build.** The model itself killed the old
  tension (`recorded=` is execution policy, not a grain change; the
  boundary rule IS I13, already typed). The `step=` → `recorded=`
  rename rides the build wave, no deprecation shim; CHANGELOG carries
  it.
- **4: split as proposed, with the default ruled.** At normal
  verbosity the report shows TASK grain only; verbose adds steps.
  The full display thread (verbosity matrix, provenance markers,
  emission-time redaction) parks as its own post-model thread.
  And confirmed (2026-07-31): a FAILED task auto-expands its failing
  step and audit line at normal verbosity — green is collapsible,
  failure is never hidden.
- **5: dissolved — hse is unblocked and moving.** Willem has been
  developing hse alongside this whole thread; it pins its current
  footman until the model ships, then migrates once (target 0.28.0).
  No interim wave, no staged migration release; staging collapses to
  "ship when coherent."

### Decision 7 — partially ruled; the generator-pump spike runs next

Ruled in the same sitting: **env needs no lane** (the router
virtualises it completely); **cwd is a lane, opt-in** — claiming it
is knowingly giving up parallelism, and the spelling must make that
legible; **console is a lane and not optional** — the terminal is one
resource, every claimant queues; **no further core lanes, ever** —
everything else is plugin-defined, so the lane mechanism is a plugin
surface and footman's own two lanes are built on it (dogfooding the
registration path). The fourth process global, stdin, dissolves
rather than lanes: the question layer resolves at the request
boundary, and an interactive body's terminal access rides the console
claim (process-globals v2's resolution, unchanged). argv was never a
contended global — read once at CLI parse.

Remaining before the call: the generator-pump prototype (walk 5's
cancellation claims — close() at a yield must release lanes through
the machinery's boundary ownership) and the plugin-lane registration
spelling (declaration-required so a typo is a taught refusal, not a
silently new lane).

### The generator-pump spike (2026-07-31): walk 5 confirmed in code

A working pump (scratch prototype: boundary-atomic lane acquire, a
`send(view)` drive loop, release in the PUMP's finally) ran the four
claims:

- **Cancel at a checkpoint works exactly as walked**: close() at the
  suspended yield → GeneratorExit → the generator's own try/finally
  ran → the pump's finally released the lane. Measured latency 36ms
  against a 50ms inter-yield stretch — the bound is real and is the
  stretch, as documented.
- **"Between yields is uninterruptible" is CPython's law, not just
  our doctrine**: close() from another thread while the frame is
  executing raises `ValueError: generator already executing`. There
  is no design choice to make — cancellation is pump-mediated
  (decline to resume, or close at the next yield) because Python
  refuses anything else.
- **The hostage generator cannot hold a lane**: a body that swallows
  GeneratorExit and yields again gets `RuntimeError: generator
  ignored GeneratorExit`; the item fails; the lane released anyway,
  because release is pump-owned. The move-4 ledger row, now run.
- **The `view = yield` protocol pumps cleanly** — send-driven, bare
  yields as checkpoints, no vocabulary needed beyond what wrap_task
  shipped.

No walk-5 sentence needs amending: the mechanism is exactly as
specified. Pinned while writing the design page (2026-07-31):
cancellation reaches a generator step only at a checkpoint, and for
exactly three reasons — fail-fast abort, full abort (Ctrl-C), and the
step's own `timeout=` (the timeout refusal's honest exception: at
checkpoints, enforcement is declining to resume). One cancellation
story, worst-case latency one inter-yield stretch. Decision 7 now
waits on one artifact only — the plugin-lane registration spelling.

### Decision 7 — the registration brief (2026-07-31, the last artifact)

**Recommendation: declaration is a Python binding — the import system
IS the registry.**

    # in a plugin module, or plain tasks.py — same mechanism, no
    # plugin-only privilege:
    db = footman.lane("database", reason="serialises the shared dev DB")

    @task(lanes=(db,))
    def migrate(): ...

- **`lanes=` takes Lane HANDLES, never strings.** A misspelt lane is
  an undefined Python name — `NameError`, the interpreter's own taught
  refusal — because there is no string lookup to typo. The ruled
  requirement ("a typo must not mint a silent new lane") is satisfied
  by construction, with zero registry machinery. The loom anticipated
  this shape: `lane(name) -> Lane`, `lanes: tuple[Lane, ...]` in
  ExecutionOpts, already four-checker green.
- **Sharing is importing.** Two modules serialise on one resource by
  importing one handle. Deliberate, visible in the import list, and
  refactorable like any other name.
- **Name collision is a taught refusal with provenance.** Declaring
  `lane("database")` at two sites is an error naming both files (the
  plugin-collision precedent) — the display name must stay unambiguous
  because the live line will say "waiting on database". Reuse is
  spelled by importing, never by re-declaring.
- **Core dogfoods the same call.** footman declares its own two lanes
  with the identical mechanism and exports the handles: cwd (opt-in —
  claiming it is knowingly giving up parallelism, and an import plus
  an explicit `lanes=` makes that legible at the use site) and console
  (claimed implicitly by `interactive=`, claimable explicitly).
  Exported names settled (2026-07-31): **`cwd_lane`** and
  **`console_lane`** — the `_lane` suffix is the warning label at the
  import site, and bare `footman.cwd` was taken by the context
  manager.
- **Claims: one holder at a time per lane in v1** — "exclusive" in
  the narrow sense only: a claim contends solely with other claimants
  of THAT lane; unrelated work runs in parallel untouched (nothing
  drains — that is `exclusive=`, the arbiter's separate regime, and
  it is not this). The split exists to RAISE parallelism over today's
  single serial lane, never to lower it. Claims are boundary-atomic
  (I8), spelled in execution policy everywhere the loom already puts
  them (`@task`, `@step`, `.opts()`, tool opts). Counted capacity
  (`lane("db", capacity=10)`) is a purely additive keyword later —
  deferred (ruled 2026-07-31) until a real payload wants it, since
  the two core lanes are physically capacity-1 and the counted
  questions (fairness, k-slot claims, slot display) deserve a driving
  user.
- **`serial=` becomes sugar** for claiming both core lanes;
  `exclusive=` stays the arbiter's full drain (process-globals v2),
  not a lane. `reason=` is optional documentation on the declaration.
- **Waits render** through the timing fields the report already has
  (`eligible`/`started` — a lane wait is launch latency) plus a live
  line state ("waiting on database"); the exact rendering belongs to
  the parked display thread.
- **Horizon note:** a Lane's name is what a cluster-level token later
  generalises from — a shippable spec can carry the name where a
  handle cannot travel; nothing else designed now.

### Decision 7 — CALLED (2026-07-31)

Ruled on the brief as amended (one-holder wording; capacity
deferred). Willem's specific reason, recorded because it is a design
property every future lane feature must keep true: **the handle
mechanism cleanly extends to multiple resources of the same type** —
two databases are two bindings (`db_main = lane("db-main")`,
`db_replica = lane("db-replica")`), so "instances" need no
type/instance machinery, no lane classes, no parameterisation. A
resource IS a binding; keep it that flat.

### Ruling: per-task hooks live on the handle (2026-07-31)

Found by the contract page's first reader doing exactly what the page
invites: Willem read the observer section, expected `post_task` to be
per-task, and rejected the global-with-if-chains shape outright — "I'd
rather the code lives local to the task." The design's own principles
agreed on inspection: knowledge lives where it belongs, attachment is
the dispatch, and observation being global-only was an unargued
asymmetry (review was already per-maker). Ruled:

- **The task handle exposes the exact mirror of the per-task
  lifecycle set** — `@build.pre_bind`, `@build.pre_task`,
  `@build.post_task`, the `wrap_task`/`wrap_bind` sugar — alongside
  the already-ruled `@build.pre_record`. Bare decorator spelling, no
  parentheses (no parameters foreseeable).
- **Handle attachment is permanent** — it changes what the task does
  for every requester (the `set_opts` family); `.opts(...)` remains
  the per-use tier. Hooks get the same two-tier story as options.
- **Decision 9 generalises**: distance from the `def` decides —
  stacked hooks inside-out first, handle attachments in the order
  they were made, `.opts` always last. Cross-file attachment order is
  registration order: deterministic every run, invisible at either
  site (plugin-order's known property); when order between two far
  attachments matters, one attachment site composing two functions is
  the honest fix.
- **The plugin lane survives, narrowly, with a one-sentence test:** a
  hook with no task knowledge in it (a tracing exporter, the timing
  collector, a CI annotator) registers globally from a plugin; the
  moment a global hook would say "if this is task X", it belongs on
  X. User task rules never need the global lane.
- **Step handles carry their two moments** (extended same day, after
  the follow-up challenge): **`pre_record` and `post_step`**. The
  whole pre side — `pre_bind`, `pre_task`, and the wrap sugar over
  that pair — belongs to the request pipeline only tasks traverse;
  the spec's own event order already said so (steps: execute → review
  → commit). The missing enter hook costs nothing: a step's body is
  always code you hold, so setup is its first line — `pre_task` earns
  its keep on tasks because outsiders attach to bodies they don't
  own, and a step has no outside callers to serve. The
  no-step-grain-observer ruling stands untouched against what it
  actually refused — the GLOBAL step stream — because its reasons
  dissolve for handle attachment: the attacher owns the context, and
  the amendment-window worry died with the static phase gate. Naming
  ruled `post_step`: the family grammar anchors pre/post to the
  thing's own noun (how `pre_record` got its name); the tombstoned
  first minting (briefly the review hook, never shipped) gets a
  second line, not a veto. And steps stay unique per mention, so
  per-request and per-execution are the same event — no shared-row
  cardinality question exists at step grain.
- **Signatures**: the per-task form drops what the attachment already
  answers — the task is the handle's own — so the loom types
  `post_task` as receiving the sealed `Result` and `pre_record` the
  draft view; the global plugin forms keep their shipped
  `(inv, task, …)` shape. (Convergence note, for the record: the loom
  had already typed the observer hook as `(result)` alone — it never
  fit the global signature, and it fits this one exactly.)

Zero invariants move: sealed stays sealed, veto-never-forge stands,
and the audit's actor slots fill better — the attributed actor is the
rule itself, never a switchboard function routing everyone's rules.

**Move 5 is complete. Every open decision of the work-item spec is
closed** — 1 through 10: ruled, dissolved, or confirmed. What remains
for the thread: the build (target 0.28.0; hse migrates on it) and the
parked post-model threads (the display-policy thread; jsonl streaming
at the horizon; the output marker, parked with the cacheable-rung
thread after the 2026-07-31 audit found nothing in 0.28.0 needs it —
name front-runner `product`, final naming when that thread opens).
The core lane handles are named: `cwd_lane`, `console_lane`.

## The build (2026-08-01): all eight stages shipped

One session, PRs #247–#260 plus the sweep: A the honest verbs, B the
record surface, C sealed observation + handle hooks, D the substrate
and the ban, E identity (the split, addresses), F the report (Results,
task-grain display, the flat items envelope), G lanes, H this sweep.
The design page's fragments are down to three, each environmental or
deliberately unrunnable (the refusal demo raises; the git read and the
djlint reviewer need their tools); everything else on the page runs in
the page session on every gate.

**Reconciliations the loom surfaced, accepted rather than silently
absorbed:** the spec's `Address` OBJECT shipped as a plain string — a
tree-derived name needs nothing richer than prefix selection — and
lifecycle moments travel as strings at runtime, the `Literal` set
remaining the documented vocabulary. **Contradictions the
reconciliation caught and the build then closed:** step observers were
handed a mutable record (the page's sealed-by-type claim) — `Result`
now refuses writes after construction; and the pump accepted yielded
values silently where I7 demands a taught error — it teaches now.
`typecheck_workitem.py` types every exhibit against the real surface;
its negative twin pins what stayed structural.

**hse's migration, when it takes 0.28.0:** delete `run_titled`, its
twin, both forged-receipt lambdas and the cwd workaround
(`djlint.opts(pre_record=…)` is the whole replacement); `step=False` →
`recorded=False`; any `fail(code=0)` → `return`; thunks in `parallel()`
→ `step(fn)(…)` items or the block form; read `--json` as `items`.

## Strays found along the way (housekeeping commit, not the model)

- `_futures._fill` is dead code (defined, zero call sites — superseded
  by claim-side registration).
- `_futures` module docstring drift: claims an unshared request "still
  fills an empty cell"; `run_bound` passes `work=None` for unshared, so
  it neither reads nor fills.

## Links

- Thinking record: [20260731-execution-model-record.md](20260731-execution-model-record.md)
- Process-globals v2 (lanes' parent): 20260725-process-globals.md
- The saga's origin: the hse receipt request, 2026-07-30 (recorded in
  the thinking record's causal chain).
