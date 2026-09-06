# Task timeout, and retry — the scheduler learns two new reasons to stop

**Status: BUILT 2026-08-20.** Both parts, in the order this note
prescribes: timeout first, then retry. What the build learned that the
design could not have known is recorded at the bottom under "What the
build found"; the open questions below were answered there too.
The roadmap entry these close is *Per-task timeout, and retry* in the
after-1.0 backlog.

Related: [20260731-execution-model-record.md](20260731-execution-model-record.md) and
[20260731-execution-model-spec.md](20260731-execution-model-spec.md) (the record model
this leans on entirely), [20260725-process-globals.md](20260725-process-globals.md)
(boundary-atomic claims — why cancellation happens where it does).

## Why one note for two features

They are the same sentence from the scheduler's side: *a task can now stop
for a reason that isn't "the body finished" or "another task failed".* One
adds a deadline, the other adds an attempt counter, and both land on the
same assumption — that failure is final — in the same two places. Designing
them apart would mean visiting that assumption twice.

They are otherwise independent: timeout is cheap and touches no semantics;
retry is cheap in mechanism and expensive in meaning. **Build timeout
first.**

## Part 1 — `@task(timeout=…)`

### The machinery already exists

Nothing about cancellation needs inventing. `run(..., timeout=30)` and a
step maker's `.opts(timeout=…)` already kill the process tree at a deadline
and answer exit 124 with `Result.timed_out` set. Two facts make the task
level a re-aiming rather than a build:

- The kill path is already shared. `context.py`'s `_KILL_GRACE` is
  commented *"shared by fail-fast and by a timeout, so 'ask, then insist'
  means the same interval whichever one asked."*
- A checkpoint already consults a deadline and the abort latch side by
  side, in that order (`_step.py`, the pump loop):

      if deadline is not None and time.perf_counter() > deadline:
          timed_out = True; gen.close(); return None
      if _context._aborting.is_set() and not ctx.keep_going:
          gen.close(); return None

So a task deadline is the fail-fast event, scoped to one task: stop
starting new work, terminate that task's in-flight subprocess trees, let
generator steps unwind at their next checkpoint.

### The work

`timeout` joins `TaskOpts` (which today carries `keep_going`, `atomic`,
`interactive`, `progress`, `confirm`, `infinite`, `shared`, `cwd`, `rel`,
`lanes`, `serial`, `exclusive`); a deadline rides the task's context; the
existing checkpoint and kill paths consult it. Small — a day, most of it
tests.

### The limit, which must be stated as loudly as the feature

It cannot interrupt arbitrary Python between checkpoints. design.md
already commits to this for cancellation — *"Between checkpoints the step
cannot be interrupted, and that is not timidity but Python's own rule — a
running generator refuses to be closed from outside, by design."*

So `@task(timeout=…)` means **cancelled at the first checkpoint or
subprocess boundary past the deadline**, not a hard stop. A body running
`while True: pass` runs forever, exactly as it does under fail-fast today.
Ship the sentence with the feature or it becomes the next false promise.

### Interaction with fetch

None to build. `fetch()` is a step (*"A fetch is a step: same grid, same
`--json` entry, same `recording()`"*), so a fetch inside a task is already
subject to the task's deadline and the same cancellation path.

## Part 2 — `@task(retries=N)`

### The objection, and why it dissolves

The first draft of this design failed on honesty: three attempts happen,
so one row is a lie by omission and N rows demand an addressing scheme.
Willem's framing removes both halves:

> a task is only retried if it failed. we need a separate state as that is
> failed but not terminally. only when attempts are at 1 and it fails will
> it go to the failed state. So the task will be scheduled up to retry
> times. the json report can distinguish between retry and fail.

Every attempt is a real row with real timing, output and audit. Nothing is
merged, nothing is hidden, and *records are never fiction* holds without
special pleading — because each attempt **is** a record.

Two pieces of existing machinery carry it, which is the strongest argument
that the shape is right:

- **The state set is already open.** json.md: *"`state` is the one word for
  what happened — `ok`, `failed`, `cancelled`, `shared`, `skipped` — and it
  is an **open set**: tolerate values you don't know."* A `retried` state is
  additive and pre-sanctioned; consumers were told to expect unknown values.
  No schema break, no version bump.
- **Addresses already number repeats.** A label appearing twice takes an
  ordinal (`check/git`, `check/git#2`). Three attempts get distinct
  addresses for free.

### The rulings (Willem, 2026-08-07)

1. **A retriable failure does not trigger fail-fast** — *"it hasn't failed
   yet"*. This is accurate accounting, not an exception carved into
   fail-fast: there is no failure to react to until attempts are spent.
   Dependents wait for the same reason — nothing has failed, so nothing is
   blocked, and no `skipped`/`blocked_by` row is written.
2. **All attempts count as one unit on the progress bar.** The report stays
   honest at N rows; the bar stays stable because a retried task is still
   one piece of work the user asked for. This is the existing display/record
   split (*noise is a display problem, never a recording problem*), and it
   preserves progress.md's written promise verbatim: *"how you spell a call
   never changes the total."*
3. **Retry is the user's choice, with no theory about what deserves it.**
   *"if they put it on a task that is not recoverable it will do the same
   three times and eventually fail. that's what would be expected."* So
   `fail()` retries like anything else. The rejected alternative — footman
   deciding a deliberate `fail()` is terminal while a crash is retriable —
   would have given footman a private theory of which failures are real,
   and given `fail()` two meanings depending on a decorator argument.

### Derived rules

- **`pre=` runs once.** A retried attempt re-runs the body only;
  prerequisites already ran and are shared.
- **A share binds to the terminal attempt.** A shared task that succeeds on
  attempt 2 hands *that* record to every later requester.
- **Fail-fast still wins over a pending retry.** If a *different* task fails
  terminally, the abort latches and an unstarted attempt never starts —
  fail-fast means "no new work", and an unstarted attempt is new work.
- **Idempotence is the user's problem, and must be said.** A task that
  half-deployed then failed re-runs its body from the top. The dry-run page
  is the precedent for how plainly to say which side effects footman does
  and does not manage.

### What actually changes

Finality moves from *"a task failed"* to *"a task failed with no attempts
left"*, in exactly two places:

1. the abort trigger (fail-fast latching on first failure), and
2. skip-propagation (`blocked_by` marking dependents the moment a
   prerequisite fails).

Everything else is plumbing that exists. Those two are why this is a note
and not a PR: the failure-is-final assumption is cheap to change
deliberately and expensive to change by discovery.

### Interaction with fetch

Retry's best case, because `fetch()` revalidates rather than
re-downloads — it sends `If-None-Match` and treats `304` as *"the cached
copy stands"*. Attempt 2 of a task containing a completed fetch costs one
round trip, not the file again. Retry and content-addressed caching
compose well.

**One caution to document:** with `[fetch] backend = "curl"`, curl already
retries internally (`--retry 2`). Add `@task(retries=2)` and two
declarations multiply to as many as six attempts. Decide and state that a
task retry is *outer* to whatever a tool does on its own.

## Open, for whoever builds this

- Does a `retried` row carry its successor's address, or does the terminal
  row carry a list of its attempts? The audit's `[moment, actor, code]`
  shape suggests attempts could ride there instead of as sibling rows —
  worth one walk-through before choosing.
- Is `retries=` per task only, or does a step maker take it too? `run()`
  and steps already take `timeout=`; symmetry argues yes, the record
  question argues wait.
- Backoff. Nothing has been said about delay between attempts; a fixed
  `retries=N` with no wait is the honest minimum, and anything else wants
  its own ruling.

## Settle this alongside services (added 2026-08-16)

`20260816-services-and-sinks.md` proposes services as a node kind, and it
lands on this note's vocabulary in three places. Since neither feature is
built, they should be shaped together rather than one retrofitted onto the
other.

- **`timeout` is the service readiness deadline.** Waiting for a service body
  to reach its `yield` is a deadline whose breach is a failure — the same
  meaning `timeout` has here, so it is reuse rather than overload.
- **Idle eviction must *not* be spelled `timeout`.** A daemon dropped for
  going unused has not failed; it stopped being worth keeping. `timeout`
  answers "did this take too long?", idle answers "is this still worth
  keeping?" — one is an execution verdict, the other a cache decision.
  Sharing the word would make an evicted daemon indistinguishable from a
  failed task in receipts.
- **`retries` transfers to the readiness phase** — the spawn did not become
  ready inside `timeout`, so retry N times. Keep it distinct from that note's
  *crash breaker*, a circuit breaker over repeated failures across
  invocations: different timescale, different trigger, easily conflated.

Also worth knowing: this note's stated limit — *"a body running
`while True: pass` runs forever, exactly as it does under fail-fast today"* —
is the same wall the services note hit from the other direction, and is why a
service body must return and yield rather than block.

## Build these in from the start (added 2026-08-19)

Four decisions that are free to make while this is being built and expensive
to change once it has shipped. Each exists because planned work will meet
this mechanism later; none of them revises a ruling above.

**The stated limit is a property of the body, not of Python.** Part 1's
sentence — a body running `while True: pass` runs forever — is true for an
ordinary function and not for every shape footman plans to accept. An
`async def` body has a cancellation point at every `await`, so a deadline can
stop one cleanly instead of waiting for a checkpoint that may never come.
Two consequences: write the user-facing sentence as *"cancelled at the first
checkpoint or subprocess boundary past the deadline"* rather than as a claim
about Python, and put the deadline check where a second stop mechanism can
be issued from later. `timeout=` will mean "will be stopped" for some bodies
and "will be reported" for others; document that asymmetry the day it becomes
true rather than the day someone trips over it.

**A timeout must record whether the work is known to have stopped.** Today it
always is — the deadline kills the process tree, so a timed-out task
certainly did not finish. That is a property of running in this process, not
a property of timeouts. Planned work has calls crossing a process or machine
boundary, where a deadline expiring says nothing about whether the far side
ran. So do not spell "timed out" as though it implied "did not run", and do
not let the retry path assume a timeout is always safe to retry. One field
now; a change to every consumer later.

**Decide whether `retries` is manifest-visible before shipping it.** A caller
that can see a task already retries will not wrap it in a retry of its own; a
caller that cannot see it will. This note already flags that multiplication
with curl's `--retry`, and every later caller — another tool, a CI wrapper,
planned work that reaches tasks from outside the CLI — is one more layer that
can stack. Baking the field in with the rest of `TaskOpts` is free while the
schema is being touched anyway, and a version bump afterwards.

**Gates are per call, not per attempt.** `pre=` already runs once; the same
must hold for everything that *guards* an attempt rather than performing it —
availability (`@requires_*`), `needs_project`, and above all the confirm
gate. A retry that re-prompts a human is a bug found late and read as broken
rather than as an oversight. The rule also generalises to planned work in
which a call carries its own permission to run: the permission belongs to the
call, and the attempts happen inside it.

One clarification while the rulings are being read. Ruling 3 (*no theory
about what deserves retry*) is about **failure kinds** — footman does not
decide that a deliberate `fail()` is more final than a crash. It says nothing
about **declared task properties**, so a future rule that keys retry
behaviour on something the author wrote down is consistent with it rather
than a reversal of it.

## What the build found (2026-08-20)

Three things surfaced only under a running scheduler, each worth keeping:

- **The futures memo answered every attempt with the first attempt's
  failure.** A retried task ran its body once and was reported N times —
  the exact opposite of "each attempt IS a record". Cells are retired
  between attempts now (`_futures.retire`), so an attempt runs fresh and
  the terminal one is what later requests share. The narrow race the note
  should have anticipated: a requester that *joined* during a failed
  attempt was handed that attempt. Sharing binds to the terminal attempt
  for everyone who asks from then on, not retroactively.
- **The exit code counted retried rows**, so a run that recovered still
  exited non-zero. `retried` had to join `skipped` and `cancelled` as a
  state that is recorded but never the verdict — and the filter belongs at
  the source, or the fallback path picks one up anyway.
- **The failure line reported the wrong number.** A task-imposed bound
  printed `0s` (the call's own, which was None) and then the microsecond
  remainder (`0.399666s`). Both read as a broken timeout. One helper now
  serves both raise sites with the declared seconds.

The three open questions, answered by building:

- **Attempt rows vs the audit.** Sibling rows, as designed. They sort
  chronologically because attempts of one node share a request stamp and
  the report's `(seq, started)` order then falls to start time.
- **`retries=` on step makers.** Task-only for now. The record question
  the note raised is real: a step's rows would need the same
  retried/terminal split, and nothing yet asks for it.
- **Backoff.** Not built — the honest minimum, as the note proposed. It
  wants its own ruling because a delay changes what a deadline means.

## How timeout and retries compose (ruled 2026-08-20)

**Both are per call.** Each attempt gets a fresh deadline, so
`@task(timeout=5, retries=2)` can take fifteen seconds. One budget shared
across attempts would be worse — the last attempt gets the least time, so
the deadline would mean something different each round. A cap on the whole
task including retries is a different feature wanting a different name.

**The post-deadline state is a token, not a boolean.** The first ruling here
reasoned about "timed out and could not be killed"; the implementation's
`stopped=False` actually meant "the body finished on its own, just late".
Both cases are real, they are different states, and a boolean cannot carry
the domain — `stopped` conflated *completed* with *escaped* (the harmless
case with the dangerous one), and `finished` would have conflated *stopped*
with *escaped*. So `after_deadline` is a token.

**The discriminator is what footman can actually observe**: not how the body
ended, but whether a stop was issued before it returned.

| value | meaning | retry? | why |
| --- | --- | --- | --- |
| `stopped` | footman issued a stop — including a body that caught the interruption and returned normally; what is recorded is that footman *asked* | **retriable** | Cut off mid-flight, so possibly slow for a transient reason. Ruling 3 applies unchanged. |
| `completed` | no stop was issued and the deadline had already passed — the body finished on its own, late | **terminal** | Nothing transient to retry: it outran the deadline once and will again, and another attempt repeats work that already happened. |

That `completed` is terminal is not footman forming a theory about which
failures deserve another chance (ruling 3); it is a structural fact about
whether another attempt can coherently start, the same category as fail-fast
beating a pending retry.

### Two values ship. `escaped` and `unknown` do not, and that is the point

**`escaped` is a missing observer, not a missing value.** The executor judges
the deadline *after the body returns*. If the body never returns, that
judgment point is never reached — there is no receipt, no state, nothing, and
the run hangs (Part 1's documented limit, and the same shape the services
measurements found: `_python_exit` joining a worker that never returns).
Detecting escape needs an observer running **concurrently with** the body: a
watchdog, a supervisor thread, or teardown noticing an unjoined worker. None
exists, so nothing can truthfully say `escaped`.

Shipping it as reserved vocabulary anyway would be worse than omitting it. A
value nothing can emit is a claim the system can never make: consumers who
branch on it write dead code forever, and may believe footman detects the
case when it does not. That is the `infinite=True` lesson in enum form — a
declaration that can disagree with reality is worse than a narrower one that
cannot.

So the field is an **open set** (the `state` convention, "tolerate values you
don't know") and each value arrives with the mechanism that can observe it:

| value | ships when |
| --- | --- |
| `completed` | now |
| `stopped` | now |
| `escaped` | a concurrent observer exists — watchdog, supervisor, or the services work on unstoppable bodies |
| `unknown` | calls cross a process or machine boundary |

**For whoever picks this up:** adding the value is not the job. Building the
observer is the job; the value is the last line of it.

**Determinism is why `completed` fails rather than passes.** A body finishing
at 30.001s against `timeout=30` exceeded its declared contract. Honouring the
late result would make the outcome depend on a race between the body and the
kill — the same task passing or failing by scheduler timing. Flaky is worse
than strict, especially for something used as a gate.

**No fiction.** Where the state is `completed`, the record says what the body
actually did — *"the body completed at 30.2s with success — the deadline
governs"*, and a body that failed keeps its own reason — rather than
presenting it as work that never finished. `escaped` reads *"could not be
stopped — not retried"*, because the diagnosis the author needs is *this body
has no checkpoint*, and that is only inferable if it reads differently from
ordinary retry exhaustion.

**Not configurable.** No flag to force retries on `completed` or `escaped`:
both are unsafe or futile by construction, and it is purely additive if a
real need appears.

### Where `escaped` is not yet emitted

Worth knowing for whoever meets this next. The executor judges the deadline
*after the body returns*, so from that vantage the state is only ever
`completed` or `stopped` — a body footman genuinely failed to terminate never
reaches the judging line at all. `escaped` is defined, carried, and honoured
by the retry rule, but nothing emits it today. It belongs to the surface that
*sees* the failure to stop (an unkillable child left behind, an `atomic=True`
subprocess that opts out of the kill), and to the remote case that brings
`unknown` with it.

That is a gap in coverage, not in design: the token exists so those surfaces
have somewhere honest to report, and adding an emitter later changes no
consumer.
