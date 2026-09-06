# Comparison

How footman stacks up against the Python task runners I measured it against,
on the same seven-task surface (`lint`, `format`, `typecheck`, `test`, `check`,
`dist build`, `dist clean`) written five ways. The runnable head-to-head lives in
the repo's [`comparison/`](https://github.com/willemkokke/footman/tree/main/comparison)
directory; reproduce the numbers with
`uv run --group comparison python comparison/bench_compare.py`. Switching from
one of these runners? The practical guides live on [Migrating](migrating.md).

Measured on duty 1.9.0, invoke 3.0.3, poethepoet 0.48.0, typer 0.27.0, CPython
3.14, M-series Mac.

!!! note "Verified, not vibes"

    Every number and checkmark here was checked against the tools themselves,
    on the same seven tasks. If any of it is wrong or has become unfair to a
    tool, [open an issue](https://github.com/willemkokke/footman/issues) and
    it will be fixed.

## Why not make, just, or task?

Different species: those are recipe runners — you write shell in a file and
they run it, and they are very good at it. footman runs typed Python
functions, which is what buys checked arguments, `--help` written from
signatures, and completion without wrappers. If your tasks are shell
one-liners, keep the recipe runner; the moment one grows arguments, logic,
or parallelism, it turns into a Python function that wants a runner.
[Migrating](migrating.md) has the tool-by-tool guides.

!!! tip "Milliseconds are the least interesting thing here"

    This is the one page on the site that carries numbers, deliberately.
    Startup timings move with your interpreter, your machine and your
    project, and a runner that wins one of these tables by 20 ms is not
    why you would pick it. Two structural facts survive any re-measurement,
    and they are the ones worth reading for: completion never imports your
    project, and independent work runs in parallel by default. The rest of
    the site talks about what footman *does*; the digits live here.

## First, some love for duty

Before any table makes footman look clever: I've been running my projects on
[duty](https://pawamoy.github.io/duty/) for nearly two years, and it's been a
pleasure the whole way. Footman exists *because* of duty: the `ctx.run` capture
model, the lazy tool wrappers, the decorator ergonomics are all ideas I'm
happily standing on. This is a "here's what I wanted to tweak," not a takedown.
Its `tools` library in particular is the direct inspiration for footman's: it
is where I got the idea that a task runner should ship typed wrappers for the
tools you actually call. Footman's take separates the two halves duty fuses:
wrapping a command-line utility is generic, and the type hints are a layer
generated on top. So the code reads the same whether or not a stub has heard
of your tool: `toolroom.terraform("plan")` runs exactly like
`toolroom.ruff.check()`, and a stub only decides whether your editor can
help. The handles live in [toolroom](https://willemkokke.github.io/toolroom/),
footman's companion package, and detect the footman host on every call.

## Completion latency

Cold-process wall time per `<TAB>`: median of 40 fresh processes after three
discarded warmups, `±` half the p10–p90 spread. The **Δ import** column is the
one that matters: completion time with a 0.25 s project-import cost minus
completion time without it. Re-import your tasks on every keystroke and you see
the whole ~0.25 s; answer from a cache and you see roughly nothing.

| runner  | completion (per <kbd>Tab</kbd>) | Δ import | re-imports every <kbd>Tab</kbd>? |
| ------- | -------------------: | -------: | ------------------------ |
| footman |         **20±1 ms** |     0 ms | no — cached manifest     |
| poe     |             45±1 ms |     0 ms | no — reads TOML          |
| invoke  |            336±20 ms |   263 ms | yes                      |
| duty    |             349±9 ms |   289 ms | yes                      |

duty and invoke reload your whole project on every <kbd>Tab</kbd>, because their
completion scripts call the tool, which imports your tasks before it can answer.
Footman reads a cached JSON manifest instead, so the hot path imports nothing (a
dynamic completer or the first build in a fresh directory spawns a bounded
subprocess). It pays the same import cost as everyone else, just on the
execution path: `fm --list` is ~307 ms, right there with the pack. Completion is
the one moment that has to feel instant, so that is the moment to spend on. poe
is quick here too, for the simple reason that its tasks are TOML strings with no
Python to load, which is also the rest of this page.

The same table on CPython 3.11 — the oldest version footman supports — reads
20 ms, 39 ms, 324 ms, 316 ms: within noise of the 3.14 run above. Which is the
useful thing to know about all of these numbers. The interpreter you run is
not what separates these tools; whether a keystroke imports your project is.

## The same `check`, composed five ways

Completion is the moment that has to feel instant; `check` is the command you
actually run fifty times a day. So: four check steps, each an identical
in-process 0.5 s sleep (a fair stand-in for an I/O-bound tool run: a
real lint step spawns a subprocess and waits, which parallelises exactly like
a sleep), composed the way each tool wants you to. Fairness cuts both ways,
so a tool that supports parallelism gets to use it. Reproduce with
`uv run --group comparison python comparison/bench_check.py`.

| runner  | composition                    | wall (mean) |
| ------- | ------------------------------ | ----------: |
| footman | parallel (pre-deps, *default*) |  **592 ms** |
| poe     | parallel (`parallel` task)     |      617 ms |
| typer   | sequential (no orchestration)  |     2080 ms |
| duty    | sequential (pre-duties)        |     2131 ms |
| invoke  | sequential (pre-tasks)         |     2210 ms |

The floors are 0.5 s parallel and 2.0 s sequential, so everyone's *overhead*
is a rounding error: the gap is architecture, not dispatch speed. duty and
invoke run prerequisites one at a time and have no parallel switch to flip; the
same four steps simply cost the sum instead of the max. poe genuinely ticks
this box (a dedicated `parallel` task type, credit where due); the
difference is spelling. In poe you declare a parallel composite per case; in
footman `pre=[fmt, lint, typecheck, test]` is parallel *by default* and goes
sequential only when you ask (`-s`). And typer hands you nothing here: four
calls in a row, unless you hand-roll a thread pool, at which point you've
written the scheduler yourself.

## Is "just write a typer app" too heavy?

Genuine question, because typer is lovely and a completely reasonable choice. If
you're building a user-facing CLI rather than a task runner, reach for
typer. It's also footman's closest relative here: typed signatures, real flags,
`Enum`/`Literal` validation, nested apps. The only thing I measured was startup,
because typer has a reputation for being heavy:

| import           | cost over a bare interpreter |
| ---------------- | ---------------------------: |
| `import footman` |                  **+0.2 ms** |
| `import typer`   |                   **+40 ms** |

typer's import really is heavier: it ships its own parser plus `rich` and
`shellingham`. (Reproduce with
`uv run --group comparison python scripts/bench_import.py`.)

That import cost is not the whole launch, though, and on launch **typer wins**:
running a no-op task costs 41±2 ms through a typer app against footman's
55±5 ms (37 ms against 53 ms on 3.11 — the gap travels). footman spends its
milliseconds elsewhere — the cascade walk, the
grammar, eager validation — and if bare launch speed is what you are shopping
for, typer is the lighter tool. The difference reverses when a typer app does
completion, because that re-runs the app and pays the typer import *and* your
project import on every <kbd>Tab</kbd>, where footman answers from cache.
Different jobs, and the honest summary is that footman's speed story is about
what it *doesn't* do on a keystroke, not about dispatch.

## Feature matrix

The list is footman's own feature set, so the left column is green by
construction, so the real content is in the other columns.

| capability                                  | footman | typer   | duty          | invoke        | poe      |
| ------------------------------------------- | :-----: | :-----: | ------------- | ------------- | -------- |
| Typed Python-function tasks                 |   ✅    |   ✅    | ✅            | ✅            | ❌       |
| No `ctx`/`c` boilerplate param              |   ✅    |   ✅    | ❌            | ❌            | —        |
| Real `--flags`                              |   ✅    |   ✅    | ✅            | ✅            | ✅       |
| `Literal`/`Enum` → validated choices        |   ✅    |   ✅    | ❌            | ❌            | ❌       |
| Union / one-or-many / `dict[K,V]` params    |   ✅    | partial | ❌            | ❌            | ❌       |
| Native nested groups                        |   ✅    | ✅      | ❌            | manual        | ❌       |
| Zero-boilerplate discovery (module = group) |   ✅    |   ❌    | ❌            | ❌            | ❌       |
| Separator-free chaining                     |   ✅    |   ❌    | ✅            | ✅            | seq task |
| Parallel-by-default DAG (`pre`/`post`)      |   ✅    |   ❌    | sequential    | sequential    | ✅       |
| `run()` capture / replay-on-failure         |   ✅    |   ❌    | ✅            | partial       | ❌       |
| Typed `tools` standard library              |   ✅    |   ❌    | ✅            | ❌            | ❌       |
| Monorepo `tasks.py` cascade                 |   ✅    |   ❌    | ❌            | ❌            | ❌       |
| Custom-branded CLI as a library             |   ✅    |   ✅    | ❌            | ❌            | ❌       |
| Completion without re-importing             |   ✅    |   ❌    | ❌            | ❌            | ✅\*     |
| Zero runtime dependencies                   |   ✅    |   ❌    | ❌            | ✅†           | ❌       |

\* poe skips the re-import only because its tasks aren't Python functions.

† invoke declares no dependencies either. It gets there by vendoring:
`invoke/vendor/` ships fluidity, lexicon and PyYAML inside the wheel.
footman carries no third-party code at all — the difference is where the
dependency lives, not whether you install one.
