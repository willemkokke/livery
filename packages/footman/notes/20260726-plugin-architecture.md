# footman plugin architecture v2: the plugin lifecycle

Status: **phases 1–2 released in 0.22.0; phase 3 in review (PR #93);
phases 4–6 unbuilt.** Every design decision was closed by Willem in the
one-by-one walkthrough of 2026-07-26, and the D-ledger at the bottom is
authoritative for phases 4–6. Body prose above it may carry earlier working
names — `init`, `conclude`, `task_wrapper` — read the ledger's spelling as
final: `pre_tasks` / `pre_bind` / `pre_task` / `wrap_task` / `wrap_bind` /
`post_task` / `post_tasks`, handle = `Invocation`.

!!! warning "The volatile rule below was superseded while phase 3 was built"

    D23's original answer made `volatile` a *call-only* opt-out, leaving
    `pre=` deduped. Willem reversed that during the build: it introduced a
    difference between declared and dynamic runs that should not exist. See
    "Phase 3, as built" for the rule that shipped — the passages marked
    SUPERSEDED are kept because the reasoning behind the reversal is the
    useful part.

## The vision (Willem)

**footman is a great task runner out of the box, and extensible enough to
be a great project runner.** Core owns what a task runner owns: typed
CLIs, the DAG, completion, the cascade. Everything a *project* runner
adds — environment stacks, toolchain provisioning, setup flows, telemetry,
a branded entry point — arrives through plugins, and footman never knows
its caller. Acceptance test: **a real project runner could be built purely
as plugins.**

## The model

**The unit of plugging is the captured import.** A provider module's
import-time registrations are caught by `registry.capture()`;
`plugin()`/`include()` is the single doorway; everything carried arrives
with provenance via the finalizer four-part carriage (capture/reset,
`_fork` field census, `load_tree` collection, `_pull` graft). Built-ins
are ordinary plugins (`footman.docs`/`footman.tools` precedent). How a
plugin computes anything is not footman's concern — footman defines the
moments and the mechanisms.

## The lifecycle (Willem's reframing + his naming proposal)

Four moments — Willem settled the fourth ("add it") and proposed the
symmetric names (front-runner, D1 still open on final spelling):

| Hook | When | Runs | Plugin handle | Gets |
|------|------|------|---------------|------|
| `pre_tasks` | once, post-merge — before finalizers, before availability gates | main thread, pre-DAG | **editable** | `root`, `cwd`, `config`, `cli` |
| `pre_bind` (Willem picked the short spelling) | before each task's **binding** — the specialist moment | task's pool thread — **in parallel** | read-only | task handle (raw CLI values only) |
| `pre_task` | after binding, before the body — **the default per-task moment** | task's pool thread — **in parallel** | read-only | task handle + **bound arguments** (read-only) |
| `post_task` | after each task | task's pool thread — **in parallel** | read-only | task handle, ResultView, pre states |
| `post_tasks` | after the run, all results in hand | main thread | read-only | run report: results + skipped/never-ran + totals |

Per-task order: `pre_bind → bind → pre_task → body → post_task`.
Post-bind is the default on purpose: the common plugin class (audit,
telemetry, argument-conditional env, guards, cache keys) wants the *real*
arguments — the truth after coercion, `env()` fallbacks, defaults,
prompts, forwarding. The pre-bind moment is the specialist: its `ctx.env`
injections are the only per-task ones that can fill `env()` params (via
the widened router window), and most env()-fill needs are already served
globally by `pre_tasks`. Named hooks, not a `postbind=` flag: one name
per moment, stackable (one plugin can register all three), no boolean
mode — the stacked-`@requires` aesthetic. (Supersedes D22's flag.)

Naming hazard, named: singular/plural differ by one letter. The natural
guard: arities differ (`pre_tasks(plugin)` vs `pre_task(plugin, task)`) —
enforce at *registration* by signature inspection, taught error naming the
plugin, not a call-time TypeError at the first task.

### pre_tasks (was init/prepare)

- The global moment. **Modifying `os.environ` here is ordinary code** —
  pre-DAG, single-threaded, everything downstream sees it (availability
  gates, `env()` param fallbacks, every child). PATH is just a key. No
  apply()/override API — the plugin already decided its values.
- Timing is load-bearing: `requires_env` gates evaluate at manifest build,
  right after discovery — init runs before that, and before finalizers
  (which may read the environment).
- Runs in the detached manifest-refresh child too (availability baked into
  completion must see the env) → **init is quiet and quick**, always.
- Editable handle: set env, stash plugin state, conditionally shape its
  own pre/post behaviour. **Not** editable: CLI options. The docs framing
  (Willem's): **by the time pre_tasks runs, everything the manifest needs
  is fixed — options included. The manifest is written from declarations,
  never from executions.**
- `fail(message)` → taught refusal, exit 2, `--json` envelope honoured.
- Out of scope by construction: footman's own config, the uv handoff.

### pre_task / post_task

- Run on the task's worker thread: **documented as parallel** across
  tasks, for every DAG node (pres, fan-out members, not just CLI-named
  segments). Read-only plugin handle is what makes that safe.
- **Never `os.environ`** here — per-task env injection goes through the
  task handle into `Context.env` (the existing overlay: `run()` merges it
  for subprocesses, the environ router serves in-process reads,
  serial/exclusive lanes apply it under the hold). This *replaces* the v1
  `TaskView.set_env` idea — one lane, not two (D10).
- **pre_task runs before parameter binding** (D5 — mechanism settled by
  investigation): the environ router already virtualises every
  `os.environ` alias as run-start snapshot + `current().env`
  (`_globals._merged`), gated on `current().in_task` — and `run_task`
  sets the context *after* bind today. So the fix is to **widen the
  routed window** (set ctx + `in_task` before bind), and `_env_value`
  needs zero changes: pre-injected `ctx.env` reaches `env()` fallbacks,
  validators, and coercers automatically. One interaction to verify at
  build time: the same `in_task` flag arms the prompt guard — check
  bind-time prompts still behave, or split the flag. Order per task:
  `pre_task → bind → body → post_task`.
- **Downsides of pre-before-bind, accepted with eyes open:** pre sees raw
  CLI values (`seg.values`), never bound/coerced arguments (post sees the
  result); bind can prompt on interactive tasks, so plugin-measured time
  spans user think-time while footman's own `duration` clock still starts
  after bind — two honest, different clocks, documented; a raising pre
  fails the task before its own coercion refusal would have taught the
  real problem (minor — both are taught).
- **Multi-plugin unwinding:** pres run in plugin order, posts in
  **reverse** order; a plugin's post fires only if its own pre completed
  (a raising pre gets no post; earlier plugins' posts still fire) —
  context-manager stack semantics, specified for style A, inherited free
  in style B.
- **State pairing (D4 — collapsed, Willem's insight):** style B is
  **syntactic sugar over style A** — run the generator to its `yield`
  (that *is* the pre phase), keep the suspended generator as the paired
  state, resume it with the ResultView (that *is* the post phase). One
  execution engine, two author-side spellings, identical semantics
  (pairing rule, reverse unwinding, failure delivery) by construction.
  **Offer both** (Willem inclined; pytest itself ships both plain
  hookimpls and wrappers). Contract enforcement lives in the sugar: zero
  yields (StopIteration during pre) or a second yield (generator not
  exhausted on resume) are taught errors naming the plugin.
- **Three per-task moments, generalized rules:** the pairing rule
  becomes *post_task fires iff any of the plugin's registered pres
  fired*, with states not produced delivered as `None` (a bind failure
  after a successful `pre_bind` still posts — the bind-time span needs
  closing; an unavailable refusal still pairs nothing). Both pres may
  return state; post receives them distinctly (exact spelling — two
  args vs a small namespace — is a D22 sub-decision). Bound arguments
  are read-only everywhere: mutation would let a plugin silently break
  the typed contract the framework exists to enforce. Explicitly
  future: a memoization plugin's "short-circuit the body with a cached
  result" — a bigger power than observation, its own design pass.

- **How the ladder lands on `task_wrapper`:** the sugar's shape is
  unchanged — one yield, decomposing to `pre_task` + `post_task` with
  the suspended generator as the paired state — but anchoring at the
  now-post-bind `pre_task` means the wrapper's pre-half **sees bound
  arguments**, a strict improvement for its whole audience (spans and
  audit records get real values). Two consequences, stated honestly:
  (1) **a wrapper never sees a task that failed to bind** — its anchor
  moment never fires, so the generator never starts and there is
  nothing to unwind; a plugin that must observe bind failures registers
  `pre_bind` (+ explicit `post_task`, which fires because pre_bind
  fired, per the pairing rule). (2) `wrap_task`'s `yield` delivers the
  ResultView only. **The full span is its own named construct — SETTLED
  (Willem, 2026-07-26): `@wrap_bind`** — enters at the bind boundary,
  exactly two yields (`bound = yield` after bind; `result = yield`
  after the body), locals + `try/finally` across all three moments, a
  span that closes even when bind fails. The family grammar is now
  fully regular: `pre_X` runs *before* moment X, `wrap_X` *enters at*
  X and rides to the end — each name one fixed protocol (the D22
  principle: contracts differ by NAME, never by a boolean on a shared
  name; Willem's `wrap_task(pre_bind=True)` idea was enforceable but
  made one decorator mean two protocols). Decomposes onto the same
  engine (pre_bind + pre_task + post_task, suspended generator as
  state); ships with wrap_task in the same phase.
- **post_task may rewrite the result** — scoped to the *reported* result
  (summary, `--json` `returned`), **not the forwarded value** (D6,
  recommended): returns are also inputs (forwarding), and a plugin
  silently changing what a dependent receives breaks task-to-task
  contracts mid-DAG.
- The handle exposes read-only **core run facts** — `json`, `quiet`,
  `verbose` — separate from `cli` (the plugin's own options), so a
  reporter can decide by output mode (Willem's `--json` case). Info flows
  core→plugin; steering never flows plugin→core.
- Under `--dry-run` nothing executes — no pre/post fire.
- Failure semantics (D8): pre_task raising fails the task like a failed
  pre-dependency; post_task raising fails an otherwise-green task
  (recommended — a reporter that crashed must not pass silently); both
  taught, naming the plugin.

### TaskResult and observers — the investigated seam

`executor.run_task` (executor.py:603) is the single choke point every
executed node passes through (both runners call it); `TaskResult` is built
by `_result` after `_call`. The full inventory of results and what fires:

| Result | Body ran? | pre/post fire? |
|--------|-----------|----------------|
| normal (ok, failed, or killed-mid-run `cancelled`) | yes | both |
| bind/coercion refusal (code 2, inside run_task) | no | **pre only → post still fires** (pairing rule) |
| `@requires` unavailable (run_task's first check) | no | neither |
| confirm-denied (synthesized pre-DAG) | no | neither |
| **skipped node** | no | neither — **and no TaskResult exists at all** (`state="skipped"`, invisible even in `--json`) |
| body-called tasks (`lint(fix=…)` from a task body) | direct call | neither — not a DAG node, no TaskResult |

- **Pairing rule (D8):** post fires iff pre fired — pre-returned state must
  always reach its post, even when bind failed in between. Unavailable is
  checked *before* pre, so refused tasks pair nothing.
- **`returned` is not consumed by the DAG.** Forwarding threads
  `forward`-marked *parameter* values in a static pre-run pass
  (`forward_map`: CLI/defaults, side-effect free) — return values never
  flow between tasks. Only `--json` (`_print_json`) and `Runner` collect
  read `TaskResult.returned`. **A post_task rewrite is reported-only by
  construction** — the v1 forwarding worry dissolves.
- **Remaining rewrite collisions:** (a) the int-return exit-code channel —
  the int already became `code` inside `_call`; a rewrite never touches
  `ok`/`code`, and a non-int rewrite of an int-returning task would start
  serialising under `--json` where the int was dropped (document); (b) the
  post-1.0 structured-results plan — a rewrite can violate an advertised
  `returned_schema`; alternative for schema safety: `annotate(key, value)`
  landing in a separate per-result envelope field, leaving `returned`
  intact (D6 chooses: rewrite, annotate, or both).
- **ResultView, not the raw dataclass.** `TaskResult` is mutable and
  `_run_tree` derives the exit code from `ok`/`code`/`cancelled`; secrets
  are redacted downstream (`_describe.redact` in `_print_json`, so a
  rewrite cannot bypass redaction). post_task gets a read-everything view
  (steps included — command/code/stdout/stderr per step, the telemetry
  payload) with exactly one write: `set_returned()` (and/or `annotate()`).
- **Duration:** pre/post run inside run_task's clock — hook time folds
  into `result.duration` and the learned progress estimates. Honest
  wall-clock; history re-learns after a plugin lands. Documented.

### Tasks as run-scoped futures (D23 v2 — Willem's generalisation)

The gap that started this: `deploy(pre=[build])` cannot see build's
returned artifact path; the body-call workaround (`artifact = build()`)
is a plain inline call that runs build *again* (no dedup against the DAG
node), on the caller's thread (no parallelism), invisibly (no
TaskResult, no hooks). v1's answer was a read-only accessor
(`ctx.returned(build)`). **Willem's generalisation subsumes it**: relax
"`@task` returns the function unmodified" and route body-calls through
the machinery — per (task, resolved arguments), a run-scoped once-cell:

- already run → return the memoised value
- currently running (any thread) → block on its future
- neither → claim it and run **inline on the caller's thread**

The careful analysis, dimension by dimension:

- **Memo key = (task, bound arguments in normal form)** — the same
  normal form binding produces, so a DAG node (CLI/default/forwarded
  values) and a body-call with identical resolved args share one
  execution, and a body-call with *different* args is honestly a
  different piece of work and runs. This is the DAG's own dedup
  philosophy (same work once per run) extended to calls; it is also a
  **semantic change**: today `notify("x"); notify("x")` runs twice,
  under memo the second returns instantly. Within one run that is
  almost always the right default — but an escape hatch for deliberate
  re-execution is needed (spelling open: `footman.fresh(build)()` /
  `build.fresh()` — sub-decision).
- **Concurrency = one `Future` per key** in a run-scoped registry.
  Blocking-on-running occupies a pool thread (bounded by `jobs`, a
  throughput dip not a hazard); claimed work runs on the caller's
  thread (no thread growth). **Cycles need runtime detection**: the
  static DAG has `_check_cycles`, dynamic calls don't — keep a wait-for
  edge set under the registry lock, check on every block, refuse with
  the taught cycle-naming error. Self-recursion becomes a same-thread
  reentrant key → immediate taught error (today it's a stack overflow —
  an improvement).
- **Full task semantics for machinery-run calls**: own Context (cwd
  policy, env overlay), pre_bind/pre_task/post_task fire, a TaskResult
  lands in the run report. This **closes the seam table's body-call
  hole** — the one execution path hooks couldn't see disappears.
  Behaviour change to name: a body-callee's output stops interleaving
  into the caller's buffer (it becomes its own reported unit — arguably
  the honest rendering). Waiters share the first claimant's result and
  attribution.
- **The hard edge — serial/exclusive lanes.** The arbiter lane is
  acquired *at the task boundary, never mid-body* — that invariant is
  what made the process-globals design deadlock-free (the chdir-lock
  predecessor died by deadlock audit). A machinery body-call to a
  `serial=`/`exclusive=` task would acquire a lane mid-body, reopening
  exactly that analysis. **v1: refuse, taught** — "declare it as pre=
  instead"; a caller already holding a sufficient lane can be relaxed
  later if a real case appears. Same taught refusal for body-calls to
  `infinite` tasks (a future that never resolves).
- **Failure flow**: a failed callee raises in the caller (today's
  direct-call behaviour preserved), and its own failed TaskResult is
  recorded; fail-fast abort mid-wait cancels the waiter like any child.
- **Identity sweep** (build cost, not design cost): `@task` returning a
  wrapper instead of the bare fn touches every fn-keyed assumption —
  DAG dedup keys, `DEFINING_DIR`/`SHADOWED`/`_PULLED` stamps, `--where`
  reading `__code__`, `resolved_signature` (already follows
  `__wrapped__`). Feasible if the wrapper is *the* object everywhere
  (registry, keys, stamps) and introspection unwraps; needs its own
  test sweep. The `TaskFn[P, R]` Protocol already types calls, so the
  author-visible contract is unchanged.
- **`--dry-run`** unchanged (nothing executes); **progress** gains
  dynamic unit insertion (the status stream already takes units one at
  a time); **`ctx.returned()` dies** — calling the task *is* the
  accessor, `pre=[build]` + `build()` in the body is an instant cache
  hit.

**Interaction with D6 stands, sharpened:** the memo stores the pristine
return (snapshot at `_call` exit); dependents and callers always receive
what the annotation promised. `set_returned` touches the report only.

Staging if adopted: the futures core (memo + wait + inline-run +
cycle detection + lane/infinite refusals) is its own build phase, after
the plugin lifecycle — it is the biggest semantic change in this plan
and independently shippable.

### conclude (now load-bearing, D7)

post_task is per task *and* only fires where pre fired — a reporter built
on it alone systematically misses unavailable, confirm-denied, and skipped
work, and skipped nodes have **no TaskResult anywhere** today (even
`--json` omits them). So `conclude` is not a convenience: it is the only
place a JUnit/OTel/notification reporter can see the whole run. It
receives a run report — all TaskResults *plus* the skipped/never-ran list
(and totals) — main thread, after the run, read-only. Recommendation
upgraded: build it with pre/post in phase 3, not post-1.0. Related new
decision (D21): should skipped nodes also become visible in `--json`
itself (an envelope addition), or only in conclude's report?

## Worked examples (for D2/D4/D6 reflection)

### Reported-only rewrite, end to end (D6)

```python
@task
def bench() -> dict:
    ...
    return {"ops": 51_234, "ms": 812}


@task(pre=[bench])
def publish(): ...  # prereqs never receive bench's return


@task
def report():
    data = bench()  # body call: direct function call, no hooks
    write_markdown(data)


# plugin
@footman.post_task
def enrich(plugin, task, result, state):
    if task.name == "bench" and isinstance(result.returned, dict):
        result.set_returned({**result.returned, "ops_per_s": 63_096})
```

- `fm --json bench` — `results[0].returned` is enriched. The only surface
  that changes (plus `Runner` collect, which is the same surface in
  tests).
- `fm publish` — bench runs as a prereq; publish's body gets nothing from
  bench either way. Returns don't flow in the DAG; forwarding threads
  `forward`-marked *CLI param* values downward, pre-run, statically.
- `fm report` — the body call `bench()` returns the raw dict; hooks never
  fire on body calls. The markdown is untouched.
- `@task def flaky() -> int: return 3` — the 3 already became `code` in
  `_call`; a rewrite cannot un-fail the run. (A dict rewrite would make
  `returned` *appear* in `--json` where the bare int was dropped.)
- The one consumer a rewrite can still mislead is future: post-1.0
  structured results advertise `returned_schema` from `-> dict`, and a
  `jq .results[0].returned.ms` consumer breaks if a plugin renamed the
  key — the case for `annotate()` (separate envelope field) as the
  schema-safe companion or alternative.

### The two pairing styles — same plugin, an OTel span per task (D2/D4)

Style A — paired hooks, pre returns state:

```python
@footman.pre_task
def span_start(plugin, task):
    return tracer.start_span(task.name)  # footman carries this to post


@footman.post_task
def span_end(plugin, task, result, span):
    span.set_attribute("ok", result.ok)
    span.set_attribute("code", result.code)
    span.end()
```

Style B — one generator, pytest-hookwrapper style:

```python
@footman.task_wrapper  # name open
def span(plugin, task):
    s = tracer.start_span(task.name)
    try:
        result = yield  # bind + body run here;
        s.set_attribute("ok", result.ok)  # resumes on pass AND fail
    finally:
        s.end()  # runs even on unwind
```

The pytest precedent is exact — `@pytest.hookimpl(wrapper=True)` with
`outcome = yield`, and even the rewrite affordance has precedent
(`outcome.force_result(…)` ≙ `result.set_returned(…)`).

Trade-offs: **B** gets pairing, reverse-order unwinding, and cleanup on
failure/interrupt free (`try/finally` — it *is* the CM stack), and the
audience writing dev-tooling plugins largely knows it from pytest; costs
are the one-yield contract (0 or 2 yields = taught error naming the
plugin), `Generator[None, ResultView, None]` typing, and unfamiliarity
outside pytest. **A** is two flat functions, trivially typed and
documented; costs are the state-threading arg, unwinding rules specified
rather than inherited, and pre/post that can drift apart. Mixed option:
B for the per-task pair only — a run-level generator is wrong for
`pre_tasks`/`post_tasks` because the manifest refresh child needs only
the env side effects and never runs tasks (closing a generator midway
fires `finally` blocks that believe a run happened).

## Author-side shape (D2 — the big naming/ergonomics call)

Front-runner, matching footman's function-decorator surface:

```python
@footman.prepare  # name open: prepare/init
def env_files(plugin):
    if path := plugin.cli.get("env_file", ".env"):
        plugin.env.update(parse_dotenv(path))  # or plain os.environ


@footman.pre_task
def start_timer(plugin, task):
    return time.monotonic()  # state → post_task


@footman.post_task
def report(plugin, task, result, started):
    if plugin.json:
        ...
```

Alternatives on the table: a `footman.Plugin` subclass grouping the
lifecycle (state gets `self`, but read-only-in-pre/post fights an
author-owned class), or the generator/context shape above. Whatever wins,
the *identity* stays the provider module — the doorway doesn't change.

## Global options v4 — `=`-only for every long option (cost/value run)

Willem's actual preference surfaced: he *prefers* `=` — in a long chain,
`--target=prod` never makes the reader ask "value or next task?". That
inverts the v3 question: instead of relaxing plugin options up to space
syntax (the two-pass), unify **downward** — `=`-only for **all long
options everywhere**: core globals, plugin globals, task options. Core's
single-dash short aliases (`-j 4`, `-C docs`) keep today's forms — they
are a closed, statically-known set (task options have no shorts;
`cli_name` mints only `--kebab-case`), so they carry zero ambiguity and
all the muscle memory.

**Value:**

- **Chain readability — footman's core UX.** Chaining is the signature
  feature, and `=` makes every token self-describing: bare word = task
  address (or positional), `--x=v` = option with value, `--x` = flag.
  The reader never disambiguates by memorised arity. No other CLI has
  footman's chain grammar, so "every other tool takes space" carries
  less weight than it first seems — no other tool has this reading
  problem.
- **The two-pass machinery dies unbuilt.** No lenient/authoritative
  divergence is possible when values are attached — no correction pass,
  no re-discovery, no manifest arity hint, no `_run` `-C`/handoff
  restructure. Plugin globals need *no special grammar at all*: they
  are simply long options under the same rule as every long option.
  The asymmetry Willem disliked dies by unification, one rule deep.
- **The `option?` kind dissolves.** Bare = the flag/detect reading,
  `=value` = the explicit value — uniform and non-greedy. Core's three
  greedy specials (`--install-completion zsh`) become
  `--install-completion` (bare already detects the shell) or
  `--install-completion=zsh`. The GLOBALS kind column shrinks to
  flag|option.
- **The splitter and the hot path simplify.** Option-value consumption
  states vanish for long options (`_GLOBAL_VALUE`/`_GLOBAL_MAYBE`
  shrink to the short aliases); chain splitting drops per-option arity
  lookups (positional counting remains — bare tokens still need the
  manifest, unchanged). `--x=v` completion is already a handled token
  shape in `_complete.py`.

**Cost:**

- **A breaking change** (pre-1.0, allowed, CHANGELOG narrative): every
  space-form long option in the wild refuses. One-time sweep of
  footman's own docs/tasks/tests; the gate catches stragglers.
- **The taught error is mandatory, not optional.** `fm build --target
  prod` must never say "unknown task 'prod'" — the splitter has the
  context (option-shaped token, bare follower, option takes a value)
  and must answer "did you mean --target=prod?". This error-quality
  work is the real price of the rule, and it is one-shot learning per
  user.
- **Permanent newcomer friction, one error deep.** First `--jobs 4`
  refuses (teaching `-j 4` or `--jobs=4`). Mitigated by shorts keeping
  space form — most reflexive invocations use shorts anyway. The
  warn-but-accept alternative is rejected: accepting space form means
  parsing it, which resurrects the entire ambiguity machinery the rule
  exists to kill.

**Verdict (recommended):** high value — the readability win lands on
footman's defining feature, an entire subsystem is never built, and the
grammar ends with two rules (long: `=`; short: as today) instead of
per-kind special cases. Moderate one-time cost (sweep + error work),
small permanent cost (one taught error per newcomer). Supersedes the
v3 two-pass, which stays recorded below as the rejected alternative.

### v3 (superseded): one grammar via lenient-then-correct two-pass

Declared statically on the plugin; values on `plugin.cli`, delivered only
to the owner. Grounding: `split.GLOBALS` is static and the completion hot
path keeps a hardcoded test-enforced mirror — plugin options ride a new
manifest `globals` section (schema bump moves the schema guard). Globals
parse before discovery, plugin specs exist after — that ordering is why
v2 proposed `=`-only for plugin options. **Willem rejected the
asymmetry, and footman's own standard agrees:** task options already
accept both spellings (`fm build --target prod`), because they too are
parsed post-discovery. Plugin globals should behave like every other
option.

**The mechanism: lenient walk, then correct (bounded two-pass).**

1. *Pre-discovery, lenient walk:* unknown `--name=value` is
   self-contained; unknown bare `--name` is assumed a **flag** (consumes
   nothing). Dash-shaped followers are therefore never swallowed —
   `--version`, `--json`, `--help` detection stays sound cold. The only
   possible misreading: an unknown option given space-form
   (`--env-file .env.prod`) makes the walk think globals ended at
   `.env.prod`.
2. *Post-discovery, authoritative parse:* full specs in hand, re-parse
   the leading globals — space-form now binds correctly, the boundary
   corrects itself, `split_chain` proceeds from the corrected remainder.
   Unknown-global refusals (with did-you-mean) live here.
3. *The correction pass:* if the corrected boundary changed something
   discovery already consumed — `-C`/`-f`/`--config` (or the uv-handoff
   probe) sat after a space-form plugin option — redo discovery once
   with the corrected globals (right cwd, right cascade). Bounded: two
   passes, then a taught refusal for the pathological cross-cwd case.
   The manifest's cached `globals` section serves as a *hint* to the
   lenient walk, making the first pass right whenever the cache is warm
   — the correction pass is the cold-cache fallback, not the norm.

Cost, honestly: `_run`'s `-C`/handoff handling restructures so path
decisions can replay once, and a rare cold-cache line pays one redundant
discovery. Bought: zero grammar asymmetry.

**What the two-pass can and cannot relax.** The two-pass dissolves
*knowledge-ordering* problems — anything that was ambiguous only because
the spec wasn't known yet: space-form values (done), and **repeatable
options** (`--tag a --tag b`, accumulating — each token still binds one
value, no ambiguity; allowed). It cannot dissolve *inherent grammar
ambiguity*: an optional-value option (`option?`) is ambiguous even with
the spec fully known — in `fm --cache warm build`, no pass count can
decide whether `warm` is the value or the chain start; both readings
parse. Core's own three `option?`s solve it by greed (consume the next
non-dash token), acceptable only because they are terminal actions
(`--install-completion zsh` ends the invocation); greed in front of a
task chain eats the first task. So plugin `option?`s exist with a
sharper rule than core's: **bare = the flag reading; a value must be
`=`-attached** (`--cache` / `--cache=warm`, never `--cache warm`) — not
a two-pass limitation but the only non-greedy answer to a genuinely
ambiguous grammar.

**Differences that remain, and why they're justified:** long-form only
(single-dash aliases stay core's namespace — collision-prone and
hot-path-mirrored); `option?` values `=`-attached (above); values
delivered only to the owning plugin; provenance in `--help`; completion
via the manifest.

Collisions: with core → refusal at discovery; plugin-vs-plugin → refusal
naming both (provenance). Completion may still prefer inserting `=`
(cosmetic, unambiguous), but never requires it.

**Sequencing in `_execute`:** discover (specs known) → authoritative
globals parse (+ correction pass if needed) → pre_tasks hooks (with
`cli`) → finalizers → manifest sync → `_run_tree` (per-task hooks fire
inside the scheduler).

## What stays from the v1 census

- **Composition** (`plugin()`/`include()`) unchanged; one relaxation:
  **lifecycle-only providers** must be pullable (D11) — the `.env`
  built-in has no tasks; `_load_entry_point` today refuses a taskless
  capture.
- **Finalizers** unchanged — the discovery-time tree-editing point
  (TaskView mutation facade). Distinct from run-time task handles in
  pre/post: TaskView edits feed the manifest; run-time injection feeds
  `Context`. Two different animals, kept apart on purpose.
- **Availability gates + `suggest()` completers** — already travel.
- **`tools.*` / `ShellTool`** — library surface, not registration.
- **Closed forever**: completion hot path (the ~20 ms invariant), config
  discovery and uv handoff (plugins arrive through them).

## Cross-cutting rules

- **No different rules for plugins and core (Willem's founding
  principle for this design):** one grammar, one completion pipeline,
  one option model, one hook surface — plugins get core's rules, never
  a dialect of them.
- Provenance everywhere; every refusal names the plugin.
- Collisions loud, local wins (compose's policy, extended to options).
- Plugin config convention: `[tool.footman.<entry-point-name>]` (D14).
- Zero-dep and hot-path purity non-negotiable.
- init quiet+quick (refresh child); pre/post parallel-disciplined (no
  process globals, no prompts; output routes through the task's streams).
- Testing: `Runner`/`recording()` exercise every hook in-process; env
  mutations snapshot/restore under xdist.

## The built-in: `footman.env_files`

A lifecycle-only plugin, pulled like any other —
`plugin("footman.env_files")` — off by default because unpulled by
default; no config key (activation by authoring keeps the tree
self-describing; supersedes v1's config-key idea). init reads `.env` from
the invocation cwd (D12), existing-environment-wins, minimal dialect
(`KEY=value`, comments, quotes; no substitution — core never learns file
semantics, D13). Declares `--env-file=PATH` as its one option. A
consumer's plugin does the same with its own stack loader — three lines,
zero consumer conventions in footman.

## Build phases (refined 2026-07-26 — the build order)

Six phases, each independently shippable. Ordering rules: the carriage
refactor goes first (every hook kind rides `contributions`); each
breaking phase lands **early in a release cycle** (right after a cut, so
it soaks before the next); futures precede hooks so the hook surface
never ships with the body-call hole in the seam table. Dependency chain:
1 → 4 → 5 → 6; phases 2 and 3 are independent breaking changes that only
need to head their own cycles (2 before 5, since plugin options assume
the lexical grammar).

### Phase 1 — carriage: `Group.contributions` (D15) + lifecycle-only providers (D11)

Non-breaking, zero behaviour change.

- registry.py: `Group.finalizers` (registry.py:360) becomes the first
  key of a `Group.contributions` dict (one key per hook kind);
  `capture()`/`reset()` save/restore (registry.py:774, 1184–1191) go
  generic over the dict.
- compose.py: `_fork`'s field copy (compose.py:143) and `_pull`'s graft
  (compose.py:553) walk the dict — the field-census test then covers
  every future hook kind for free.
- discover.py: `load_tree`'s collection (discover.py:135–152) collects
  per kind.
- compose.py: `_import_source`/`_load_entry_point` accept a capture with
  contributions but no tree (via both `plugin()` and `include()`); a
  fully-empty capture keeps the taught refusal (compose.py:73).
- Tests: census enumerates contributions keys; lifecycle-only pull both
  doorways; empty-capture refusal unchanged.

### Phase 2 — grammar: `=`-only values (D19 v4). BREAKING; heads its cycle

One PR — rule + taught refusal + sweep, never split.

- split.py: the GLOBALS kind column shrinks to flag|option; every
  value-consumption state dies; the grammar goes lexical — dash tokens
  are self-contained, the first bare word starts the chain. The greedy
  terminal specials (`--install-completion zsh`) become bare-detect /
  `=`-form.
- The MANDATORY refusal ships in the same change: option-shaped token +
  bare follower + value-taking option → "did you mean `--target=prod`?"
  — never "unknown task 'prod'". Globals AND task options (the splitter
  holds the spec at that point).
- _complete.py: `_GLOBAL_VALUE`/`_GLOBAL_MAYBE` (lines 41/54) and every
  consuming state (92/99/429/598) die; `_consume_globals` goes lexical;
  the hardcoded test-enforced mirror shrinks with it.
- Sweep: every space-form long option AND short (`-j 4` → `-j=4`) in
  docs, tasks.py, tests, README; split.py's own "list options repeat the
  flag" doc line. Lists/dicts already verified compatible (first-`=`
  split) — cover with tests, not new code.
- Tests: ERROR_CASES grows the refusal; completion mirror shrink;
  functional shell tests; `--jobs=-1` dash-value; dict/list spellings
  (`--d=k=1,k2=0`, repeat-accumulate).

### Phase 3 — futures core (D23 v2). BREAKING (memo semantics); heads its cycle

- registry.py: `@task` returns the wrapper; the wrapper IS the object
  everywhere — identity sweep: DAG dedup keys, DEFINING_DIR / SHADOWED /
  _PULLED stamps, `--where`'s `__code__` unwrap, `resolved_signature`
  (already follows `__wrapped__`).
- Run-scoped memo registry: key = (task, bound-args normal form — the
  binder's own); one Future per key; done → value, running → wait,
  fresh → claim and run inline through `run_task` (own ctx, TaskResult
  recorded; hooks fire here automatically once phase 4 lands).
- Runtime wait-graph cycle detection under the registry lock; taught
  cycle-naming error; self-recursion refuses immediately.
- Taught refusals: body-calls to `serial=`/`exclusive=` ("declare it as
  pre= instead" — lane acquisition is boundary-only, the deadlock-free
  invariant) and to `infinite` tasks.
- `@task(volatile=True)`: SUPERSEDED as written — see "Phase 3, as
  built". Volatile became *never shared, whoever asks*: a
  tree-propagating property of the **request**, not a call-only opt-out.
- Failure flow: callee raises in the caller; its failed TaskResult is
  recorded; fail-fast abort cancels waiters like any child. Progress
  gains dynamic unit insertion.
- Cross-phase invariant to carry forward: the memo stores the return
  **snapshotted at `_call` exit** — when phase 4's `set_returned` lands,
  dependents and body-callers keep reading the snapshot.
- Tests: cycle refusal; volatile concurrency (two parallel callers both
  run); lane/infinite refusals; memo keying (CLI node vs body-call with
  the same normal form = one execution, different args = two); waiter
  shares result + attribution; callee output no longer interleaves into
  the caller; identity sweep (`--where`, provenance, dedup).

### Phase 4 — lifecycle hooks + Invocation. BREAKING (`@finalize` retired). One phase, four PRs

**4a — Invocation + `pre_tasks` + `@finalize` retirement.**

- `Invocation`: born with `cli`/`config`/`root`/`cwd`; editable at
  pre_tasks (`inv.env` / direct `os.environ` writes are ordinary code at
  the single-threaded moment; `inv.tasks` = the Tasks/TaskView facade;
  `fail()` → exit 2, `--json` honoured); frozen writes raise, taught;
  carries results later.
- Hook decorators + registration-time signature/arity check (taught,
  naming the plugin); carried via contributions.
- `_app._execute` ordering: merge → pre_tasks → availability gates →
  manifest sync → run. The refresh child runs pre_tasks too (quiet +
  quick) — test: `requires_env` flips available in the child-rebuilt
  manifest when a hook supplies the var.
- `@finalize` retired; migration mechanical; determinism rule
  documented (tree/availability edits never from `inv.cli` — the child
  has no CLI line).

**4b — the per-task ladder + `task.state` + router widening + ResultView.**

- `run_task`: availability → pre_bind → bind → pre_task → body →
  post_task; ctx + `in_task` set BEFORE bind (widen the window at
  executor.py:816; `_env_value` unchanged). Build-time CHECK: the
  prompt guard rides the same `in_task` flag — verify bind-time prompts
  on interactive tasks still behave, or split the flag.
- `task.state`: current-plugin contextvar around each hook call, storage
  on Context, lazy, cleared after post; `post_task(inv, task, result)`
  three args always; a pre returning non-None gets the taught note.
- Pairing: post fires iff any of the plugin's pres fired (bind failure
  still posts; unavailable pairs nothing); posts unwind in reverse
  plugin order. Failures: pre = failed pre-dep; a post exception fails a
  green task; both taught, naming the plugin.
- ResultView: read everything (steps included), one write —
  `set_returned` (report-only; pristine snapshot at `_call` exit);
  redaction stays downstream.
- Env lane: per-task via `ctx.env` only. Tests: two parallel tasks with
  conflicting overlays each see their own; Runner snapshots/restores
  `os.environ` under xdist.

**4c — `wrap_task` + `wrap_bind`** (sugar over the same engine: run to
yield = pre, suspended generator = the paired state, resume = post).
wrap_task: one yield, ResultView, bind failures bypass it (documented —
observe those with explicit pre_bind + post_task). wrap_bind: two
yields, enters at the bind boundary, sees bind failures. Yield-count
violations taught, naming the plugin.

**4d — `post_tasks` + skipped-into-`--json` (D21, same PR).** Main
thread, read-only, `inv.results` + `inv.skipped` + totals; stderr-only
under `--json`; the envelope gains skipped nodes (exact spelling —
skipped:true entries vs an array — decided at build); hook time folds
into `result.duration` (documented).

### Phase 5 — global options (D16/D17/D18/D20)

- `GlobalOption` singletons: construction = registration
  (capture-caught); ownership stamped from the DEFINING module, never
  the importing capture; collisions loud (vs core at discovery;
  plugin-vs-plugin names both).
- Manifest `globals` section; schema bump; move the completion schema
  guard.
- Parsing: under `=`-only, plugin globals are ordinary long options —
  the lexical walk is already right; post-discovery binding delivers
  values to the owner's `inv.cli` and `OPT.value` (frozen after parse;
  taught error outside a run). Long-form only.
- `@task(uses=[OPT])` metadata → task `--help` + read-marking; static
  warning (no hooks + no uses anywhere); runtime notes (undeclared
  `.value` read → taught note; declared-but-unread-this-run → `-v`
  advisory only; reads attribute through the task's ctx window).
- Cross-plugin: import the singleton; unpulled owner → taught error /
  availability reason, composing with `@requires`.
- Completion by reuse: `choices` answered from the manifest hot path;
  path-typed values complete as files; `suggest()` via the `_suggest`
  subprocess, which learns to address options by name.
- `-v` reporting: names + count via the on_note lane; values never
  print (Secret/redact law).

### Phase 6 — `footman.env_files` + docs

- Hook-only plugin, in-tree beside `footman.docs`/`footman.tools`,
  activated by `plugin("footman.env_files")` (off = unpulled; no config
  key). cwd/`.env` only; common-denominator dialect (`KEY=value`, `#`
  comments, blanks, quote stripping, `export` prefix, env-wins, NO
  interpolation, malformed lines `-v`-noted never fatal); one option
  `--env-file=PATH` (path-typed → file completion).
- Docs: composing gains "plugins" (both hook spellings, the pytest
  precedent; the `[tool.footman.<entry-point-name>]` convention);
  configuration documents the built-in; the "manifest is written from
  declarations, never executions" framing; timeless voice, CHANGELOG
  owns the breaking narrative.

### Release mapping (proposal — Willem's call at each cut)

Phase 1 rides any release. Grammar heads 0.22.0; futures heads 0.23.0;
phase 4 heads 0.24.0 (with the `@finalize` retirement); options + the
built-in complete that cycle or head 0.25.0. Compression option if the
cadence prefers fewer breaking minors: 4+5+6 as one "plugins" release,
still headed by 4a.

## Phase 3, as built (2026-07-27, PR #93)

Phases 1 and 2 shipped in **0.22.0**. Phase 3 landed as six commits, and
the design moved in four places while it was being built. What follows is
the rule that shipped; where it disagrees with the prose above, this wins.

### Sharing is a property of the request

One rule, whichever way a task was reached — Willem's constraint: *people
should not have to consider whether the task was called as part of the
initial DAG or the runtime DAG.*

- A run performs a task's work **once per (task, resolved arguments)**,
  whoever asks: a chain segment, a `pre=`/`post=` edge, or a body call.
- `volatile` means **never shared**. Every request for a volatile task
  runs — two dependents get two runs, exactly as two calls do.
- Resolved per request by a ladder, mirroring `keep_going`'s:
  `.opts(volatile=…)` → the task's declaration → inherited from whatever
  asked → shared. Own beats inherited. Tri-state: unset means "whoever
  asks decides", which is what makes both propagation and the
  `volatile=False` pin meaningful.
- It **propagates down** the dependency subtree: a freshly requested task
  asks freshly for what it needs, or "fresh" is a half-truth. The cost —
  marking one task volatile unshares its whole subtree — is a docs
  admonition on the orchestration page, at Willem's request.
- `.opts(volatile=True)` **replaces the deferred `fresh()`**: one spelling
  that works on a call *and* on a declared edge, riding machinery that
  already existed.
- **First-write-wins.** The first result of a run is what the run
  remembers; a fresh re-run gets its own value but never rewrites it, so
  later shared requests stay stable. A volatile request never *reads* a
  cell but does fill an empty one.

Default stays **shared** (`volatile` unset). Willem floated defaulting to
volatile — "path of least surprise, since reuse is an optimisation only the
author can decide" — and was argued off it by three things: the docs
already promise that `fm build --release deploy` runs `build --release`
once and `deploy`'s `pre=[build]` waits on *that* run, and default-volatile
would silently ship a second, **defaulted** build; diamond dependencies
multiply per level; and non-idempotent prerequisites (a clean, a migration)
would break by default rather than by opt-in. His words: "you have
convinced me for now."

### Two identities, deliberately distinct

The DAG's dedup key (`schedule._dep_key`) separates nodes by **any**
difference in policy. A memo cell asks a different question — *is this the
same work?* — so `registry.work_key` is the task plus only the overrides
that change what the work is (`cwd`/`rel`), never those that change how it
runs. Without that split, a fresh request and a shared one could never
share a cell and first-write-wins would be unreachable.

Volatility cannot be a pass over a finished graph the way
`_scope_keep_going` is, because it decides node **identity**: a volatile
node is minted per requester and never registered for reuse, so it is
resolved during `_build_dag`, travelling down `_link`. `ctx.volatile`
carries the resolved answer into the body so calls inherit it.

### The report reads chronologically

Dependency order has no slot for a task reached by a call, which is why the
first cut appended those — a hack. Results are ordered by when each task
began (`TaskResult.started`). Sequential runs are unchanged, since
dependency order already *is* chronological, and a prerequisite still
precedes its dependent; only independent tasks in a parallel run move, to
where they actually ran. Anything that never began sits directly after
whatever prevented it (`TaskResult.blocked_by`) — Willem's rule: *non-runs
should come after the task that ended up not running them* — so the report
reads as cause then consequence. Most non-runs need no placement rule at
all, because they happened at a moment: an unavailable task was asked and
refused, a denied confirm was answered.

The cost is real and bit immediately: the free-threaded CI runners failed a
test that asserted an order between two *independent* prerequisites. Order
between independent tasks is only as deterministic as the run was.

### Parity gaps the rule exposed

Applying "no difference between declared and dynamic" to the code found two
defects, both fixed: a body call slipped past `@requires` availability (an
unavailable task **ran** when called, though the same task as a
prerequisite refuses), and it never asked a `@task(confirm=)` gate. The
confirm is asked *at the call* — the one difference that survives, because
a call cannot be known before the run the way a segment can, and a
segment's gate is resolved up front so a denial prunes the subtree before
its prerequisites run and the human answers everything in one sitting.

### Build gotchas worth not rediscovering

- `executor._call` must invoke `registry.task_body(fn)`, never the handle,
  or the scheduler's own invocation re-enters the machinery and hangs.
- `inherited()` likewise calls the shadowed **body**: a cascade override is
  an override chain like `super()`, sharing the caller's context and
  result, not a second unit of the run.
- An `int` return is a **segment's** exit code; a *call* gets the number as
  a value (`run_bound`/`_call` carry an `as_call` flag). Otherwise
  `n = measure()` returning 7 fails the run.
- `inspect.getsource`/`getsourcefile` do **not** follow `__wrapped__`.
  Missing that is invisible: `getsource` raises, `_empty_body` catches it,
  every empty-body group default silently stops fanning out. Both are
  wrapped once as `registry.task_source`/`task_source_file` — Willem's
  instruction, so the core logic carries no unwrapping caveats.
- The `@task` handle must be **one object per decoration**, registered and
  returned: the DAG dedup key reads `id(fn)`, the cascade tests
  `previous is not fn` to detect shadowing (two handles = a task shadowing
  itself and a `shadow_chain` that loops), and `.opts()` must close over
  the registered object.

### Still open after phase 3

- A DAG node does not yet reuse a cell a body call filled first (the
  reverse works). Closing it needs a decision on how a node whose work was
  already done should *render* — a second entry marked reused, or none.
- Body-called tasks are in the results but not yet units in the live
  progress line.
- Skipped nodes still have no `TaskResult` (D21 stays phase 4), but the
  ordering rule for them is built and tested, so `post_tasks` fills it in.

## Open decisions (all naming and semantics await Willem)

- **D1 — hook names.** Willem's proposal is the front-runner:
  `pre_tasks` / `pre_task` / `post_task` / `post_tasks` (symmetric,
  self-describing; one-letter typo hazard mitigated by the
  registration-time arity check). Final confirmation pending.
- **D2/D3/D4 — collapsed (Willem's sugar insight).** One engine: paired
  hooks with pre-returned state; the generator wrapper ships as sugar on
  top (the suspended generator *is* the paired state). Both spellings
  offered — pytest precedent ships both. Sugar's name SETTLED (Willem,
  2026-07-26): **`wrap_task`** — completes the modifier+task family
  (pre_tasks / pre_bind / pre_task / wrap_task / post_task /
  post_tasks); docs clarify it wraps *executions*, not the function
  definition.
- **D5 — pre_task before binding: AGREED (Willem).** Mechanism settled:
  widen the environ router's window (ctx + `in_task` set before bind);
  `_env_value` unchanged. Build-time check: the prompt guard rides the
  same `in_task` flag — verify bind-time prompts, or split the flag.
- **D6 — SETTLED (Willem, 2026-07-26): `set_returned` ships now;
  `annotate()` deferred to structured results.** (He leaned do-both-now
  — the deferral is workload pragmatism, not a design objection; when
  structured results define the envelope, annotate() is pre-approved in
  spirit.) Pristine-snapshot rule locked by D23: rewrites touch the
  report only.
- **D7 — the fourth hook: SETTLED (Willem: "add it") — `post_tasks`.**
  Run report (results + skipped/never-ran + totals), main thread,
  read-only, stderr-only under `--json`; built in phase 3.
- **D8 — SETTLED (Willem, 2026-07-26).** pre fails the task like a
  failed pre-dep; **an exception in post is task failure too** (his
  words); both named, taught, never a bare traceback. Pairing rule:
  post fires iff any registered pre fired (bind failures still post;
  unavailable pairs nothing).
- **D9 — SETTLED (Willem, 2026-07-26): frozen.** The Invocation raises
  on write at the task moments (taught error); editable only at
  pre_tasks. (Also considered and rejected: renaming the handle `Run` —
  a hook param `run` shadows `footman.run` inside hook bodies; prose
  keeps saying "the run", the type stays `Invocation`.)
- **D10 — SETTLED (Willem, 2026-07-26): single per-task env lane**
  (hooks via the task handle → `ctx.env`; no `TaskView.set_env`).
- **D25 — SETTLED (Willem, 2026-07-26): `@finalize` retired, subsumed
  by `pre_tasks`** — the editable handle carries the tree view
  (`inv.tasks`, the same Tasks/TaskView facade); plain tasks.py may use
  the hook directly; migration is mechanical, pre-1.0 breaking.
  Documented determinism rule: tree/availability edits derive from
  files, config, environment — never `inv.cli` (the refresh child
  builds the manifest with no CLI line; same staleness budget as
  completion).
- **D26 — SETTLED (Willem, 2026-07-26): the handle is `Invocation`**
  (param `inv`) — Plugin was a misnomer once tasks.py could use hooks
  directly. ONE object flows through the lifecycle: born with
  `cli`/`config`/`root`/`cwd`; editable at pre_tasks (`inv.env`,
  `inv.tasks`, `fail()`); frozen facts at task moments; carries
  `inv.results`/`inv.skipped` at post_tasks (no separate RunReport
  class). Vocabulary triangle documented: **invocation** (one fm line)
  / **context** (one task, ctx) / **run** (one step). Internal plumbing
  names not churned; `run()` keeps its name.
- **D11 — SETTLED (Willem, 2026-07-26): lifecycle-only providers
  confirmed.** A capture with contributions but no tree is a valid pull
  (plugin() and include() both); the taught refusal remains for a
  fully-empty capture (no tasks, no Group, no hooks).
- **D12 + D13 — SETTLED (Willem, 2026-07-26): cwd-only, as
  industry-standard as possible, `--env-file=` as the one option.**
  "No interest in fractioning standards" — the dialect is the common
  denominator all dotenv implementations agree on: `KEY=value`, `#`
  comments, blanks, quote stripping, an `export` prefix tolerated,
  existing-env-wins, malformed lines noted under `-v` never fatal, and
  NO interpolation (exactly where implementations stop agreeing).
  **Stated purpose (his words): the built-in is mostly a funnel to get
  people looking at the concept of plugins** — and preempts what would
  otherwise be a top feature request. Anyone needing more writes a
  plugin; that's super low friction now.
- **D14 — SETTLED (Willem, 2026-07-26): `[tool.footman.<entry-point-name>]`
  blessed** — documented, not enforced; one paragraph in the composing
  docs; core never validates plugin sub-tables.
- **D15 — SETTLED (Willem, 2026-07-26): the `Group.contributions`
  dict.** Phase-1 pure refactor, while `finalizers` is the only field
  to migrate; census test covers all future hook kinds for free.
- **D16 — SETTLED (Willem, 2026-07-26): module-level `GlobalOption`
  singletons; D17 absorbed.** Construction is registration (caught by
  capture); the shared-option case killed the decorator spelling (two
  hooks needing one option: decorator-only looks incomplete on one,
  duplicating on both is by-definition a collision, help text would
  duplicate). **Ownership = the provider** (stamped at construction
  from the defining module — never the importing capture, so import
  order can't misattribute; the _module_trees lesson). Delivered to
  `inv.cli` in any of the owner's hooks. The full bundle:
  - **`.value` on the singleton** — the parsed value lands on the
    Invocation; `OPT.value` reads it anywhere inside the run (frozen
    after parse; taught error outside a run). `inv.cli` stays as sugar.
  - **`@task(uses=[OPT])`** — metadata, not binding (never becomes a
    task parameter): task `--help` lists the globals the task consults;
    marks the option as read.
  - **Static taught WARNING** (Willem asked): an option with no hooks
    in its provider and no `uses=` anywhere — text teaches "declare
    uses=[OPT] on the task that reads it".
  - **Runtime detections**: undeclared read (body read `.value` without
    `uses=`) = a fact → taught note suggesting the declaration;
    declared-but-unread-this-run = evidence only (conditional branches)
    → `-v` advisory, never a warning. Reads attribute through the
    task's ctx window (helpers count, hook reads don't, D23
    machinery-calls attribute to their own task).
  - **Cross-plugin use**: import the singleton — the reference IS the
    dependency. Activation belongs to the OWNER's pull: unpulled owner
    → `.value` is a taught error naming the missing plugin; a
    `uses=`-declaring task surfaces it via availability ("needs plugin
    acme.devkit"), composing with @requires.
- **D18 — SETTLED (Willem, 2026-07-26): full symmetry by reuse.**
  `GlobalOption` carries the same completion surface a task parameter
  does — static `choices` (hot-path-answered from the manifest),
  path-typed values complete as files (covers `--env-file=`, no special
  boolean), `suggest()` via the existing `_suggest` subprocess (which
  already imports the cascade where the singletons live). Only new
  piece: `_suggest` learns to address a global option by name alongside
  params. Docs line: "options complete like parameters, wherever they
  live."
- **D19 v4 — SETTLED (Willem: yes, 2026-07-26): `=`-only for EVERY
  value, shorts included.** Ships as its own early-in-cycle breaking PR
  with the "did you mean --target=prod?" refusal in the same change.
  One sentence, zero exceptions: *a value is always
  `=`-attached* (`--jobs=4`, `-j=4`, `--target=prod`). The v3 hybrid
  (shorts keep space) traded rule-simplicity for getopt familiarity —
  but the shorts are a dozen leading-position globals, `-j=4`'s
  unfamiliarity is one taught error deep, and full uniformity makes the
  ENTIRE option grammar lexical: `_GLOBAL_VALUE`/`_GLOBAL_MAYBE` and
  every value-consumption state die in both `split` and the hot path —
  `_consume_globals` becomes "dash tokens are self-contained; the first
  bare word starts the chain". Bonus: values that start with a dash
  (`--jobs=-1`) parse trivially where space form always choked.
  Footman-internal consistency (one spelling per concept — the dotted
  addressing lesson) beats consistency with getopt tradition footman's
  chain grammar never fit anyway. Mandatory companion unchanged: the
  "did you mean --target=prod?" refusal. Breaking, pre-1.0. Awaiting
  final go.
  **Collections verified compatible:** every name/value split in the
  splitter is first-`=` (`split("=", 1)`), so nesting survives by
  construction. Lists: `--opt=a,b` (comma-split, `nosplit` opt-out
  unchanged) and `--opt=a --opt=b` (repeat-accumulate — the repeat
  spelling simply becomes `=`-form; split.py's own doc line "list
  options repeat the flag (`--tag a --tag b`)" gets the sweep). Dicts
  (first-class: `dict[K, V]`, `_consume_pair`): `--d=k=1,k2=0` and
  `--d=k=1 --d=k2=0` both work — the token yields value `k=1,k2=0`,
  comma-splits to pairs, each pair splits on *its* first `=`, so dict
  *values* may even contain `=`; keys may not (already true today).
  Verified in the bind flow: duplicate scalar keys are last-wins
  (`result[k] = v`); `dict[K, list[E]]` appends per key
  (`--d=k=a --d=k=b` → `{"k": ["a", "b"]}`) — commas never play two
  roles, because pairs split on commas and list-values grow by
  repeating the key. `env()` on a dict stays a SpecError (outer-only).
  Values containing commas need `nosplit` (existing semantics,
  unchanged). Bonus: `=`-form is *more* readable for dict pairs — the
  reader anchors on the first `=`, where `--d k=1` in a chain read as
  value-or-task.
- **Tools machinery: fully separate, by construction.** The `=` rule
  governs fm's *own* command line only. `tools.*`/`.flags()` render the
  **child tool's** argv in the child's dialect (`single_dash`, the
  tool's own `=` vs space conventions — `_emit` in tools.py), scraped
  stubs record the tool's syntax, and `run()` passes what it's given.
  The one shared surface is `--` passthrough, which is opaque by design
  — tokens after `--` are never footman grammar. A branded CLI
  (`App`/`Brand`) inherits fm's grammar, which is the point of
  branding.
- **D20 — SETTLED (Willem, 2026-07-26): names + count, by reuse.**
  `env: dotenv set 7 keys: API_TOKEN, APP_HOME, …` through the
  existing `-v` advisory channel (config's on_note lane);
  values-never-print is already law via Secret/`_describe.redact` — no
  new display machinery.
- **D21 — SETTLED (Willem, 2026-07-26): skipped nodes appear in the
  `--json` envelope** ("no consumers, breaking things costs nothing"),
  same PR as post_tasks; exact spelling (skipped:true entries vs a
  skipped array) at build time. The envelope and the plugin surface
  must agree about what happened.
- **D22 — the five-hook ladder: SETTLED (Willem).** `pre_tasks` /
  `pre_bind` / `pre_task` (post-bind default) / `post_task` /
  `post_tasks`; named moments, no flag. Remaining sub-decision only:
  ~~how post_task receives pre states~~ — **DISSOLVED (Willem,
  2026-07-26, his goal): `task.state`**, per-task plugin state as the
  next virtualised global resource (house pattern: the environ
  router). A namespace scoped to (plugin, task-execution) —
  current-plugin contextvar around each hook call, storage on the
  task's Context, lazy creation, cleared after post. Consequences:
  `post_task(inv, task, result)` — three args, always, no state
  threading, no arity variance; both pres write the same namespace;
  partial-failure cleanup works (state written before a raising pre is
  still there for post's teardown — a correctness win over
  return-threading); wrapper sugar unchanged (locals around yield). A
  pre hook returning non-None gets a taught note pointing at
  task.state. The wrapper side stands (`wrap_task`: one yield,
  ResultView, bind failures bypass it; `wrap_bind`: two yields, enters
  at the bind boundary, sees bind failures).
- **D23 v2 — tasks as run-scoped futures: SETTLED (Willem: yes,
  2026-07-26) — memo-by-default semantics and the wrapper identity
  sweep both accepted; its own build phase.** Body-calls route
  through the machinery: memoised per (task, bound args), wait if
  running, run inline if fresh — full task semantics (own ctx, hooks,
  TaskResult; closes the body-call hole). Subsumes and retires
  `ctx.returned()`. Hard edges settled in the analysis: runtime cycle
  detection; **v1 refuses body-calls to serial/exclusive tasks** (lane
  acquisition is boundary-only — the deadlock-free invariant) and to
  infinite tasks; pristine-return snapshot keeps D6's reported-only
  rule. **Memo opt-out — SUPERSEDED DURING THE BUILD.** The original
  answer was `@task(volatile=True)` as a *call-only* opt-out, with
  `pre=` still deduped, on the reasoning that *pre= declares "after X
  has run" while a call says "run X"*, so a volatile `clean` shared by
  two builds must not run between them. Willem reversed it: that is a
  difference between declared and dynamic runs, and there should be as
  little difference as possible between them beyond what is unavoidable
  by construction. The rule that shipped is in "Phase 3, as built"; the
  deferred call-site `fresh()` is retired, replaced by
  `.opts(volatile=…)`. Its own build phase — the largest semantic change
  in the plan.
- **D24 — SETTLED (Willem, 2026-07-26): closed as trivial.** Under
  `=`-only, repetition is the splitter's existing list behaviour
  (`--tag=a --tag=b` accumulates, `--tag=a,b` comma-splits, `nosplit`
  opts out) — same rules as task parameters. His framing, now a
  cross-cutting rule of the whole architecture: **no different rules
  for plugins and core — plugins get core's rules, never a dialect.**

(v1's D1–D15 are absorbed above; nothing from v1 was ever settled.)
