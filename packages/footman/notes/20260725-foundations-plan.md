# Foundations — the didactic docs category (plan)

**Status:** planned 2026-07-25, after the process-globals build finished —
its precondition ("implemented and tested, lessons in hand") is met. Name
**Foundations** decided. **Page-writing waits for dotted addressing** so
every example spells addresses natively; nothing else blocks. This plan is
the deliverable of the deferred task in `notes/process-globals.md`.

## Purpose

Take a *beginning Python programmer* to a solid grasp of every concept the
process-globals design rests on — process, working directory, environment,
shell, threads, subprocesses, deadlock — so they can re-derive footman's
conclusions themselves: maximum parallelism by default, very few don'ts,
serialisation only by declaration. The category teaches the *ground*, the
Guide teaches the *product*; a reader who finishes Foundations should read
every taught error as obvious rather than arbitrary.

## Shape

- **Nav**: a new top-level "Foundations" group after the Guide. Order is a
  dependency chain — each page assumes only the pages above it.
- **Page pattern** (every page, same skeleton):
  1. a 2–3 question **"already know this?"** self-check with a skip pointer
     ("all yes → skip to …") — the skip mechanism, per page, no gating;
  2. the concept, from zero, in plain words;
  3. **why it matters to a parallel task runner** (the bridge paragraph);
  4. **what footman does about it** (the design, linked to the Guide);
  5. **the one rule to remember** (a single bolded sentence, closing).
- **Concept map**: the category index page draws the dependency graph
  (mermaid) so a reader can enter anywhere and see what a page assumes.
- **Teaching anchors**: every taught error and note in the runtime links to
  its Foundations page (a short `#fragment` under "what footman does").
  The errors are the on-ramp; the pages are the depth. Wire the links as a
  docs-drift test: each anchor named in an error text must exist.

## The pages (with their real-lesson payloads)

1. **One process, many tasks** — what a process is; threads share one
   process; a task body is a thread. Payload: why two `fm check` runs
   clobbered one `.coverage` file (the SQLite race that started this whole
   design), and why run-scoped state must die with the run (the abort-latch
   lesson). Rule: *anything process-global belongs to nobody in parallel.*
2. **The working directory** — what a cwd is; relative paths; per-process,
   not per-thread. Payload: the Windows anchored-path lesson (`/x` is not
   absolute on Windows, and joining it replaces the base — why `rel=`
   rejects anchors), and the ladder/`footman.cwd()` as the answer. The
   worked example is the pair the uniform `rel` rule settles:
   `@task(cwd="root", rel="dist")` + `run(rel="web")` → `root/dist/web`,
   vs `.opts(rel="web")` → `root/web`. Rule: *build paths from
   `footman.cwd()`; only a serial task may move the real thing.*
3. **The environment** — what env vars are; inheritance at spawn; why
   `os.environ` is process-global. Payload: the in-process/subprocess
   parity hole the router closed (two calls reading different worlds), and
   scoped writes (children see them, siblings don't). Rule: *env flows down
   at spawn — say it with `env=`/`ctx.env`.*
4. **The shell** — what a shell is and is not; why `run("a | b")` without a
   shell is a taught error; strict mode; quoting across platforms. (Mostly
   exists in the Guide — this page is the from-zero version.) Rule: *no
   shell unless you ask; ask when you mean pipes.*
5. **Spawning programs** — fork/exec vs spawn; what a child inherits (cwd,
   env, fds); process groups and why Ctrl-C reaps trees. Payload: the Popen
   injection (raw spawns get the task's context), `os.system`'s bucket, and
   why fork-in-a-threaded-process is unsafe (the child can inherit locks
   mid-hold — CPython itself deprecates it). Rule: *spawn through `run()`;
   explicit args always win.*
6. **Threads, the GIL, and why footman parallelises anyway** — threads
   orchestrate, subprocesses work; I/O-bound release the GIL; the
   free-threaded build. Payload: the perf principle (a self-parallelising
   tool loses little in the serial lane — it saturates the cores itself).
   Rule: *parallelism lives in the processes; the threads just conduct.*
7. **Deadlocks** — hold-and-wait, circular wait, why detection beats
   debugging. Payload: the v1 chdir-lock obituary as the worked example —
   join-then-escalate composing two individually-safe moves into a certain
   deadlock — and why boundary acquisition (the arbiter) is immune by
   construction: locks taken mid-body in a dynamic call graph are
   hold-and-wait; resources granted at task boundaries can be scheduled.
   Rule: *never wait while holding; declare, don't contend.*
8. **The four globals, two regimes** — the recap that ties it together:
   cwd, env, spawn, terminal; parallel regime (globals are data) vs
   declared regime (`serial`/`exclusive`/`interactive`); the routers as
   the stdout-router pattern applied three more times. Ends with the
   pinned claim: *the only non-parallel execution is declared.*

## Sequencing

**Amended 2026-07-25 (Willem: continue as far as autonomously possible):**
the pages barely spell nested addresses — they teach concepts with flat
`@task` examples — so waiting for dotted was over-cautious. Pages are
written now, flat-spelled, and ride the same ≈zero post-dotted sweep as
everything else.

1. Pages 1–3 first (they anchor the most error texts), then 5 and 7 (the
   payload-heavy ones), then 4, 6, 8. One PR per batch; strict docs build
   and the anchor-drift test from the first PR on.
2. The drift test v1 keys on a mapping table *in the test* (error kind →
   page#anchor) and asserts the anchors exist; actually inserting doc
   pointers into runtime error texts is a separate, user-visible design
   call — flagged for Willem, not assumed.

## Done criteria

- All eight pages live under a Foundations nav group with the concept map.
- Every taught error/note in `_globals.py`, `context.py`, and `executor.py`
  links to an existing anchor (drift-tested).
- A beginning-Python reader path exists: Foundations in order → the
  working-dir Guide page → the cookbook recipes, with no forward reference
  that the concept map doesn't declare.
