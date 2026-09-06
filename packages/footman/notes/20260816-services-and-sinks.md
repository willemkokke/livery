# Services and sinks: two node kinds hiding behind two flags

Status: EXPLORATORY — 2026-08-16, opened by Willem while the signal fixes
(#455–#458) were still landing. Everything below was measured against
`origin/main` at `913a59b`, in throwaway worktrees, with the reproductions
kept in the appendix. **Nothing here is built.** Two rulings have been made —
`infinite=True` goes, and the scope ladder plus what `timeout` may be reused
for, all in Part 1 — and five questions at the bottom are still Willem's to
call, of which one gates a build.

Three parts: **services** (a node kind with an inverted contract), **sinks**
(the splitter's one trailing-consumer concept is two), and **transports** (the
frame the first two turn out to share).

## The idea (Willem)

> I'm a little worried about infinite tasks and tasks with variadic arguments
> being mixed. Should an infinite task combined with other tasks work? Does it
> permanently take up a job slot? […] Should variadic tasks always be at the
> end? […] Either way let's experiment and see if we can get to an exhaustive
> coherent behaviour.

and then, once the exhaustive behaviour turned out to be a pile of exceptions:

> If we didn't make new rules to fit the current situation, but can change
> things […] would we be able to improve on this design?

## The thesis

`infinite=True` and `*args` are both **flags standing in for a node kind that
does not exist**. Everything catalogued below is some subsystem discovering it
needs a distinction the core does not make, and inventing a local one.

The tell is that the *peripheral* subsystems already model it correctly and the
core does not:

- completion knows a variadic segment is terminal — `fm test <TAB>` offers
  nothing, while `fm lint <TAB>` offers every sibling task
- `_split.py:16` says so in prose: *"variadic / `--` passthrough segments are
  terminal"*
- `_futures.py:654` knows an infinite task can never be awaited
- `_describe.py:422` knows it "runs until Ctrl-C"

Only `_split.py`'s consumer loop and `_schedule.py`'s node loop treat them as
ordinary. That is where every finding lives.

---

## Part 1 — services

### What was measured

The trigger was the README chain the launch audit found
(`launch/audit-2026-08-16.md`, "Fixed already — text"). Chasing it turned up
the following, all on `913a59b`.

**An infinite task permanently holds a job slot.** With a bounded stand-in
(`infinite=True`, returns after 3s) so the run completes and receipts print:

| run | wall |
|---|---|
| `-j=2 slow1 + slow2 + slow3` (three 1s tasks) | 2.0s |
| `-j=2 held + slow1 + slow2 + slow3` | 3.0s — the three ran **one at a time** |
| `-j=2 slow1 + slow2 + slow3 + held` | 4.0s — position decides when it starts |
| `-j=1 held + slow1 + slow2` | 5.0s — with a real server, slow1/slow2 never run |

With a real service that is for the life of the process: the default `--jobs`
permanently loses a worker, and `-j=1` means nothing after it ever runs.

**Stopping a service is filed as a failure, and takes the run's receipts with
it.**

```console
$ fm lint + stays          # `stays` is run(["sleep", "600"]), Ctrl-C at 2.3s
lint ran
FAIL stays  sleep 600  (2.3s)
fm: interrupted                    # <- no `ok lint` row. lint's receipt is gone.
```

footman prints *"stays runs until you stop it — Ctrl-C"*, you do the one thing
it told you to, and it reports `FAIL` — and throws away the receipt for the
work that genuinely succeeded. `docs.serve` chained with anything means never
learning whether the anything passed.

It is also inconsistent by node count: `fm stays` alone prints **no receipt at
all**, `fm lint + stays` prints `FAIL`.

**A crash and a stop are not distinguishable at the node.** Both print `FAIL
<task>`; only the run-level exit code (1 vs 130) and the presence of a
`RunFailed:` line separate them. A server that died on "address already in use"
and a server you ran for an hour and then stopped produce the same node
receipt.

**`pre=[infinite]` hangs forever, and footman recommends it by name.**
`_futures.py:654` refuses a body call with *"Run it as its own segment, or
declare it as `pre=[watch]`."* Measured: `@task(pre=[watch])` never returns
(killed at 8s, exit 137). Nothing refuses it at declaration or plan time.

**One infinite node costs the whole run its status line.**
`_schedule.py:745` computes `endless` over every node and passes
`progress and not endless`, so five finite tasks lose progress because one
sibling is a server.

**A pure-Python infinite body plus any sibling is unkillable.** This one is
narrow but it is the diagnosis that unlocked the design. Using the SIGQUIT
stack dump from #457:

```text
Thread [fm:watch]   lab/tasks.py:45 in watch        <- the body, on a POOL WORKER
                    _schedule.py:1034 in run_node
                    concurrent/futures/thread.py:119 in _worker
Current thread      _schedule.py:1109 in _run_parallel   <- main thread, futures.wait
```

One node takes `_run_sequential` (`_schedule.py:944`) and the body runs **on
the main thread**, so the signal lands in it and unwinds. Two or more nodes and
the body is on a non-daemon pool worker; the main thread takes the signal,
prints `fm: interrupted`, and `_python_exit` then joins a thread that will
never return.

| | SIGINT | SIGTERM |
|---|---|---|
| `fm sub` / `fm pure` (alone) | 130, 0.3s | 143, 0.1s |
| `fm lint + sub` (subprocess body) | **130, 0.3s** | **143, 0.1s** |
| `fm lint + pure` (Python loop) | SIGKILL, 10s | SIGKILL, 10s |
| `fm -j=1 sub + lint` | **130, 0.2s** | **143, 0.1s** |
| `fm -j=1 pure + lint` | SIGKILL, 10s | SIGKILL, 10s |

No orphans in any case — #453's child kill holds, and #457's SIGTERM handler
works. The subprocess shape, which is what `docs.serve` actually is
(`run("zensical serve")`), is clean. **The pure-Python loop is the only broken
body**, and it is broken because CPython cannot interrupt a thread.

`--keep-going` plus a service is a guaranteed hang: keep-going means nothing
ends the run, and the service is what would have to end it.

### A service is not a task that runs forever

It is a task whose **completion condition is external**. That is a different
contract, and inverting it is the whole design:

- a **job** succeeds by terminating with status 0
- a **service** succeeds by coming up and staying up until its scope ends —
  *exiting on its own is the failure*

Once that is the definition, the special cases stop being special:

| a rule we would otherwise have to write | under the service model |
|---|---|
| "stopping an infinite task counts as success" | it is the contract |
| "an infinite task does not consume a `--jobs` slot" | it is not queued work; it is held for a scope, like the console lane |
| "`pre=[infinite]` must refuse" | it **works** — the edge is readiness, not completion |
| "a crash and a stop should read differently" | one breaks the contract, one fulfils it |
| "fail-fast must reach an infinite sibling" | ending the scope stops the service |
| "receipts must stream when a run has an infinite task" | "up" is an event, so there is something to report |
| "the pure-Python loop must not strand the pool" | held, not joined — a daemon thread is now correct rather than a hack |

Two things become *expressible* that are not today: **teardown** (the scope's
end is the stop) and **readiness handoff** (the service tells you its port).

And the chain cases stop being traps. `fm docs.serve + e2e` starts the server,
runs the tests against it, tears it down. `fm lint test + docs.serve` runs the
checks and leaves the server up. Today neither works, in either order — which
is exactly the README line that started this.

### The spelling: read the shape, do not add a flag

```python
@task
def serve(port: int = 8000) -> Iterator[str]:
    with run.background(f"zensical serve --port={port}"):
        yield f"http://localhost:{port}"  # up — everything in scope runs here
    # torn down here
```

A generator body **is** the service. Why this and not a `service=True` flag:

- it is a **typed** difference — `Iterator[T]` vs `T` — which the manifest
  already reads. "footman groups a shape it can type" is the existing rule;
  this obeys it rather than adding to it.
- `@step` already documents generator bodies. The idiom exists in the codebase.
- it converts an open audit item into a feature. Today `@task def gen(): yield`
  prints `ok gen (0.0s)` and **never runs the body** — measured. Refusing that
  closes a hole; using it closes the hole and buys services.
  *(Status 2026-08-18: the hole is closed by an interim refusal, ruled by
  Willem — a yielding task body refuses by name, wording the shape as
  reserved. The check keys off `isgeneratorfunction` at `_executor._call`,
  which is the detection site this ruling re-uses: building services means
  replacing that refusal with the readiness pump, not finding a home for it.)*
- the yielded value is the readiness handoff, and it is `Forward[T]`-shaped.

### The rule that makes it work: no infinite bodies

CPython cannot interrupt a thread. There is no cooperative cancellation point
inside somebody's `while True` and there never will be — that is what the stack
dump shows, and it is a property of the shape, not a bug to fix. Any design
that lets a task body block forever inherits it.

So a service body **returns**. It yields in the middle; both halves are finite;
the waiting is not in user code at all. footman is never blocked inside a body
with no way out, which is why stopping, teardown, readiness and fail-fast all
become expressible at once.

This does **not** push everything into subprocesses. In-process long-lived work
still works — the author just owns their own cooperation, which is the only
arrangement CPython permits:

```python
@task
def consume() -> Iterator[None]:
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield
    stop.set()
```

### `infinite=True` goes — RULED 2026-08-16 (Willem)

> I'm tempted to remove `infinite=true`. I have a long standing rule of only
> having one way to do things.

The rule is enough on its own, but there is a sharper reason. **The flag is a
claim *about* a body; a generator *is* the body's shape.** Claims can lie, and
footman prints the lie:

```python
@task(infinite=True)
def quick():
    print("quick ran")  # returns instantly
```

```console
$ fm --list
  quick  ...  (runs until Ctrl-C)    ← in the help text, and untrue
$ fm quick lint build
ok   quick  (0.0s)                   ← it just returns
```

Measured. That is `design.md:520` — *"No API mints a receipt without work
behind it"* — broken by a declaration rather than an API, but the same failure.
A shape cannot lie: a function either has a `yield` or it does not. Detection is
`inspect.isgeneratorfunction(fn)`, so it does not even depend on the author
writing `Iterator[T]`; it works on unannotated bodies.

An earlier draft of this note kept the flag as a "lossy one-liner" — a blocking
body on an abandoned daemon thread, no teardown, no readiness. That hedge does
not survive the lying-help-text argument. A two-line saving is not worth a
second spelling that is worse in every dimension *and* can misrepresent itself
in `--help`.

**What has to move.** The flag drives three things today — the "runs until
Ctrl-C" note in help and listings (`_describe.py:422`, `_app.py:572`), progress
and timing suppression (`_schedule.py:745`), and the body-call refusal
(`_futures.py:654`). All three re-key off `isgeneratorfunction`, which is
mechanical and strictly better, since a shape cannot disagree with itself.
Callers to migrate: `reference.md:49`, `index.md:175`, `execution-model.md:63`,
`orchestration.md:181`, and the repo's own `tasks.py:567`. Pre-1.0, so no
deprecation cycle.

**The dependency that sequences the work.** `run()` is blocking-only — the
signature at `context.py:2799` has no background or detach parameter and returns
`Result` after the child exits. So `with run.background(...)` does not exist,
and until it does the generator form is not writable and `docs.serve` has
nothing to migrate *to*. One API, not a redesign, but it fixes the order:
background spawn → migrate `docs.serve` → delete the flag.

**The one case removal costs something**, and it is not enough to keep it: a
third-party blocking call with no background variant (`app.run()`,
`serve_forever()`) makes the author thread it themselves, as in the `consume`
example above — four lines instead of two, and they have to know about the
daemon flag. But that ceremony *buys* something. It makes visible that nothing
will stop that thread cleanly, which is exactly what `infinite=True` does
silently. The harder spelling is the honest one.

### Scope is a ladder, not a choice — RULED 2026-08-16 (Willem)

The first draft of this design offered two scopes and asked which. That was
wrong; it is three rungs, and the third one is what Willem's language-server
question turned up. Willem's follow-up is what settled the shape:

> So does `pre=[serve]` mean start, then this task, then teardown? How do we
> distinguish between do teardown and leave running until timeout?

**"Leave running until timeout" is not a third behaviour — it is what the
project rung *is*.** Every rung already carries its own end condition, so there
is nothing left to distinguish beyond which rung applies:

| scope | up from | ends when |
|---|---|---|
| **subtree** | before its dependent | the **last** dependent finishes |
| **run** | first request in the run | the run finishes |
| **project** | first request, any invocation | idle, key change, explicit stop, gc sweep |

So `pre=[serve]` is start → dependent → teardown, and
`pre=[serve.opts(scope="project")]` leaves it up. Or the service declares
`scope="project"` and every `pre=` leaves it.

**Resolution is the ladder `shared` already uses** — this reference's own
`.opts(scope=…)`, then the task's declaration, then whatever asked for it, then
a default. `.opts()` exists and already carries overrides of exactly this kind;
`scope` joins `TaskOpts`, which is pleasing since `TaskOpts` carries `infinite`
today and that is being deleted. The flag that was a *claim* is replaced, in the
same struct, by the thing that actually varies.

**Subtree teardown needs refcounting, not "the dependent finished".** Two tasks
in one run both declaring `pre=[serve]` get one instance from the once-cell, so
the teardown belongs to the last dependent to finish. Getting this wrong makes
the second task's server vanish mid-test, and it will present as a flake.

**The author cannot know which scope they got**, since it is resolved on the
request. That is the `shared` precedent exactly — a task does not know whether
it was shared either — so it is consistent, but it should be documented rather
than discovered.

### Three time limits, and `timeout` means exactly one

> Can we reuse the task `timeout` parameter for that on daemons rather than
> tasks? — Willem, 2026-08-16

`@task(timeout=…)` does not exist yet: `20260807-timeout-and-retry.md` is
DESIGNED, not built, with retry ruled on 2026-08-07 and timeout *"assessed but
unruled beyond 'do it first, it's the cheap half'"*. So this is two designs
shaped together rather than one retrofitted onto the other.

There are three time limits around a service, and `timeout` correctly means one:

| limit | breach means | spelling |
|---|---|---|
| **readiness deadline** — waiting for the `yield` | the spawn **failed** | `timeout` — same meaning, same machinery |
| **idle eviction** — unused for too long | nothing failed; it stopped being worth keeping | its own word (`idle=`) |
| **max age** — alive regardless of use | nothing failed; hygiene | its own word |

**Reuse `timeout` for the first, never for the second.** `timeout` answers *"did
this take too long?"*; idle answers *"is this still worth keeping?"* One is an
execution verdict, the other a cache decision. Overloading them would make an
evicted daemon indistinguishable from a failed task in receipts and leave
`fm --daemons` unable to say why something stopped. A dev server up for eight
hours is not timing out.

This is the same principle deleting `infinite=True` — one name must not carry
two meanings — and it is worse here, because one of the two means "you failed".

`retries` transfers cleanly to the same phase: the spawn did not become ready
inside `timeout`, so retry N times. Keep that distinct from the **crash
breaker** below, which is a circuit breaker over repeated failures across
invocations — different timescale, different trigger, easy to conflate.

**A third convergence.** That note's stated limit is *"a body running
`while True: pass` runs forever, exactly as it does under fail-fast today."*
That is the wall this note hit from the other direction, and why service bodies
must return and yield. After the incremental-caching serialisation wall and the
failure-vocabulary split, three separate threads have now arrived at constraints
this design already had to satisfy.

The ladder is a shape footman already uses, for `shared` and for the config
cascade, and resolving it on the *request* rather than the declaration is the
precedent `shared` sets.

The project rung is the run-scoped once-cell lifted across process boundaries.
`_futures.py` already does *"one execution per task and arguments, whoever
asks"* within a run; a project-scoped service is that cell persisted, so
`pre=[pyright]` resolves to a running daemon or spawns one, with the same
memoisation and a wider lifetime.

**The rung has a typing consequence.** A run-scoped service can yield anything,
including a live object, because the consumer is in the same process. A
project-scoped one cannot — the next `fm` is a different process, so nothing
live survives the gap.

That is the same wall `20260727-incremental-caching.md` hit: *"a persistent
cache hit generally cannot serve a body-call, because the caller wants the
return object and most returns are not serialisable."* Same constraint, found
twice, from two directions — decent evidence the ladder is carving at a real
joint. Part 3 gives it its proper name: it is not a rule about daemons, it is
a property of the **transport**, and footman can enforce it because it can see
the annotation.

**And the rung needs a relay, which the first draft of this note missed.** The
obvious design is that the generator yields a descriptor, footman caches it,
and a later invocation hands it to the dependent task. That breaks on stdio: a
language server started with `--stdio` is bound to *its* parent's pipes, so the
next `fm` cannot reach it. Something has to sit in the daemon and serve
requests — and once something must, it should be footman rather than every
plugin reimplementing a socket server. Part 3 is what that something is.

### The project rung: resident analysers

Willem's question: could footman host language servers so lints and type checks
return instantly?

Measured on this repo:

| step | wall | on re-run |
|---|---|---|
| `ruff check .` | 26 ms | 26 ms |
| `ruff format --check .` | 26 ms | 26 ms |
| `pytest --collect-only` | 0.46 s | — |
| **`basedpyright`** | **3.07 s** | **3.07 s** — no caching whatsoever |
| `pytest -q -n auto` (full suite) | 15.0 s | — |

Two conclusions, and the second one is unwelcome:

1. The lints are **already** instant. There is nothing to win there. The prize
   is entirely basedpyright, which is 3.07s and identical on re-run.
2. `fm check` runs its steps in parallel, so its wall time is the test suite at
   ~15s with basedpyright hidden underneath. **A resident type server makes
   `fm typecheck` about 30× faster and `fm check` not faster at all.**

The gate's 15s is execution, not import (collection is 0.46s), so no daemon
touches it — only affected-test selection does, and that is
`20260727-incremental-caching.md`, which already concluded "resume first, if
either."

So the honest claim is "`fm typecheck` gets instant", not "the gate does". That
is still a real loop — typecheck on save, and a monorepo cascade where a
per-package check is type-bound rather than test-bound.

**footman already has the plumbing.** `_refresh.py` is a detached child that
outlives the invocation and writes to a cache — exactly the spawn shape.
`_paths.py` has brand-scoped dirs for a socket and pidfile. `_gc.py` is the
age-sweep that reaps an idle one. LSP framing is Content-Length-delimited JSON
over a pipe: stdlib, no dependency violated, a couple hundred lines.

**The hazard is the one this project has spent a month designing against.** A
resident checker reporting green on stale state is a forged receipt —
`design.md:520`, *"No fiction. No API mints a receipt without work behind it."*
footman is not the editor, so nothing tells the daemon a file changed; the
editor owns those notifications and footman never sees them. Freshness has to
be **proven** per invocation, not assumed: stat the source set (~10ms, stdlib),
diff mtime+size against the daemon's snapshot, push `didChange` for the deltas,
pull diagnostics. When freshness cannot be proven — version mismatch, config
hash changed, an unreadable file — fall back to the cold CLI rather than guess.

A second trust problem sits underneath: `basedpyright` and
`basedpyright-langserver` need not agree. LSP is open-file-driven and resolves
config differently. If `fm typecheck` says green and CI says red, that is far
worse than three seconds. The daemon path can only ever be an accelerator —
`--cold` always available, CI never using it, and something that periodically
runs both and refuses on divergence.

And a cost to state rather than hide: an editor already running
`basedpyright-langserver` means footman's is a *second* instance, hundreds of
megabytes. LSP servers are single-client over stdio, so attaching to the
editor's is not possible.

**Do the cheap version first.** Hash the source set, cache the result, skip the
run entirely when nothing changed: 3.07s → ~10ms for the no-change case, no
daemon, no LSP, no staleness risk beyond the hash. It does nothing for the
one-file-changed case, which is the actual development loop — but it builds
precisely the freshness machinery the daemon needs, it is independently useful,
and it shares the key derivation the incremental-caching note already worked
out. Treat the resident server as the second half of that feature, not a
separate idea.

---

## Part 2 — sinks

### What was measured

A variadic consumes to end of line, including tokens that name tasks. Silently,
exit 0.

| line | result |
|---|---|
| `fm lint test build` | `lint ran`, `test ran with ('build',)`, **build never ran**, exit 0 |
| `fm many a b lint` (`paths: list[str]`) | `many ran with ['a','b','lint']`, exit 0 |
| `fm ci a b lint` (group default `*args`) | same, exit 0 |
| `fm test a b + files c d` | both run correctly |

Two variadics is **not** a separate case — the first eats the second's *name*,
so it collapses into row 1, and Python forbids two `*args` in one signature.
With `+` it already works. Nothing to design.

Completion already enforces the rule the runtime does not: `fm lint <TAB>`
offers seven sibling tasks, `fm test <TAB>` offers nothing, `fm test a <TAB>`
offers nothing. And because it offers nothing, `+` is **undiscoverable by the
mechanism footman is built around**.

**`--` silently discards on any task without `*args`:**

```console
fm lint -- build        -> lint ran, exit 0.  "build" evaporated
fm plain bob -- extra   -> plain name='bob', exit 0.  "extra" evaporated
fm many a -- --x        -> many paths=['a'], exit 0.  "--x" evaporated
```

That third line is the sharpest. `_split.py:1250` treats the two trailing
consumers as one concept, `rest` — a `multiple` positional or a `variadic`,
same branch, both greedy to end of line. `_executor.py:838` merges passthrough
only for `VAR_POSITIONAL`. **The splitter says they are the same shape; the
executor says they are not.**

**What `--` actually buys**, given a variadic already eats bare words:

| line | `pytest_args` |
|---|---|
| `fm test --port=1` | `()` — bound test's own `--port`, silently |
| `fm test -- --port=1` | `('--port=1',)` |
| `fm test -k=foo` | `('-k=foo',)` — **single-dash already flows, no `--` needed** |
| `fm test -- lint` | `('lint',)` — the only unambiguous way to pass a task's name |
| `fm test -- --tb=short + lint` | `('--tb=short', '+', 'lint')` — the literal `+` |

Three jobs: double-dash tokens, the literal `+`, and task names. Everything
else reaches a variadic without it. It correctly refuses to fill a required
fixed positional (`fm exec -- ls -la` → *missing required positional(s):
`<cmd>`*).

### The redesign

Split `rest` into the two things it is:

- a **sink** (`*args`) — opaque, terminal, receives `--`
- a **value list** (`list[str]` positional) — typed, validated, greedy

and then: **a greedy consumer ends at `+`, `--`, or a token that names a task.**

The argument that settles it: footman **already does this for fixed
positionals**. `_consume_positional` (`_split.py:1436`) refuses a token when
`_is_address(tree, tok)`. The greedy consumer is the only construct in the
grammar that does not follow the rule. This is not a new rule — it is deleting
an exception.

The payoff is the thesis. Under this rule TAB offers sibling task names after a
sink, because they would be legal — the grammar and the completion agree, and a
user discovers chaining by pressing TAB instead of by reading docs.

`--` then has one job — *"stop reading this as grammar"* — instead of three
narrow ones. And being load-bearing, it should get a `+` exit:
`fm test -- build + lint`. (Open question 4.)

`--` on a task with no sink should **refuse**, not evaporate. There the words go
nowhere at all; that is a hole, not an ambiguity.

---

## Part 3 — transports

### The frame (Willem)

> This is really just another way of using typed function signatures as an RPC
> mechanism. That is the generic core that footman itself is built upon.

That is the right reading, and it is worth stating more strongly: footman is
**already** an RPC framework whose interface definition language is Python's
type annotations. The CLI is not the product; it is the most visible transport.
One signature is already projected into all of these:

| projection | where |
|---|---|
| argv — flags, positionals, coercion | `_split.py`, `_coerce.py` |
| a JSON document over stdin / stdout | `_binder.py`, `Stdout[T]` |
| the environment | `params.env` |
| the completion manifest | `_manifest.py` |
| what TAB offers | `_complete.py` |
| help text and the docs exporter | `_describe.py`, `markdown.py` |
| JSON Schema | `--describe` |
| an in-process memoised call | `_futures.py` |

A daemon socket is the ninth, and the closest of the lot: it is the
stdin/stdout projection with a different file descriptor.

### What each transport can carry

Two axes, and they are not the same axis — capability does not order neatly
(argv carries strings, a document carries typed JSON, neither carries a live
object), but **trust does**, and it tightens monotonically going down:

| transport | runs where | trust boundary | can carry |
|---|---|---|---|
| in-process call | this process | none | anything, including live objects |
| argv | this process | none | scalars and containers, from strings |
| document (stdin/stdout) | a child process | none | serialisable shapes |
| unix socket (daemon) | another process, same user | filesystem permissions | serialisable shapes |
| HTTP (remote) | another machine | delegated auth; **manifest is untrusted** | serialisable shapes |

### The invariant nobody enforces

If one signature drives every transport, the architecture has exactly one
failure mode: **two transports disagreeing about one signature.** That is not
hypothetical — two of the sixteen fixes in #454 were instances of it:

- **M1** — `between()` and path checks were skipped on a piped document, so
  `[1, 9]` passed bounds that `--flag=9` refused.
- **M2** — a numeric enum could not survive its own round trip: `--json` wrote
  `2`, piping it back was refused.

Neither is a binder bug in isolation; both are transport drift. And nothing in
the suite asserts against it, which is why they shipped.

**Proposed gate item: the same task, called every way, answers the same.** Over
footman's own task set — argv, document, in-process call, and socket once it
exists. Run as a property test rather than a fixture list, so a new marker or a
new coercion is covered without anyone remembering to add it. That test would
have failed on M1 and M2 before release.

This is the strongest argument for building the daemon transport at all —
stronger than the timing numbers. A fourth transport *forces* the invariant to
be written down.

### The daemon as the fourth transport

Because it is a transport and not a feature, **there is no new protocol to
design.** A daemon call is bind typed args → run the body → return a typed
value, which is exactly what `fm --json <task>` does across a process spawn
today. The process-boundary work shipped in 0.21.0 is the contract; the socket
is the transport. Stdlib throughout (`socketserver`, `json`).

footman must never learn LSP, or nailgun, or any tool's protocol. The plugin's
task body speaks the tool's language *inside* the daemon; footman moves typed
calls across a boundary. Same division as everywhere else: footman owns the
boundary, not the semantics.

What footman owns comes to five verbs and no wire semantics:

| verb | what it means |
|---|---|
| **spawn** | run the body in a detached child, up to the `yield` |
| **rendezvous** | the child publishes its descriptor, pid and start-time; the parent waits with a timeout |
| **liveness** | structural only — same key, process alive, same start-time (so pid reuse cannot fool it) |
| **stop** | SIGTERM |
| **reap** | `_gc.py`'s existing age sweep, plus eviction on key change |

An earlier draft of this note excluded health entirely — footman guarantees
"this is the process I started for this key and it is still running", and a
daemon sick in some other way is the plugin's problem. That line is in the wrong
place, and Willem said so: footman already owns **liveness** (structural) and
**readiness** (the `yield` *is* the ready signal), so excluding only the third
of the three is arbitrary. See the next section.

Stop has a property that falls out of the generator shape rather than being
designed: **SIGTERM resumes the generator.** The second half of the body *is*
the shutdown handler.

### Health, and how much of it is transport-independent

> Can we at least provide syntactic sugar / affordances for health? There has to
> be some functionality all health monitoring systems need? Ideally transport
> independent. — Willem, 2026-08-16

Health decomposes into five parts. Four are the same for every daemon and
belong to footman; one is the tool's business:

| part | whose |
|---|---|
| **when** to check | footman |
| **what to do** with a bad answer | footman — it is a stop trigger |
| **how not to hammer** a sick one | footman — the crash breaker, already listed |
| **how a human hears about it** | footman — a reason string, rendered |
| **what the check actually does** | the plugin |

Three of footman's four already exist under other names, which is the sign that
this is not new surface.

**The idiom already exists.** `@requires_env("CI")` is a predicate the framework
calls and whose *reason* it renders — `(unavailable: set CI)`. A health probe is
the same shape one layer down, so it should be spelled the same way: a stacked
declaration, not a constructor argument.

```python
@task
def pyright() -> Iterator[Endpoint]:
    ...
    yield endpoint


@pyright.health  # raise, or return a reason, to say unhealthy
def _(ep: Endpoint) -> None:
    ep.request("$/status", timeout=2)
```

**Transport-independence comes from the handle, not from footman knowing
anything.** The probe receives exactly what the consumers receive: a live object
for a run-scoped service, a descriptor plus client for a daemon, the same for a
remote one. One signature, three transports — the transport-legality ladder
doing its job rather than a special case.

**Declaring a probe is entirely optional, and the zero-declaration default has
to be good on its own.** It can be, because the useful half needs no plugin
input: connection refused, connection reset, a timeout and a malformed reply are
all *structural* failures footman can classify without knowing what the daemon
does. So a service with no `@health` at all still gets stop-respawn-retry when
its daemon dies or wedges. A declared probe adds only what footman genuinely
cannot see — *semantic* health, the daemon that answers promptly and wrongly.

**The cheapest probe is the request you were going to make anyway.** Running a
health round-trip before every reuse taxes the happy path for nothing. So the
default is *no periodic probe at all*: footman classifies the failure of a real
request as either "the call failed" or "the daemon is bad", and on the latter
stops it, respawns, and retries **once**. Periodic self-probing is opt-in, for
slow degradation that requests do not surface.

That classification is not new surface either — it is precondition 4 of the
remote rung, arriving early. *"`Result`/`Failed` says 'it failed'; it does not
distinguish 'could not reach it' from 'it ran and failed'."* One distinction,
two payoffs: health-based respawn locally, and an honest answer to "did my
deploy run?" remotely. Build it once, here.

**What footman should still not do**, so this does not become a monitoring
product: define what healthy means, poll by default, or grow Kubernetes'
three-probe vocabulary. Healthy or not, plus a reason string, plus a
last-checked stamp in `fm --daemons`. A task runner does not need `degraded`.

### Eight triggers, one stop

Every cross-cutting daemon feature turns out to be a reason to stop one, and
there must be exactly one stop path:

| trigger | why |
|---|---|
| idle timeout | no interaction in the window |
| key change | tool version, config hash or footman version moved — the daemon is now *wrong* |
| crash breaker | died N times in M minutes; stop respawning, run cold, say so |
| failed health | a real request classified as "the daemon is bad", or an opt-in periodic probe |
| LRU eviction | the live-daemon ceiling was hit |
| explicit | `fm --daemons stop <name>` |
| gc sweep | its project is gone, or its cache entry was reaped |
| SIGTERM | the ordinary one |

One verb: unlink the socket, resume the generator, tear down, exit. A new
reason to stop is a new trigger, not new machinery — the same discipline as this
note's thesis.

**Unlink before teardown.** A client connecting while pyright is being killed
should get a refused connection and spawn fresh, not block on a dying daemon.
Two daemons for a second, self-correcting; clients treat any connection failure
as cold regardless.

### Idle shutdown

Willem's ask, and the mechanism is free: footman owns the socket, so it sees
every request and owns the timer. The plugin never learns idleness exists. On
timeout it does what SIGTERM does.

Two details worth getting right rather than discovering:

- **Idle counts open connections, not just completed requests.** A long e2e run
  that connects once and streams for twenty minutes must not trip a
  thirty-minute timer with a live client attached. Idle means no open
  connections *and* no completed request in the window.
- **The failure mode is a timeout too short, not too long.** At five minutes you
  pay spawn cost constantly and never reuse — strictly worse than no daemon.
  30 minutes is a sensible default; the dial belongs in the existing config
  cascade (`[tool.footman]`, per-task override at declaration), with
  `idle=None` for never.

### The rest of the framework half

Three more belong to footman because it owns the socket, the key and the cache:

- **Single-flight spawn.** Two invocations racing with no warm daemon both
  spawn, and one child is orphaned. Needs a lock in the cache dir so one spawns
  and the other waits on the rendezvous. This is the same bug as `_fetch.py:154`
  in the launch audit — *"two parallel cold fetches hand each other a 0-byte
  file"* — so it should be the same primitive, fixed once.
- **Observability**, which is the biggest operational risk here. A daemon has no
  terminal, so its stderr goes to a per-key log in the cache dir, reaped by the
  same sweep, plus `fm --daemons` showing key, pid, uptime, last request and
  memory. Without it, "my checks got weird" has nowhere to look.
- **The cold escape hatch.** `--no-daemon` is not a nicety. Part 1 says CI must
  never use daemons; that needs a flag to be true.

The plugin keeps what a request means, what the source set is, what the tool's
protocol is, and what teardown actually does.

### One implementation directive

The socket's serve loop must dispatch into the same `_execute` path everything
else uses — **not a parallel entry point.** Then lifecycle hooks, global-option
binding, receipts, `--json` envelopes and refusals all behave identically by
construction rather than by diligence. Cheap on day one, very expensive once a
daemon path has grown its own quirks.

### Transport-legality, named

In-process calls can return live objects; documents and sockets cannot. That is
not a daemon rule — it is a property of the transport, and footman already
half-knows it (`Stdout[T]` exists; refusals exit 64). Naming it as a concept
explains why the incremental-caching note hit the same wall from the other
side: a persisted memo and a socket call are the same transport class.

It also predicts a bug that does not exist yet. **Path-validating markers are
transport-relative.** `exists` and `isfile` on a *local* task check the local
filesystem, correctly. On a task that runs elsewhere they would validate the
caller's disk against the callee's world, which is simply wrong. `between()` is
fine — it is a property of the value. So transport-legality is not one bit per
task; it is per marker, and the manifest can already see which markers a
parameter carries.

### The fifth transport: a remote server

> I hate to say it, but I'd love to be able to run a central (API) server that
> advertises its own tasks on a CLI via a plugin. — Willem, 2026-08-16

Recorded as wanted, **not** designed and not scoped. It fits the frame better
than it has any right to: the manifest is already a serialisable description of
a task tree built to be fetched and cached, `plugin()`/`include()` already mount
a tree under a prefix, `_fetch.py` already does cached downloads with ETag
sidecars, and the completion architecture was built for exactly this cost
profile — a cached remote manifest answers TAB in ~30 ms without touching the
network, which is the same stale-while-revalidate trade `_refresh.py` already
makes locally (and `M6` is what it looks like when it goes wrong).

What changes is not the mechanism, it is the trust model. Preconditions before
this could be built, none of which are footman features today:

1. **Authorization is delegated, never implemented.** The plugin supplies
   credentials and headers; footman never sees or stores them. Consistent with
   owning the boundary and not the semantics.
2. **`Secret` redaction must be complete first.** It still leaks in the profile
   trace and on the failure line (launch audit, `H46`). Incomplete redaction is
   a bug when the wire is a pipe and an incident when it is a network.
3. **A remote manifest is untrusted input.** It drives footman's parser, its
   completion and its help text. It cannot execute local code, but it can
   *shadow names* — a mounted `deploy` that the user believes is theirs is a
   phishing vector. Mounted remote trees must be prefixed and visibly marked as
   remote in listings and help. `into=` already does the prefixing half.
4. **The failure vocabulary must widen.** `Result`/`Failed` says "it failed"; it
   does not distinguish "could not reach it" from "it ran and failed". For a
   deploy that distinction is the whole ballgame. **This one arrives early** —
   the health section above needs the same distinction to decide whether a bad
   request means respawn the daemon or report the failure, so it gets built with
   the local transports and the remote rung inherits it.
5. **Transport-relative markers must land first** (above), or a remote task with
   `exists` on a parameter validates the wrong machine's disk.

**The line, stated because the frame will not draw it for you.** Local
transports are: same machine, same user, filesystem permissions as the entire
authorization story. Everything in Parts 1–3 above lives inside that line. The
remote rung crosses it, and crossing it turns footman from a task runner into
something with an authentication story to defend. That is a product decision,
not a design detail, and the launch audit already caught `SECURITY.md`
classifying documented behaviour as a vulnerability once.

---

## Rejected

- **Refuse the swallowed token (exit 64).** `pytest build` is a legitimate line
  and `build` is a legitimate directory. Refusing breaks real usage.
- **Advisory note only, generalising `_default_notes` from group children to the
  whole tree.** This was the first proposal and it is the wrong shape: there is
  no complete escape hatch (`--` costs the rest of the line, and `./build`
  changes the value) and no way to silence it. `_default_notes` carries the same
  weakness today for non-path values.
- **Keep `infinite=True` and patch the exceptions.** That is how it got here —
  seven local rules, each individually defensible.
- **Keep `infinite=True` as a lossy one-liner beside the generator form.** This
  note's own first draft. Rejected 2026-08-16: a declaration can disagree with
  its body and footman prints the disagreement in `--help`; a generator cannot.
  See the ruling above.
- **Ban in-process long-lived work.** Unnecessary. The author can own the
  cooperation; footman only needs the body to return.
- **Attach to the editor's language server.** LSP servers are single-client over
  stdio. Not possible; footman's is a second instance.
- **A resident pytest.** The suite's 15s is execution, not import (collect is
  0.46s). A warm process wins nothing. Test *selection* is the lever, and it is
  the incremental-caching note's problem.
- **A descriptor and no protocol at all.** The appealing first cut for Part 3:
  the generator yields a socket path, footman caches it, later invocations hand
  it to the dependent task, and footman never runs a server. It breaks on stdio
  — an LSP server started with `--stdio` is bound to *its* parent's pipes, so
  the next `fm` cannot reach it, and something has to relay. Once something
  must, it should be footman rather than every plugin writing a socket server.
- **footman speaking the tool's protocol.** An LSP client in the core means
  nailgun next, and protocol knowledge accumulating where it cannot be removed.
  The plugin speaks LSP inside the daemon; footman moves typed calls.

## Where this went next

Part 3's transports frame turned out to point at something larger than the
remote rung. Asked whether it helps with distributed processing, the honest
answer was "one slice of it" — and the slice it does not reach is gated not by
transport but by **declared inputs and outputs**, the same blocker
`20260727-incremental-caching.md` already found from the caching side.

That thread became its own note:
[20260817-global-address-space.md](20260817-global-address-space.md), on the
observation that footman already has a global address space for *tasks* and the
once-cell's `(task, bound args)` key is already a name for a result. Read it
before building the project rung — it argues a reach-address is
project-relative and therefore not good enough as a global name, which is a
constraint on how task identity gets recorded.

## Open — Willem's call

1. **Exit code on a clean stop.** `fm docs.serve`, Ctrl-C, the service did its
   job: 0 or 130? Leaning 130 when anything was still pending and 0 when the
   service was all that remained — principled, but it makes the exit code depend
   on the plan.
2. **Does `--jobs` bound services?** If they do not count, `--jobs` means
   "concurrent jobs" and services are unbounded. Probably right, but
   `fm a.serve + b.serve + c.serve` with no limit is a thing someone will do.
3. **Note or refuse for the swallowed token, and does `--` get a `+` exit?**
   These are coupled: whether "note" is defensible depends on whether `--` is a
   complete escape hatch, and today it is not.
4. **What is it, once this lands?** Willem, 2026-08-16: *"we're turning this into
   more of a project runner now than just a task runner. I wonder if there is a
   better word for it."* A task runner runs tasks to completion; a service does
   not complete, and a daemon outlives the invocation entirely.

   The observation that resolves it: **the name already covers this and the
   tagline does not.** A footman runs errands, attends the door, waits, and
   announces callers. Errands are tasks; attending the door is a service;
   announcing is help and completion. Nothing about the product needs renaming —
   one sentence does.

   The trap is trading discovery for accuracy. "Task runner" is a category
   people search for and instantly place; "project runner" is not a category and
   nobody types it. So the split worth considering is by *slot*: keep the
   searchable category where search happens (PyPI summary, the GitHub
   description, `comparison.md`'s framing against duty/invoke/just/typer), and
   let the README's first sentence carry the wider claim — something in the
   shape of *"your project's command line"*, which covers running tasks,
   holding services, completion, branding and the remote rung without inventing
   a category. Both slots stay honest; neither pays for the other.

   Not decided here. Flagged because the design forces the question and the
   answer is cheapest before a launch, not after.
5. **Is the remote rung in scope for this design, or explicitly deferred?**
   Wanted (Part 3), and it fits the frame — but it crosses the local-only line
   and brings an authentication story with it. Deciding *now* costs nothing and
   changes two things: whether transport-legality is built per-marker from the
   start, and whether `SECURITY.md` needs rewriting before or after.

*(Two more were ruled on 2026-08-16 and moved into Part 1: whether
`infinite=True` survives — it goes — and how a service's scope resolves, which
took `timeout` reuse with it.)*

**Nothing gates the service build any more.** Question 3 gates the sink build.
The two do not touch each other and can land in either order — the sink half is
smaller, is mostly deleting an exception, and has a visible payoff in TAB.

The service half has a fixed internal order, set by the two rulings:
`run.background()` → migrate `docs.serve` → services as a node kind (scope on
`TaskOpts`, resolved by the `shared` ladder; `timeout` as the readiness
deadline; `idle` as its own word) → delete `infinite=True`.

Question 1 (exit code on a clean stop) is the only one the service build can
hit without an answer, and it is a one-line decision that can be taken when the
receipt is written rather than before.

## Appendix — reproductions

Every measurement above came from throwaway task files driven against a
worktree at `913a59b`. The shapes worth keeping:

- **slot occupancy** needs a *bounded* stand-in — `@task(infinite=True)` whose
  body sleeps 3s and returns — or the run never completes and no receipts print.
- **signal behaviour** needs the child in its own process group
  (`start_new_session=True`) and `os.killpg`, plus a `communicate(timeout=…)`
  fallback to SIGKILL. A `timeout -s KILL` wrapper kills `fm` only, not its
  group, so any surviving grandchild is the harness's doing and not footman's.
- **the subprocess/pure-loop distinction is essential.** Measuring with
  `while True: sleep()` reports a hang that the real `docs.serve` shape
  (`run("zensical serve")`) does not have. The first draft of this analysis got
  that wrong.
- **`--complete` takes a word list, not a line**: `fm --complete -- fm test ""`.
  A cold cache answers empty; warm it with any real invocation first.
