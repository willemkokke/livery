# Per-test-file selection: the closure, the footprint, and the auditor

Status: written 2026-10-10 as a future plan, not scheduled. It consumes
the one affected engine the extensions plan builds in its phase 16
(`notes/20261002-extensions-plan.md`), so it starts after that phase
lands, and no phase here is started before Willem schedules it. The
merge queue discussed on 2026-10-09 has no note; if one is written,
its batches select through this plan's engine.

## The prompt (Willem)

> Ideally I want to only ever run affected tests, that's what I'm
> building towards. Willem, 2026-10-09

> Record this as a future plan. Willem, 2026-10-10

The intent, as ruled: a change runs the test files it can influence
and no others, on a machine and in CI, and the gate's verdict stays a
verdict. A selector that is a belief is not acceptable; its claim
gets the machine check that proves it, in the same work.

## What exists that this builds on

- The gate context carries a per-package test-file selection
  (`GateContext.tests`, `livery.workshop._checks`), the suite runner
  passes it to pytest (`run_suites(selection=...)`,
  `livery.workshop._kinds`), and the affected engine hands it an
  empty map (`affected_from_paths` in `livery.workshop._graph`
  returns `Scope(members, {}, alone)`) with a comment that a better
  narrowing does not exist yet. This plan fills the map; nothing
  downstream changes shape.
- The layering check parses every judged source once, memoised by
  path, size and mtime (`livery.workshop._ast_rules.parsed_modules`),
  and `imports_of` lists a module's imports. The module graph reads
  that parse.
- A workspace check declares its inputs, what widens it, and what
  runs it whole (`livery.workshop._influence.Inputs`), and `select`
  answers from the changed paths. A test file's closure is the same
  shape, derived rather than written.
- In CI the test runner arms `COVERAGE_PROCESS_START`, the `.pth`
  meters every child, `patch = subprocess` follows the `fm` children
  a test spawns, the ctrace core keeps every context complete, and
  the contexts plugin (`livery.workshop._pytest_contexts`) names each
  test's phases by node id. The footprint is a second reading of data
  the full run already collects.
- The local gate record (`livery.workshop._gate_record`) chains the
  trees green local gates proved, narrowed gates proving what a delta
  can influence; CI's verified series (`livery.workshop._verified`)
  is the chain's root. The coverage record
  (`livery.workshop._coverage_store`) keeps arcs per suite keyed by
  the suite's closure identity, and a leg skips a suite its record
  holds.
- The state store (`livery.workshop._state`) declares a series in one
  line, CI-only or local, windowed, written by compare-and-swap, and
  `fm store.show` reads it. The series are gathered in
  `livery.workshop._series`.
- The four points (`livery.workshop._points`): gate, merge (inherits
  the gate's jobs, plus what only a merge has), nightly, release. A
  job is one `fm ci.run --point=<p> --job=<j>`; what it does is data.
- The extensions plan's affected engine: one record, one changed-paths
  call, each consumer supplying its influence rule. The test-file
  rule is one more consumer, and a kind supplies its file graph the
  way it supplies its suites runner.

## Ground-truth contracts (do not violate)

1. **A proved tree runs nothing.** A narrowed gate proves the test
   files the delta can influence; every other file keeps its earlier
   proof. The unit moves from the package to the test file and the
   chain rule is unchanged.
2. **The static closure is computed from the tree alone.** No record,
   no earlier run: a fresh clone answers. The footprint, the
   selection, the escapes and the flaky rows are CI-only series; the
   local reflex reads none of them.
3. **Selection is a pure function** of the measured tree, the changed
   paths and the graph. The auditor recomputes it; it never trusts a
   stored row, and a difference between the leg's row and its own is
   a finding.
4. **The file graph narrows within the package closure and never
   widens past it.** A selected file is always one that today's
   package-level selection runs. Where the file graph cannot answer
   (an unparseable file, a kind with no file graph) the package
   closure is the answer, said in the same line.
5. **The invalidate-all list runs everything.** The lock as a whole
   when attribution cannot narrow it, the root contracts, the
   coverage configuration, the Python version, the tool pins, the
   pytest plugins. A change to any of them selects every test file.
6. **Shadow before on.** `[qa] test-selection` is `off`, `shadow` or
   `on`; under `shadow` the leg runs everything and records what it
   would have selected; the switch to `on` is a person's act; the
   auditor keeps running after it.
7. **An undeclared edge and an escape each fail the merge point** with
   a row a person can read: the test file, the file, the declaration
   that would add the edge. Never a boolean, never silent.
8. **The coverage floors judge the same union as today.** An
   unselected test file carries its recorded arcs from the base at
   its closure identity; a selected one is measured. The floors, the
   epsilon and the ratchet do not change.
9. **Fallbacks before happy paths.** The closure check and the auditor
   each get a test forcing their failure before their happy path
   exists.
10. **A kind supplies its file graph.** Python's is the import rule.
    A kind without one stays at package grain.
11. **No logic in YAML.** The merge point's full run, the closure
    check and the auditor are verbs the emitted shell calls.
12. **A selector a person cannot read is not trusted.** Every
    selected file prints the edge that selected it.

## The design

### The unit

The test file. A test's node id churns with parametrisation, and
pytest's cost is per session, not per test, so the file is the
smallest unit worth selecting. The suite is today's grain.

### The closure of a test file

The union of six sources, from the tree alone.

1. **Imports, transitively.** The test file's imports, resolved: a
   first-party module to its file by the source roots (every
   package's `src/`, the shared tests path, a test-support module by
   the layout rule's prefix); a third-party module to its
   distribution, and the distribution to the lock's entry, so a lock
   change that moves it selects the file. The graph crosses packages,
   so the dependents closure is subsumed.
2. **The conftest chain.** Every `conftest.py` from the file's
   directory to the root, and the workspace's `pytest11` plugins (the
   workshop's five and the pytest extension's). A change to one
   selects every file under it.
3. **Entry points, statically.** The extensions `workshop.toml` lists
   mount through entry points declared in each `pyproject.toml`. A
   test file that imports the mount code (`_extensions`, `_mount`)
   depends on every listed extension's sources. Coarse and correct:
   the gate's tests run the gate, and the gate mounts everything.
4. **Declared data.** `[tests] reads` in a package's contract: patterns,
   root-relative, that every test file of the package depends on
   (`content/`, `contract.toml`, seeds, templates). The categories a
   kind already assigns derive most of it; the table adds what the
   kind does not know. `**` is allowed and honest for a suite that
   spawns `fm` against the workspace.
5. **String literals.** A dotted literal naming a first-party module,
   and a path literal matching a declared data pattern, add an edge.
6. **The invalidate-all list**, contract 5.

### Selection

One intersection: the changed paths since the measured tree against
each test file's closure. A file whose closure meets the diff is
selected; a test file in the diff selects itself. The result fills
`Scope.tests` per package, and a package with no selected file and no
changed source runs no suite. `fm graph.affected --tests` prints each
selected file with the edge that selected it. Both reflexes select:
`fm check` on a machine from the gate record's nearest proved tree,
the CI check leg from the base `ci_affected_base` picks today.

### The footprint

On a full run in CI, each test's phases are recorded under its
context. The footprint of a test file is that data folded by file: the
workspace files whose lines any of its tests executed, plus the files
they opened. Opens come from an audit hook the contexts plugin installs
beside its context switch, recording every open under the workspace
root against the current context, in-process and in every metered
child (the `.pth` that starts the meter starts the hook). One CI-only
series, keyed by tree, one row per test file, windowed.

### The closure check

On every full run the merge point compares each test file's footprint
to its static closure. A file in the footprint and outside the closure
is an undeclared edge: the point fails, naming the test file, the file,
and the declaration that would add it (an import the parser missed, a
`[tests] reads` pattern, an invalidate-all entry). This is the
sandbox's property found by observation on the run where the test
touched the file, before the file changes.

Observation, not refusal: pytest runs many test files in one session,
so a module an earlier test imported is in `sys.modules` and a finder
cannot refuse it for a later test. Refusing opens works and the hook
does it under a flag for a local run that wants an early failure.
Full prevention is one process per test file with a venv of its
closure, which is Pants' cost; observation gives the same information
one merge later.

### The outcome auditor

For inputs no hook sees. On the full run at tree T1 from proved tree
T0, the merge point recomputes the selection for T0 to T1 and takes
failed minus selected. Each candidate is re-run alone at T1; a pass is
a flaky row. A second failure is run at T0, checked out; pass then
fail is a confirmed escape. Its footprint intersected with the diff
names the missing edge. The point goes red, the escape series gets its
row, and `fm issue.create` files it from the row. One leg audits, since
the selection is platform-independent; the nightly matrix covers what
is platform-specific.

### Coverage by test file

The heaviest consequence. The coverage record's unit becomes the test
file: the arcs its contexts reached, keyed by its closure identity.
The union the gate job judges is then by test file, unselected files
carrying their recorded arcs from the base. The contexts and the
ctrace core already make the split complete; the store's keying and
union change, the floors do not.

### What it does not cover

- A dynamic import from a computed name: declared, and the closure
  check finds it the first time it runs.
- A child that bypasses the meter (a scrubbed environment): only the
  auditor catches a change to what it read.
- The clock, the network, the environment: the invalidate-all list
  and the proof's age limit, as today.
- A test that should fail at T1 and passes: no selector concern.

### The measures

Three rows under `fm store.show`: selectivity per gate (selected over
the suite's size); undeclared edges per full run, expected zero;
escapes per merge, required zero. The first says what the work saves,
the other two whether it may be trusted.

## Phases

Each lands alone, gate-green, in a day or two. The order is fixed by
dependency: the graph before the selection, the footprint before the
checks, coverage by file before the switch.

### Phase 1: the module graph

Deliverables:

- `livery.workshop._module_graph`: the closure of a test file from
  sources 1 to 3 (imports, conftest chain, entry points), reading
  `parsed_modules`; the source roots; third-party resolution to the
  lock's entries; each edge carrying the reason it exists.
- `fm graph.closure <test-file>`: the closure, one line per file with
  its edge.
- The python kind supplies the graph; `kind_chain` resolves it the way
  it resolves `suites`.

Acceptance:

- `fm graph.closure packages/workshop/tests/test_workshop_dispatch.py` lists
  `packages/forge/src/livery/forge/_protocol.py` with the import that
  reaches it.
- A pinning test: for every test file in the workspace, the packages
  its closure touches are within today's dependents closure of its
  package (`fm test.pytest -- -k closure_within_package_closure`).
- A test of an unparseable file answering the package closure, with
  the reason printed, before the happy-path tests.
- `fm check` green.

### Phase 2: declared data and the invalidate-all list

Deliverables:

- `[tests] reads` in the contract schema (`contract.toml`), with its
  documentation line; the kind-derived defaults from the categories.
- String-literal edges (source 5).
- The invalidate-all list from the root attribution, as one function
  with one test per entry.
- `fm graph.closure` shows data edges and invalidate-all hits.

Acceptance:

- A scratch workspace with `[tests] reads = ["content/**"]`: a test
  file's closure lists `content/root/.coveragerc.jinja`
  (`fm graph.closure`).
- A change to `uv.lock` the attribution cannot narrow selects every
  test file (`fm graph.affected --tests` on a scratch commit).
- `fm check` green.

### Phase 3: selection into the gate, shadow mode

Deliverables:

- `affected_from_paths` fills `Scope.tests` from the closures;
  `fm graph.affected --tests` prints the selected files with reasons.
- `[qa] test-selection`: `off` (default), `shadow`, `on`; read where
  `affected_legs` is read. Under `shadow` the check leg runs
  everything and writes the would-be selection to a CI-only
  `selection` series; under `off` nothing changes.
- Gate record rows name the test files a narrowed gate proved.

Acceptance:

- A one-line change in `packages/forge/src/livery/forge/_types.py`
  on a branch: `fm graph.affected --tests` lists the forge test files
  and the workshop test files whose closure imports it, and no other.
- Under `shadow`, `fm store.show selection` on the loop's Gitea shows
  one row per check leg naming the files.
- A pinning test that under `off` the map stays empty.
- `fm check` green; `fm ci.e2e` green.

### Phase 4: the footprint series

Deliverables:

- The audit hook in `livery.workshop._pytest_contexts`, recording opens
  under the workspace root per context, started by the `.pth` in every
  metered child.
- The fold by test file; the CI-only `footprint` series written by the
  merge point's full run; `fm store.show footprint`.

Acceptance:

- A test that spawns `fm` and reads a template: the footprint row of
  its file lists the template and the child's module
  (`fm store.show footprint` on the loop's Gitea after one merge).
- The hook records nothing outside a metered run (a test asserting the
  gate's own driver writes no row).
- Windows: the `.pth` starts the hook on the Windows leg (the loop's
  Windows scenario, or the branch leg procedure).
- `fm check` green; `fm ci.e2e` green.

### Phase 5: the closure check

Deliverables:

- At the merge point: footprint against closure per test file; a
  failure names the test file, the file and the declaration to add.
- The `undeclared` measure row.

Acceptance, the fallback first:

- A scratch workspace with a data edge removed from `[tests] reads`:
  the merge point fails naming the test file, the file and
  `[tests] reads` (`fm ci.e2e` scenario).
- With the edge declared, the point is green and `fm store.show`
  shows zero undeclared edges.
- `fm check` green; `fm ci.e2e` green.

### Phase 6: the outcome auditor

Deliverables:

- At the merge point: the recomputed selection, failed minus
  selected, the re-run at T1, the run at T0, the escape and flaky
  series, the issue filed from the row, the red point.
- The `escapes` measure row.

Acceptance, the fallback first:

- A scratch workspace whose test reads a file through a child with a
  scrubbed environment: a merge changing that file makes the merge
  point red with one escape row naming the test file and the file,
  and one issue whose body is the row (`fm ci.e2e` scenario,
  `fm issue.list`).
- A flaky test (fails once, passes alone) writes a flaky row and no
  escape.
- `fm check` green; `fm ci.e2e` green.

### Phase 7: coverage by test file

Deliverables:

- The coverage record's unit becomes the test file, keyed by its
  closure identity; the union by file; the floors unchanged.
- Pinning tests of the store's current properties (skip on a held
  unit, union across legs, the day's grace for a replaced row) before
  the unit moves.

Acceptance:

- The pinning tests pass before and after the change
  (`fm test.pytest -- -k coverage_store`).
- Under `shadow`, every package's reported figure equals its figure
  before the phase, within epsilon, on one full run (`fm store.show
  coverage/marks`).
- `fm check` green; `fm ci.e2e` green.

### Phase 8: the switch

Deliverables:

- `on`: the check leg runs the selection; the auditor keeps running.
- The `selectivity` measure row; `fm store.show` reads all three.
- The docs page for `[qa] test-selection` and `fm graph.affected
  --tests`.

Acceptance:

- A one-line change in a leaf module on a branch: `fm check` prints
  the selected files and runs those alone; the gate record row names
  them.
- The window Willem sets (open 3) has passed under `shadow` with zero
  escapes, read from `fm store.show escapes`.
- `fm check` green; `fm ci.e2e` green.

## Temporary, replaced by

| Temporary | Replaced by | When |
|---|---|---|
| `[qa] test-selection = "shadow"` in this repository | `on` | Phase 8, after open 3 |
| The `selection` series' would-be rows | The gate record's own rows | Phase 8 |

## Decision record

- 2026-10-09, Willem: the goal is to only ever run affected tests.
- 2026-10-10, Willem: recorded as a future plan, not scheduled.
- 2026-10-10, the agent, in the design: the unit is the test file, not
  the test or the suite. Observation over refusal, since a shared
  pytest session cannot refuse a cached import. One leg audits. Shadow
  before on. The coverage unit moves to the test file, named as the
  heaviest consequence.

## Open

1. Sequence: after the extensions plan's phase 16, or folded into it
   as the first consumer of the one engine. Willem.
2. The e2e suite declares `**` and is always selected. Acceptable, or
   do those files move to the merge point. Willem.
3. The window under `shadow` before `on`: proposal, 30 audited merges
   or 14 days, whichever is later. Willem.
4. The merge queue: if its note is written, do batches select through
   this engine from the start. Willem.
5. How much of `[tests] reads` the kind derives from the categories
   before a package writes any. The agent, at phase 2.
6. The audit hook on the Windows leg: whether the `.pth` path meters
   the child the same way. The agent, at phase 4.
