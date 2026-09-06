# One process, many tasks

!!! question "Already know this?"
    1. What belongs to a *process*, and what do its *threads* share?
    2. Can two threads in one process have different working directories?
    3. If two processes write the same file, who wins?

    All three easy? Skip to [The working directory](foundations-cwd.md).

## The concept

When you run a program, the operating system gives it a **process**: a
private little world holding its memory, its open files, its **working
directory**, its **environment variables**, and its connection to your
terminal. Two processes never share any of this by accident: each lives in
its own world, and the only ways to reach across are things you can see:
files, pipes, sockets.

A **thread** is different. Threads live *inside* one process and share that
entire world. Two threads see the same memory, the same working directory,
the same environment, the same terminal. That sharing is what makes threads
cheap and convenient, and it is also the entire problem this section of
the docs exists to explain: anything one thread changes about the shared
world, every other thread sees immediately, whether it wanted to or not.

There is exactly one working directory *per process*, not per thread. There
is one environment. There is one stdin. These are called **process
globals**, and no amount of careful code makes them per-thread: nothing the
operating systems agree on offers that. (Linux alone can give a thread its
own filesystem context via `unshare` — nothing portable, and nothing
CPython exposes.)

## Why it matters to a task runner

Footman runs your independent tasks **in parallel, as threads of one
process**. That is the right trade: tasks spend their time waiting on the
subprocesses they spawn, so threads give real concurrency without paying
for process isolation. But it means every parallel task shares one cwd, one
environment, one terminal. A task that "just quickly" changes any of them
changes it for *everyone, mid-flight*.

Two concrete shapes of that, both worth recognising on sight:

**Separate processes protect you; shared files don't.** Two `fm check` runs
are separate processes with safely isolated worlds, and they will still
corrupt each other if both write one shared `.coverage` file: whichever
finishes second clobbers the database mid-write, and the survivor reports a
nonsense total with every test passing. Process isolation ends where shared
resources begin, so footman gives each invocation its own file, the same
move it makes for cwd and env.

**State scoped to a run must die with the run.** footman's fail-fast abort
is *latched*: once a run is doomed, any subprocess registered afterwards is
killed at birth, so a doomed run can't outrun the kill. A latch that
outlived its run would be process-global state with no owner: it would
reach forward and kill an innocent `echo hi` started by whatever ran next
in the same process. In a shared world, state needs a declared scope and a
declared end.

## What footman does about it

Every task gets its own **context**: `ctx.cwd` is *that task's* working
directory, `ctx.env` is *that task's* whole environment: plain data,
per task, no sharing. When a task spawns a subprocess, footman hands the
child its world *at spawn* (`cwd=`, `env=`), which is the one moment the
operating system lets you set a child's globals race-free. The real process
globals stay untouched in a parallel run, and run-scoped machinery (the
abort latch among it) is cleared when the run ends.

The rest of Foundations walks each global in turn; the
[Working directory & environment](working-dir.md) guide page shows the
product surface built on top.

## The one rule

**Anything process-global belongs to nobody in a parallel run. Give each
task its own data, and hand children their world at spawn.**
