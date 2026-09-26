# The end-to-end CI profile: one timeline from the local command to the last test

Status: ruled 2026-09-26, no phase started. Five phases, each
gate-green and mergeable alone. The feature is investigative: a leg
pushes its trace whatever happens, and nobody pays for it until
someone asks a question.

## The prompt (Willem)

> My ultimate goal for the profile feature is that we can kick off a
> CI task, and get very detailed timing information back of exactly
> what happened. If another CI job is launched inside the CI, its
> profile output should become embedded. So we have an end to end
> overview of exactly what happened from issuing a local command to
> make a release or submit a PR all the way to the end, including
> which tests ran and what happened. I don't need this every time,
> only when investigating where CI time is going or debugging issues.
> Willem, 2026-09-26

And the principle that shapes it:

> I'm trying to abstract CI from the actual forge as much as possible.
> It should be the minimum boilerplate needed to call `fm whatever`,
> which then profiles if configured. Willem, 2026-09-26

## What already exists

- **One trace per run.** `fm --profile=<path> <task>` writes the run
  as a Chrome trace: a slice per task with its state and exit code, a
  slice per step named by the *shown* command line, lane waits,
  sections, marks, and a flow arrow per dependency edge.
- **Every test in it.** footman's pytest plugin puts each test's
  setup, call and teardown on the timeline, xdist workers as named
  tracks, under a `pytest` process beside `fm`'s own.
- **Composition across processes.** A profiled run exports
  `FM_PROFILE_DIR`; any child may drop Chrome-trace fragments there
  with `ts` in **epoch microseconds** and its own `pid`, and the
  writer sweeps them, shifts each onto the run clock through the
  two-clock anchor, and embeds it as its own process group. Epoch
  stamps are what make this work between machines as well as between
  processes.
- **Secrets already redacted.** A step is named `s.shown`, the shown
  line, never the record's, and every showing joins through `redact`,
  so a `Secret` in argv prints as `***`. The `Secret` reaches argv
  whole on purpose, so display-time redaction has a marker to act on.
- **A summary in the metrics row.** The fold keeps each task, each
  lane wait, each package's test time, the slowest tests, and what
  each task's own steps took.
- **A forge-independent job skeleton.** `repo.checks.jobs(run)`
  answers every job with its status, conclusion, start, end and its
  own workflow steps, normalised across a workflow run and a
  pipeline, implemented for GitHub, GitLab and Gitea.

## What the goal needs

A trace that spans machines: the local command at the top, every leg
of the run beneath it, and a run that a leg dispatched beneath the
step that dispatched it.

## Ground-truth contracts (do not violate)

1. **No logic in the YAML.** A job stays one line calling one verb.
   This plan *removes* a step rather than adding one: the artifact
   upload of `fm-profile.json` goes, because the verb pushes the
   trace itself. Nothing is passed through workflow inputs.
2. **The forge is reached only through `livery.forge`.** The job
   skeleton comes from `checks.jobs`; no code here knows a forge.
3. **A sync never pays for this.** The traces live outside
   `refs/workshop/*`, the one refspec `fetch_store` mirrors, so no
   sync brings them and no gate reads them.
4. **A trace is never a second spelling of a command line.** What is
   pushed is what the writer produced, whose step names are the shown
   lines. A secret stays `***`.
5. **Configurable, with defaults that work.** Whether a leg pushes,
   how many runs are kept, and where an assembled file lands are
   `workshop.toml` keys, kebab-case, read by the verb.
6. **Every leg is drawn, whatever happened to it.** A cancelled or
   skipped leg is a slice at its true length carrying its conclusion,
   with its workflow steps inside it. A leg with no trace has no
   children; it is never a gap and never a zero-length span.
7. **Observational, never a verdict.** A push that fails, a fetch
   that finds nothing, a trace that will not parse: each is named and
   none decides a job or a command.

## Phase 1: the trace leaves the leg

Deliverables:

- `[ci] profile` in the workspace contract, three keys with defaults:
  `profile-legs = true` (a leg writes and pushes its trace),
  `profile-window = 20` (runs kept), `profile-into = ".fm/profiles"`
  (where an assembled file lands).
- A `Series` for the traces, declared outside `NAMESPACE`: a third
  class beside shared and local, *pushed* like a shared series and
  *never mirrored* by `fetch_store`. One tree per run keyed by the
  run id, one file per leg, matching how `RUNS` already keys the
  per-leg metrics.
- `fm ci.run` writes its trace with its epoch origin recorded, and
  pushes it to that series when the contract asks.
- The generator drops `_profile_step`: no artifact upload in any
  emitted workflow.

Acceptance:

- `uv run fm check` exits 0.
- A test asserts the emitted `.github/workflows/ci.yml` carries no
  `fm-profile.json` and no upload action.
- A test asserts the new namespace is outside the mirror's refspec:
  `fetch_store` brings nothing of it.
- A test asserts a leg with `profile-legs = false` pushes nothing and
  says nothing.
- A test asserts a push that fails is named and the job's verdict is
  unchanged.

## Phase 2: the skeleton and the assembler

Deliverables:

- An assembler that builds a trace from a run: every job a slice at
  its forge start and length, its conclusion in its args, its
  workflow steps as child slices, and, where the series holds one,
  that leg's own trace re-based onto the run clock and nested under
  it.
- A leg whose trace is absent keeps its slice and its steps; its
  args say why, from the job's own conclusion.
- The assembler is one function both consumers call.

Acceptance:

- `uv run fm check` exits 0.
- A test builds a run of three jobs, two with traces and one
  cancelled, and asserts the cancelled leg is a slice of the right
  length with its conclusion and no children.
- A test asserts a leg's tasks land inside that leg's span, by the
  epoch re-basing and not by order.
- A test asserts a trace that will not parse is named and the other
  legs still assemble.

## Phase 3: the local command carries it

Deliverables:

- A profiled `fm submit`, or any verb that follows a run, fetches the
  legs' traces at the end and drops them into its own
  `FM_PROFILE_DIR` before its writer sweeps, so one file holds the
  local command and every leg beneath it.
- `fm ci.profile [--run=<id>] [--into=<dir>]` assembles the same file
  for a run that has already ended, for investigating afterwards.

Acceptance:

- `uv run fm check` exits 0.
- `uv run fm --profile=<path> submit …` on a branch produces one file
  whose tasks include the local verb's own and every leg's.
- `uv run fm ci.profile --run=<id>` writes a file naming every job of
  that run.
- A test asserts a run whose traces have aged out of the window
  assembles from the skeleton alone and says so.

## Phase 4: the chain, by recorded fact

A run triggered by a push or a squash knows nothing of what caused
it, and the command that caused it has usually exited before the run
it caused exists. So a cause records what it caused, as it learns it,
and the assembler walks those links forward. Three kinds, each a fact
one side already holds:

- **A dispatch.** The forge hands back the run id. Today that is the
  merge point's wave dispatch and `fm workflow.release.dispatch`.
- **A follow.** A verb that pushes a branch follows the run for that
  head, so it holds that run's id, which is how it prints a line per
  job.
- **A merge.** A submit learns the squash commit when the merge
  lands, and the run the merge point starts carries the same commit,
  so the two ends join on a commit both knew independently.

Deliverables:

- Each cause writes its link where the assembler can read it, with
  the child's run id or the commit it will be found by.
- The assembler walks forward from a starting point through those
  links and nests each child under the step that caused it,
  recursively: a local command, its pull request's run, the merge's
  run on main, the wave that run dispatched, the publish at the end.
- `fm ci.profile` takes a starting point as well as a run: a local
  command's own trace, or a commit.

Acceptance:

- `uv run fm check` exits 0.
- A test asserts a parent recording a dispatched id produces a trace
  whose child jobs sit under the dispatching step.
- A test asserts a merge's link joins a local command to the run the
  merge point started, by the commit and never by time.
- A test asserts a commit nobody recorded a link for is assembled as
  a root, never guessed into a tree.

## Phase 5: the numbers decide the defaults

Deliverables:

- A measured window: what a run's traces actually cost on origin once
  packed, from real runs rather than the desk's estimate.
- The defaults tuned to it, in the contract and in the docs.
- A page in the workshop's docs: what the file holds, how to open it,
  and what a leg without children means.

Acceptance:

- `uv run fm check` exits 0.
- The note's decision record carries the measured cost per run.
- `uv run fm docs.build` renders the page.

## Temporary, replaced by

| Scaffolding | Replaced by |
| --- | --- |
| The defaults of phase 1, chosen before any run had pushed | Phase 5's measured window |
| The artifact upload step in every emitted workflow | Phase 1's push from the verb |

## Decision record

- 2026-09-26, Willem: the goal is one timeline from the local command
  to the last test, with a dispatched run embedded, for investigating
  CI time and debugging, not for every run.
- 2026-09-26, Willem: CI is abstracted from the forge as far as it
  goes; a job is the minimum boilerplate to call `fm whatever`, which
  profiles if configured. So no workflow input carries this, and the
  existing upload step goes.
- 2026-09-26, Willem: the side channel is pushed but never mirrored,
  and ages out by a window; both are configurable, "those are fine
  defaults when we know nothing".
- 2026-09-26, no compression of our own. Measured on a real 3.00 MB
  trace: git's own zlib gives 10.2x (0.29 MB), zstd at its default
  9.9x, zstd 10 11.8x, zstd 19 13.2x at 0.78 s a job. Thirty-six
  traces of six legs across six runs are 103.1 MB raw, 11.1 MB
  compressed one by one, and 10.0 MB once git packs them, because
  near-identical traces delta against each other. A zstd blob cannot
  delta, so pre-compressing loses more than it saves.
- 2026-09-26, Willem: a skip is something that happened and belongs in
  the trace as an event, not a note beside it. So every leg is a slice
  from the forge's own timestamps carrying its conclusion, and the
  absent case disappears.
- 2026-09-26, secrets need no new rule: a step is named by the shown
  line, which redacts a `Secret` to `***`, and the `Secret` reaches
  argv whole so that redaction has a marker to act on.
- 2026-09-26, Willem asked whether a release is traceable from the
  local command through the merge and the squash to the publish. It
  is, by recorded fact rather than by time, but not inside the live
  command: the merge point's run starts as the submit ends and the
  wave later still, so a cause writes its link down and the assembler
  walks it afterwards. Three kinds of link: a dispatched id, a
  followed run's id, and a merge's commit, which both ends know.

## Open

1. **The window's depth**, from phase 5's measurement. Owner: Willem.
2. **Whether a leg of a nightly or a release run pushes too**, or the
   gate alone. Owner: Willem, once phase 1 has run a few days.
