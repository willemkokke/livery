# Migrating

Coming from another runner? Each section below is the shortest path
in: what carries over, what you gain, and what you give up. The measured
head-to-head behind these claims is on the [Comparison](comparison.md) page.

## From duty

The gentlest move, since it's the family footman grew up in. Drop the `ctx`
parameter and shell out through `run()`:

<!-- example: fragment -->
```python
# duty
@duty
def lint(ctx, fix: bool = False):
    ctx.run("ruff check ." + (" --fix" if fix else ""))


# footman
@task
def lint(fix: bool = False):
    run("ruff check ." + (" --fix" if fix else ""))
```

Chaining (`duty format lint test` → `fm format lint test`) and `--flags` carry
over. You gain eager choice/type validation (duty happily accepts an invalid
`Literal`; footman stops it), native nested groups, and instant completion.
duty's big `tools` library maps to
[toolroom](https://willemkokke.github.io/toolroom/): `from livery.toolroom.tools import
ruff` gives the same kind of typed wrapper, and a tool duty never heard of
imports just the same. Flag syntax note: duty also takes `lint fix=true`;
footman uses `--fix`.

## From invoke

Drop the `c` parameter and delete the manual `Collection` wiring: in footman a
module *is* a group and `group()` opens a nested one:

<!-- example: fragment -->
```python
# invoke: hand-assembled namespaces
ns = Collection()
ns.add_task(lint)
ns.add_collection(dist)

# footman: nothing to assemble
dist = group("dist")


@dist.task
def build(): ...
```

`inv dist.build` stays `fm dist.build` (the same dotted address); `c.run(...)` →
`run(...)`.

## From typer

Your typed signatures port almost verbatim, because footman reads the same annotations.
Delete the app object and the per-parameter `typer.Option`/`Argument` wrappers;
use plain defaults plus footman's `Annotated` markers (`suggest`, `Many`,
`nosplit`) where you need them. `typer.Typer()` + `add_typer(sub)` → a module or
a `group()`. You'll trade typer's polished `--help` for cached completion, zero
dependencies, and separator-free chaining, a fair swap for a task runner, though
if you're shipping a CLI to users, typer's help is worth staying for.

## From poe

Move each TOML task into a Python function, swapping declarative strings for
real Python, types, and validation:

```toml
# poe
[tool.poe.tasks.lint]
cmd = "ruff check ."
args = [{ name = "fix", options = ["--fix"], type = "boolean" }]
```

```python
# footman
from livery.footman import run, task


@task
def lint(fix: bool = False):
    run("ruff check ." + (" --fix" if fix else ""))
```

You keep parallelism (poe's `parallel = [...]` task type → footman is parallel by default) and
pay the project import only on execution, never on completion.

## From make / just

Recipes become `@task` functions and shell lines become `run(...)`; you keep
chaining and gain parallel-by-default execution and typed arguments. What you
give up is the file-target / up-to-date model: footman runs commands, it isn't a
build system (see `doit` for that niche).

## Other runners

Not covered above, and why: **taskipy** (pyproject shell aliases, no
Python-function tasks), **doit** (a proper build system with file-target and
up-to-date tracking, a different game), **nox** / **tox** (environment and
test-matrix orchestration), and the non-Python **go-task** / **mise**
(great UX and completion, no Python dynamism). `uv`'s own task support
is [in design](https://github.com/astral-sh/uv/issues/5903) and will cover the
simple-named-command case; footman's patch of ground is typed Python-function
tasks with real CLI semantics.
