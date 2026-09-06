# The environment

!!! question "Already know this?"
    1. When a child process starts, whose environment does it get, and
       does it see the parent's *later* changes?
    2. Is `os.environ` shared between threads?
    3. What does `os.putenv` *not* do that `os.environ["X"] = …` does?

    All three easy? Skip to [The shell](foundations-shell.md).

## The concept

The **environment** is a small string-to-string map every process carries:
`PATH`, `HOME`, your `API_KEY`. Its defining property is *how it moves*:
a child process receives a **copy at spawn**, and from that moment the two
maps are independent: the parent's later edits never reach a running
child, and nothing a child sets ever flows back up. Environment flows
*down*, once, at spawn.

Inside one process, though, there is just the one map, shared by every
thread, because `os.environ` is a process global like the cwd. And it has a trap
of its own: `os.putenv` writes the *C-level* environment without updating
`os.environ`, so a program can end up with two views of its own env that
disagree. Plain Python advice, footman aside: assign through `os.environ`,
never `putenv`.

## Why it matters to a task runner

Two parallel tasks writing `os.environ` race exactly like two threads
writing any shared dict: last write wins, at a time nobody chose. And a
runner that can run the same tool two ways has a subtler problem waiting: a
tool run **in-process** reads the live `os.environ`, while the
**subprocess** form of the very same call receives a constructed `env=`,
two paths of one tool call reading *different worlds*, so correct code
breaks by switching paths.

## What footman does about it

For the run's duration, `os.environ` goes through footman's **environment
router**, the same move as its stdout router applied to a second global:

- **Each task owns a whole environment**, copied from the run's at the
  boundary, not a diff against something. `os.environ` answers from it,
  every child footman spawns receives it, and the subprocess path gets the
  same value the in-process path reads. The two paths read one world.
- **Writes scope to the task.** `os.environ["API_KEY"] = "…"` is visible to
  this task's reads and to every child it spawns, and invisible to
  siblings. A one-time note names the deliberate spellings: `env=` for one
  call, `ctx.env` for the task.
- **Deleting is ordinary.** `del os.environ["NO_COLOR"]` removes the key
  from this task's environment and from the children it spawns after, while
  a sibling's copy is untouched, because a task holds a value, not an
  overlay with nothing to say for absence. That matters for variables read
  by *presence* rather than value, where setting `""` is not the same as
  unsetting.
- **`os.putenv`/`os.unsetenv` are taught errors**, because they bypass
  `os.environ` even in plain Python, so nothing could scope them.
- **Two variables are never inherited.** `PYTHONHOME` and
  `PYTHONEXECUTABLE` describe *footman's* interpreter, and a tool that has
  its own has no use for them: a console script handed the wrong one dies
  during start-up, before it can say why. So a task starts without them
  whatever the surrounding process holds. Set either deliberately and it is
  passed on untouched: what is dropped is the inheriting, not the variable.
  `PYTHONPATH` is left alone, because `PYTHONPATH=src` is a thing people
  mean.

## Handing one to a child

`env=` is the child's environment, exactly as `subprocess` means it: what
you pass is what it gets. Both standard idioms work, and neither needs a
footman-specific spelling:

<!-- example: fragment -->
```python
run(cmd, env={**os.environ, "CI": "1"})  # add to what this task has

leaner = dict(os.environ)  # or take something away
leaner.pop("GITHUB_TOKEN", None)  # no token for a linter
run(cmd, env=leaner)
```

Inside a task `os.environ` *is* the task's environment, so `dict(os.environ)`
is exactly what a child would otherwise receive, so the copy is a faithful
starting point rather than an approximation. Omit `env=` and the child simply
inherits the task's.

Outside a run, `os.environ` behaves exactly as stock Python. And because
environment flows down at spawn, everything composes: a scoped write made
with plain `os.environ` rides into a raw `subprocess.Popen` child just as
it would into `run()`: the child's copy is cut from the task's world, not
the process's.

## One variable, four hops

Where an environment variable travels is easiest to see by following one
all the way down. Say a task selects a test tier:

<!-- example: fragment -->
```python
@task
def gate():
    os.environ["TIER"] = "smoke"  # hop 1: this task's overlay
    run("pytest tests/fast")  # hop 2: the child sees TIER
    instance_checks()  # hop 3: a nested task call
```

**Hop 1, the task overlay.** The write scopes to `gate` and everything
under it. Siblings running in parallel never see `TIER`; the process
outside the run never does either.

**Hop 2, a `run()` child.** The child's environment is cut from the
task's world, so `TIER` rides in. This is the hop most writes are aimed
at, and for exactly one child the tighter spelling is
`run(cmd, env={**os.environ, "TIER": "smoke"})`, scoped to the call, with no
overlay write at all.

**Hop 3, a nested task call.** `instance_checks()` gets its own context,
and that context's environment is a **copy of the caller's**, overlay
included. `TIER` flows down into the callee (writes never travel back
up), and from the callee into everything *it* runs.

**Hop 4, the children of children.** If `instance_checks` runs pytest,
and those tests spawn subprocesses of their own, plain OS inheritance
carries `TIER` the rest of the way; footman is no longer involved, and
nothing strips it.

The bite is always the same shape: a variable aimed at hop 2 is still
alive at hop 4, where some distant process changes behaviour on it: a
test runner deselecting suites, a tool switching profiles. Flow-down
never stops at a task boundary, so scope the write to match the reader:
`env=` on the one call that needs it, or `ctx.env.pop("TIER", None)` /
`del os.environ["TIER"]` before the nested call when the overlay was the
right tool but the callee must not inherit it.

## The one rule

**Environment flows down at spawn. Say what a child should see with
`env=` or `ctx.env`, and never expect a write to travel sideways.**
