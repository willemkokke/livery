# The four globals, two regimes

No self-check here — this page is the recap that ties the section
together. If the previous pages held, this one should feel inevitable.

## The four globals

A process has four things its threads cannot help but share, each covered
by one page and one footman answer:

| Global | The problem | footman's answer |
|--------|-------------|------------------|
| working directory | one per process; chdir moves it for everyone | `ctx.cwd` as data; children get it at spawn; `footman.cwd()` in bodies |
| environment | one map; flows down only at spawn | each task owns a copy; reads, writes and deletes are its own |
| spawning | the child's world is fixed at spawn | `run()`/injection fill `cwd=`/`env=` per child, race-free |
| the terminal | one stdin, consumed not mutated | `ask()` and the `stdin` marker at the boundary; `interactive=True` to own it |

## The two regimes

**The parallel regime** is the default: process globals are nobody's, every
task carries its own data, children are handed their world at spawn, and
the guards turn each classic mistake into a lesson — `os.chdir` errors,
environment writes scope with a note, a bare `input()` names the spellings
that work. Nothing blocks, so nothing deadlocks.

**The declared regime** is for the tasks that genuinely need a real
shared thing — and it is one ladder, each rung claiming strictly more
than the one below it:

- `lanes=(db,)` — owns exactly the named resources; contends only with
  other claimants of the same lanes, everything unrelated runs beside
  it. (`cwd_lane` roots the real directory at the task's cwd for the
  hold; `console_lane` is the terminal, which `interactive=True`
  spells for you.)
- `serial=True` — owns the process globals: the all-lanes claim, said
  in one word. At most one at a time, *overlapping* the parallel pool;
  real `chdir` legal inside.
- `exclusive=True` — owns the machine; nothing else in flight.

Every rung is granted at task boundaries by the scheduler, all at once,
which is why claims can be scheduled instead of contended
for — the deadlocks page is why that distinction earns its keep.

## The sentence to defend

**In a parallel run, the only non-parallel execution is the kind you
declared.** Everything else runs at full width; the worst degradation
anywhere else is speed, never parallelism — and every remaining wait says
its name.

From here, the [Working directory & environment](working-dir.md) guide
page shows the product surface, and the [Cookbook](cookbook.md) has the
worked recipes.
