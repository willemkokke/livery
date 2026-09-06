# Monorepos & config

## The task cascade

In a monorepo you rarely want one giant tasks file. Footman collects every
`tasks.py` from the **repo root** (the nearest checkout above you) down to your
current directory and merges them into one command set:

```text
repo/            .git  pyproject.toml  tasks.py   →  build  test  lint
  services/
    api/         tasks.py                         →  serve  migrate  build*
```

Standing in `services/api`, `fm` sees `build*` (the local override), `test`,
`lint`, `serve`, and `migrate`. The rules are the ones you'd guess:

- a **new name appends**;
- a name already defined higher up is **overridden** by the directory nearest you;
- a **group present at both levels merges**, with its tasks overlaid the same
  way.

There is one rung further out than the repo root: your own
[personal tasks file](#personal-tasks), which every project sees and any
project may shadow.

!!! note "How footman finds the top of the cascade"

    The walk goes up from your current directory and stops at a **ceiling**,
    then collects downward. The rules, in order:

    1. **The ceiling is the nearest checkout at or above your cwd**: a
       `.git`, `.jj`, `.hg` or `.svn` directory. That is the repo edge, and
       where both the task cascade and the [config search](#configuration)
       start. footman only looks for the directory; it never runs your
       version-control tool or reads its metadata.
    2. **No checkout? The nearest ancestor holding a project marker** (a
       `pyproject.toml`, a `footman.toml`, or a `tasks.py`) is the ceiling
       instead, so a single-package checkout with no VCS still has a sensible
       top.
    3. **Nothing above you at all? Your current directory is the ceiling**, and the
       walk never climbs past your home into the filesystem root looking for
       one.

    From that ceiling **down to your cwd**, footman loads every `tasks.py` that
    exists, root first and cwd last so nearer files override, skipping directories
    that have none. The filename is the `tasks` [config key](#configuration), so
    a repo can look for something other than `tasks.py`. The walk's whole reach
    is the user-level `cascade` key (`none`, `repo` (the default above), or
    `filesystem`) with `FOOTMAN_CASCADE` as the per-invocation override; see
    [Configuration](configuration.md#keys).

## Personal tasks

The cascade has an outermost rung above the repo root: **`~/.config/footman/tasks.py`**
(honouring `XDG_CONFIG_HOME`). Tasks you write there ride everywhere: inside
every project, and in directories that are no project at all:

```python
# ~/.config/footman/tasks.py
from footman import run, task


@task
def scratch():
    """Spin up a throwaway venv here."""
    run(["uv", "venv", ".scratch"])
```

```console
$ cd ~/anywhere && fm scratch      # works; there is no project in sight
```

It is the same cascade, extended one rung outward, so the rule you already
know applies: **project > user**. A project that wants the name owns it, and
`inherited()` still reaches the personal task it shadowed;
[Composing tasks](composing.md) has the mechanics.

### When a personal task needs a project

Some personal tasks only make sense in a checkout. Say so, and footman keeps
them out of the way everywhere else:

```python
@task(expose="project_only")
def sync_upstream():
    """Rebase onto upstream/main."""
    run(["git", "fetch", "upstream"])
```

Outside a project that task is not listed, not completed, and not offered as
a did-you-mean, and asking for it by name is refused with the reason rather
than a "no task named", because it does exist:

```console
$ cd /tmp && fm sync-upstream
fm: sync-upstream needs a project — no tasks.py found here or in any parent of /tmp
```

Silence means "rides everywhere", which is what a personal tasks file is for,
so nothing you already wrote changes. (A [branded CLI](custom-cli.md)
defaults its *built-in* set the other way round, because a package declared
`builtin=` mostly ships tasks that need a project. Each default is that
rung's own promise.)

## Where a task runs

Every task **runs from the directory of the file that defined it**. Root's `build`
always builds from `repo/`, `api`'s `serve` from `services/api/`, wherever you
invoke it:

```sh
cd services/api
fm build      # the api override, running in services/api/
fm test       # inherited from the root, running in repo/
```

`run(cwd=…)` still overrides the working directory per command.

## Sibling helpers

Each `tasks.py` may `import helpers` (or any module) from **its own directory** at
the top of the file, and footman searches that directory first and gives each file
its own copy, so `services/api/helpers.py` and the root `helpers.py` never
collide. Import at module top; a deferred `import` inside a task body, in a
project with same-named helpers in several directories, is a known limitation.

## Completion is per directory

The completion manifest is cached **per directory**, so <kbd>Tab</kbd> in
`services/api` offers the merged set while the repo root offers only its own.

??? tip "Load exactly one file"

    `-f/--tasks-file=PATH` loads a single tasks file, with **no tasks cascade**:
    the tasks-side mirror of `--config=PATH` for config. The two are orthogonal:
    `-f` alone still reads the cwd's config (and any plugins it declares add
    tasks), so pass both for total control. <kbd>Tab</kbd> after `-f=<file>`
    completes *that file's* tasks: a `-f` run caches its manifest under a key
    pairing the file with the cwd, separate from the plain-cwd cache, which it
    never touches (so plain <kbd>Tab</kbd> keeps describing the real cascade).

## Configuration

Footman discovers behavioural settings with the same upward walk it uses for
tasks files. It reads `[tool.footman]` from `pyproject.toml` and a standalone
`footman.toml` (whole-file), from the repo root down to your cwd, where **nearer
files win**, so a package can override repo-wide defaults:

```toml
# repo/pyproject.toml
[tool.footman]
tasks = "tasks.py"     # the filename to look for in the cascade
sequential = false     # run tasks one at a time by default
```

```toml
# repo/services/api/footman.toml   (no pyproject here, so a standalone file)
sequential = true      # this package prefers one task at a time
```

Within one directory, `footman.toml` wins over `pyproject.toml`'s
`[tool.footman]`. `--config=PATH` points at a single TOML file that overrides
everything else. Unknown keys are ignored, so a newer setting never breaks an
older footman.

The full key table, and the whole precedence ladder with the user-level file
included, lives on the [Configuration](configuration.md) page.

A local task that overrides an inherited one can still *call* it:
`inherited()` is footman's `super()`; see the
[cookbook recipe](cookbook.md#extend-an-inherited-task-instead-of-replacing-it).
