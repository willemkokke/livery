# Profiling a run

A run already records where its time went: when each task started and
finished, which worker ran it, how long it queued for one, which lanes
serialised it, every `run()` step inside it. That record exists because
it is the ledger [the design](design.md) commits
to, so a trace costs nothing new. The `footman.profile` plugin
writes all of that as one trace file that profiler UIs read directly, and a
small API lets a task add its own timing to the same picture.

## The profile plugin

Mount it like any plugin:

<!-- example: fragment -->
```python
from footman.compose import plugin

plugin("footman.profile")
```

That grants one global option. `--profile` writes `fm-profile.json` in the
invocation's directory; `--profile=FILE` chooses the name:

```console
$ fm --profile check
profile: /work/project/fm-profile.json
ok   check  (12.4s)
```

Open the file at [ui.perfetto.dev](https://ui.perfetto.dev) by dragging it onto
the page. `chrome://tracing` and [speedscope](https://speedscope.app) read
it too: the format is Chrome Trace Event JSON, the same file Bazel's
`--profile`, Clang's `-ftime-trace` and TypeScript's `--generateTrace`
produce, so whatever reads those reads this.

What you see:

- **One track per worker**, named (`fm-worker_0`, …), a slice per task.
  Parallelism is the picture: stacked lanes, wide gaps where something
  serialised.
- **Queue time in the slice's details**, as `queue_ms`: how long the task sat
  ready waiting for a free worker. It is not drawn as a bar: during that
  wait the task had no worker, and every worker's row is truthfully showing
  what it was busy with.
- **Lane waits at the head of the slot.** A claim that genuinely stalled
  shows as its own slice (`lane: cspell-cache`) before the body's time.
- **Every `run()` step nested inside its task**, and the task's own
  [sections](#timing-inside-a-task) beside them.
- **A dependency arrow per plan edge**, so the critical path is traceable
  by eye.
- Work that genuinely overlaps on one timeline (a `parallel()` block's
  children, a stream's windows) renders as async spans instead of stacked
  slices. The trace never rearranges time to make a prettier picture.

The last slice is the writer itself: `profile: write`, the serialisation
cost, measured to just before the dump. The one thing a closed file cannot
contain is its own write.

## Timing inside a task

The trace subdivides further wherever a task says so. Three primitives, all
recorded on the run's clock and carried on the task's row:

<!-- example: fragment -->
```python
import footman
from footman import task


@task
def check(affected: bool = False):
    with footman.section("resolve affected set"):
        targets = _resolve(affected)
    footman.mark("targets known")
    for t in targets:
        _check_one(t)
```

`section()` times a block on the task's own timeline: nested blocks nest,
and the slice appears inside the task's span exactly where it happened.
`mark()` drops a labelled instant, no duration.

Work that *overlaps*, with several waits in flight at once, belongs on a
**stream**, a named parallel timeline under the task where overlap is legal.
`footman.stream(name)` returns a `Stream`; sections on it come in two
forms:

<!-- example: fragment -->
```python
@task
def await_ci(ref: str):
    ci = footman.stream("ci")
    with ci.section("poll"):  # bracketing, like section()
        checks = _wait_for_checks(ref)
    for c in checks:  # retroactive: the window the CI
        ci.section(c.name, start=c.started_at, end=c.completed_at)
```

The retroactive form takes what a CI API reports (`datetime`s, or epoch
seconds) and places the window by wall clock, which may be before the task
started, or before the run did. The profile shows the checks' *real*
timelines, not the shape of the polling loop that discovered them.

A stream handle remembers its task, so a helper thread the body spawned may
record through a handle made in the body. `section()` and `mark()` read the
running task from the calling thread, so they belong in the body itself;
outside a task, all three are a taught error.

Each record is a `Section` (name, start, duration, stream) and the
`--json` envelope carries them per task row, so the same data feeds scripts
as feeds the picture (see [JSON output](json.md)). The plugin is one
consumer of the records, not their owner: they are there whether or not a
profile is written.

## What a child process can add

A run's children often know their own time far more finely than the parent
can see: a test runner knows every test, a build knows every translation
unit. A profiled run opens a door for them: `FM_PROFILE_DIR` is exported to
every task's environment, and any process may drop Chrome-trace fragments
there, as `*.json`, either `{"traceEvents": […]}` or a bare event array, with
`ts` in **epoch microseconds** and its own `pid`. The writer sweeps the
directory, shifts every fragment onto the run's clock, and embeds each
child as its own process group beside `fm`'s tracks. A malformed drop is
named on stderr and skipped, never fatal.

**pytest speaks the convention out of the box.** footman ships a pytest
plugin (the same one that provides the [testing fixtures](testing.md)),
and when a pytest process inherits `FM_PROFILE_DIR` it records every
test's setup, call and teardown and drops the fragment on exit. So inside
a profiled gate, the suite stops being one long slice:

```console
$ fm --profile check
profile: /work/project/fm-profile.json
ok   check  (21.2s)
```

opens with every individual test on the timeline, under a `pytest` process
group, with xdist workers as named tracks, so the suite's own parallelism is
as visible as the runner's. A pytest that is not a profiled run's child
pays one environment read and does nothing.

The convention is deliberately tool-agnostic: anything that can write JSON
can join: a `cargo build --timings` converter, a `clang -ftime-trace`
copy step, a script of your own. Drop the file, keep your `pid`, spell
`ts` in epoch microseconds, and the run's profile carries your timeline
where it happened.
