# The working directory

!!! question "Already know this?"
    1. What does a relative path like `dist/app.zip` resolve against?
    2. Is the working directory per process or per thread?
    3. On Windows, is `Path("/x")` absolute?

    All three easy? Skip to [The environment](foundations-env.md).

## The concept

Every process has one **working directory** (cwd): the directory that
relative paths resolve against. `open("notes.txt")` means "`notes.txt` in
the cwd"; `Path("dist") / "app.zip"` only becomes a real location once the
cwd says where `dist` is. A child process **inherits** its parent's cwd at
spawn, or is handed a different one, which is the parent's single
race-free chance to choose for it.

`os.chdir()` moves the cwd, *for the whole process*. Every thread, every
relative path anywhere in the program, every subsequently spawned child:
all of them re-anchor at once. There is no per-thread cwd; the previous
page explains why not.

One sharp edge worth knowing even outside footman: **"starts with a slash"
does not mean absolute**. On Windows, `Path("/x")` has no drive letter and
is *anchored* but not absolute, and joining it onto a base does not
append, it **replaces** the base's whole path portion: `C:/base / "/x"`
is `C:/x`. That is why every `rel=` in footman rejects anchored paths, not
just absolute ones.

## Why it matters to a task runner

Parallel tasks plus relative paths is a race by construction: task A
resolves `dist/` while task B chdirs elsewhere, and A's files land wherever
B happened to point the process. And the obvious repair is worse than the
disease: chdir under a lock around each call that needs it, and any such
call silently **serialises the whole run**, whether the code cared about
the cwd or not. One global quietly costs all the parallelism.

## What footman does about it

The task's directory becomes **data**: `ctx.cwd`, resolved once per task by
a small policy ladder (where the task's file lives by default; overridable
per definition, per use, per call; the
[Guide page](working-dir.md) has the table). Subprocesses receive it at
spawn. Nothing chdirs.

For code inside the task body:

- **`footman.cwd()`** is the blessed base for path arithmetic:
  `footman.cwd() / "dist"` is this task's `dist`, whatever the process cwd
  happens to be.
- **`os.chdir` in a parallel task is a taught error**, because the cwd belongs to
  nobody there. The error names the exits: build paths from `footman.cwd()`
  (no chdir at all); claim `lanes=(cwd_lane,)` when the real directory only
  needs to *be* the task's cwd for the duration; or mark the task
  `serial=True` (one owner at a time, overlapping the pool, where a *real*
  chdir via `footman.chdir()` is legal again).
- **`os.getcwd` earns a one-time note** pointing at `footman.cwd()`, since in a
  parallel run the process cwd can be anyone's, so reading it is usually a
  question answered wrong.
- An in-process tool call that needs a *different* directory than the live
  process cwd runs as its subprocess twin instead: right directory, still
  parallel, only the startup saving lost.

## The one rule

**Build paths from `footman.cwd()`; only a serial task may move the real
thing.**
