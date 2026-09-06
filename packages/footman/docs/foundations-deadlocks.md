# Deadlocks

!!! question "Already know this?"
    1. What four conditions must all hold for a deadlock?
    2. What is "hold and wait", and how do you design it away?
    3. Why is a deadlock worse than a crash?

    All three easy? Skip to [The four globals, two regimes](foundations-regimes.md).

## The concept

A **deadlock** is a circle of waiting: A holds something B needs while
waiting for something B holds. Nobody errs, nobody crashes: everything
simply stops, forever, with no stack trace pointing anywhere. That silence
is what makes deadlocks worse than crashes: a crash tells you where; a
deadlock tells you nothing.

The classic analysis names **four conditions that must all hold** — the
quiz question above, answered: *mutual exclusion* (the resource serves one
holder at a time), *hold-and-wait* (taking one resource, then blocking for
another while still holding), *no preemption* (nothing takes a held
resource away), and *circular wait* (the holders form a ring). Break any
one and deadlock is impossible.

The ingredient that matters most in practice is **hold-and-wait**: taking
a resource, then blocking for another while still holding the first. Code
that never waits while holding cannot complete the circle. The strongest
designs don't detect deadlocks; they make one of the ingredients
impossible.

## A worked example: how a careful design still deadlocks

The tempting way to share one working directory between parallel tasks is a
clever lock: tasks needing the same directory *share* a hold; a nested block
can *escalate* to a different directory by waiting for its co-holders to
leave; a fan-out child re-targeting under its parent's hold is detected and
refused. Every rule is individually sound. Composing two of them is fatal:

1. A parent takes the lock at directory `A`, then fans out children and
   waits for them. Legal.
2. A child joins the hold at `A` (same target, "not even a conflict").
   Legal.
3. The child then nests a block for directory `B`: an escalation, which
   waits for its co-holders to leave. Its co-holder is the parent. The
   parent is waiting for the child.

A certain, silent deadlock built from two blessed moves, and since most
projects point every task at one directory, that setup is the *common
case*, not a corner. No detection rule fires, because each rule is checked
at the moment of one move, and the circle only exists across both.

Footman narrows the ingredient instead of trusting rules to compose.
Task-level serialisation is **declared on the task and granted at the task
boundary**, before the body runs, so the scheduler *orders* those claims
rather than letting bodies contend mid-flight. One mid-body wait exists — a
step's `lanes=` claim — and it is allowed to because it cannot complete the
circle:

- **All lanes at once, or none.** A step's claim is granted atomically in a
  single check: no partial holds, so holding one lane while waiting for
  another cannot be spelled, and the fatal composition above has no grammar
  to be written in.
- **Lineage extends a hold, never contends with it.** A claim from inside a
  serial or exclusive task — whose hold already conflicts with every lane —
  would be waiting for itself; it is exempt, and the hold extends. Fan-out
  is safe the same way: a child of a lane holder extends the hold instead
  of queueing behind its parent.
- **A waiting body says so.** A body blocked on a claim counts itself as
  parked, so the bookkeeping that decides "is everyone waiting on me?" sees
  a wait as a wait — never as work still in flight that must be waited for
  in turn.

And the waits that remain are never silent — a queued claim prints what it
is waiting for after a couple of seconds, because an invisible wait is a
deadlock you haven't confirmed yet.

## The one rule

**Declare, don't contend; when a body must wait, all-or-nothing and never
on your own lineage — and make every remaining wait say its name.**
