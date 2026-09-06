# Threads & the GIL

!!! question "Already know this?"
    1. What does Python's GIL actually serialise, and what releases it?
    2. Why can a thread-pooled task runner still saturate all your cores?
    3. What changes on the free-threaded (no-GIL) builds?

    All three easy? Skip to [Deadlocks](foundations-deadlocks.md).

## The concept

CPython's **global interpreter lock** lets only one thread *execute Python
bytecode* at a time. That sounds fatal for parallelism, but the lock is
released the moment a thread waits on the outside world: a subprocess
finishing, a file, a socket. Threads that mostly *wait* run concurrently
in every way that matters; threads that mostly *compute Python* do not.
(The free-threaded builds remove the lock entirely; well-behaved threaded
code runs unchanged — faster where threads genuinely compute in parallel,
at a measurable single-thread cost — and footman's CI runs them.)

## Why it matters to a task runner

A task body spends its life waiting on the tools it spawns: pytest, a
compiler, a bundler. Those are separate *processes*, each free to use as
many cores as it likes, no GIL anywhere between them. So the right
architecture is exactly footman's: **threads conduct, processes work**.
Thread-per-task gives cheap fan-out, shared scheduling, dynamic DAGs
(a body can compute arguments and dispatch more work, closures and all,
which process isolation would forbid) while the heavy lifting happens in
GIL-free child processes.

This also explains a cost worth saying out loud: putting a
self-parallelising tool (pytest with xdist, a build system) in footman's
serial lane costs almost nothing. The tool saturates the machine by
itself; serialising the *task* only forgoes running other tasks beside
it, for which such a tool leaves no room anyway.

## What footman does about it

Independent tasks run on a thread pool; `parallel()` fans a body out onto
one; everything real runs in spawned children with the task's world applied
at spawn. In-process tool calls exist as a startup-time optimisation for
Python tools, and where one would need a different working directory than
the live process has, it runs as its subprocess twin instead, keeping the
parallelism and losing only the startup saving.

## The one rule

**Parallelism lives in the processes; the threads just conduct.**
