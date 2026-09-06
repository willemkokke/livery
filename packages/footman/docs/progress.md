# Progress & timing

Every run gets a live status line, and footman learns how long your
tasks take so it can show a real progress bar, with no configuration and no
instrumentation. When a task knows its *own* progress (23 of 150 migrations),
it can report that and the bar fills from the truth. This page gathers the
whole story; the knobs live in [Configuration](configuration.md).

## The live status line

A finished run reads as a receipt (mark, name, command, time) captured
from a real terminal:

![fm format lint: green check marks, task names in cyan, dim commands, and a took line](_generated/shots/run.svg)

On a TTY, every run keeps one live status line on stderr: a **progress
bar** when footman has seen this exact invocation enough to estimate
reliably, meaning five recent green runs with a steady spread; the bar fills
against the history's 90th percentile and labels elapsed vs. typical
time, and a bouncing pulse with elapsed time when it hasn't. Both
parallel engines feed the same line, so a chain and a `parallel()` inside
a task body present identically, with running names appearing the moment
each unit starts. A **unit is a request**: a task in the chain, a
prerequisite, a body call, a `parallel()` child. A step item that
runs no task counts as the one piece of work it is. So the same fan-out
written `parallel(build)` or `parallel(step(lambda: build("web"))())`
counts the same: how you spell a call never changes the total. The bar
can count exactly because nothing anonymous runs; footman owns every
unit, so the total is a fact, not a guess. It always clears itself before any output lands, so
blocks and live step lines stay clean. Without a TTY, a confident
estimate prints once as `eta ~5.8s` on stderr instead: the same information,
one line.

Green runs teach: wall totals are stored per invocation shape and
directory beside the completion manifests (`$FOOTMAN_CACHE_DIR` moves
every footman cache at once). Three off switches: `--no-progress` for one
run, `progress = false` in `[tool.footman]` permanently, and
`@task(progress=False)` for a task whose duration has no rhyme. A run
containing one never records and only ever pulses. The line is absent
entirely under `--no-color`/`NO_COLOR`/`TERM=dumb`, `--quiet`, `--json`,
or when stderr is piped.

## Report a task's own progress: `track()` / `progress()`

Some work knows exactly where it is (23 of 150 migrations, bytes of a
download) and that beats any timing history. Report it and the live
bar fills from the truth:

```python
from pathlib import Path
from footman import task, track, progress


def load_records() -> list: ...  # your own work, whatever shape it takes
def apply(record): ...
def build_index(path): ...


@task
def migrate():
    "Apply pending migrations."
    for record in track(load_records()):  # total from len()
        apply(record)


@task
def index(path: Path):
    "Rebuild the search index."
    for done, total in build_index(path):
        progress(done, total)  # the explicit form
```

Counted beats estimated, so a reporting task is exact on its *first*
run, where the estimator would still be gathering samples. A reporter
contributes a fractional unit to the run's bar, so three tasks done and a
fourth halfway is 3.5/4, and a chain of reporters fills smoothly and a
mixed chain is smooth where it can be. `track()` takes the total from
`len()`, accepts `total=` for generators, and clears the report if you
break out early. Outside a run, both are no-ops.

## Where the timing history lives

The progress bar's estimates come from `*.times.json` files beside the
completion manifests (`~/.cache/footman/`, or wherever `$FOOTMAN_CACHE_DIR`
points). The cache tends itself: at most once a day, the cache collector (a
detached child) removes pairs whose directory no longer exists and pairs idle
for 90 days. Everything in the cache rebuilds on the next run, so collection can never
lose anything that matters. Delete files by hand to reset a stale history,
or turn the whole apparatus off: `--no-progress` for a run, `progress =
false` in `[tool.footman]` for good.

## In CI

Without a TTY there is no progress bar, but timing still works both ways:
CI runs are recorded into the timing history, and when footman has a
confident estimate it prints a single `eta ~5.8s` line to stderr at run
start. `--no-progress` (or `progress = false`) turns the line and the
recording off. See [CI & automation](ci.md) for the rest of the
automation surface.
