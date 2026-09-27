# The end-to-end CI profile: one timeline from the local command to the last test

Status: phases 1 to 4 landed 2026-09-27 (issues #782, #795, #798,
#802): a profiled run carries what it launches, a leg's trace leaves
the runner for a channel no sync mirrors, one run assembles into one
timeline, and the local command that caused a run carries it. The
prompt this plan opens with is answered. Phases 5 and 6 wait their
turn, each gate-green and mergeable alone. The feature is
investigative: a leg pushes its trace whatever happens, and nobody
pays for it until someone asks a question.

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

## Phase 1: a profiled run infects what it launches

`--profile` opens a drop box and names it in the environment, and a
child may leave a fragment there, stamped in epoch microseconds, which
the writer embeds as its own process group. What no child does is
notice: the plugin arms on `--profile` being mentioned on *this*
command line, so an `fm` a task spawns writes nothing, and a re-exec
carries the flag through `sys.argv` but loses everything that happened
before it, which is exactly the sync a reconcile had just run. The
drop box leaks on the way out, since an exec runs no exit handler.

This phase stands alone, wants no forge, and is what the later phases
reuse: a leg's `fm` is a child like any other, one machine further
away.

Deliverables:

- A child `fm` that finds the drop box in its environment profiles
  itself into it, as a fragment rather than a standalone file, and a
  child of that child does the same.
- A process about to re-exec writes what it has done so far as a
  fragment into the same drop box and hands the box on, so the work
  before the exec is in the trace and the directory is consumed
  rather than leaked.
- A run that mentions no profile and finds no drop box behaves as it
  does today, writing nothing and reading nothing.

Acceptance, and what proves each. The tests live in
`packages/footman/tests/test_profile.py` unless another file is
named; a test's name stays on one line, wrap or no wrap, so `grep`
finds it.

- `uv run fm check` exits 0.
- A profiled parent whose task spawns `fm` gets that child's tasks as
  a process group of its own, and a child of that child the same:
  `test_a_profiled_parent_gets_the_inside_of_the_fm_it_spawned`
  spawns three real runs and pins each one's slice inside the step
  that spawned it.
- The work before a re-exec is in the trace and the box goes to the
  successor: `test_the_handoff_hands_the_box_on_and_writes_no_file`
  and `test_the_successor_adopts_the_box_it_was_handed`, which also
  pins the slide that keeps every stamp positive.
- A replacement that could not start costs nothing:
  `test_a_handoff_taken_back_writes_the_file_after_all`.
- An unprofiled child writes nothing and reads nothing:
  `test_without_the_flag_nothing_is_written` and
  `test_a_child_whose_box_has_gone_writes_nothing`.
- Windows, where the handoff waits instead of becoming its
  replacement, writes no file of its own: the same handoff test,
  which holds on every platform because standing the writer down is
  what the handoff does.
  `test_workshop_entry.py::test_a_profiled_rerun_hands_its_trace_on_with_the_guard`
  pins the environment the reconcile builds, which both its arms
  share.
- No box outlives its run:
  `test_the_exit_sweep_removes_every_box_not_only_the_newest`, and, for
  the box no exit handler could reach,
  `test_gc.py::test_collect_sweeps_a_profile_box_its_run_never_took_away`.

## Phase 2: the trace leaves the leg

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

Acceptance, and what proves each. The tests live in
`packages/workshop/tests/test_workshop_traces.py` unless another file
is named.

- `uv run fm check` exits 0.
- The emitted workflows carry no trace out:
  `test_workshop_templates.py::test_a_gate_leg_is_one_verb_and_uploads_no_trace`
  reads both lanes and finds no `fm-profile.json` and no upload step,
  and the check job's last step is the one verb.
- The channel is outside the mirror's refspec, and the trace is on the
  run's own ref:
  `test_the_trace_lands_on_the_run_s_ref_and_no_mirror_brings_it`,
  which fetches the store and finds nothing of it.
- A leg told to push nothing says nothing:
  `test_a_contract_that_wants_no_traces_pushes_nothing_and_says_nothing`.
- Every other refusal is a line and no verdict: a run that is not CI,
  a leg the runner did not name, a trace never written, a key of the
  wrong type, and origin's own refusal, one test each.
- The window is the janitor's:
  `test_the_janitor_keeps_the_window_s_newest_runs_and_drops_the_rest`,
  which also pins that a second sweep finds nothing to do.
- The trace says where its zero sits on the wall clock, which phase 3
  re-bases by:
  `packages/footman/tests/test_profile.py::test_the_trace_records_the_epoch_its_own_zero_sits_at`.

## Phase 3: the skeleton and the assembler

Deliverables:

- An assembler that builds a trace from a run: every job a slice at
  its forge start and length, its conclusion in its args, its
  workflow steps as child slices, and, where the series holds one,
  that leg's own trace re-based onto the run clock and nested under
  it.
- A leg whose trace is absent keeps its slice and its steps; its
  args say why, from the job's own conclusion.
- The assembler is one function both consumers call.

Acceptance, and what proves each. The tests live in
`packages/workshop/tests/test_workshop_traces.py` unless another file
is named.

- `uv run fm check` exits 0.
- A run of four jobs assembles:
  `test_the_run_is_its_jobs_their_steps_and_every_leg_that_left_a_trace`
  pins a skipped job as an instant carrying its conclusion, a running
  job as a slice up to the reading, the wait before a job as its own
  span, and the steps inside their job.
- A leg lands inside its own job by the epochs and not by order: the
  same test pushes the two legs newest first, so their order and their
  origins disagree, and each still lands in its job's span.
- A trace that will not parse is named and the rest assemble:
  `test_a_leg_whose_trace_will_not_parse_is_named_and_the_others_assemble`.
- The refusals: a channel that cannot be listed, a run the forge does
  not list, a stamp the forge spells wrongly, a ref carrying no trace,
  and a read the store refuses, one test each.
- The laying on is the plugin's, and proved there:
  `packages/footman/tests/test_profile.py::test_a_trace_is_laid_on_another_clock_by_the_origins_alone`
  and its refusals beside it.

## Phase 4: the local command carries it

Deliverables:

- A profiled `fm submit`, or any verb that follows a run, fetches the
  legs' traces at the end and drops them into its own
  `FM_PROFILE_DIR` before its writer sweeps, so one file holds the
  local command and every leg beneath it.
- `fm ci.profile [--run=<id>] [--into=<dir>]` assembles the same file
  for a run that has already ended, for investigating afterwards.

Acceptance, and what proves each. The tests live in
`packages/workshop/tests/test_workshop_traces.py` unless another file
is named.

- `uv run fm check` exits 0.
- A profiled submit carries the run it followed:
  `test_workshop_submit.py::test_a_profiled_submit_puts_the_run_it_followed_in_its_own_trace`
  drives the real submit flow to a merge against the fake forge and
  reads the fragment back, and
  `test_the_run_a_command_followed_joins_its_own_trace` pins the wall
  clock stamps a box takes.
- An unprofiled submit asks the forge for no run at all:
  `test_a_command_keeping_no_trace_drops_nothing` and
  `test_workshop_submit.py::test_an_unprofiled_submit_asks_the_forge_for_no_run_at_all`.
- A file a person can open:
  `test_a_run_is_written_where_a_person_can_open_it`, which pins the
  contract's directory, the recorded origin, and stamps starting at
  zero.
- A run whose traces have aged out assembles from the skeleton alone:
  `test_a_run_whose_traces_have_aged_out_writes_the_skeleton_alone`.
  What ages out is the detail, never the shape of the run, which is
  what makes a short window safe.
- A run nobody can name refuses rather than writing an empty file:
  `test_a_run_nobody_can_name_refuses_to_write_a_file`.

## Phase 5: the chain, by recorded fact

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

## Phase 6: the numbers decide the defaults

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
| The defaults of phase 2, chosen before any run had pushed | Phase 6's measured window |

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

- 2026-09-27, Willem asked for the infectious half to be parked: a
  profiled run must carry what it launches, which is phase 1 here.
  Recorded because it was ruled after the plan first merged.
- 2026-09-27, the handoff is a block, `profile.handing_off()`, which
  takes itself back when the process was not replaced after all. An
  exec that fails leaves the run going, and it must not have paid for
  the attempt with its own trace. A plain call would have had to be
  undone by a second call that a caller can forget.
- 2026-09-27, Willem: a box belongs in footman's cache, not the
  system's temporary directory. So the collector gets a rule for one
  that outlived its run, which is the only backstop an exec or a kill
  leaves. Found on the way: every armed run that never reached the
  writer leaked its box, because the exit sweep asked whether a box
  was the newest rather than whether it was still open. 107 empty
  directories had gathered on this desk since 2026-09-24.
- 2026-09-27, two things the first profiled submit taught, both found by
  running the acceptance's own command rather than by a test. A leg runs
  its own tests under a profile, so a submit test's rig with no
  repository in it turned a watched merge into a failed submit: a trace
  is observational, and nothing it does may change what the run decided,
  so every failure inside it is a printed line. And the run was traced
  only on the way to a merge, so a red run wrote a file holding the
  local command and nothing else, which is the case a timeline is wanted
  for most. Every way out of the watch now traces the run.
- 2026-09-27, assembling a real run found two more, and both were
  invisible to every test that had a job named `check`. A leg's ref is
  keyed by the label the runner sets and the forge lists a matrix job
  under another spelling entirely, so the join matched nothing and every
  matrix leg's own trace was dropped while the skeleton looked whole.
  The name the forge uses is written down beside the trace now and joined
  on. And a forge times in whole seconds, so a skipped job came back
  ending a second before it began: a job or a step with no extent is an
  event, never a span, which is the plan's own ground truth. The display
  name of a one-dimension matrix was wrong too, `nightly (, 3.14)`
  against the forge's `nightly (3.14)`, which nothing had needed until
  the join did.
- 2026-09-27, and the deeper half of the same finding: inside a profiled
  leg every test inherited the box, so a `Runner` in a test dropped a
  fragment of its own once per test, and any code asking whether a trace
  is kept answered one way on a runner and the other on a desk. The box
  now leaves each test's environment, and a test that wants one sets its
  own. The suite's own fragment is untouched, because the recorder takes
  the name when the session begins.
- 2026-09-27, Willem asked whether a group can have a default task. It
  can, and `fm ci.profile` is one, so the plan's own spelling stands.
  The leg's push stays hidden beside it.
- 2026-09-27, Willem: a re-exec is footman's moment, not a plugin's
  caller's. So footman grew a hook kind, `pre_reexec`, whose
  subscriber is a context manager yielding environment entries for the
  successor, and `footman.handing_off()` enters every one the run
  mounted. The workshop's reconcile now knows nothing about traces,
  and nothing mounted hands nothing on. The block's shape carries the
  semantics: returning normally means the replacement did not happen,
  so each subscriber takes its state back, while an exception means it
  did and what they wrote stands.
- 2026-09-27, Willem asked why footman's core should know the drop
  box's variable name, and it should not. The name is the plugin's,
  spelled once there and once in the pytest plugin, which speaks the
  convention as any foreign tool would. A caller that wants to know
  whether building a timeline is worth it asks the plugin:
  `profile.keeping()`, one environment read, so a submit that nobody
  profiled asks the forge for nothing at all.
- 2026-09-27, the assembler answers with a value rather than a tuple:
  the events, the wall-clock moment they are measured from, and the
  lines. The local command wants the events on the wall clock and the
  file writer wants them from the run's own start with that moment
  recorded, and both read one object.
- 2026-09-27, Willem asked where this code belongs. The trace
  format's own work is the plugin's: laying one trace on another's
  clock, which is the shift by two recorded origins, the renumbering
  of process groups so two traces never read as one process, and the
  refusals that go with reading a trace. The forge's skeleton cannot
  go there, because footman declares no dependency on `livery.forge`
  and forge is stdlib-only, so a slice per job, an instant for a job
  the forge never timed, the wait before a job and its steps stay in
  the workshop. The channel stays there too: refs, contract, window.
- 2026-09-27, a job is a track of the run's own process, so jobs that
  ran at once read as the parallel work they were, and a leg's own
  trace keeps process groups of its own, renumbered per leg. Chrome's
  format has no way to nest one process inside another's slice, and
  the alignment in time is what makes it read as nesting, which is
  what phase 1 already proved on a real run.
- 2026-09-27, the wait before a job is drawn only when the caller
  knows the head: the run's acceptance time comes from the run's own
  row, and a run id alone cannot ask the forge for it. Without a head
  the jobs' own times are the whole skeleton, and the line says so.
- 2026-09-27, measured from run 36287488870, for phase 6's window:
  three check legs pushed 3,061,718, 3,060,393 and 3,065,870 bytes,
  9.19 MB together, which repack to 837 KiB in an empty clone, 11.2x.
  Better than the 10.2x one trace measured alone, because the three
  platforms' traces delta against each other. Twenty runs at that
  rate is around 16 MB on origin.
- 2026-09-27, a leg's trace is the profiled entry's own file, pushed
  by a later entry of the same job (`fm ci.profile.push`), and not a
  trace of the whole leg. The leg's timing row is built by an entry
  that reads the file the profiled entry wrote, and a trace of the
  whole leg is only written once every entry has run, so the row would
  have come out empty. What the leg's other entries cost is visible
  from the forge's job skeleton instead, which is phase 3's input
  anyway.
- 2026-09-27, a profiled run that is itself a child hands its whole
  trace up into the parent's box as well, rather than appearing there
  as the one step that spawned it. So a profiled entry that spawns
  another profiled run keeps its inside, which the release wave does.
- 2026-09-27, the traces live under `refs/workshop-trace/`, a third
  ref class beside the shared and the local ones. The store's write
  and delete guards name it, and one listing carries it so a re-run's
  compare-and-swap has a sha to lease against. The files are traces
  and not rows, so the family declares as much and the row sweep
  leaves them alone.
- 2026-09-27, the window is a count of runs, applied by the janitor
  through a family's own `keep`, and the runs are ranked by their ids
  rather than by their refs' commit times: a forge hands out ids in
  increasing order, and reading a commit time would mean fetching
  every trace the janitor is about to delete.
- 2026-09-27, what the pre-exec fragment carries: this process's own
  span and the running task's `run()` steps. Tasks that finished
  earlier in the same process are not reachable from a task body, and
  reaching them would mean a live ledger the scheduler does not keep.
  The reconcile re-execs inside the first task of a sync, so the
  fragment holds what that sync had done.

## Open

1. **The window's depth**, from phase 5's measurement. Owner: Willem.
2. **Whether a leg of a nightly or a release run pushes too**, or the
   gate alone. Owner: Willem, once phase 1 has run a few days.
