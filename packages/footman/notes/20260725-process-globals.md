# process globals — design note (v2)

How footman keeps tasks maximally parallel while cwd, env, and the terminal
are process-global: policy tokens, the two regimes, the arbiter, and the
interception layers. (Renamed from `cwd-policy.md`, 2026-07-25 — the design
outgrew the cwd; stdin folded in the same day.)

**Status: BUILT AND MERGED** (2026-07-25): the core landed as PR #53, the
ask front-loading + cookbook as PR #54 — every increment gated, CI green
across the matrix. Remaining crumbs (not dotted-blocked): status line
beside a live wizard, a late-suggest+`--no-input` test, `serial`/
`exclusive` in `--describe`/`--json`. The Foundations planning task is now
unblocked (its implemented-and-tested precondition is met). **Replaces the
v1 chdir-lock design entirely** — a deadlock audit killed it; see the
obituary at the bottom, so nobody resurrects it. Breaking change (removed
the automatic in-process chdir *and* the global env application).

**Amendment (found during the build):** a `-C` global already existed
(F36 — "run as if launched from PATH"; it chdirs around `_execute` and
restores). The "no `-C`" decision below means no flag *redefining root* —
`-C` moves the whole launch point, so discovery, `asinvoked`, and
`invoked_dir` all follow it automatically; they compose cleanly.

## The problem

Process cwd and env are process-global; task bodies are plain Python running
in parallel threads. Today:

- `executor.run_task` sets `ctx.cwd = defining_dir(fn)` when unset — an
  undocumented, unconfigurable "taskfile" policy.
- Subprocess `run()` passes `ctx.cwd`/env explicitly — clean, parallel, no
  global state.
- In-process callables get a **real `os.chdir` + `os.environ` patch** in
  `_process_state` (`context.py`), guarded by a re-entrant lock — any
  in-process call that needs either silently serialises the run.
- `ctx.cwd` is `Path | None`; no config knob, no per-task or per-use override.

## Core principle

**In the parallel regime, nobody mutates process globals — cwd and env are
context data, not process state. The only code allowed to touch the real
globals acquires that right at a task boundary, where the scheduler can order
it.** Mid-body lock acquisition inside an arbitrary dynamic call graph
(fan-outs, body-calls) is hold-and-wait by construction and cannot be made
deadlock-safe (audit, below); boundary acquisition can always be scheduled.

Consequences, in order:

1. The parallel path has **zero locks and zero deadlock surface**.
2. Serialisation only ever happens by **declaration** (`serial` / `exclusive`
   / `interactive`), never as an emergent runtime convoy.
3. Every cross-task wait that remains is **arbiter-mediated**: footman holds
   the complete waits-for graph, so any cycle or starvation is a taught error
   naming the tasks — detected, never suffered as a hang.

## Policy tokens (unchanged from v1, one refinement)

`cwd` is a policy token or an absolute path:

| Token | Resolves to |
|-------|-------------|
| `"root"` | the directory of the highest tasks file in the cascade — `files[0].parent` from discovery — or, under `-f <file>`, that file's own directory. *Not* `find_repo_root`'s ceiling. Pinned by discovery; **no `-C` flag**, the invocation cwd never redefines it. |
| `"taskfile"` | the directory of the cascade file the task was defined in — or `include()`d into (the `_overlay`/`_tag` stamp gives include()d tasks the including file's dir). Today's implicit behaviour, named. |
| `"asinvoked"` | the process cwd where `fm` was launched, pinned as a snapshot at startup. |
| `"unmanaged"` | footman stays out entirely — and this now has a crisp operational meaning: **it is the one thing that switches the Popen injection off** (below). Subprocesses spawn with `cwd=None` (inherit the live process cwd), `ctx.cwd` reflects the process cwd at task start, and the unguarded-call teaching is silent — this token *is* the "insensitive at my own risk" declaration. `rel=` with it is a taught error. Documented hazard: while a serial task holds the real cwd elsewhere, an unmanaged spawn launches from *that* directory — the accepted meaning of unmanaged. |
| absolute `Path` / `str` | itself. A relative path here is a taught error — "use `rel=`". |

Fallback chain: `taskfile` → `root` when the task has no `DEFINING_DIR` stamp
(config-mounted plugins). `root` needs no fallback (discovery refuses to run
with no tasks file). `root` can sit below the invocation cwd (`fm -f
subdir/tasks.py`) — the tokens are genuinely independent directions.

### The ladder (unchanged from v1)

1. **Config default**: `[tool.footman] cwd = "taskfile"` (leaning `taskfile` —
   today's behaviour). Lands on `Context` as `cwd_policy` beside
   `shell_default`.
2. **Per definition**: `@task(cwd=…, rel=…)` (and `@group.default(cwd=…)`),
   stamped via `_apply_policy`.
3. **Per use**: `.opts(cwd=…, rel=…)` — new `_OPTS_ATTRS` entries; values
   hashable, so DAG dedup identity keeps working (same task at two cwds is two
   nodes).
4. **Per manual body-call**: the same `.opts()` spelling.
5. `TaskView.set_opts(cwd=…)` for free (finalizers).

`rel=` appends to the resolved base; a nearer `rel` replaces a farther one; an
absolute `rel` is a taught error mirroring the relative-`cwd` one.

Below the ladder sit **per-call overrides** — `run(cwd=, rel=)` and
`Tool.opts(cwd=, rel=)` — governed by one uniform rule (decided 2026-07-25):
**`rel=` always suffixes whatever base is in force at the point it appears.**
On the ladder that's the policy-resolved base (nearest `(cwd, rel)` pair
wins); at a call site it's `ctx.cwd`, so a bare `rel="web"` means
`ctx.cwd / "web"`; in `chdir(rel=…)` likewise. `rel=` alone is therefore
legal everywhere a `cwd=` is; the `unmanaged`-has-no-base error and the
absolute-`rel` error apply unchanged at every surface.

`ctx.cwd` is resolved once, before the body runs, and is **always concrete**
(`Path | None` → `Path`); a preset `ctx.cwd` (tests / `use_context`) still
wins, so `testing.Runner` and `recording()` don't churn. Resolution is pure
path arithmetic — the directory need not exist at resolve time; existence
errors surface where the path is used.

**Body-call semantics:** direct body-calls (`lint(fix=True)`) inherit the
caller's `ctx.cwd` — intended, documented. `lint.opts(cwd=…)(…)` installs the
override as `ctx.cwd` around the base invocation (a save/restore of the field
— pure data now, no process state anywhere near it).

## The two regimes

### Parallel regime (default): cwd is data, env is data

`ctx.cwd` is *the* working directory; the process cwd is never touched. For
work that needs a cwd, three lanes — the first two keep **full parallelism**:

1. **Subprocesses** (`run`, tools): explicit `cwd=`/`env=` per child —
   injected from `ctx.cwd`/the overlay automatically, so bodies pass nothing
   for the task's own directory. Per-call overrides (pure data,
   explicit-wins, fully parallel): `run(cmd, cwd=…, rel=…)` and, on the
   bridge, the existing policy carrier — `tools.npm.opts(rel="web").run(…)`
   (`Tool.opts` returns a `Tool`, so a bound `web_npm =
   tools.npm.opts(rel="web")` roots every call through it).
2. **In-process tool calls** (Python tools only — a binary like ruff or git
   *never* runs in-process, so lane 1's explicit `cwd=` covers it exactly):
   honoured *without* chdir by **demoting to the subprocess twin** — same
   command, same semantics, still fully parallel, with a `-v` note ("pytest:
   ran as subprocess — in-process can't apply cwd in parallel"). Degradation
   is losing a startup-time optimisation, never losing width, never a lock.
   **Demotion is per-call and conditional**: when the resolved `ctx.cwd`
   equals the run-start cwd (the single-package common case) the call stays
   in-process untouched — only a *foreign* cwd demotes, i.e. exactly the
   case `_process_state` silently serialised before. In-process at a foreign
   cwd remains available via `serial=True` (real chdir, declared).
3. **Bare `run(callable)`** that asks for a cwd it can't have (resolved
   `ctx.cwd` ≠ the run-start cwd, task not serial/unmanaged): taught error
   with the three exits — build paths from `ctx.cwd`, use the subprocess form,
   or mark the task `serial`. In-process callables in parallel tasks are
   cwd-insensitive by contract; one-line hazard note that a concurrent serial
   task may move the live cwd under a callable that reads it anyway.

**The translation table launches empty — and may stay that way** (Willem's
correction, 2026-07-25: ruff is a binary — binaries never run in-process, so
subprocess `cwd=` is already exact for them, `git -C` included; translation
was solving a non-problem there). The table's only possible members are
*Python* tools with an in-process lane, and the bar is an argument *exactly*
equivalent to "run in X" — pytest fails it (invocation dir affects rootdir
resolution and relative test ids), so it demotes. Keep the optional
per-driver field specced for the day a qualifying tool appears; until then
the rule is one sentence: an in-process call that needs a cwd other than the
run-start cwd runs as its subprocess twin. Wrong-but-close translations are
worse than demotion; exactness is the bar.

Env rides the same lanes. `_process_state` and `_state_lock` are **deleted**.

### Serial regime (declared): global mutation is legal because you own it

`@task(serial=True)` (+ `.opts(serial=True)` per use) declares "this task
needs the process globals to itself". The scheduler runs **at most one serial
task at a time, concurrently with the full parallel pool** — a single-lane
queue that overlaps everything (parallel tasks don't consume the globals, so
they can't be harmed). Inside a serial task footman restores today's
convenience automatically and safely: real chdir to `ctx.cwd` around the
body, env overlay applied to the real `os.environ`, everything snapshotted
and restored on exit. Durable cross-task env is not a supported pattern —
that's `ctx.env`.

**Performance principle (record it everywhere the serial advice appears):
serialising a process that parallelises *itself* costs little.** A
well-designed parallel tool (pytest-xdist, a build system, a compiler driver)
saturates the cores on its own; the serial lane's single-file line is not a
throughput loss, because the only thing the lane forgoes is running *other*
tasks beside it — which such a tool leaves no room for anyway. This is the
answer to "won't `serial=True` hurt?" for exactly the tasks most likely to
need it.

This is the entire multithreading education a user needs: *"if your task
needs the process to itself, say `serial=True`."* No locks, no escalation, no
join/re-target rules. `-s` (whole-chain serial) makes every task effectively
exclusive, so "it works under `-s`" is automatically true and the parallel-
regime errors explain the difference.

### `exclusive=True`: the honest full drain

`serial` was quietly carrying two meanings; split them. **Globals-serial**
(`serial=True`, the common migration case) costs one lane and overlaps.
**Machine-exclusive** (`exclusive=True` — benchmarks needing a quiet box,
migrations that must see no concurrent activity) is the old drain: runs with
*nothing* else in flight. Same arbiter, one mechanism, two declared strengths
— the expensive behaviour is never the accidental default.

## The fourth global: stdin (2026-07-25 — the interactive fold-in)

Willem's observation: **interactive input is just another process global** —
one terminal, one stdin, *consumed* rather than mutated. The old
interactive-input plan's two layers land exactly on the two regimes, and
nothing in its scheduling seam is sacred (Willem: process-globals is 100%
the way forward). One prior insight survives verbatim, the three-layer
de-muddling: (1) the task **body** is ALWAYS an in-process thread — never a
subprocess — so dynamic DAGs (run()-output → parallel() with computed args,
closures, shared state) are preserved by construction; (2) **question
resolution** happens in the main process at task boundaries; (3) `run()`/
tools calls are the only per-call subprocess choice. Don't re-flatten this
to "exclusive-lock A/B" or "force bodies to subprocesses" (it has been
mis-reconstructed both ways before).

### Question layer = the data lane

`ask()` params and `@task(confirm=)` are the stdin analogue of `ctx.cwd` and
the env snapshot: **the body receives values, never touches the terminal.**
Precedence per parameter: CLI value → `env` var → param default → prompt —
the prompt only for a *defaultless* `ask()` param, resolved at the task
boundary before the body runs. Off a tty / under `--no-input`: taught error
naming the flag — never a silent default, never a hang. `--yes` auto-answers
`confirm=`; confirms gate top-down before any execution, each gating its own
subtree. Recorded durations exclude think-time. Plain `ask()` under `--json`
is fine (prompts ride stderr/tty; the JSON stays clean).

**Ask-as-early-as-correct** (settles the old ask-as-ready vs ask-all-up-front
question: the DAG decides). Boundary questions are first-class arbiter
objects, so the scheduler front-loads every question whose answer can't be
affected by a dep's effects — the human answers everything up front and
walks away; max parallelism *and* min human-wait. Questions with a live
`suggest` completer that must see a pre-dep's output resolve late, after
those deps: as early as correctness allows, no earlier. Front-loading also
shrinks the fail-fast-during-live-prompt window to near zero for the
question layer (a stuck `stdin` read isn't killable — the fewer mid-run
prompts, the better). Post-1.0 door this opens: questions-as-objects make
the answer transport pluggable (terminal today; a notification or web answer
later).

### Terminal ownership = the console lane

`@task(interactive=True)` declares "my body owns real stdio" — a wizard or
REPL. It acquires the `console` resource at its boundary: at most one owner
at a time, **overlapping the parallel pool** — the old "hard-error under
parallel width > 1" is dead. Everyone else keeps running captured and
flushes when the console frees; the owner runs with `sink=None`; the status
line yields while a wizard owns the terminal. `interactive=True` under
`--json` stays a taught error (a body owning stdio has no machine-readable
story). The old open question "in-process vs force-external interactive
body" stays open and composes: force-external would wire the wizard
subprocess to the real tty — the console lane doesn't care which side of the
fork the body runs on.

### Guards (the stdin router)

Same principle, third stream: **replace `sys.stdin` for the run** with a
guard object whose reads raise a taught error in a non-interactive parallel
task — "task X reads stdin — declare the value with `ask()`, or mark the
task `interactive=True`". This catches `input()` too: with `sys.stdin`
replaced, Python falls back to `sys.stdin.readline()` instead of the C
readline path. The public `prompt()`/`confirm()`/`select()` primitives keep
their own friendlier guard (same two exits). Console owners and boundary
question resolution pass through to the real stdin. Direct `/dev/tty` opens
(`getpass` does this) bypass the router → on-their-own table.

### What survives of `interactive-input-plan.md`

Nearly everything — the question-layer machinery is orthogonal to the
concurrency model: the `--yes`/`--no-input` globals, `@task(confirm=,
interactive=)` stamping + manifest keys, the guarded primitives +
`select()`, the `ask()` marker with coercion/menu/getpass reuse, dynamic
select via the live completer, the security hardening (Secret redaction,
ANSI-strip, clean prompt abort), and the docs shape. **Only its Phase 4
(the scheduler seam) is replaced**: `_prompt_lock` held for the node's life
becomes the `console` lane; the width>1 error dies; ask-at-launch upgrades
to ask-as-early-as-correct.

## The arbiter

A small set of named scheduler resources: `globals` (cwd + env), `console`
(interactive — the existing flattening rides the same mechanism, unifying the
serialisation points), and "the run" (exclusive claims all). Acquisition is
at **task boundaries** (the marker is checked at invocation, before the body),
which is what keeps it schedulable. Dynamic cases:

- **Ancestry exemption**: "drain" / "sole lane" never counts an ancestor
  parked in a pool wait on your own subtree — it's blocked in footman code and
  can't touch globals. So a parallel parent fanning out to a serial child
  works: siblings finish, parent exempt, child runs. This is v1's "join"
  relocated to the one place it's always safe — a single lineage.
- **Lineage inheritance**: a serial task body-calling another serial task
  inherits the lane; nested exclusivity is one lineage, trivially safe.
- **Sibling serial partials in one `parallel()`**: both queue; run one at a
  time. Ordering, not conflict.
- **Multi-resource tasks acquire atomically**: a task may need several
  resources (an interactive migration: `console` + `globals`); the grant is
  all-or-nothing at the boundary, so partial holds can never chain into
  hold-and-wait between lanes.
- **Detection by construction**: footman mediates every remaining cross-task
  wait, so the arbiter holds the full waits-for graph. Cycles and starvation
  (e.g. a drain blocked behind an `infinite` serve task, or console waiters
  behind a long wizard — named on the status line) are taught errors or
  visible diagnostics naming the tasks involved — never hangs.

## The three interception layers + two guards

Installed at run start, delegate untouched outside a run. Prior art: the
stdout router — these are routers two and three.

### `os.environ` — virtualise (it has a real choke point)

`os.environ` is one shared `os._Environ` instance; **wrap the class methods**
(not replace the object) so every alias (`from os import environ`,
`os.getenv`) is covered and `_Environ`'s key encoding (Windows case rules) is
preserved. Mutators are just `__setitem__`/`__delitem__` (`MutableMapping`
funnels `update`/`pop`/`clear`/`setdefault` through them); readers are
`__getitem__`/`__iter__`/`__len__`/`copy` (`get`/`in` ride the Mapping mixin);
`os.environb` gets the same wrap. Per-read cost: one contextvar lookup.

- **Reads, parallel task**: run-start snapshot + the task's overlay — exactly
  what the subprocess twin would see as `env=`. Fixes the in-process/
  subprocess parity hole. Correct answers need no warning; noise floor zero.
- **Writes, parallel task**: scoped to the task's context overlay — visible to
  its own reads and its children's spawns, invisible to siblings. Teach-once,
  task-attributed note: "task `deploy` sets `AWS_PROFILE` via `os.environ` —
  footman scoped it to this task (children see it, siblings don't). Say it on
  purpose with `ctx.env` / `env=`."
- **`os.putenv`/`os.unsetenv`, parallel task**: taught error (they bypass the
  mapping even in plain Python).
- **Serial/exclusive tasks**: pass through to the real environment — theirs.
- **Base snapshot** pinned at run start; parallel spawns build child env from
  snapshot + overlay, never live `os.environ`, so serial-task mutations can't
  leak into concurrent children.

### `subprocess.Popen` — inject (it's the spawn choke point)

Nearly every spawn funnels through `Popen.__init__` (`subprocess.run`,
`check_output`, `os.popen`, third-party code). Wrap it:

- **`env=None` in a parallel task**: inject snapshot + overlay (including the
  task's scoped `os.environ` writes) — a raw child sees what the task sees.
- **`cwd=None` in a parallel task**: inject `ctx.cwd` — the author meant
  "here". **Unless the task's policy is `unmanaged`** — the one off-switch.
- **Explicit args always win**, untouched — including `env={}` (deliberately
  clean). Composition bonus: an explicit `env=os.environ` is *already*
  correct, because Popen copies the mapping through the virtual layer.
- **Serial/exclusive/outside-run**: pass through.
- Teach-once note: "task `deploy` spawns `git` via raw subprocess — footman
  filled in `cwd` and 3 env vars from the task context. Prefer `run()` for
  capture and reporting, or pass `cwd=`/`env=` to make it deliberate."

This upgrades the old guidance: raw subprocess is **quietly correct in
parallel**; `serial` shrinks to in-process code mutating globals beyond the
choke points.

### `os.fork` / `multiprocessing` — detect and teach (worthwhile tradeoff, Willem 2026-07-25)

Both have Python-level choke points, so they move *out* of the on-their-own
table and into detection:

- **`os.fork`** is a module attribute → wrap it. Fork from a parallel task
  gets a taught warning: forking a threaded process is unsafe (the child can
  inherit locks mid-hold; CPython itself deprecates fork-with-threads) —
  "mark the task `serial=True`".
- **`multiprocessing.process.BaseProcess.start`** is pure Python → wrap it.
  Teach-once, task-attributed: "task `X` spawns worker processes from an
  in-process call — mark it `serial=True`. A tool that parallelises itself
  loses almost nothing in the serial lane: it saturates the cores on its
  own." This also covers `concurrent.futures.ProcessPoolExecutor` (rides
  `BaseProcess.start`). The note also says why correctness wants it: spawn
  children go through `fork_exec` directly and inherit the *real* process
  env, missing the task's overlay.
- **pytest-xdist doesn't use multiprocessing** — execnet spawns local workers
  through `subprocess.Popen`, so xdist workers ride the Popen router and get
  injected cwd/env like any raw spawn. The in-process xdist *master* is still
  cwd-sensitive (rootdir), so the pytest driver demotes/serialises regardless
  — but the worker side is quietly correct for free.

Warn (not error) is the leaning: spawn-method multiprocessing from a thread
does work; the teaching is about scoping and safety, and the limitations are
documented plainly rather than policed.

### cwd — guard, never virtualise

cwd is ambient state with two writers and a thousand readers (open(),
every relative Path op, C extensions) — virtualising it would lie to C code.
So: **`os.chdir`/`os.fchdir` in a parallel task → hard taught error** ("mark
it `@task(serial=True)`, or build paths from `ctx.cwd`");
**`os.getcwd`/`Path.cwd()` in a parallel task → warn-once** nudging
`ctx.cwd` (it can now return a concurrent serial task's directory).
Ergonomics so nobody misses chdir: `footman.cwd()` → concrete Path.
`footman.chdir()` is **kept** (decided), as trivial sugar inside
serial/exclusive tasks only (plain `contextlib.chdir`, default target
`ctx.cwd`, keeps `ctx.cwd` in sync; the taught error elsewhere). Its
arguments follow the **marker grammar exactly** — a token or absolute path,
`rel=` for suffixes, a bare relative path is the same taught error (decided:
consistency, no CM-special grammar).

### Truly on their own (one docs table)

Shrunk by the fork/multiprocessing detection above to:
`os.system`/`os.spawn*` (C-level spawn; the Python names are module
attributes, so a warn is cheap), direct `/dev/tty` opens (`getpass` — reads
the terminal past the stdin router), and C extensions spawning, chdir-ing,
or reading the tty natively — the boundary of what a pure-Python runner can
police. Each row gets the same advice: `run()`, explicit args, `ask()`, or
the matching declared marker (`serial=` / `interactive=`).

## Parallelism claim (pinned)

**The only non-parallel execution left is declared**: `serial` (one-lane,
overlaps the pool), `exclusive` (full drain), `interactive` (console lane),
and run-level `-s`. Everything else keeps full width — the worst degradation
anywhere else is *speed* (in-process → subprocess demotion), never width.
Every remaining wait is arbiter-visible with cycle detection.

## Companion change: the cascade walk becomes configurable (unchanged from v1)

Tri-state config option (key leaning `cascade`), **user-level-only**
(`config.USER_LEVEL_KEYS` beside `gc`): `"none"` (cwd's tasks.py or exact
`-f` only) / `"repo"` (default — `.git` ceiling down to cwd, today) /
`"filesystem"` (past repo boundaries; `root` moves to the highest file).
`FOOTMAN_CASCADE=none|repo|filesystem` overrides the key (env over user-level
config, the `FOOTMAN_NO_GC` relationship); read in `resolve_task_files`,
shared with completion (`_suggest`), so completion and execution can never
disagree. Unknown value → taught error naming the three tokens. This is a
*discovery* change recorded here because `root`'s meaning depends on it.

## Decided (Willem)

- 2026-07-24: tokens `root`/`taskfile`/`asinvoked`/`unmanaged`; no separate
  insensitivity marker (`unmanaged` is the declaration); `rel=` composition
  rules; no `-C`; `root` = `files[0].parent`; the cascade tri-state +
  `FOOTMAN_CASCADE`; the ladder.
- 2026-07-25: **replace the v1 chdir-lock design wholesale** ("so much better
  it's not even comparable"). Two regimes + boundary-acquired arbiter
  resources; `serial` = globals lane overlapping the pool (not a drain);
  **`exclusive` split out** as the honest full drain (Willem likes this a
  lot); environ virtualisation with scoped writes; Popen injection with
  `unmanaged` as the off-switch; cwd guard-not-virtualise; teaching via
  correct-behaviour-plus-note over errors wherever a choke point allows it.
- 2026-07-25 (later still): **stdin is the fourth process global** — the
  interactive-input plan folds in; nothing in its scheduling seam is sacred.
  Question layer = data lane (boundary-resolved values), `interactive=` =
  the `console` lane (overlaps the pool; width>1 error dead), stdin router
  guard, ask-as-early-as-correct.
- 2026-07-25 (final pinning): the five open calls closed on the
  recommendations — see Open decisions (now a decided list). `footman.chdir()`
  argument grammar mirrors the markers: consistency, no CM-special rules.
  Per-call overrides confirmed: `run(cwd=, rel=)` + `Tool.opts(cwd=, rel=)`
  (the existing policy carrier — flags live on `.flags()`); `rel=` alone is
  legal everywhere, suffixing the base in force at that point (`ctx.cwd` at
  call sites).
- 2026-07-25 (later): fork/`multiprocessing` **detection is a worthwhile
  tradeoff** when the limitations are clearly explained (wrap `os.fork` +
  `BaseProcess.start`); record the performance principle (self-parallelising
  processes lose little when serialised); the translation table launches
  **empty** (binaries never run in-process — subprocess `cwd=` is exact for
  them; pytest fails the equivalence bar; demotion is the rule); a didactic
  docs category is wanted, planned *after* implementation — named
  **Foundations**.

## Open decisions

**None — fully pinned 2026-07-25** (Willem blessed the recommendations):

1. Default policy token: **`taskfile`** (today's behaviour — one breaking
   lesson, not two).
2. Note channel: **footman stderr note** (capture-proof, task-attributed;
   `warnings.warn` rejected).
3. Marker names: **`serial=` / `exclusive=`** as specced.
4. **`footman.chdir()` kept**, serial/exclusive-only, arguments mirror the
   marker grammar (see the cwd guard section).
5. `os.system`: **warn** (spawn bucket, not mutation bucket).

Deferred, maybe never: spawn-by-qualified-name for picklable callables as a
parallel-preserving lane for cwd-sensitive `run(callable)`.

## Implementation sketch

- **`config.py`** — `cwd` token validation (∈ {root, taskfile, asinvoked,
  unmanaged} or absolute); `cascade` user-level key; thread to
  `Context.cwd_policy`.
- **`context.py`** — delete `_process_state`'s chdir + global env application
  (and `_state_lock`); env router (wrap `_Environ` methods, install/restore at
  run boundary); Popen wrapper; `os.chdir`/`putenv` guards + `getcwd` warn;
  run-start env/cwd snapshots; `footman.cwd()`; `parallel()` stays lock-free;
  stdin router (guard object replacing `sys.stdin`; pass-through for console
  owners + boundary questions).
- **`schedule.py`** — arbiter: resource lanes (`globals`, `console`, run),
  boundary acquisition, atomic multi-resource grants, ancestry exemption,
  lineage inheritance, waits-for graph with cycle/starvation taught errors;
  `interactive` rides `console`; question front-loading
  (ask-as-early-as-correct over the DAG).
- **`registry.py`** — `cwd=`/`rel=`/`serial=`/`exclusive=` on `@task` +
  `@group.default`; `_OPTS_ATTRS` gains all; `_Opted.__call__` installs a cwd
  override around body-calls; validation (token-or-absolute cwd, relative
  rel).
- **`executor.py`** — replace the `defining_dir` one-liner with the ladder
  resolve → `ctx.cwd` (preset wins); serial tasks: apply globals around the
  body with snapshot/restore.
- **tools bridge** — in-process → subprocess demotion when a call needs a
  non-run-start cwd (the translation field is specced but launches empty —
  see the lanes).
- **`manifest.py`/listings** — surface `serial`/`exclusive` in `--describe`/
  `--json` (scheduling-relevant); cwd policy per task nice-to-have.
- **Tests** — token resolution + fallbacks; `-f` root; ladder precedence; rel
  append/replace + taught errors; serial lane overlaps pool (one at a time);
  exclusive drains; ancestry exemption; sibling serial partials queue;
  waits-for cycle → taught error; environ router (read parity, scoped writes,
  putenv error, snapshot spawn base); Popen injection (env=None, cwd=None,
  explicit wins, env={} untouched, unmanaged off-switch, serial passthrough);
  chdir guard + getcwd warn; body-call `.opts(cwd=)`; DAG dedup distinguishes
  cwds; preset ctx.cwd wins (Runner unaffected); stdin router (input() in a
  plain parallel task → taught error; console owner passes through);
  interactive overlaps the pool (no width error); front-loaded asks vs
  late-resolved live-completer asks; console+globals atomic grant. Sweep
  footman's own `tasks.py` for in-process calls needing lanes.
- **Docs** — "Working directory & environment" page: the token table, the
  ladder, the two regimes, the three routers + two guards, the on-their-own
  table, migration notes ("in-process tools no longer chdir; env is scoped").
  **Every concept ships with worked examples** (cookbook entries: a serial
  legacy task, an exclusive benchmark, a monorepo `cwd="root"` task, a raw
  subprocess that gets injection, an `ask()` release flow, an interactive
  wizard beside a parallel build) — the taught errors quote the docs, so the
  docs must exist when the errors do, not after.
  Parallelism docs: the serialisation points collapse to one arbiter section.
  **Last, after everything is implemented and tested: write the plan for the
  Foundations category** (section above) with the lessons folded in.
  **CHANGELOG** Changed (breaking) + Added.

## Docs: the "Foundations" category (deferred — plan it last)

Willem wants a new docs category — named **Foundations** (decided 2026-07-25;
replaced the working name Knowledgebase) — that takes a *beginning
Python programmer* to a solid grasp of every concept this design rests on, in
didactic detail, so they can arrive at the same conclusions we did — and see
why footman's default is maximum parallelism with very few don'ts.

Shape (agreed in outline):

- **One concept per section**: the process model (processes vs threads), the
  working directory, environment variables, the shell, spawning subprocesses,
  parallelism & the GIL (threads orchestrate, processes work), deadlocks (and
  why footman's parallel regime cannot have them), and the two-regimes recap
  that ties it together. Plus the performance principle above
  (self-parallelising tools lose little when serialised).
- **Skippable by design**: the category index is a small concept map showing
  which sections depend on which; every page opens with a 2-3 question
  "already know this?" self-check and a skip pointer, and closes with "the
  one rule to remember". Page pattern: the concept → why it matters to a
  parallel task runner → what footman does about it → the rule.
- **Teaching anchors**: every taught error/warning in this design links to
  its Foundations page — the notes are the on-ramp, the pages are the depth.

**The plan now exists: [foundations-plan.md](foundations-plan.md)**
(2026-07-25, written once the implemented-and-tested precondition was met) —
eight pages with their real-lesson payloads (the coverage-clobber origin
story, the Windows anchored-path lesson, the v1 obituary as the deadlock
worked example, the parity hole, fork-with-threads), the per-page self-check
skip mechanism, the concept map, and taught-error anchors wired as a
drift test. Page-writing waits only for dotted addressing (spellings).

## Obituary: the v1 chdir-lock design (do not resurrect)

v1 kept the automatic in-process chdir behind a shared/exclusive lock on the
target directory: same-target blocks join (refcount, last-out restores),
different targets wait, nested escalation to sole ownership, a
`ctx.chdir_target` inheritance check to catch a fan-out child re-targeting
under an ancestor's hold. A deadlock audit (2026-07-25) found the detection
unsound at its root, not at its edges:

1. **Join-then-escalate**: a fan-out child joins the inherited target (blessed)
   then nests a different-target chdir (blessed) → waits for co-sharers → the
   parent, parked in the pool wait, waits on the child. Certain deadlock from
   two legal moves; no rule fires. Common under `taskfile` default, where every
   task shares one target.
2. One `ctx.chdir_target` field can't distinguish inherited from own hold —
   whichever branch an implementation picks, either a false-positive error or
   deadlock 1.
3. Classic ABBA with `_state_lock` (held across user callables) and kin.
4. Waiter-admission fairness unspecified; both possible answers break
   (starvation vs a three-party cycle).
5. Holders that never exit (`infinite` serve tasks) block all waiters silently.
6. Abort/Ctrl-C never wakes condition-variable waiters.

The structural lesson, which v2 is built on: **a lock acquired mid-body inside
an arbitrary dynamic call graph is hold-and-wait by construction; no
enumeration of detection rules closes it. Acquire at task boundaries and
schedule instead.**

## Relationship to the rest

- Builds on the shell-policy shape and `.opts()` (#29) / `set_opts` (#31).
- Independent of dotted addressing.
- **Supersedes the separate interactive-parallelism plan** (and
  `interactive-input-plan.md`'s Phase 4): its goal — max parallelism in the
  face of needing stdin — is native here as the question-layer data lane +
  the `console` lane. The question-layer build phases survive as specced.
- The old "three serialisation points" paragraph dissolves into the arbiter.
