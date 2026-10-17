# Shard the check legs across runners, and carry coverage across a change

Status: written 2026-10-17; phase 1 in progress.

## The prompt (Willem)

> I'd like to find a way to shard tests for a platform over multiple
> runners, to improve CI times. I'd love something generic, that auto
> balances the platforms to finish roughly at the same time. Willem,
> 2026-10-17

> If we have to change coverage, could we think about making asymmetric
> platform coverage not an issue, so we could temporarily only run linux
> ci during heavy development? What are github's fair use policies? I
> don't want to take the mick. Willem, 2026-10-17

The intent: a pull request's wall is the slowest platform's test
step, and that platform runs its tests over several runners so every
platform ends at about the same time. The counts are chosen from the
timing record, by a verb, and recorded in the contract. The same
union change that lets shards combine lets a pull request run on one
platform while the other platforms' readings carry across the
change, so heavy development runs on linux alone without losing the
platform-only lines, and the runners GitHub gives a public repository
are used less, not more.

## What exists that this builds on

- The gate point's `check` job fans out over `[ci] runners` and the
  gate Pythons (`livery.workshop._ci_generate._actions_job`, matrix
  `legs`); a pull request's legs are `[ci] pull-request-runners`,
  picked by the `legs` job from two literals when the lists differ
  (`_legs_job`). The GitLab shell renders one job per `legs` matrix
  (`_gitlab_job`). Every runner's label and Python reach the leg as
  `fm ci.run --os --python`, and `livery.workshop._points.run_point`
  names the leg `check-<os>-<python>` in `WORKSHOP_LEG`, the key of
  its rows, its per-run ref and its traces.
- The gate inside a leg narrows to the packages a change affects
  (`[ci] affected-legs`), and each package's suite runs as one
  pytest call over its test directory, or over the test files the
  run selects (`PhaseContext.tests`), under `-n auto --dist=worksteal`
  (the pytest extension's `judge_test`, the python extension's
  `suite_runner`).
- Every leg runs profiled. Its trace holds every test's setup, call
  and teardown with its node id; `livery.workshop._metrics.put_leg`
  sums them per package into the leg's timing row, names the ten
  slowest, and the gate job's `collect` joins the rows with the
  forge's job times on the `metrics` series. `fm ci.timings` reads
  them; `fm ci.profile` assembles a run's trace.
- Coverage is global: each leg leaves its suites' parts in the line
  format, keyed by the suite's closure identity
  (`livery.extensions.coverage._leg.gather_leg`), and the gate job's
  `coverage.union` merges every leg's parts with the rows the records
  carry for what a leg did not run (`_union.combine_union`), judges
  the floors on that union, and writes it back to the branch's record
  or main's. A leg the contract names and a pull request did not run
  is carried whole from the records. The merge keeps, per file, the
  readings at the checkout's content and names every reading it
  leaves out (`_format.merge`), so a file the change touched loses
  its carried readings, and that is why pull requests run every
  runner today (ruled 2026-10-12, #1499).
- The verified stamp reads every check leg's scope from the run's
  rows (`_verified.stamp_from_metrics`) and stamps the tree `full`
  when every leg the run had ran the whole gate; a leg skips its gate
  on any full row naming its tree, whichever legs that row names
  (`_gate_record.leg_plan`, `_verified.proved`); the speed marks judge each
  leg's summed test time per package (`_speed.judge_run`); the
  coverage records and the janitor's key set are per leg
  (`_record.current_keys`, `livery.workshop.check_legs`).
- A step declares the flags it reads (`livery.workshop._steps.FLAGS`,
  `point` today), and the generated verb offers them.

## The measures

From the CI store on 2026-10-17, run 38974040814, the newest green
run where every leg ran the full gate:

| leg | job wall | test step | summed test time | fixed setup | largest file |
|---|---|---|---|---|---|
| ubuntu | 359 s | 239 s | 534 s | 31 s | 72 s |
| macos | 952 s | 675 s | 1231 s | 74 s | 164 s |
| windows | 1329 s | 1029 s | 2514 s | 69 s | 279 s |

The run's wall was 1407 s; the gate job waited 1335 s on Windows.
The test step is 67 to 77% of each leg, and xdist reaches 2.2 to
2.9 of the runners' cores. There are 348 test files;
`packages/workshop/tests/test_workspace_provenance.py` is the largest
on every platform.

Longest-first packing of per-file durations into n shards, the
partition taken from run 38966204107 an hour earlier and evaluated on
run 38974040814 (`fm ci.profile --run=<id>` for each, the per-file
sums from the traces):

| shards | ubuntu | macos | windows |
|---|---|---|---|
| 1 | 276 s | 730 s | 1082 s |
| 2 | 157 s | 408 s | 576 s |
| 3 | 117 s | 296 s | 411 s |
| 4 | 115 s | 241 s | 325 s |

One platform's shards land within 3% of each other on hour-old
durations. Per-file durations between the two runs moved by a factor
of 0.57 to 0.99 (p10 to p90) on ubuntu, 0.74 to 1.46 on macos and
0.93 to 1.19 on windows, and the packing absorbed it. With counts of
1, 3 and 4 the slowest leg is 325 s, the run about 400 s with the gate
job, and runner seconds rise 17%, from 2088 to 2443.

The load over the store's last six days: 50 runs and 18.4
runner-hours a day (linux 7.2, windows 6.2, macos 5.0), 171 pull
request runs against 129 pushes, the busiest day 81 runs and 29.7
runner-hours. GitHub's billing page says standard hosted runners are
free in public repositories; its Actions terms bar cryptomining,
reselling, "any activity that places a burden on our servers, where
that burden is disproportionate to the benefits provided to users",
and anything "unrelated to the production, testing, deployment, or
publication of the software project"; the limits are 20 concurrent
jobs and 5 concurrent macOS jobs on this plan, 6 hours per job and
256 jobs per matrix (read 2026-10-17). Pull requests on linux alone
cut the macOS and Windows load by the pull request share, about 57%.

## Ground-truth contracts (do not violate)

1. **The verdict is a verdict.** A sharded platform proves what one
   leg proved: the gate job refuses, naming the shards, unless the
   shards of a platform ran one scope, partitioned one test-file set
   from one durations row, and together left every unit's parts.
   Nothing is assumed from a shard that did not write.
2. **A shard is invisible below the gate job's fold.** The coverage
   records, the verified stamp, the speed marks and the janitor's key
   set are keyed by the platform leg, `check-<os>-<python>`, as today.
   The fold happens once, in the gate job, before any of them read.
3. **The partition is a function of its inputs.** Given the test-file
   set and the durations row, every shard computes the same
   partition, and every file lands in exactly one shard. A file the
   row does not name takes the row's median. No shard reads a row
   written after the run's own start.
4. **A carried reading stays at the lines it measured.** A reading
   carried across a change applies to a line only where the change
   left that line and its statement in place; a reading on a line the
   change touched or removed is dropped and named. Nothing is
   remapped by guess.
5. **The decision is the contract's, the YAML is plumbing.** The
   shard counts live in `[ci] shards` and `[ci] pull-request-shards`;
   the rendered workflows carry literal matrices; the legs job picks a
   literal by the event and by a label a verb sets, and computes
   nothing.
6. **The floor never drops on a platform-only line unseen.** A pull
   request run on fewer platforms judges changed lines on the legs
   that ran and unchanged lines on every platform's carried readings;
   a changed line only another platform reaches is red by name, and
   the merge point's full run corrects the record.
7. **The balancer reads the record and writes the contract, never the
   run.** `fm ci.balance` changes nothing inside a run; a changed
   count lands by pull request. The counts can be set by hand.
8. **Every verb is idempotent and fails open on the record.** A
   store that cannot be read prints why and the leg runs unsharded
   as one shard of one; the balancer writes nothing it cannot
   derive from at least three full green rows per platform.

## The design

### The carry across a change

`_format.merge` gains a remap: a reading whose digest differs from
the checkout's is remapped line by line through the diff between the
blob the reading was taken at and the checkout's content, when the
blob is in the checkout's object store (a git read by the digest, one
per file). The diff's equal blocks map a line to a line; a statement
on a mapped line keeps its hit, its counts, its branches when both
ends map, and the contexts that reached it; everything else is
dropped and named in `left_out`, as a left-out reading is today. The
remapped reading merges with the readings at the current digest, by
the existing rules, and takes the current content's facts. A reading
whose blob the checkout does not hold is left out as today.

The merge takes the facts from a reading at the current digest, so a
file needs one leg that measured it at the current content; a run
that ran at least one leg has that for every file in a unit's
closure, and a file no leg measured keeps its carried reading whole,
as today.

What the carry gives up: an unchanged line whose reachability changed
elsewhere, its only Windows caller deleted say, keeps a stale reached
reading until main's full run, where today it would show as uncovered
on the pull request; the kind of staleness is the one the whole-file
carry already has, extended to touched files.

The verified record becomes a proof per platform: a row keeps naming
the legs that ran, and a leg reads a row as proving its tree only when
the row names its own platform leg. Today a run on one platform would
stamp the tree `full` and the other platforms' legs would skip a tree
they never ran. After a pull request on linux alone merges, the merge
point's ubuntu job still starts, since the matrix is a literal: it
sets up, finds its platform in the row, and ends its check in 0 s,
about 45 s of runner time off the critical path; macos and windows
run the full gate, and the stamp then names all three.

With the carry in place `[ci] pull-request-runners = ["ubuntu-latest"]`
is sound: a pull request on linux alone judges the union with the
other platforms carried across its change. A pull request that
changes a line only another platform reaches is red naming the line,
and needs the full matrix: `fm submit --every-runner` sets the label
`ci:every-runner` on the pull request through the forge, the gate
workflow runs on `labeled` as well as on a push, and the legs job
picks the full literal while the label is there. The label is the
developer's to set on the forge by hand too.

### The shard axis

`[ci] shards` maps a runner label to a count, one for a runner it
does not name; `[ci] pull-request-shards` the same for a pull
request's legs, `[ci] shards` when absent; `[ci] shards-max` caps
what the balancer may choose, 4 when absent. The check matrix renders
as `include` entries, one per os, python and shard, the shard spelled
`<i>of<n>`: `check (windows-latest, 3.14, 2of4)` on GitHub and Gitea,
the leg label `check-windows-latest-3.14-2of4`. The platform leg of a
label is the label without its shard, and `check-<os>-<python>`
stays the spelling of a count of one. GitLab's job takes `parallel:
matrix` over the first runner's shards and passes `--shard`. The
legs job renders when either the runner lists or the shard maps
differ between the events, or the label applies, and holds the
literals.

`fm ci.run --shard=2of4` reaches `run_point`, which formats it into
the entries' arguments (`{shard}`), the leg label and the display
name. `fm check --shard=2of4` is the flag the `shard` entry of
`livery.workshop._steps.FLAGS` names: a step that declares
`flags = ["shard"]` receives the shard and runs its share; a step
that does not runs on the first shard alone, and the walk says so
for each step it leaves to the first shard. `fm test.pytest
--shard=2of4` runs one shard's share on a machine, reading the
mirror the last sync fetched.

### The slice

The pytest test step declares `flags = ["point", "shard"]`. With a
shard it lists the test files of every suite the run reaches, after
the affected narrowing and the point's selection, as
`PhaseContext.tests` names them or as the suite's directory holds
them, and partitions them by the durations row: longest first, each
file into the shard with the least summed seconds, ties by name. The
step hands the runner the slice as the per-package selection it
already takes. A suite that is not worker-safe (`parallel = false`)
is sliced the same way and still runs in its own `-n 0` call. The
top-up a leg runs for suites no record can supply slices the same
way, since it runs through the same step.

The durations row: the family `durations/<platform leg>` on the state
store, window 30, one row per run, `{"run": <id>, "files":
{<path>: <seconds>}}`, written by the gate job from the shards' rows,
each shard's row carrying the per-file sums `put_leg` reads from its
trace beside the per-package sums it reads today. A shard reads the
rows written before its run's `created_at`, which the forge names
for the run, and takes the median per file over the newest five. A
run with no row runs unsharded on the first shard and says so, and
the gate job proves that too.

The marker a leg leaves (`fm-gate.json`) gains `shard`, the digest of
the test-file set it partitioned, the files it ran, and the row it
read. The gate job folds the shards of a platform into one leg before
the union, the stamp and the marks read: one scope, one file set, one
row, the parts of every shard concatenated per unit, the package sums
added. A platform whose shards disagree, or one of whose shards left
no ref, is red by name.

### The balancer

`fm ci.balance` reads the `metrics` window's full green rows, folded
per platform, and takes per platform the fixed setup F (every step
but the call), the longest task that is not the test step O, the
summed test seconds S and the measured parallelism p, the median of
each shard's S over its test wall. It predicts the leg wall at n
shards as F + max(O, S / (p n)), the first shard's slice smaller by
O's measured seconds. The counts: the slowest platform takes the
largest n within `shards-max` whose last shard saved at least F;
every other platform takes the smallest n whose prediction is within
15% of the slowest platform's; the pull request counts the same over
the pull request runners. A platform with fewer than three full green
rows keeps its count. Without `--write` it prints the table; with it,
it rewrites the two keys when a count changes and the predicted run
wall moves by more than 10%, so the counts do not flap; `--submit`
commits the change on a branch of its own and submits it, as
`toolroom.refresh --submit` does. The nightly runs `ci.balance
--submit` once a day through `[[ci.schedule]]`.

### What it does not cover

- The type checkers and the examples step are not split. A platform
  floors at F + O, about 115 s on ubuntu. A step may declare a grain
  of its own later, packages, and shard the same way.
- The nightly's `pythons` matrix and the release point are not
  sharded; a local `fm check` is unchanged.
- The cores a leg leaves idle inside xdist, 1 to 2 of 4, are a
  separate measurement.
- Deciding the runners per change from the records' platform-only
  lines, so no mode is needed. The carry makes the mode sound; the
  automatic version is an extension.

## Phases

### Phase 1: the carry across a change, and the every-runner label

Deliverables:

- `livery.extensions.coverage._format.merge` remaps a reading at
  another digest through the diff to the current content, by the
  rules of contract 4, and names what it drops. The git read by
  digest is a function of the coverage extension, through
  `livery.git`.
- `_union.combine_union` passes the checkout's content so the remap
  applies to carried rows and to absent legs' rows alike; the union's
  printed lines say how many readings carried whole, carried across a
  change, and were left out.
- The verified record proves per platform: `_verified.proved` and
  `_gate_record.leg_plan` read a row as proof for the leg's platform
  alone, and `stamp_from_metrics` stamps the legs that ran; a merge
  after a one-platform pull request runs the other platforms in full
  and ends the proved one at once.
- `fm submit --every-runner` sets `ci:every-runner` on the pull
  request through the forge (GitHub, Gitea, GitLab), the gate
  workflow lists `labeled` among its pull request events, and
  `_legs_job` picks the full literal while the label is there.
- The docs: the coverage extension's page describes the carry, what
  it drops, and the label; the workshop's docs name the mode.
- Tests first for the refusals and the drops: a full row naming
  another platform's leg proves nothing for this one; a reading on a changed
  line is dropped and named; a deleted file's reading is left out; a
  blob the checkout lacks leaves the reading out; a branch with one
  end moved is dropped; then the carry of an unchanged line's hit,
  counts, branches and contexts, and the union of a linux-only run
  with a changed file against a record from main.

Acceptance:

- `fm check` green in the worktree.
- A workspace test runs the union for a pull request event with
  `[ci] pull-request-runners = ["ubuntu-latest"]` over a changed file
  whose Windows-only branch is unchanged, and the floor holds; the
  same with the branch's line changed, and the floor is red naming the
  line: `fm test.pytest -- -k carry`.
- `fm ci.e2e` passes with the loop's pull request scenario, and the
  loop's e2e lines pin the union's carried-across-a-change line.
- The plan's status line names phase 1 complete, in the same change.

### Phase 2: the shard axis, the slice and the fold

Deliverables:

- The contract keys `[ci] shards`, `[ci] pull-request-shards` and
  `[ci] shards-max`, declared in the workshop's `contract.toml`;
  the matrix rendered as `include` entries for GitHub and Gitea, the
  GitLab job's `parallel: matrix`; `fm ci.run --shard`; `run_point`
  naming the shard in the label, the display and the entries'
  arguments; `livery.workshop.check_shards` beside `check_legs` and
  `platform_leg`.
- `shard` in `FLAGS`; the walk runs a step without the flag on the
  first shard alone and prints the steps it left there; `fm check
  --shard` and `fm test.pytest --shard`.
- The pytest step's slice, the partition function with its tests
  (determinism, every file once, unseen files at the median, ties by
  name), the marker's new fields, the leg row's per-file sums, the
  gate job's `durations/<leg>` write and the pin on the run's
  `created_at`.
- The fold in the gate job, before `coverage.union`,
  `ci.verified.stamp` and `speed.judge`, and the proof of contract 1
  with its refusals.
- `fm ci.timings` prints a platform's shards folded and each shard's
  own row; `fm ci.profile` lays shards side by side.
- The docs: the workshop's CI section describes the axis, the slice,
  the fold and the keys.
- Tests first for the refusals: a shard that left no ref, shards on
  different rows, a file in two shards or in none, a row newer than
  the run, an unreadable store falling open to one shard.

Acceptance:

- `fm check` green in the worktree, and `fm check --shard=2of2` in
  the worktree runs the test step over a slice and every other step,
  `fm check --shard=1of2` the complement, and the two slices' file
  lists partition the whole: a workspace test proves it.
- `fm ci.e2e` passes with `[ci] shards = { "ubuntu-latest" = 2 }` in
  the loop's workspace: two check jobs, the union green, the verified
  stamp `full`, the durations row written; the e2e lines pin the fold's
  line and the proof's line.
- On this repository, with `[ci] shards = { "macos-latest" = 3,
  "windows-latest" = 4 }` set by hand in the same pull request: the
  pull request's run green with eight check jobs, and
  `fm ci.timings` after the merge shows the slowest leg under 400 s.
- The plan's status line names phase 2 complete, in the same change.

### Phase 3: the balancer

Deliverables:

- `fm ci.balance`, its prediction table, `--write` with the
  hysteresis, `--submit`, and the nightly's schedule entry.
- Tests first: fewer than three rows keeps the count; a change under
  10% writes nothing; the slowest platform's marginal rule; the other
  platforms' 15% rule; the cap; the pull request counts over the pull
  request runners.
- The docs: the balancer's page, what it reads, and how to set a
  count by hand.

Acceptance:

- `fm check` green in the worktree.
- `fm ci.balance` on this repository prints the table of the measures
  section from live rows and proposes the counts the contract holds,
  or says what moved: run by hand, output in the pull request.
- The nightly after the merge runs the entry and prints its verdict:
  `fm ci.logs --point=nightly`.
- The plan's status line names phase 3 complete and the plan closed.

## Temporary, replaced by

| temporary | replaced by |
|---|---|
| counts set by hand in phase 2 | `fm ci.balance --write`, phase 3 |
| the mode, `pull-request-runners` on linux | the runners decided per change, an extension |

## Decision record

- 2026-10-17, Willem: the shard counts live in the contract,
  rebalanced by a nightly verb that submits a pull request; the
  grain is the test file. Rejected: counts chosen at run start by the
  legs job; the package as grain.
- 2026-10-17, the agent: the unsplit steps run once per platform,
  on the first shard, never on every shard; the packer loads the
  first shard with their measured time.
- 2026-10-17, the agent: GitLab shards too, through `parallel:
  matrix`, by the first runner's count; the earlier claim that it
  stays one leg was wrong.
- 2026-10-17, Willem: the carry across a change is in, so pull
  requests can run on linux alone during heavy development; both
  this repository and GitHub's runners come out ahead. The carry
  remap is phase 1, the shard axis phase 2, the balancer phase 3.
- 2026-10-17, Willem: keep going until told otherwise, merge on green
  with good coverage, and release when a phase needs it to progress.
- 2026-10-17, Willem asked whether ubuntu still runs on the merge
  after a pull request proved it. It starts, since the matrix is a
  literal, and ends its check at once on the per-platform proof;
  not starting it would need the matrix decided at run start, which
  the first ruling rejected.
- 2026-10-17, the agent: the pin is the run's `created_at` from the
  forge, since rows named by run id do not order a write that lands
  between two shards' reads; the proof in the gate job catches the
  rest.

## Open

1. `fm ci.balance --submit` armed or unarmed. The toolroom refresh
   arms when every change only added; a count change is mechanical
   and the render gate proves it. Willem, before phase 3.
2. Whether `labeled` on the gate workflow should also cancel the
   in-flight run of the same head, as a push does. The agent, in
   phase 1, from the concurrency group the workflow renders.
3. The tolerance, 15%, and the hysteresis, 10%: constants chosen
   before the rows can set them; a reading of a month of rows may
   move them. The agent, after phase 3.
