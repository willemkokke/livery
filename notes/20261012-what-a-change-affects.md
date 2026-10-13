# What a change affects: one spelling for what a unit reads, one engine, its proof, and per-test-file selection

Status: written 2026-10-12 by merging three pieces of the plan on
Willem's ruling of that day:

- the destination note's open item 4, claims against inputs
  (`notes/20261006-workshop-destination.md`);
- the destination note's phase 16, one affected engine and sync at
  scale, which is the extensions plan's phase 15
  (`notes/20261002-extensions-plan.md`, "The affected engine");
- the per-test-file selection plan of 2026-10-10, which this note
  replaces.

Timing: phases 1 and 2 are the destination plan's phase 16 and keep its
place in that order. Phases 3 and 4 add a check and narrow nothing;
open 2 decides whether they follow phase 16 at once. Phases 5 to 11,
per-test-file selection, start only when Willem schedules them: on
2026-10-11 he ruled that reach-based test selection waits until the
workshop stops changing wholesale.

## The prompt (Willem)

> Ideally I want to only ever run affected tests, that's what I'm
> building towards. Willem, 2026-10-09

> Record this as a future plan. Willem, 2026-10-10

> merge the plans. Willem, 2026-10-12

The intent: a change runs the steps, the test files, the CI jobs and
the builds it can influence, and no others, on a machine and in CI,
and the gate's verdict stays a verdict. Every declaration of what a
unit reads gets the machine check that proves it, in the same work. A
selector that is a belief is not acceptable.

## The units

One question, what does a change influence, asked at five grains.
Each is a consumer of the one engine with its own influence rule.

| Unit | What says what it reads | Today |
| --- | --- | --- |
| a step of the gate | its declaration: `claims` or `inputs` | two spellings (phase 1 makes one) |
| a package's suite | the dependency closure | the gate's engine |
| a test file | its closure from the tree, and `[tests] reads` | nothing: the map is empty (phases 5 to 11) |
| a CI job | its `inputs` | a job skips a pull request that changed nothing it reads |
| a build, `fm sync`'s render | a package's inputs, the outputs' receipts | each its own rule (phase 2) |

## What exists that this builds on

- A step declares what it reads in one of two spellings. `claims`:
  categories with suffixes and the rule codes a tool skips there,
  judged per package against the catalogue
  (`livery.workshop._steps.catalogue`); the categories come from the
  package-level extensions' `[categories]` and a package's own.
  `inputs`: root-relative patterns with `reads`, `widens`, `ignores`,
  `on-removal`, `per-file` and a `widen` function
  (`livery.workshop._influence.Inputs`, answered by `select`), for
  workspace steps. A CI job declares `inputs` in the same shape, and a
  job runs nothing on a pull request that changed nothing it reads
  (`unread_by_the_job` in `livery.workshop._points`).
- The gate's affected engine: what changed since the nearest proved
  tree, mapped to units and their dependents, transitively
  (`livery.workshop._graph.affected_from_paths`). It returns
  `Scope(members, {}, alone)`: the per-package test-file map is empty,
  with a comment that a better narrowing does not exist yet. The
  context carries that selection (`PhaseContext.tests`,
  `livery.workshop._phases`) and the suite runner passes it to pytest
  (`run_suites(selection=...)`). The test-file phases fill the map;
  nothing downstream changes shape.
- The layering steps parse every judged source once, memoised by path,
  size and mtime (`livery.extensions.python._parse.parsed_modules`), and
  the `references` query's answer lists what each source references,
  at import time or lazily. The module graph reads that answer.
- In CI the test runner arms `COVERAGE_PROCESS_START`, the `.pth`
  meters every child, `patch = subprocess` follows the `fm` children a
  test spawns, the ctrace core keeps every context complete, and the
  contexts plugin (`livery.extensions.pytest._contexts`) records one
  context per test, its node id (ruled 2026-10-13). The footprint is a
  second reading of data the full run already collects.
- The nightly point and a dispatch pay the whole gate
  (`ci_affected_base` in `livery.workshop._quality`). A push to main
  narrows from the nearest tree CI's record proves (`ci_push_base`), so
  a merge runs no full suite, and a merged tree a pull request already
  proved runs nothing.
- The local gate record (`livery.workshop._gate_record`) chains the
  trees green local gates proved, narrowed gates proving what a delta
  can influence; CI's verified series (`livery.workshop._verified`) is
  the chain's root. The coverage record
  (`livery.extensions.coverage._record`) keeps parts in the line
  format per suite, keyed by the suite's closure identity, and a leg's
  coverage entry measures the suites its record does not hold.
- The state store (`livery.git.StateStore`, built with the workshop's
  policy) declares a series in one line, CI-only or local, windowed,
  written by compare-and-swap, and `fm store.show` reads it. The series
  are gathered in `livery.workshop._series`.
- The four points (`livery.workshop._points`): gate, merge, nightly,
  release. A job is one `fm ci.run --point=<p> --job=<j>`; what it
  does is data.

## Ground-truth contracts (do not violate)

1. **A proved tree runs nothing.** A narrowed run proves the units the
   delta can influence; every other unit keeps its earlier proof. The
   chain rule is unchanged when the unit moves from the package to the
   test file.
2. **What a unit reads is computed from the tree alone.** The
   declarations and the static closure need no record and no earlier
   run: a fresh clone answers. The footprint, the selection, the
   escapes and the flaky rows are CI-only series; the local reflex
   reads none of them.
3. **One spelling.** A step, a CI job, a build's inputs and a test
   file's declared data say what they read the same way, and the
   engine reads one shape.
4. **Selection is a pure function** of the measured tree, the changed
   paths and the declarations. The auditor recomputes it; it never
   trusts a stored row, and a difference between the leg's row and its
   own is a finding.
5. **A finer grain narrows within the coarser and never widens past
   it.** A selected test file is always one today's package selection
   runs. Where the finer answer is missing (an unparseable file, an
   extension with no file graph) the coarser one is the answer, said in
   the same line.
6. **The invalidate-all list runs everything.** The lock as a whole
   when attribution cannot narrow it, the root contracts, the coverage
   configuration, the Python version, the tool pins, the pytest
   plugins.
7. **Shadow before on.** A selector a person has not seen proved runs
   in shadow first: everything runs, and the would-be selection is
   recorded. The switch to on is a person's act; the auditor keeps
   running after it.
8. **An undeclared read and an escape each fail the nightly point**
   with a row a person can read: the unit, the file, the declaration
   that would add the edge, and for an escape the merge that brought
   it. Never a boolean, never silent. The nightly's full run is the
   proof's only full run; a merge narrows as every push does.
9. **The coverage floors judge the same union as today.** An
   unselected test file carries its recorded arcs from the base at its
   closure identity; a selected one is measured. The floors, the
   epsilon and the ratchet do not change.
10. **Fallbacks before happy paths.** The closure check and the auditor
    each get a test forcing their failure before their happy path
    exists.
11. **A package-level extension supplies its file graph**, as an answer
    to a query. Python's is the import rule. A package whose extensions
    answer none stays at package grain.
12. **No logic in YAML.** The nightly's full run, the closure check
    and the auditor are verbs the emitted shell calls.
13. **A selector a person cannot read is not trusted.** Every selected
    unit prints the edge that selected it.

## The design

### One spelling for what a unit reads

Today a tool over package files says `claims` (categories with
suffixes), and a workspace step or a CI job says `inputs` (patterns
with influence keys). Both reduce to a set of files and a rule for
what a change to them does. The choice is open 1:

- (a) **Patterns everywhere.** `reads` holds root-relative patterns,
  with the influence keys beside it; a category becomes a named set of
  patterns a package-level extension declares, written by name. Does
  not cover: a step that judges each package's files would spell every
  package's prefix, or a pattern grammar for "in each package" is
  added; the rule codes a tool skips per category move into the tool's
  configuration fragment.
- (b) **Categories everywhere.** A workspace step's or a job's patterns
  become categories of the workspace's own unit, declared in the root
  contract. Does not cover: a pattern one step needs becomes a
  category every step sees, and the influence keys (`widens`,
  `on-removal`, `widen`) have no category form yet.
- (c) **One key, two kinds of entry.** `reads` takes category names
  (each package's files of that category) and root-relative patterns
  side by side, with the influence keys beside it; the rule codes a
  tool skips per category stay in its configuration fragment, where
  they are written today. Does not cover: two kinds of entry remain,
  though under one key and one engine.

Recommendation: (c). A category is already a named, per-package set
of patterns the extensions derive, which a pattern cannot say without
a new grammar; a pattern is what a workspace step needs. One key and
one engine is the unification that matters to the engine, and
`[tests] reads` takes the same entries.

### The engine

One engine answers "what changed since the last proved state, and what
does it influence" for every consumer. It owns the record (a tree id
and each unit's input fingerprint), the changed paths since it in one
git call, the mapping of a path to its unit, and the skip when nothing
changed and every output's receipt is present. Each consumer supplies
only its influence rule:

| Consumer | A change to package A influences |
| --- | --- |
| the gate | A and everything that depends on A, transitively; a change outside every package, everything |
| a gate step | the files its `reads` names that changed, and everything under its influence keys |
| a CI job | whether its `reads` names a changed file; none, and the job runs nothing |
| sync | the packages whose rendered files the change touches: a package's own contract, that package; an extension's source or locked version, every package listing it; the root contract or the lock as a whole, everything |
| the development build | A and what depends on it within the closure being built; a package's inputs include the output fingerprints of the members it depends on |
| a test file (phase 7) | the test files whose closure meets the change |

A package-level extension declares its build inputs by category; a file
no extension claims and that is not documentation counts as an input,
so the engine errs toward rebuilding, and the tool's own incremental
build stays as the second net. The gate's build steps and `fm run`
share one record, so a green gate leaves nothing for `fm run` to
build.

### The proof: the footprint and the closure check

On the nightly's full run, what each unit actually read is recorded: for a
test file, the workspace files its tests executed lines of, plus the
files they opened; for a step that runs as code in the gate's process,
the files it opened while it ran. Opens come from an audit hook beside
the context switch, recording every open under the workspace root
against the running context, in-process and in every metered child
(the `.pth` that starts the meter starts the hook). One CI-only
`footprint` series, keyed by tree, one row per unit, windowed.

The nightly point compares each unit's footprint with what its
declaration says it reads. A file in the footprint and outside the
declaration is an undeclared read: the point fails, naming the unit,
the file, and the declaration that would add it (a `reads` entry, an
import the parser missed, an invalidate-all entry). This finds the
missing edge on the first nightly after the unit started reading the
file. A change to that file before that nightly can escape the
selection, and the auditor finds the escape on the same nightly.

Observation, not refusal: pytest runs many test files in one session,
so a module an earlier test imported is in `sys.modules` and a finder
cannot refuse it for a later test. Refusing opens works and the hook
does it under a flag for a local run that wants an early failure.
Full prevention is one process per unit with an environment of its
closure, which is Pants' cost; observation gives the same information
one nightly later.

A step that runs a native tool (ruff, a type checker, a compiler) is
outside the hook: its reads are the files the engine hands it plus the
configuration files its declaration names. Observing a native
process's opens needs an operating-system trace, which this plan does
not build.

### Per-test-file selection

**The unit** is the test file. A test's node id churns with
parametrisation, and pytest's cost is per session, not per test, so
the file is the smallest unit worth selecting. The suite is today's
grain. Measured on 2026-10-13 (decision record): selecting from what
the tests reached, the test grain reruns 19.1% of the suite per commit
against the file grain's 26.5% and the package grain's 61.2%, and it
needs a selector read from a recorded run, which contract 2 rules out.

**The closure of a test file** is the union of six sources, from the
tree alone:

1. **Imports, transitively.** The test file's imports, resolved: a
   first-party module to its file by the source roots (every package's
   `src/`, the shared tests path, a test-support module by the layout
   rule's prefix); a third-party module to its distribution, and the
   distribution to the lock's entry, so a lock change that moves it
   selects the file. The graph crosses packages, so the dependents
   closure is subsumed.
2. **The conftest chain.** Every `conftest.py` from the file's
   directory to the root, and the workspace's `pytest11` plugins. A
   change to one selects every file under it.
3. **Entry points, statically.** The extensions `workshop.toml` lists
   mount through entry points declared in each `pyproject.toml`. A
   test file that imports the mount code (`_extensions`, `_mount`)
   depends on every listed extension's sources. Coarse and correct:
   the gate's tests run the gate, and the gate mounts everything.
4. **Declared data.** `[tests] reads` in a package's contract, in the
   one spelling: categories and root-relative patterns that every test
   file of the package depends on (`content/`, `contract.toml`, seeds,
   templates). The categories the extensions assign derive most of
   it; the table adds what they do not know. `**` is allowed and
   honest for a suite that spawns `fm` against the workspace.
5. **String literals.** A dotted literal naming a first-party module,
   and a path literal matching a declared data pattern, add an edge.
6. **The invalidate-all list**, contract 6.

**Selection** is one intersection: the changed paths since the
measured tree against each test file's closure. A file whose closure
meets the diff is selected; a test file in the diff selects itself.
The result fills `Scope.tests` per package, and a package with no
selected file and no changed source runs no suite.
`fm graph.affected --tests` prints each selected file with the edge
that selected it. Both reflexes select: `fm check` on a machine from
the gate record's nearest proved tree, the CI check leg from the base
`ci_affected_base` picks today.

**The outcome auditor** covers inputs no hook sees. On the nightly's
full run at tree T1, with T0 the tree of the previous audited nightly,
each failing test is re-run alone at T1; a pass is a flaky row. A
second failure is bisected across the merges between T0 and T1, each
run alone in a worktree of its own, to the first merge M at which it
fails; a test that fails at T0 already belongs to no merge of this
window. The selection for M is recomputed (contract 4), and a failing
test outside it is a confirmed escape. Its footprint intersected with
M's diff names the missing edge. The point goes red, the escape series
gets its row naming M, and `fm issue.create` files it from the row.
The bisect runs one test per tree, so its cost is the test's time
times the logarithm of the window's merges. One nightly job audits,
since the selection is platform-independent.

**Coverage by test file** is the heaviest consequence. The coverage
record's unit becomes the test file: the arcs its contexts reached,
keyed by its closure identity. The union the gate job judges is then
by test file, unselected files carrying their recorded arcs from the
base. The contexts and the ctrace core already make the split
complete; the store's keying and union change, the floors do not. The
legs stay on ctrace until a coverage.py release fixes the sysmon
core's false positives under branch measurement (#1547). The per-test
contexts are needed on every leg that narrows, since the union drops a
selected file's old arcs only when the record knows which arcs were
that file's.
Since 12e, the coverage record and the floors are the coverage
extension's (`notes/20261006-workshop-destination.md`, 12e).

### What it does not cover

- A dynamic import from a computed name: declared, and the closure
  check finds it the first time it runs.
- A child that bypasses the meter (a scrubbed environment): only the
  auditor catches a change to what it read.
- A native tool's own reads beyond what it is handed and its declared
  configuration.
- The clock, the network, the environment: the invalidate-all list and
  the proof's age limit, as today.
- A test that should fail at T1 and passes: no selector concern.
- The hours between a merge and the next nightly: an undeclared read a
  merge adds, and a change to that file before the nightly, can escape
  the selection on main until the nightly's auditor finds it and names
  the merge.

### The measures

Four rows under `fm store.show`: selectivity per gate (selected over
the suite's size); undeclared reads per nightly, steps and test files
apart, expected zero; escapes per nightly, each naming its merge,
required zero. The first says what the work saves, the others whether
it may be trusted.

## Phases

Each lands alone, gate-green, in a day or two. The order is fixed by
dependency: one spelling before the engine reads it, the engine before
its consumers, the footprint before the checks, coverage by file
before the switch.

### Phase 1: one spelling for what a unit reads

Deliverables:

- The option open 1 rules, in `contract.toml` for steps and CI jobs,
  read into one shape the engine takes; every declaration of the
  workshop, its extensions and this repository's contract in it.
- The old spellings refuse, naming the new one.

Acceptance, the pins first:

- A pinning test, written before the change: for a fixture of changed
  paths, every step's selection and every job's skip are what they are
  today; it passes before and after.
- `test_a_step_naming_claims_refuses_naming_reads` and its sibling for
  `inputs`.
- `fm check` green; the loop's release scenario green on GitHub's
  runner.

### Phase 2: one affected engine, and sync at scale

The destination plan's phase 16, the extensions plan's phase 15.

Deliverables:

- The gate's affected engine extracted into the shared form: the
  record, the changed paths, the unit mapping, the skip, and an
  influence rule per consumer; its behaviour pinned by its existing
  tests before it moves.
- Sync as a consumer: the synced record (the locks, every contract,
  every in-repository extension's source, each package's input
  fingerprint); a sync skips whole when nothing changed and every
  output's receipt is present, and otherwise re-renders only the
  packages its rule names.
- The development build as a consumer, shared with the gate's build
  steps; the gate's steps and the CI jobs through the same engine.
- One filesystem walk per sync, no process per package.
- The budgets of the extensions plan's contract 21, set from its phase
  1 baseline.
- Follow-ups beside it, not inside it: #1201 (concurrent tool supply in
  `fm sync`, measured against the same scale fixture) and #1200 (the
  locking review).

Acceptance:

- The scale fixture's warm, nothing-changed sync and its one-package
  sync meet their budgets in the CI store's metrics.
- `test_a_warm_sync_with_nothing_changed_renders_nothing`,
  `test_a_missing_output_receipt_defeats_the_skip`,
  `test_an_extension_change_resyncs_only_the_packages_listing_it`.
- `fm run` of a C++ application rebuilds only what changed (a counting
  seam).
- The gate's affected tests pass unchanged before and after the
  extraction.

### Phase 3: the footprint series

Deliverables:

- The audit hook beside the context switch, recording opens under the
  workspace root per context: a test's in the contexts plugin, a code
  step's in the walk, started by the `.pth` in every metered child.
- The fold by unit; the CI-only `footprint` series written by the
  nightly's full run; `fm store.show footprint`.

Acceptance:

- A test that spawns `fm` and reads a template: the footprint row of
  its file lists the template and the child's module
  (`fm store.show footprint` on the loop's forge after one nightly).
- A code step that reads a rendered file: its row lists the file.
- The hook records nothing outside a metered run (a test asserting the
  gate's own driver writes no row).
- Windows: the `.pth` starts the hook on the Windows leg (open 7).
- `fm check` green; the loop green.

### Phase 4: the closure check for steps

Deliverables:

- At the nightly point: each code step's footprint against its
  declared reads; a failure names the step, the file and the `reads`
  entry to add.
- The `undeclared` measure row for steps.

Acceptance, the fallback first:

- A scratch workspace whose code step reads a file its `reads` leaves
  out: the nightly fails naming the step, the file and `reads` (a loop
  scenario).
- With the entry declared, the point is green and `fm store.show` shows
  zero undeclared reads.
- `fm check` green; the loop green.

### Phase 5: the module graph

Deliverables:

- `livery.workshop._module_graph`: the closure of a test file from
  sources 1 to 3 (imports, conftest chain, entry points), reading
  `parsed_modules`; the source roots; third-party resolution to the
  lock's entries; each edge carrying the reason it exists.
- `fm graph.closure <test-file>`: the closure, one line per file with
  its edge.
- The python extension answers the file-graph query.

Acceptance:

- `fm graph.closure packages/workshop/tests/test_workshop_dispatch.py`
  lists `packages/forge/src/livery/forge/_protocol.py` with the import
  that reaches it.
- A pinning test: for every test file in the workspace, the packages
  its closure touches are within today's dependents closure of its
  package (`fm test.pytest -- -k closure_within_package_closure`).
- A test of an unparseable file answering the package closure, with
  the reason printed, before the happy-path tests.
- `fm check` green.

### Phase 6: declared data and the invalidate-all list

Deliverables:

- `[tests] reads` in the contract schema, in the one spelling, with
  its documentation line; the defaults the extensions derive from the
  categories (open 6).
- String-literal edges (source 5).
- The invalidate-all list from the root attribution, as one function
  with one test per entry.
- `fm graph.closure` shows data edges and invalidate-all hits.

Acceptance:

- A scratch workspace with `[tests] reads = ["content/**"]`: a test
  file's closure lists a file under `content/` (`fm graph.closure`).
- A change to `uv.lock` the attribution cannot narrow selects every
  test file (`fm graph.affected --tests` on a scratch commit).
- `fm check` green.

### Phase 7: selection into the gate, shadow mode

Deliverables:

- `affected_from_paths` fills `Scope.tests` from the closures;
  `fm graph.affected --tests` prints the selected files with reasons.
- `[tests] selection`: `off` (default), `shadow`, `on`. Under `shadow`
  the check leg runs everything and writes the would-be selection to a
  CI-only `selection` series; under `off` nothing changes.
- Gate record rows name the test files a narrowed gate proved.

Acceptance:

- A one-line change in `packages/forge/src/livery/forge/_types.py` on a
  branch: `fm graph.affected --tests` lists the forge test files and
  the workshop test files whose closure imports it, and no other.
- Under `shadow`, `fm store.show selection` on the loop's forge shows
  one row per check leg naming the files.
- A pinning test that under `off` the map stays empty.
- `fm check` green; the loop green.

### Phase 8: the closure check for test files

Deliverables:

- At the nightly point: footprint against closure per test file; a
  failure names the test file, the file and the declaration to add.
- The `undeclared` measure row for test files.

Acceptance, the fallback first:

- A scratch workspace with a data edge removed from `[tests] reads`:
  the nightly fails naming the test file, the file and `[tests] reads`
  (a loop scenario).
- With the edge declared, the point is green and `fm store.show` shows
  zero undeclared reads.
- `fm check` green; the loop green.

### Phase 9: the outcome auditor

Deliverables:

- At the nightly point: each failing test re-run alone at T1, the
  bisect across the window's merges to the first failing merge, that
  merge's recomputed selection, the escape and flaky series, the issue
  filed from the row, the red point.
- The `escapes` measure row.

Acceptance, the fallback first:

- A scratch workspace whose test reads a file through a child with a
  scrubbed environment: a merge changing that file makes the next
  nightly red with one escape row naming the merge, the test file and
  the file, and one issue whose body is the row (a loop scenario,
  `fm issue.list`).
- Two merges in one window, the first the escape and the second
  innocent: the row names the first.
- A test that already failed at T0 writes no escape for this window.
- A flaky test (fails once, passes alone) writes a flaky row and no
  escape.
- `fm check` green; the loop green.

### Phase 10: coverage by test file

Deliverables:

- The coverage record's unit becomes the test file, keyed by its
  closure identity; the union by file; the floors unchanged.
- Pinning tests of the store's current properties (skip on a held
  unit, union across legs, the day's grace for a replaced row) before
  the unit moves.

Acceptance:

- The pinning tests pass before and after the change.
- Under `shadow`, every package's reported figure equals its figure
  before the phase, within epsilon, on one full run.
- `fm check` green; the loop green.

### Phase 11: the switch

Deliverables:

- `on`: the check leg runs the selection; the auditor keeps running.
- The `selectivity` measure row; `fm store.show` reads all four.
- The docs page for `[tests] selection` and `fm graph.affected --tests`.

Acceptance:

- A one-line change in a leaf module on a branch: `fm check` prints the
  selected files and runs those alone; the gate record row names them.
- The window open 4 sets has passed under `shadow` with zero escapes,
  read from `fm store.show escapes`.
- `fm check` green; the loop green.

## Temporary, replaced by

| Temporary | Replaced by | When |
| --- | --- | --- |
| `claims` and `inputs`, two spellings of what a unit reads | `reads`, one spelling | Phase 1 |
| each consumer's own change rule (the gate, sync, the build, the CI jobs) | one engine with an influence rule per consumer | Phase 2 |
| `[tests] selection = "shadow"` in this repository | `on` | Phase 11, after open 4 |
| the `selection` series' would-be rows | the gate record's own rows | Phase 11 |

## Decision record

- 2026-10-02, the extensions plan: one affected engine, the gate, sync
  and the development build as its consumers, each with its influence
  rule (its phase 15, the destination plan's phase 16).
- 2026-10-09, Willem: the goal is to only ever run affected tests.
- 2026-10-10, Willem: per-test-file selection recorded as a future
  plan, not scheduled.
- 2026-10-10, the agent, in that design: the unit is the test file,
  not the test or the suite. Observation over refusal, since a shared
  pytest session cannot refuse a cached import. One leg audits. Shadow
  before on. The coverage unit moves to the test file, named as the
  heaviest consequence.
- 2026-10-11, Willem: reach-based test selection waits until the
  workshop stops changing wholesale.
- 2026-10-12, Willem: merge the plans. Claims against inputs (the
  destination note's open item 4), the one affected engine (its phase
  16) and per-test-file selection are one plan, since all three answer
  what a change influences: the per-test design already held the proof
  that claims and inputs lack, and `[tests] reads` must use the one
  spelling. The merge changes no timing.
- 2026-10-12, the agent, in the merge: the proof covers code steps as
  well as test files, from the same hook; a native tool's reads are
  what it is handed and its declared configuration. `[tests] selection`
  replaces the earlier `[qa] test-selection`, since 12e moves `[qa]`'s
  coverage keys to `[coverage]` and the key belongs beside
  `[tests] reads`.
- 2026-10-13, Willem: the proofs run on the nightly's full run, not on
  a full run per merge: contract 8, phases 3, 4, 8 and 9, and the
  auditor's bisect to the merge that brought an escape. A merge narrows
  as every push does. What this gives up: an undeclared read or an
  escape is found within a day of its merge instead of minutes after
  it, and naming the merge takes a bisect. A full run per merge costs
  the merges times the suite, which at monorepo scale gives back on
  main what selection saves on pull requests; the nightly costs one
  full run a day whatever the merge rate.
- 2026-10-13, measured by the agent, on Willem's question whether the
  test is a better unit than the file: no. From one full ctrace run's
  per-test contexts (6,447 tests, 1,936 s of summed test time) and the
  60 commits among the last 200 on main that changed Python files
  alone (99 touched another file, 41 only notes), selecting from what
  the tests reached reruns per commit 61.2% of the suite at package
  grain, 26.5% at test-file grain and 19.1% at test grain. The test
  grain saves a further 142 s of summed test time per commit (median
  135 s, at most 481 s), about 35 s of wall at most on a 4-core runner,
  and less after the module fixtures a file pays once. It needs a
  selector read from a recorded run, which contract 2 rules out, and
  context passed into every child, whose lines land today in the empty
  context. Five workshop test files hold 56% of the gap between the two
  grains: `test_workshop_release_driver.py` 21%,
  `test_workshop_submit.py` 12%, `test_workshop_currency.py` 12%,
  `test_workshop_workflow.py` 6%, `test_workshop_shells.py` 6%.
  Splitting them by feature takes about half of it inside the file
  grain, once phase 11's selectivity row shows which files dominate.
- 2026-10-13, Willem: incremental proofs for larger repositories are
  an option (open 8), not part of the plan. They are measured once the
  closure check exists, since what they save depends on its cost and
  on decisions not yet taken.

## Open

1. **One spelling**: (a) patterns everywhere, (b) categories
   everywhere, (c) one key with both kinds of entry. Recommendation:
   (c). Willem, before phase 1.
2. **The proof's timing**: phases 3 and 4 add a check and narrow
   nothing, so they could follow phase 16 at once, or wait with the
   selection phases. Willem.
3. The e2e suite declares `**` and is always selected. Acceptable, or
   do those files move to the merge point or the nightly. Willem.
4. The window under `shadow` before `on`, counted in nights since the
   auditor runs on the nightly: proposal, 14 audited nightlies holding
   at least 30 merges, whichever is later. Willem.
5. The merge queue: if its note is written, do batches select through
   this engine from the start. Willem.
6. How much of `[tests] reads` the extensions derive from the
   categories before a package writes any. The agent, at phase 6.
7. The audit hook on the Windows leg: whether the `.pth` path meters
   the child the same way. The agent, at phase 3.
8. **Incremental proofs, for a repository larger than this one.** An
   option beside the ruled nightly full run, not a plan:
   - The closure check moves onto every narrowed leg. By induction from
     one full proof: a unit whose closure a change does not touch reads
     nothing that changed, so its footprint is unchanged and stays
     inside its closure. Only the units a change selects need
     re-proving, and those are the units the leg already runs. With the
     footprint recorded on every leg, an undeclared read fails on the
     pull request, before its merge. This is contract 1's chain rule
     applied to the footprint.
   - The auditor cannot narrow the same way: its runs would be chosen
     by the closure whose misses it looks for. It runs the day's
     selection plus a slice of the rest, 1/N of it, so every unit runs
     whole every N nights, and an escape through a hidden input is
     found within N days. N = 1 is the ruled nightly.
   - A full proof starts the chain, and follows every invalidate-all
     change and the proof's age limit.

   Does not cover: a unit that reads differently on two runs of one
   tree (the clock, the network, randomness) breaks the induction, and
   only the rotation finds it, up to N days late; a day that touches a
   module nearly every test reaches selects nearly everything, and the
   option saves nothing that day. For this repository it saves little:
   over the 30 days to 2026-10-13, a day's merges selected 96.3% of the
   suite's test time on average, and 76.7% counting Python files alone,
   since about 20 merges a day touch the workshop's pytest plugins or
   `livery.footman`.

   To measure before it is ruled, once the closure check exists: the
   check's own cost per leg (the audit hook in every metered process,
   the fold, the comparison), the footprint rows a leg writes, and a
   repository's daily selected share, which the `selection` series
   gives per day. Willem, when a repository's daily share and the
   check's cost show it pays.
