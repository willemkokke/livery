# The profile plugin — a run as a trace file

Opened 2026-08-05. **Built the same day** (branch `worktree-profile-plugin`;
gate and strict docs green; dogfooded on footman's own `fm --profile check`).
What shipped differs from the design below in one name and three additions:

- **`track()` became `stream()`** (`Stream`, `Section.stream`): the name
  `footman.track` was already taken by the progress iterator. "Stream" is
  the word the original request used for the parallel mode.
- **`TaskResult.after`** — the scheduler stamps each row with the addresses
  it waited for, where it already computes `eligible`. The profile's flow
  arrows draw from it, and `--json` carries it.
- **Steps and sections carry placement in `--json`** (`at_ms`, relative to
  the task's start; negative legal for retroactive windows).
- **Overlap honesty in the serialiser**: spans that would mis-stack on a
  track (a `parallel()` block's folded child steps, stream windows) are
  routed to async begin/end pairs by a containment check — slices on a
  track must nest, and the trace never rearranges time.

The plugin lives at `src/footman/profile.py`, entry point
`footman.profile`, pulled by this repo's own tasks.py. Docs:
`docs/profiling.md` (+ json.md, composing.md, plugins.md, api.md touches).
`GlobalOption(bare=…)` rides the manifest as a `bare` key and the splitter
as the `option?` kind.

**Second wave, same day — children join the profile.** A profiled run
exports `FM_PROFILE_DIR` (armed by a `pre_tasks` hook reading `inv.cli`,
never `PROFILE.value` — the refresh child has no line); any child drops
Chrome-trace fragments there (`ts` epoch µs, own `pid`) and the writer
sweeps, shifts through the wall anchor, embeds each as its own process
group. footman's pytest plugin speaks it natively: per-test
setup/call/teardown, xdist controller records once with workers as named
tracks. Gotcha that bit: worker detection must be `config.workerinput`
(process-local), never the `PYTEST_XDIST_WORKER` env var — a pytest
spawned *by a test* inherits the outer suite's variable and must still
record. Also that day: the `checkers` lane in tasks.py (format, lint,
typecheck, typecomplete share one slot so `pytest -n auto` keeps the
cores) — A/B on the gate: 23.8s → 22.4s wall, typecheck itself 6.2 → 5.6s,
lane waits 12/830/24 ms, all visible in the profile. Async pairs use
`id2: {local: …}` so Perfetto files them under fm's process, not "Global
Legacy Events".

## What

`fm --profile[=FILE] check …` writes the run as a Chrome Trace Event Format
JSON file — the format Perfetto UI (ui.perfetto.dev), chrome://tracing and
speedscope all read — so "where did this run's time go" is a picture instead
of a `--json` spelunk. Bare `--profile` writes the default filename;
`--profile=FILE` chooses.

Shipped **in-tree as a plugin named `profile`**: an entry point footman's own
`pyproject.toml` declares under `footman.tasks`, pulled like any third-party
provider with `plugin("profile")`. That placement is half the point — the
feature is a dogfood test of the plugin surface. If the exporter needs a
private hook, the plugin system has a hole; as designed it needs none.

## Why Chrome Trace Event Format

Profiler files split into stack-shaped (pprof, collapsed stacks, speedscope's
native format — where time went inside a call stack) and timeline-shaped
(named intervals on tracks over wall time). A task run is timeline-shaped: a
DAG of named work on concurrent workers.

Within timeline formats, Chrome Trace Event JSON is what this category of
tool already speaks: Bazel `--profile`, CMake `--profiling-format=google-trace`,
Clang `-ftime-trace`, TypeScript `--generateTrace`, Ninja via converters.
Perfetto ingests the legacy JSON (gzipped too), so targeting the old format
buys the modern reader. And it is emittable with stdlib `json` alone — the
zero-dependency invariant never comes up. OTLP was considered and rejected:
right shape, wrong delivery (it wants a collector, not a file you double-click).

## The model: tracks contain slices

The vocabulary is the profiler graph's own, not OTLP's. A **track** is a
horizontal row; a **slice** is a named interval on one; slices on a track
nest, never overlap; overlap belongs on separate tracks. The run maps on:

| run fact | trace form |
|---|---|
| worker thread | a track (`tid` = `thread_id`, named from `thread` via `M` events) |
| task row | a slice on its worker's track (`address`; args: state, code) |
| lane wait (`lane_waits`, since 0b81f76) | slices at the **head** of the task's slice — exact, because acquisition happens inside the timed window, after `started` is stamped and before the body runs |
| `run()` step | a nested slice, once step `Result` gains `started` (agreed below) |
| dependency edge | a flow arrow (`ph: "s"/"f"`) from prerequisite end to dependent start |
| queue wait (`eligible` → `started`) | **not** a slice — see the open call |

The queue wait can't be a slice on the worker track: during the wait the task
*had* no worker, and the worker was busy with someone else's slice. See open
call 4.

## Task-authored profiling (core, not plugin)

Tasks add finer timing through the same model — the two requested modes are
the two structures, not flags:

```python
with footman.section("resolve affected set"):  # subdivides the task's own slot
    ...

ci = footman.track("ci")  # a parallel stream
with ci.section("poll"):  # live, bracketing
    ...
ci.section("build-linux", start=t0, end=t1)  # retroactive: the window the
# CI API reported, not the poll loop

footman.mark("manifest ready")  # an instant, no duration
```

The verb is `section` — a section of the task's time, a section of a track.
"Slice" stays the *file format's* word (it is what Perfetto draws); the API
never says it, and nothing shadows the `slice` builtin.

- Own-track sections come only from the context manager, so they nest by
  construction (one thread, lexical scope). A body's helper threads must use
  named tracks — that is where overlap is legal, and Perfetto agrees.
- The retroactive form takes wall-clock datetimes and maps them through the
  run's clock anchor (below). It is the form the CI-waiting tasks actually
  want: the checks' real windows, learned by polling, not the poll loop's own
  shape.
- `mark()` records an instant (`ph: "i"` in the trace) — a moment worth a
  label, on the task's own track.
- Storage: `TaskResult.sections`, a tuple of a small frozen dataclass
  `Section(name, started, duration, track="")` (`""` = the task's own track;
  a mark is duration `0.0`), recorded via a contextvar keyed to the current
  execution. Flows into the `--json` envelope like `steps` do — the plugin is
  one consumer, hse's CI scripts another. Renderers derive nesting from time
  containment, so the stored list stays flat.

## Core changes owed (all small, all additive)

1. **Step `Result` gains `started`** on the run clock (agreed 2026-08-05).
   Every existing task's `run()` receipts then render as nested slices with
   no annotation anywhere.
2. **`footman.section` / `footman.track` / `footman.mark`** as above, plus
   the `sections` field on `TaskResult` and its `--json` spelling.
3. **A clock anchor**: the run records wall epoch beside its `perf_counter`
   zero, so retroactive datetimes land correctly. Only the retroactive form
   needs it; trace output itself stays run-relative.
4. **Value-optional plugin globals**: built-ins already have the concept
   (`[SHELL]` hints → `_VALUE_OPTIONAL` in `_split.py`); plugin globals reach
   the splitter as bare `flag`/`option` kinds, so `GlobalOption` needs the
   spelling (e.g. `bare=` — the value a bare mention means) threaded through
   the manifest's `globals` entries into `_parse_globals`.

## The plugin itself

`src/footman/plugins/profile.py` (loaded only when pulled — nothing on the
completion hot path, nothing at import time):

- `PROFILE = GlobalOption("profile", Path, bare=Path("fm-profile.json"), …)`
- a `post_tasks(inv)` hook: walk `inv.results`, skip `started is None` rows,
  normalise `ts` to the earliest start, emit `X` slices per task with lane
  waits at the head, nested step and task-authored slices, `M` metadata
  naming worker tracks, async events for named tracks, flow arrows for edges,
  `json.dump` to the chosen file. Stderr gets one line naming the file.

Whole-file write at `post_tasks` (no streaming during the run) — same as
Bazel, and the hook moment is single-threaded on the main thread.

## Decided (Willem, 2026-08-05)

- Plugin ships in-tree, named **`profile`**; the flag is **`--profile`** with
  an optional value and a default filename.
- Profiler vocabulary (tracks/slices), not OTLP's span/trace.
- Step start-stamps: yes.
- Lane waits: already landed (0b81f76); the plugin consumes `lane_waits`
  as shipped, placement at slot head is exact. Durations are trustworthy
  since c4e77b1: the clock covers only the predicate stall, so every
  recorded row is a genuine serialisation — a wait slice never shows
  arbiter-mutex noise.

## Decided in review (Willem, 2026-08-05, second round)

0. **Queue-wait rendering: a number, not a bar.** The eligible → started
   interval has no natural row (the task had no worker, and every worker's
   row was truthfully busy), so the task's slice carries `queue_ms` in its
   args instead of the timeline drawing a bar. Flow arrows make the gap
   findable; a synthetic "queued" track group stays a possible later
   addition if profiles show scheduling pressure earning the rows.

1. The verb is **`section`** (Willem's spelling), not slice/region/zone.
2. **Marks ship in v1.**
3. **Flow arrows ship in v1.**
4. **Default filename confirmed**: `fm-profile.json`, invocation cwd, stable
   name overwritten per run.

## Not in scope

OTLP export, counters, streaming write during the run, cross-run comparison.
The structured-returns thread (parked, post-1.0) is unrelated: `slices` are
machinery-recorded timing, not a task's declared return shape.
