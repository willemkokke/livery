# Spawning programs

!!! question "Already know this?"
    1. Name three things a child process inherits from its parent.
    2. What is a process group, and why does killing one beat killing a pid?
    3. Why is `os.fork()` dangerous in a program that runs threads?

    All three easy? Skip to [Threads & the GIL](foundations-threads.md).

## The concept

Starting a program means asking the operating system to create a child
process running it. At that moment — and only then — the parent chooses the
child's world: its argument list, its working directory, its environment
copy, which of the parent's open files it keeps. After the spawn the two
processes are independent; the spawn is the parent's one race-free chance
to set things up.

Children spawn children. Killing only the direct child leaves
grandchildren orphaned and running, which is why the OS offers **process
groups**: put a child (and its descendants) in its own group, and one
signal reaches the whole tree.

POSIX also offers **fork**: duplicate the current process wholesale. It is
how spawning is built underneath, but calling it directly from a program
that runs threads is a trap — the child gets a copy of memory *as it was*,
including any lock some other thread held mid-operation, with no thread
alive in the child to ever release it. CPython warns about
fork-with-threads (from 3.12); the safe shape is always "spawn a fresh program".

## Why it matters to a task runner

Everything a task runner does ends in spawns, so the spawn moment is where
per-task worlds become real: the child's `cwd=` and `env=` are set
race-free even while twenty other tasks run. It is also where cleanup
lives — fail-fast, Ctrl-C and a supervisor's stop signal must reap process
*trees*, not just direct children, or a killed build leaves its compiler
grandchildren running.

## What footman does about it

`run()` and the toolroom handles spawn with the task's world applied
per-child, group-isolate what they spawn (so fail-fast's signal reaches the
whole tree), and register every child so an abort can find it. The one
deliberate exception is an `@task(atomic=True)` child: unregistered and not
group-isolated, so neither fail-fast nor a stop signal delivered to footman
can truncate the write the flag protects — it runs to completion even when
the run ends without it. Beyond its own calls:

- **Raw `subprocess` is quietly correct.** A spawn that passes neither
  `cwd=` nor `env=` gets both filled from the task's context — the child
  starts where the task lives, seeing what the task sees — with a one-time
  note naming the deliberate spellings. Explicit arguments always win.
- **`os.fork` earns a note** naming the trap above. `os.system` and
  friends spawn at the C level, beneath everything footman routes — no
  context is filled in and no note fires, which is itself the reason to
  prefer `run()`: what footman cannot see, it cannot manage.
- **`multiprocessing` workers earn a note** too: they inherit the *real*
  environment, not the task's own — and a tool that parallelises
  itself loses little by taking the serial lane instead, since it
  saturates the machine on its own.

## The one rule

**Spawn through `run()` — the spawn moment is where a task's world becomes
its child's, and explicit arguments always win.**
