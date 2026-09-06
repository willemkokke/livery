# The execution model — the thinking record

**Status: CLOSED — every open decision was called on 2026-07-31, and the
model it describes shipped in 0.28.0 (2026-08-01).** The build that
implemented it is [20260731-execution-model-build.md](20260731-execution-model-build.md),
the called spec is [20260731-execution-model-spec.md](20260731-execution-model-spec.md).

The body below is unchanged, and stays as it was written: a thread being
thought through live, with the reasoning, the probes, the rejected turns
and the horizons. Its "Open decisions" section is a record of ten
decisions *and their dispositions* — each carries the RULED / CLOSED /
DISSOLVED verdict it received, ending "MOVE 5 COMPLETE; every open
decision closed". Read the marks, not the section title.

Tasks and steps are two ontologies where one may do. This note holds the
evidence that the split has decayed, the model that would replace it, what
must survive any unification, and what it costs. It exists because we kept
arriving at the same seam from different directions and patching it one
request at a time — and it is the first note of a longer arc: the same
DAG is intended, eventually, to carry data processing (see "The
horizon").

## Design stance: limitations are a legitimate purchase (2026-07-31)

Ruled as a stance: imposing limitations is acceptable — welcome — where
it buys consistent DX. The standing candidate: **no bare callables at
footman's boundaries at all.** Every piece of work handed over
(`parallel()`, its block, `run()`) must already have a chosen grain — a
task reference, or a lifted/observed item. House precedent exists:
`suggest()` already refuses a bare callable inside `Annotated`, for the
same reason (a wrapper states intent; a bare callable is ambiguity).

What the ban would buy: the *foreign* rung becomes body-internal only —
footman never holds code of unknown grain, so `parallel()`'s coercion
rules die (it takes work items, full stop — the acceptance test at full
strength), `p.also` dies with them, and `run(callable)` retires in
favour of the block/generator spelling — taking the entire
foreign-cwd-refusal class of taught errors with it *by construction*
(the guard existed because footman executed code it couldn't vouch for;
under the ban it never does).

What it costs: a wrapper's worth of ceremony on quick one-liners
(`parallel(lambda: rmtree(tmp))` becomes a lifted spelling — which gains
a receipt, so the tax buys a record), and migration for every thunk in
the wild. Pre-1.0 window applies.

Status: the stance is ruled; the specific ban is open decision 8.

## The concept register (started 2026-07-31 — rule on the words)

Three tiers, because a concept may need three names — or better, may
surface only as behaviour: **docs** (the average user reads it; the
plain-words rule applies with full force), **API** (visible in code and
signatures; power users meet it), **notes** (design vocabulary; never
leaves this directory). Status: *settled* (ruled or long-shipped),
*provisional* (used here, awaiting a ruling), *unnamed* (the concept is
agreed, its public name is not).

| term | one-line meaning | tier | status |
| --- | --- | --- | --- |
| task | named, declared, CLI-addressable work; dedups by declaration | docs | settled |
| step | one recorded piece of a task's work | docs | settled |
| receipt | the step's reported line: title, verdict, duration | docs | settled (shipped phrasing) |
| report | everything a run tells you after: rows, steps, `--json` | docs | settled |
| off the record | a managed-but-unrecorded execution: how a task learns something, not something it did | docs | **settled 2026-07-31**: the phrase, and `recorded=False` as the parameter (pairs with `recording()`; `tracked=` rejected — collides with `track()`). `step=False` is the legacy spelling it replaces; migration timing rides decision 6 |
| (just code) | unlifted body statements — deliberately NOT a named category; "code you don't lift is just code" | docs | settled as a non-name |
| (resource) lane | a serialised claim on a named resource — process globals (serial, console, cwd…) AND, kept open by ruling, custom user-declared resources ("the database", "the GPU") | docs/API | settled 2026-07-31; custom resources are deliberate design space, not scope creep |
| work item | the one substrate under tasks and steps | notes | settled as note-word; ruled likely never a docs word |
| grain / default bundle | which property-defaults a work item carries (task-shaped, step-shaped) | notes | settled 2026-07-31 — notes-tier ONLY; appearing in docs or an error message is a bug |
| the ladder | foreign → observed → owned | notes | settled by rulings |
| ownership | footman controls `__call__`: can queue, dedup, schedule, hook | notes | settled (= schedulability) |
| observed | foreign code with a handle and a record (the middle rung) | notes | settled 2026-07-31 as the rung's only name; "span" retired everywhere, notes included |
| lifting / lifting operators | wrappers that change an item's grain at the use site | notes; surfaces as plain function names | settled 2026-07-31 (notes-tier) |
| the record (family) | what happened vs what's recorded — supersedes the "narration" family (2026-07-31): off the record, `recorded=False`, `recording()`, receipt = the rendered record, adjudication = amending the record before commit | docs/API/notes — one family across all tiers | settled 2026-07-31, fusion rename included |
| ~~narration / narrative~~ | (tombstone) my coinage, second metaphor family for the record concern | — | superseded by the record family |
| ResultView | THE view — one type across grains (Willem, 2026-07-31: no separate Step/Task views): the record's draft, phase-gated writes; committed it is a `Result`. Already shipped (post_task, wrap_bind) — zero new vocabulary | API | settled. Criticism 4 narrows rather than falls: under one substrate, `returned` is None by CIRCUMSTANCE (fine on the shared view); absent-by-KIND surfaces (plugin `state`, CLI binding) stay off it |
| pre_record | review an item's draft before the record commits — the code is final, the verdict is not | API | settled 2026-07-31 (renamed from "post_step", which died grain-neutral — it stacks on `@task` too; `pre_record` stays in the hook family's moment-grammar: pre/post + the anchoring noun). Declared: `@pre_record(fn)` stacked on any maker (the `requires=`→stacked-`@requires` precedent); dynamic: `.opts(pre_record=…)` for def-less attachments and per-use overrides; lifecycle `post_task` stays the plugin-lane global observer. Cardinality (ruled 2026-07-31): fires once per EXECUTION of the item its maker makes — a task-attached hook reviews the row's draft, never the contained steps (children are reviewed only by their own makers' attachments); shared rows: one review, observer events per request. Phase rule: reviewed → observed → committed — ENFORCED (ruled same day): at the observed phase the verdict-bearing fields (code, ok, title) are read-only; post_task observes, never judges. Same one ResultView, phase-gated windows |
| post_step | the step handle's observer: after seal, holds the immutable `Result`, veto-never-forge — `@clean.post_step` | API | settled 2026-07-31, the name's SECOND minting: the first (the review hook) died grain-neutral into `pre_record` and never shipped; reused for the observe attachment per the family grammar (pre/post + the thing's own noun) |
| `step()` | one name, three grammatical positions mirroring `@task`'s own grammar (settled 2026-07-31): `@step` decorating a local plain/generator function (the lifter at definition — the visible mark that calling it builds-not-runs), `with step("title"):` for the immediate inline block (title at entry, so dry-run can skip the body), `step(fn, title=…)` as the expression lift (decision 8's cheap spelling). No second noun for what they make — they make steps ("span" retired: rung leaked as user noun, duration-not-story, tracing's word) | API | settled |
| yield contract | DISSOLVED into existing idioms (settled 2026-07-31, the third non-name): bare `yield` = checkpoint (cancellation window); every yield evaluates to the item's `ResultView` (the `result = yield` idiom `wrap_task` already ships); progress = ordinary `progress()`/`track()` calls; **yielding a value is a taught error** — the set stays closed, the channel reserved for a future that earns it | API | settled — zero new types |
| address | a node's tree-derived name: parent-path + label (+ ordinal) | notes; surfaces in `--json` | settled |
| shareable identity | (declaration, overrides) → dedup; declared nodes only | notes | settled |
| placement | local-only / placeable / shippable — the ladder's horizon reading | notes | settled as constraint |
| generator-shaped | the property of in-process work spelled as a generator: yields = checkpoints, record via the sent-in view | notes; docs say "generator step" | settled 2026-07-31 |
| ~~cooperative~~ | (tombstone) async/multitasking baggage — the neighbourhood the span ruling moved us out of | — | superseded by "generator-shaped" |
| boundary-atomic | all lane claims acquired at the item's boundary, never mid-body | notes (invariant) | settled |
| execution–record fusion (informally: "the weld") | execution and its record fused in `run()`; the model separates the concerns, `run()` stays their default composition | notes | settled 2026-07-31 (renamed same day with the record-family takeover) — descriptive name is the register key; the metaphor is local shorthand only |
| forged receipt | a record unmoored from work (the anti-pattern) | notes | settled as the thing we refuse |
| projection | report tree and dependency DAG as two views of one item set | notes | settled |
| product (output marker) | a Path param whose value names a produced file — path is key-input, content is output (Bazel's duality) | API | concept settled (Bazel side quest); feature PARKED with the cacheable-rung thread (2026-07-31 audit: nothing in the 0.28.0 build needs it). Name front-runner ruled `product` (2026-07-31, over `creates`/`artifact`/`output`/`result`): value-describing like the rest of the marker family, direction built into the word, pairs with "produce" across every surface; `output`/`result` collide with core vocabulary, `artifact` carries no direction. Final naming when the thread opens |
| optional input | a declared input that may be absent — absence is a digestable state, never an eager error | API | open — marker spelling undecided (`Path \| None`? softer marker?) |
| (preserved work code) | the code the grain carried when a later lifecycle moment (review, observe) failed it — a vetoed green keeps its 0 on the record, visible not inferred; None when nothing had concluded | API | DISSOLVED 2026-07-31 into the audit — a derived reading, not a stored field; the naming question is moot |
| audit | the record's verdict provenance: every lifecycle moment that acted on (or failed) the grain, as (moment, actor, code-or-None) entries in execution order; body always enters with the raw code, failures always enter, quiet undeclared moments skipped; verdict-scope only | docs/API | settled 2026-07-31 — Willem's name and generalisation (decision 10) |
| cwd_lane / console_lane | the two exported core lane handles — the only core lanes ever, by ruling | API | settled 2026-07-31: the `_lane` suffix is the warning label at the import site (`from footman import cwd_lane` says what it costs), and bare `footman.cwd` was already taken by the context manager |
| sealed | the docs-tier word for a committed record ("the record is sealed") — notes/API keep "commit"; the design page introduced it because "committed" drags git along for a docs reader | docs | provisional — liked in principle (Willem, 2026-07-31); final ruling once the design page is read in place |
| task-handle hooks | every per-task lifecycle moment attachable on the task's own handle (`@build.pre_task`, `@build.pre_record`, `@build.post_task`, `pre_bind`, the wrap sugar) — bare decorator, permanent (set_opts tier), code local to the task | API | settled 2026-07-31 (the contract page's first finding): exact mirror of the per-task set; the plugin lane keeps only hooks with no task knowledge in them. Extended same day to STEP handles: `pre_record` + `post_step` only — the pre side is the request pipeline, which steps never traverse |

Register rules: a notes-tier term appearing in docs or an error message is
a bug; the two **unnamed** rows block their features (a thing users touch
ships with its real name, not a placeholder); renames are cheap now and
breaking later — flood them here.

## How this thread opened (the causal chain)

1. hse asked for `silent=` (a tool call that reports nothing but returns
   its value). Shipped as `run(step=False)` — the knowledge lane. Correct,
   but built without naming the need underneath.
2. hse bent `step=False`: run the real work silently, then forge a titled
   zero-cost receipt via `run(lambda: 0, title=…)` — because some tools
   (djlint) only reveal what a step *was* in `(code, output)` together,
   after the fact, and a record-decided-after-the-work had no spelling.
3. The forged receipt collided with the 0.27 foreign-cwd refusal for
   in-process callables — record-making borrowing an execution primitive met
   an execution guard. `hse check` broke from subdirectories.
4. Designing the fix (the review hook, then unnamed) surfaced the `step()` context-manager
   idea (record work footman didn't execute — an API call), which
   surfaced the containment question (an observed step holding `run()`s), which
   surfaced the task/step audit, which surfaced `parallel()`'s payload
   taxonomy. Every step up revealed the same seam.

The lesson, stated once: when requests cluster around a seam, model the
lane — don't grant fixes one at a time. `step=False` was a well-made
point-fix to an unexamined need, and everything above is its bill.

## The execution–record fusion (informally: "the weld")

`run()` fuses two concerns:

- **execution** — spawn/call, capture, guard (cwd, env, lanes), time it;
- **the record** — title, verdict, output shown; the receipt is its
  rendered form.

Every incident probes the weld: an off-the-record call is execution
without a record; the forged receipt is a record without execution;
hse's real need was a record *decided after* execution; the `step()`
idea is a record over execution footman didn't perform; the noise itch
is how much gets recorded by default and how records render. None of these are features
of `run()` — they are the two concerns asking to be separately addressable.

## The audit: what the task/step split still provides

1. **Dedup semantics.** Rows share (a prerequisite requested twice runs
   once; the second request reports `shared`); steps never dedup — every
   mention is its own event. The deepest real difference.
2. **Names and the CLI surface.** Tasks are addressable — completion,
   help, chains, dotted paths, `only=`. Steps are anonymous.
3. **Typed binding.** Tasks have signatures, coercion, stdin/ask; steps
   take argv.
4. **Policy grain.** Lanes (serial/exclusive), atomic, interactive,
   confirm, availability gates, forwarding: all row-grain today.
5. **Two-level failure vocabulary.** `skipped` (never ran; something it
   needed failed) exists only at row grain.

## The erosion (all present in today's code)

- **The live status line already runs on one grain.** `parallel()`
  children count as *units* "exactly like scheduler nodes — a chain and a
  task-body fan-out present identically". Display unified first; only the
  report still speaks two languages.
- **Rows are created dynamically.** Body calls route through the futures
  layer into real task requests mid-run. "The DAG is declared, steps are
  discovered" — the original justification — is already false.
- **One `parallel()` call produces both grains**, by payload type:
  `parallel(lint, lambda: run(...))` yields a row and folded steps from
  the same fan-out.
- **`shared=False` already makes a row behave step-like** for identity.
  Dedup is a per-node property in practice, not an ontological one.
- **`run(callable)` and a body-called task are near-twins** — in-process,
  captured, timed, recorded — differing only in name, dedup, and hooks.
- **The record requests re-invent row machinery one level down**:
  the review hook is `post_task`'s twin; a `step()` block is an anonymous
  inline task in all but name.

Tempering the list (self-criticism, kept): each of these generalisations
shipped *cheaply inside the split model* — leaks prove the wall leaks,
not that demolition beats repair. The case for one substrate rests on
which FUTURE work gets cheaper: the record surface, containment, and `parallel()`
hygiene genuinely do; binding, scheduling, completion, and forwarding are
untouched either way. The payoff is real and narrower than this list
reads on its own.

And the decision criterion that governs anyway (Willem, 2026-07-31):
"what we have now is not bad, and can be fixed to be genuinely useful.
I'm just not interested in settling — this is one chance to get
something right that I want to use for the rest of my life." The
effort-vs-payoff ledger above informs sequencing, not the destination:
pre-1.0 is the one window where the model can be chosen for rightness
rather than compatibility, and this project's bar is a tool for decades.
Repair-because-cheaper is settling; the question is only ever which
model is right.

## The ownership insight

The primitive distinction was never task-vs-step. It is:

- **owned work** — footman controls the callable's `__call__` (a `TaskFn`,
  an opted reference, a runnable group), so it can *intercept*: queue,
  dedup, schedule, hook, report;
- **foreign code** — a bare callable or an inline block; footman can only
  execute and observe it.

Names, signatures, dedup identity, and policy all hang off ownership.
`parallel()` is the fossil record: it accepts the whole union, forks
behaviour exactly on ownership (tasks queue; foreign callables run where
they stand), needed a second API (`p.also`) *only because* it cannot hook
foreign calls, and then flattens everything to "units" for display anyway.

The `step()` context manager, in this frame, is a rung, not a full
conversion — **agreed (Willem, 2026-07-31)**. Ownership here means
*schedulability* — intercept, defer, dedup, hook — and an observed step
confers observation (a handle, a record, a place in the report), not
schedulability: footman still cannot move or dedup a block of inline
code. The honest ladder is **foreign → observed → owned
(declared)** — and the horizon section adds its placement reading
(local-only → placeable → shippable). `p.also` retires only if the
middle rung can also be *deferred* to the pool — an execution question
the CM shape has not answered yet, so that claim stays conditional.

## Generator-shaped steps (probe, 2026-07-31 — promising, not ruled)

Willem's probe: make steps local generator functions. Three limitations
fall by construction, with `wrap_task`/`wrap_bind` as house precedent for
generators-as-lifecycle:

1. **Deferability.** A CM-made step executes where it stands; a generator
   function is code footman holds but hasn't run — it can defer it to the
   pool and own its activation. The observed rung becomes schedulable
   without becoming declared, `p.also`'s retirement condition discharges,
   and `parallel()`'s coercion completes (refs queue, generator-steps
   defer, the rest wraps).
2. **Safe cancellation at yields.** The `run(timeout=)` refusal for
   callables ("no safe way to unwind another thread") meets its honest
   exception: yields ARE safe unwind points — decline to resume, or
   `close()` (GeneratorExit) at a yield, `try/finally` cleans up. Between
   yields stays uninterruptible; that is the contract a yield declares,
   said plainly.
3. **The record written during the work.** Every yield evaluates to the
   item's `ResultView` (the `result = yield` idiom `wrap_task` already
   ships), so a title decided mid-work is a plain attribute write;
   progress is ordinary `progress()`/`track()` calls — a *during*, where
   `title=` is before and `pre_record` is after. The event vocabulary already exists from
   the inside: the `Status` protocol's units.

What does not fall: placement (generators don't pickle — the rung stays
local-only, consistent with the ladder) and ergonomics for the two-line
wrap (the CM reads better). Not a choice but a layering: **the CM is the
immediate form, the generator the deferrable form**, and the CM can be
sugar over a zero-yield generator — one machinery.

The stringly-event-bus risk dissolved with the yield contract (see the
register: bare yield = checkpoint, yields evaluate to the `ResultView`,
yielding a value is a taught error — an empty vocabulary cannot rot).
The slope remains real and is ruled the other way up: "generator-shaped"
is a property of in-process work items generally, not a step-only trick
— generator task bodies arrive as a consequence of the model, not a
special request.

### Lifting operators: grain as a wrapper choice (2026-07-31)

Willem: provide wrapper functions yielding partials with the choices
already applied — wrap a tool call to be a step, a task, or nothing.
This is "defaults, not walls" made operational: the rungs get explicit,
composable **lifting operators**, and grain stops being where a call was
born and becomes which lifter the use site applied. Generator functions
supply the deferred form's ergonomics for free — calling a generator
function builds the unstarted item with arguments bound, so
`parallel(fmt_html(target))` needs no lambda; the wrapper-partials give
plain callables and tool calls the same pre-application by hand.

Pinned consequences:

- **Task-lifting meets the manifest boundary — "name" splits.** The
  manifest and completion bake at import, so a runtime-lifted task can be
  named, deduped, scheduled and reported, but never TAB-completed or
  CLI-addressed. "Name" is two properties: *manifest presence*
  (import-time declaration → CLI surface) and *report label* (anytime).
  Wrappers grant the second, never the first — the completion hot path's
  invariant asserting itself, and the model table must say so.
- **Open: the `.opts()`-vs-wrapper boundary.** Clean rule: wrappers
  change grain; `.opts()` configures within a grain. Today `step=False`
  is a grain change spelled as intra-grain policy — straddling the exact
  line. Whether `step=` migrates into the lifting vocabulary (breaking,
  cleaner) or remains the legacy spelling: open decision.
- **The generator-call footgun, owned:** `fmt_html(target)` reads as
  "ran it", means "built the deferred item". Python culture has the
  precedent (`range(10)` does nothing) and the static type of the
  expression says work-item-not-result, but the cost is real and the
  docs must teach it, not a bug report.

### Is this fibers/async-await reinvented? (asked 2026-07-31)

Only a third of it, and the third that doesn't bite. Async is three fused
ideas: (a) explicit suspension points, (b) an event loop multiplexing
other work into them, (c) the colored-function split that (b) forces on
callers. This borrows (a) alone: a yield hands *information* (an event)
and *permission* (a cancellation window) — then the same pool thread
continues. **The bright line: yields are never scheduling points.** The
moment "and meanwhile run something else here" arrives, we own an event
loop and are reimplementing asyncio badly; concurrency stays what footman
chose on purpose — threads for parallelism, subprocesses for work. No
color infection either: the framework drives the generator at one seam
(exactly `wrap_task`'s shipped idiom); callers never await anything.
Fibers are the opposite discipline (implicit switching anywhere) and are
not this.

Correction this question forced: deferability was never the generator's
gift — a bare callable defers fine (thunks). The honest matrix:
**callable = deferrable, no protocol; CM step = protocol handle, not
deferrable; generator = callable + protocol.** The generator is "a
callable that can speak while it works", not a new execution model.

Escape valve, in writing: mass concurrent I/O *inside* a step is asyncio
interop — a body that runs a real event loop as foreign code — never a
homegrown loop.

## The model (sketch)

**Ruled (Willem, 2026-07-31): task and step both survive as concepts —
they become the same thing with different defaults.** The unification is
substrate, not vocabulary: one underlying node kind, and "task" and
"step" are its two canonical default bundles. The docs keep teaching both
words; what disappears is the ontological wall between them, not the
words themselves.

| property  | task (defaults)       | step (defaults)                 |
| --------- | --------------------- | ------------------------------- |
| name      | yes → CLI-addressable | no                              |
| signature | yes → typed binding   | no (argv / inline code)         |
| identity  | dedups by declaration | unique per mention              |
| policy    | full (`@task`/.opts)  | inherits; per-call overrides    |
| record    | title/verdict/output  | same view, same vocabulary      |

Defaults, not walls — and the identity row, which first read as the one
un-slidable wall, dissolves once identity splits in two (Willem's move,
2026-07-31: derive it from the tree):

- **Address** — which node is this? Every item gets one from the tree:
  parent-path + label (+ ordinal among same-labelled siblings). Serves
  reports, collapse state, `--json` pointers, cross-run matching — and
  opens per-step duration history later. Stability is line-number-like
  (a conditional body shifts ordinals): fine for reference.
- **Shareable identity** — is this the same *work* as that request? Only
  declared nodes have it (by declaration + frozen overrides), and only
  they need it: dedup happens at the declaration, and a shared row's
  subtree rides along with it. Two `git fetch` steps in different tasks
  have different addresses and no shared identity — both correct.

Whether the substrate needs a user-facing name at all ("work item" may
be a note-word, not a docs-word) is open.

- The run's report is **one tree in creation order**: a task's children
  are the work it created — subprocess runs, observed steps, and the task requests
  it made — named or not. Containment stops being a question; it is the
  model. (`skipped` remains a row-only state naturally: only owned,
  declared work can be skipped.) Criticism to answer: most tasks run 1-3
  steps and no observed blocks, and a literal tree makes every consumer recurse to
  answer "did lint pass". Candidate answer: items stay a flat
  creation-order list carrying a parent reference; treeness is derived by
  readers that need it. Simple readers stay simple, nesting is expressible.
  The choice is part of open decision 1.
- The **record is one surface** (title, code/verdict,
  output-to-show; duration and provenance machinery-owned), phase-gated,
  with three producers: `run()` (builds one automatically; `recorded=False`
  = execute only), the adjudication hook (receives it after the code is
  final, before commit), and `with step("…") as s:` (opens it over a
  block of body code). Shared *interface*, not shared blob — **agreed (Willem,
  2026-07-31)**: rows keep their grain's payload (`returned`, documents,
  plugin state) off the record surface; one view type carrying every
  grain's fields as maybe-None is the God-object this repo has refused
  before (TaskFn/TaskView: orthogonal, don't merge).
- **Hooks collapse** (sharpened 2026-07-31): one review hook over
  work items, attached two ways — declared, as `@pre_record(fn)` stacked
  on any maker (`@step` and, by I1, `@task`); or dynamically, via
  `.opts(pre_record=…)` for tool expressions and runtime steps (the
  escape hatch, kept by ruling). Attachment is the dispatch either way.
  The lifecycle-family `post_task` is relieved of adjudication duty and
  stays what it is: the plugin-lane global observer — with the relief
  enforced (ruled 2026-07-31): verdict-bearing fields (code, ok, title)
  are read-only at the observed phase. Residue: whether any
  display-only fields remain observer-writable.

  The family, in event order (verified against source 2026-07-31 —
  `@finalize` is long gone, absorbed by `pre_tasks`):
  `pre_tasks(inv)` [invocation editable] → per request: `pre_bind` →
  binding → `pre_task` → body (steps: execute → own `pre_record` →
  commit; generator yields hand out the view) → `pre_record(view)` per
  execution → `post_task(inv, task, result)` per request, reverse
  plugin order → commit → `post_tasks(inv)`. Plural = invocation-level,
  singular = per-thing; every name is pre/post + anchoring noun; hooks
  exist where their moments exist (steps have no binding moment, so no
  step-level pre_bind — consistency, not absence).

  **And no step-grain observer, as an opinion (ruled 2026-07-31):
  observation follows the request grain; records travel with their
  parents.** Observers see every step — committed, in context, riding
  its row (`result.steps`). A live global step stream as a lifecycle
  hook would hand observers decontextualised events (a step out of its
  row is a command line with no story), reopen the amendment-window
  question per grain, and duplicate the lane that already exists for
  liveness: the Status protocol's units today, and — when the horizon
  asks for tracing exporters — an export of the record stream
  (presentation over committed records; stream consumers cannot judge
  by construction). Review at every grain, observe at the request
  grain, export streams for liveness.
- **Acceptance test**: the design is right when `parallel()` and its
  block need no special cases — they take work items; anything else is
  coerced into an anonymous one.

## What must survive unification

- **The knowledge lane.** Some work is genuinely not story
  (`step=False` today). The lane is re-justified, not removed: it is the
  spelling for "how a task knows something", never for hiding recordable
  work — the adjudication hook removes the reason to hide.
- **A record by default.** Being recorded stays the default (considered and
  kept, 2026-07-31): dry-run's value depends on runs being story by
  default, and opt-in honesty inverts the failure mode from visible noise
  to invisible silence. Report *noise* is a display-policy question over
  the record (collapse green items at normal verbosity; keep
  everything in `--json`/`recording()`), not a recording question.
- **The policy grain in practice.** Lanes/atomic/confirm stay
  row-flavoured defaults; the model permits them anywhere ownership
  exists but nothing forces that generality in v1.
- **The completion hot path** — untouched by all of this; the manifest
  knows only names.

## Costs, named honestly

- **`--json` is a public machine surface with no consumers yet** (Willem,
  2026-07-31: nobody is using the JSON output). That inverts the cost's
  sign: rows-with-steps becoming a tree is the largest *format* change
  since dotted addressing, but today it breaks nobody — and every month
  of delay grows the audience it would break later. An argument for
  sooner, not a tax.
- `recording()` and every consumer assertion over steps.
- The docs teach "tasks contain steps" in several places; the timeless
  rewrite is substantial.
- Dry-run must skip observed steps the way it skips subprocesses — which
  is itself evidence for the context-manager shape (the step announces
  itself on
  entry).

## Corollaries (fall out; not separate features)

- hse's outcome-titling: `djlint.opts(pre_record=…)` reviewing the draft —
  title and code override; the `RunFailed` decision reads the post-hook
  code, so "a Result is its exit code" survives untouched.
- The forged-receipt idiom, the `cwd=invoked_dir()` workaround, and both
  hse comment sites become deletable.
- `p.also` retires once foreign code has a conversion.
- The step-grain hook never meets the DAG dedup question: tool-level
  `.opts()` merging is the bridge's own affair.

## Rejected along the way

- **Callable `title=`** (my first shape): right seam, wrong carrier —
  per-call argument where per-tool policy belongs, and a return-value
  protocol (`str | None`) where a mutable view is the house idiom.
- **`fail(reason, code=0)` as the pass-branch spelling**: one verb
  fighting its own name; `Failed` would mean success in one context and
  failure in another.
- **A receipt primitive** (`step(title, code=0)` as a plain call):
  a record unmoored from work — a forged receipt with the mask off.
  The context-manager form is different in kind: it binds the record to a
  measured block of real work.
- **Flipping `step=` to default-off**: guts dry-run, inverts the failure
  mode to silence, and solves yesterday's workaround (which the
  adjudication hook dissolves) rather than tomorrow's need.
- **Splitting `post_step` and `step()` into separate threads**: the
  meta-rejection that produced this note. Point-fixing this seam is how
  the saga started.

## The horizon: data processing, potentially distributed

Ruled direction (Willem, 2026-07-31): the DAG concept extends, eventually,
all the way to data processing — potentially distributed. "We are at the
beginning of a long journey." Nothing distributed gets designed now; the
horizon's job is to keep today's model from foreclosing it. Four
constraints it imposes, and one gift it gives:

- **Identity must leave the door open to durable, input-keyed identity.**
  Today's sharing keys on object identity + frozen overrides, per run.
  Data processing wants (declaration, inputs) → value, cacheable across
  runs — memoization, incrementality, the build-system move. The
  address/sharing split above already separates the axes; the constraint
  is only that the substrate's public shapes never hardwire per-run
  object identity as THE identity.
- **The ownership ladder gains a placement reading.** A declared, typed
  work item is a serialisable request — shippable to a remote worker. A
  subprocess step is spec-shaped (argv, env, cwd — serialisable) and so
  placeable too. An in-process callable or observed step is local-only by nature.
  Placement is thus a *derivable* property of rungs that already exist —
  record that now so it never needs inventing as a bolt-on.
- **Report tree and dependency DAG are two projections of one item set.**
  The report is the tree of who-requested-what; sharing already makes the
  underlying structure a DAG (a shared row under two requesters is the
  diamond, rendered as reference rows). In data terms the reference row
  becomes "value reused". Both projections stay first-class; neither is
  the other's implementation detail.
- **Typed returns are the other half.** The parked structured-task-results
  thread (return annotations → schemas, `results[].returned`, `--describe`)
  is the data boundary of this same future: a work item's output is a
  typed value. The two threads must be designed to meet, and this note is
  where the control half commits to that rendezvous.

The gift: lanes, keep-going subtrees, `skipped`, and confirm gates map
cleanly onto dataflow vocabulary (resource constraints, partial failure,
upstream-failed, approval gates) — the model does not need to be bent for
the horizon, only not nailed to the floor.

## The horizon, part two: lessons from Bazel (side quest, 2026-07-31)

Bazel is the benchmark for hermeticity and reproducibility; once the
horizon reaches data processing "there isn't all that much difference
left" (Willem). Compared against the model as it stands:

**Where we already rhyme:** identity ↔ their action key (I6's uniform
key is the same shape; `.opts()` is their configuration axis in
miniature); plan/execution split ↔ loading/analysis vs execution — and
the completion hot path's imports-must-be-cheap discipline is a
Starlark-flavoured constraint arrived at from TAB latency instead of
determinism; run-scoped cells ↔ the memoised action graph; the ladder's
"spec-shaped = shippable" ↔ REAPI's serialised actions.

**The one structural gap: items don't declare inputs and outputs** —
and the answer is the founding trick applied once more: **the typed
signature IS the declaration** (prior brainstorm, confirmed to fit).
Path markers (`exists`/`isfile`/`isdir`) declare file inputs — the
marker names the kind, the bound value names the file, eager validation
extends to content digesting; `env("VAR")` and `uses=` already declare
environment inputs; the tools bridge already knows binary + version
(toolchain identity). One hole: produced files — a `creates`-shaped
output marker on a Path param, with Bazel's own duality (the path is
input to the key, the content is the output). Worked example: declare
djlint's config as `config: Annotated[Path, isfile] = Path(".djlintrc")`
— one line is simultaneously CLI surface (an override flag users never
had), validation, docs, and cache-key input. A named corner: optional
inputs need may-be-absent semantics where absence is a digestable state,
not an error (creating the file must invalidate as surely as editing
it).

**Lessons with teeth:**

1. **Declare, don't observe.** Keying on observed reads is unsound (the
   read-set depends on the inputs). Cacheability is therefore declared,
   opt-in — boundary policy, an I13 instance: `cacheable` exists exactly
   where declaration does.
2. **Enforcement over convention.** Declarations nothing checks become
   lies. The guards are the seed; the env router's read-tracking is
   half the plumbing for a `hermetic` policy that refuses undeclared
   reads with taught errors — Bazel's rigor, footman's surface.
   Signature-derived coverage is the ergonomic 90%; enforcement is what
   makes the declared set sound for caching. Unenforced durable keys
   are partial and must say so or not be offered.
3. **Value-flow first.** Bazel is file-centric because builds are;
   data pipelines can hand off through typed serialisable returns (the
   structured-results rendezvous), which gives Merkle-style durable
   keys for free — an item's key = declaration + digests of resolved
   inputs, where inputs are upstream outputs. Cleaner than Bazel, not
   weaker; file artifacts are the later, optional vocabulary.
4. **Keep our dynamism; island the cacheable subset.** Bazel suffers
   where outputs aren't knowable pre-execution; we are strong there.
   The lesson is not to restrict dynamism but to keep the
   durable-cacheable subset identifiable — hermetic islands in a
   dynamic run.
5. **The gradient is the moat.** Bazel's real-world failure is the
   adoption cliff. The ladder extends with rungs Bazel never had —
   cacheable, hermetic, shippable — each opt-in, per item, purchasable
   incrementally. Bazel-grade guarantees as a gradient is the one thing
   Bazel cannot retrofit.

(Toolchain pinning ↔ provisioned tools + lockfile discipline: seed
exists. Deterministic outputs for cacheable items: docs-level lesson.
Skyframe's analysis-cost story: a warning the completion budget already
polices.)

## Staging is legitimate once the lane is modelled

The point-fix sin was designing without the model, not shipping in
pieces. With this note pinning the lane, the stage-safe pieces are the
record surface and the per-tool adjudication hook — both land
identically on the split model today and on the substrate later, and the
first stage alone deletes hse's workarounds. The tree/report question is
the piece that must NOT ship until called, because its shape is the
substrate. The model must not become the reason nothing ships.

## Per-resource lanes: cwd as the first split (2026-07-31)

Willem: cwd should get its own serialised lane for work that absolutely
must control the real working directory. His own aside while asking —
"tasks (or steps? see it's getting confusing already)" — is evidence for
the model: today the feature cannot even be *addressed* to a grain,
because lanes are row-only policy. In the model, a lane attaches to any
owned item; the grain confusion dissolves.

Shape: today's serial lane bundles ALL process globals; a per-resource
split (cwd lane, env lane, console lane — the console gate already
exists as exactly this precedent) raises concurrency: a cwd-holding item
and an env-mutating item overlap where today both drain into one serial
lane. Maps directly onto the horizon's "lanes generalise to resource
constraints" gift.

Three boundaries that keep it sound:

- **Subprocess steps never need it** — `run()` injects `cwd=` for free.
  The lane serves *in-process* work only (blocks, callables, generator
  steps, in-process tools): it is the missing fourth exit in the
  foreign-cwd refusal's taught list (subprocess / footman.cwd() /
  unmanaged / — now — "claim the cwd lane").
- **The chdir-lock ghost stays dead.** The v1 chdir-lock design was
  killed by deadlock audit (notes/20260725-process-globals.md); what
  makes per-resource lanes different is the v2 arbiter's invariant,
  carried forward as a spec invariant here: **lane acquisition is
  boundary-atomic** — all resource claims declared and acquired at the
  item's boundary, never escalated mid-body. Mid-body "now I need cwd"
  is the deadlock door the audit closed; it stays closed.
- Declaration is policy (`@task(...)` / `.opts()` / lifting wrappers),
  so the `.opts()`-vs-wrapper boundary (open decision 6) governs its
  spelling too.
- **Custom resources are kept open by ruling (2026-07-31):** lanes are
  not a closed set of process globals — a user may declare a named
  resource to serialise on ("the database", "the staging environment").
  The boundary-atomic invariant covers them identically (all claims at
  the item's boundary), and under the horizon a named resource is what
  a cluster-level token later generalises from.

## How this becomes coherent (process, agreed 2026-07-31)

The danger has a name in this note's own history: soundbite-driven
design is how `step=False` happened. The antidote is inverting the
direction of derivation — this note is archaeology (conversation-ordered,
each fragment locally justified); coherence means a small normative core
from which every fragment derives or gets cut. Five moves, in order:

1. **The spec-shaped core**: one page — definitions (work item, the two
   default bundles, the ladder and its three readings, address,
   shareable identity, the record, lanes) and the ~ten invariants already
   latent here (yields are never scheduling points; manifest presence
   only at import; a Result is its exit code; dedup only at declared
   identity; lane acquisition is boundary-atomic; the record is an
   interface, not a blob; the completion hot path is untouched; …). Then
   a **derivation ledger**: every feature re-derived from core, marked
   *derives / axiom / doesn't fit*. The doesn't-fits are where
   incoherence hides.
2. **Payloads walked end-to-end on paper**: hse's djlint gate; footman's
   own `check` (mixed fan-out); dry-run; `recording()`; Ctrl-C mid-run;
   one horizon dataflow case. What exists at each moment, what the
   report holds, what every hook sees.
3. **The type checkers as the loom**: a stub-only skeleton of the view,
   the lifters, the yield vocabulary, item/row shapes — required to pass
   the four-checker gate. Types force fragments that "sound compatible"
   to actually meet.
4. **Adversarial pass with fixed personas**: hse-the-abuser (the next
   forgeable primitive), the report reader, the distributed future, the
   completion hot path, Windows — run against the spec, not the chat.
5. **Decisions called in dependency order**: report shape first, then
   identity encoding, the ResultView surface, lifters, generator protocol,
   lanes, staging. Spikes where measure-first demands: a shadow report
   emitter over the current runtime (tree-vs-flat against reality), a
   generator-pump prototype (the cancellation claims).

Division of labour as practised: Willem floods and rules; the assistant
consolidates, derives, attacks. This note remains the thinking record;
the spec becomes its own note linking back once the flood is done.

## Open decisions

1. **The report tree.** Shape of the unified `--json` (and `recording()`):
   how rows-with-steps becomes items-with-children. With no JSON consumers
   yet there is no versioning/migration story to design and no case for a
   flat compatibility view — the shape just needs to be *right*, chosen as
   if 1.0 were watching. Still first: nothing else lands before this is
   called. Move 4 (the report-reader persona) adds three requirements to
   the call: a lookup contract under I6's same-label multiplicity (by
   name → a list, by address → unique); the `state` × `failed_at` pair
   codified as two axes with one word each (the shipped `state`
   docstring's rule, extended); and reference-row accounting (duration
   and steps live on the execution row, references link — aggregation
   never double-counts a shared subtree). RULED 2026-07-31: flat
   creation-order list, parent by address, per the shadow-emitter
   brief (spec note, move-5 section) — with the schema field STAYING
   1 (no consumers; footman launches with the new shape rather than
   migrating to it). Streaming corollary parked at the horizon: the
   flat list is jsonl-shaped, the file form of the record-stream
   export the observer opinion anticipated.
2. **Hook name and family membership.** Largely resolved 2026-07-31:
   `@pre_record(fn)` stacked on makers (declared) + `.opts(pre_record=…)`
   (dynamic/per-use); no new global observer — `post_task` keeps that
   role. Residue: the stacked form's exact typing (identity like the
   gates) and whether `@pre_record` above vs below the lifter reads
   order-free the way the gates do. CLOSED 2026-07-31 by move 3 + the
   phase-gate ruling: the stacked form types as the identity
   `Callable[[F], F]` — order-free both ways (loom finding 6) — and
   the observer-writable residue is none: `post_task` is purely
   read-only (it holds the immutable `Result`, not the view),
   `set_returned` is review-window-only, and a raising observer fails
   the grain with `failed_at="observe"` lifecycle provenance (spec I5;
   the "no downsides" audit — redaction was never soundly an observer
   write — lives in the spec's move-3 section).
3. **Does adjudication fire for anonymous items?** CLOSED 2026-07-31 as
   a corollary of the observer opinion: self-review via the item's own
   handle and its maker's `pre_record`; watching-from-outside is the
   request-grain observer plus (someday) record-stream export — never a
   step-grain lifecycle hook.
4. **Display policy**. RULED 2026-07-31: split — minimal defaults ship
   with the build (task grain only at normal verbosity; verbose adds
   steps; a failed task auto-expands its failing step and audit line —
   confirmed same day), the full display thread (verbosity matrix,
   provenance markers, emission-time redaction) parks as its own
   post-model thread.
5. **Migration for hse**. DISSOLVED 2026-07-31: hse is unblocked and
   being developed alongside; it pins its current footman until the
   model ships, then migrates once (target 0.28.0). No interim wave;
   staging collapses to "ship when coherent."
6. **The `.opts()`-vs-wrapper boundary**. DISSOLVED 2026-07-31 by the
   model itself: `recorded=` is execution policy, not a grain change,
   and the boundary rule is I13 (typed in the loom). The `step=` →
   `recorded=` rename rides the build, no shim.
7. **Per-resource lanes**. PARTIALLY RULED 2026-07-31: env — no lane
   (fully virtualised by the router); cwd — a lane, OPT-IN (claiming
   it is knowingly giving up parallelism); console — a lane, NOT
   optional; no further core lanes ever — plugin-defined via a
   registration mechanism footman's own two lanes are built on.
   stdin dissolves (boundary questions + the console claim); argv was
   never contended. Both artifacts are in: the generator-pump spike
   (DONE 2026-07-31 — walk 5 confirmed in running code) and the
   registration BRIEF (spec move-5 section): declaration is a Python
   binding — `lane()` returns a handle, `lanes=` takes handles never
   strings (a typo is a NameError by construction), sharing is
   importing, collision is a provenance-naming refusal, core's two
   lanes dogfood the same call, `serial=` becomes sugar for both.
   CALLED 2026-07-31 on the brief as amended (one holder per lane;
   capacity deferred as a purely additive keyword). Willem's recorded
   reason: the handle mechanism cleanly extends to multiple resources
   of one type — a resource IS a binding. MOVE 5 COMPLETE; every open
   decision closed.
8. **The bare-callable ban**. RULED GO 2026-07-31 ("hell yeah"). I12
   unconditional; `step(fn)` is the one-liner; refusal is structural
   (no `.opts` on a bare callable — the loom's typing is the
   enforcement).
9. **Reviewer composition** (opened by move 4). RULED 2026-07-31:
   chained draft, bottom-up — the reviewer nearest the `def` runs
   first, each further-out one sees the accumulated edits,
   `.opts(pre_record=…)` last; uniform regardless of where the lifter
   sits. Docs phrasing pinned in the spec's move-5 section ("reviewers
   run from the inside out").
10. **Review provenance** (opened by move 4). RULED 2026-07-31 as
    **the audit** (Willem's generalisation and name): not a reviewer
    list — every involved lifecycle moment, (moment, actor,
    code-or-None), execution order. Register row; full shape in the
    spec's definitions. Subsumes `failed_at` and the preserved work
    code as derived readings; the audit's order also documents
    decision 9's order by construction. ADDENDUM 2026-07-31 (the
    contract page's first finding): per-task hooks live on the task's
    HANDLE — the exact mirror of the per-task lifecycle set, bare
    decorators, permanent (set_opts tier) — and the plugin lane keeps
    only hooks with no task knowledge in them ("the moment a global
    hook says 'if this is task X', it belongs on X"). Full ruling in
    the spec's move-5 section.
