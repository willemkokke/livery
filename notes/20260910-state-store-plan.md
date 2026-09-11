# The state store: one feature for every row the workshop keeps

Ruled by Willem on 2026-09-10: "It feels like we should have only one
way of doing this, with one cleanup method, one way of getting the
data." This note is the plan for that: what the workshop keeps today,
the one shape it becomes, the slices that get there, and the rulings
still open. Sibling of `20260907-ci-quality-plan.md`, whose slices
built the pieces this note consolidates.

## What exists today, measured 2026-09-10

Five kinds of row, in six places, with three cleanups.

On the remote, under `refs/workshop/`, 38 refs on livery's origin:

| kind | refs | entry | window | writer, reader |
| --- | --- | --- | --- | --- |
| `coverage/<leg>/<package>` | 28, one per check leg and unit | `<time>--<closure>`: the unit's reached lines per file | 6 per ref | `coverage.leg` writes, `coverage.union` reads the skipped units |
| `metrics` | 1 | `<run id>.json`: every job's times, the run's wall, the coverage row | 300 | `ci.metrics.collect` writes, `ci.timings` and `ci.verified.stamp` read |
| `run/<id>/<leg>` | 8, orphans of two runs | `row.json`: a leg's trace half | dropped by the collect | `ci.metrics.leg` writes, `ci.metrics.collect` joins and drops |
| `verified` | 1 | `<tree id>`: the run that proved it, its scope and base | 200 | `ci.verified.stamp` writes, every check leg and the release train read |
| `coverage/marks` | none on livery | `<time>`: the auto-ratchet mark and its writer | 400 | the gate writes under auto-ratchet, `coverage.accept` writes by hand |

The coverage store is nine tenths of the bytes: every entry lists every
reached line of every file, which the union needs.

On the machine, in the runner's data directory:

| kind | file | bound | writer, reader |
| --- | --- | --- | --- |
| the gate record | `livery-workshop/gate-record.json`, rows by tree id | 200 rows and 7 days, on write | a green `fm check` writes, `fm submit` reads |
| the diagnostics | `diagnostics/<time>-<branch>-<verdict>.json` | 20 newest, on write | every unmerged follow writes, a person reads |

The transport is one layer and not duplicated: `_state.py` owns `read`,
`put`, `list_refs`, `drop`, `sweep`, and `Series`, and every remote
kind goes through it. The duplication is one layer up. Each module
parses its own rows: nine `json.loads`, four schema constants with four
checks, seven "does not parse; skipped" wordings, ten fall-open
wordings, and the window and the newest-first order derived from file
names in each. The two local kinds have their own readers and writers
beside all that.

Cleanup happens three ways: `fm ci.janitor` drops per-run refs older
than six hours and trims the remote windows; `fm maintenance.sweep`
and the daily collector child bound the local files through the
workshop's sweeper (`fm issue.sweep` runs one of its rules); the
diagnostics and the gate record also bound themselves on write.

Reading the data by hand is not possible through `fm`: today's
measurements were three scratch scripts against `_state.read`.

## The one shape

One module owns the machinery, `_store.py` (name to rule on); each
kind declares itself where its rows mean something.

**A series** is a named ref holding JSON rows of one schema. A
declaration names the series, its window, its scope (remote or local),
who may write it (CI only, or anyone), and the row's dataclass. A
**keyed series** has one ref per key, `coverage/<leg>/<package>` and
`run/<id>/<leg>`, declared once with its key names; the store lists
its refs by key.

**One way in.** `series.put(root, name, row, message)` stamps the
schema and the time, applies the window, refuses a write its scope
forbids (a local run on a CI-only series), and pushes the ref for a
remote series or writes it in the checkout's own git directory for a
local one. The retries, the read-back, and the stale-push detection
stay as they are in `_state.put`.

**One way out.** `series.rows(root)` reads the ref once and returns the
rows of the series' schema, newest first, with the names it skipped and
why (one wording), and the reason when the ref could not be read, so a
caller falls open the same way everywhere. `series.row(root, name)`
reads one. For a keyed series, `series.keys(root)` lists the refs and
`series.rows(root, key=...)` reads one key. For a person,
`fm store.show <series> [--key ...] [--json]` lists rows through the
same reader, and `fm store.ls` lists every declared series with its
counts and sizes, so the scratch scripts retire.

**Who writes.** A remote series is written by CI only: the legs, the
gate job, and the merge point's jobs. The one exception is
`fm coverage.accept`, a person's act with a reason on the row, which
writes the marks from a machine; the ratchet's own writer guards the
rule that only a CI run raises a mark. A local run reads every remote
series and writes none, by the series' declaration, so a machine can
never move a mark, a record, or a timing row by accident.

**One cleanup.** The one janitor (#404) walks every declared series,
remote and local: it enforces the windows, drops the orphans of a keyed
series (a per-run half older than six hours; a coverage ref whose leg
the current matrix no longer produces, such as the windows legs and the
3.11 legs after #385; a package that no longer exists), and ages the
rows of a series that declares an age. It sweeps the scope it runs
in: on a machine the local series and the runner's own files, on the
runner the remote series too. The remote sweep is an entry of the
merge point's gate job after the collect, so every merge tidies the
store, and the daily child runs the local part alone, unattended. A
keyed ref the current matrix still produces is never dropped, and
every line says what went and why the rest stayed.

**Local scope.** A local series lives under `refs/workshop-local/` in
the checkout's git directory: never fetched, never pushed, shared by
the checkout's worktrees, empty in a fresh clone. The remote namespace
never lands locally (a read fetches into `FETCH_HEAD` and creates no
ref; the default refspec brings only heads), so the two scopes share
nothing. A local series' `put` never pushes, by construction and by a
test. The gate record and the diagnostics move onto it, and the data
directory keeps no rows of the workshop's.

**Main's coverage.** The coverage store is keyed by main: one record
per check leg, `coverage/main/<leg>`, one row per unit with the unit's
closure identity and its reached lines, written by the merge point's
gate job from the union it judged and replaced in place at every
merge; a pull request's run never writes it. A leg's own measurements
ride its per-run ref beside its timing half, and the collect drops
them with the ref. A leg skips a suite when main's record on that leg
holds the unit at the same closure identity. The gate job's union is
the per-run refs plus the carry from main's record; the deploy renders
the site's coverage pages from main's record alone. No artifacts: the
store is the one transport.

**What does not change.** The rows' meanings, the verdicts, the union,
the skips, the CI-only rule, the windows, and the printed lines the
loop's proofs read (`reused from run`, `not recorded:`,
`unjudged this run`, `skipping the gate`, `on top of tree`): a proof
that changes wording changes in the same slice.

## The slices

Each lands alone, gate-green, with this note updated in the same
change.

1. **The row layer.** `Series.rows`, `row`, and `put` with schema,
   time, window, and scope; `metrics`, `verified`, and `coverage/marks`
   moved onto it, their parsers gone, one wording for skipped rows and
   unreadable refs. Refusals first: an unreadable ref returns its
   reason, a row that does not parse is skipped and named, a row of
   another schema is skipped and named, the window holds on write, a
   CI-only write from a local run is refused. Landed 2026-09-10,
   #407, in `_state.py` beside the transport. Two things it changed
   beyond the move: the window keeps the newest rows by the stamp
   the store writes, where the transport trimmed by file name, which
   would have evicted the verified record's trees at random once it
   passed 200 rows; and the marks series declares that anyone may
   write, with the rule that only a CI run raises a mark kept by
   the ratchet's caller, where before both guarded.
2. **Keyed series.** The coverage store and the per-run halves declared
   as keyed series; `coverage.leg`, `coverage.union`, and
   `ci.metrics.collect` read and write through the store. The coverage
   entry's shape stays. Landed 2026-09-10, #409: `Keyed` in
   `_state.py` declares a family's key parts once, makes the series of
   one key with every part made ref-safe, and lists the keys the
   remote holds; `_coverage_store.COVERAGE` and `_metrics.RUNS` are
   the two families. The per-run refs now spell the leg the way the
   coverage refs do, dots to dashes. A newest entry for a closure that
   is not a row stays a named reason rather than a silent miss, so the
   leg runs the suite fresh and its stamp replaces the entry.
3. **Local scope.** The gate record and the diagnostics as local
   series; the data-directory files and their bounds-on-write gone;
   the pin that a local series never pushes and that a fresh clone
   starts empty. Landed 2026-09-10, #411: the transport dispatches on
   the namespace, a local ref read with `rev-parse` and `ls-tree`,
   written with `update-ref` and the old value as the compare-and-swap,
   listed with `for-each-ref`; `_gate_record.SERIES` and
   `_diagnostics.SERIES` are the two local series, and the sweeper
   removes their old homes in the data directory as leftovers. The
   record's age bound stays applied on read until slice 4's janitor
   ages rows.
4. **One janitor** (#404 folded in). Every declared series enumerated
   for windows, orphans, and age; `ci.janitor`, `maintenance.sweep`,
   and `issue.sweep` folded into `fm janitor`, which sweeps the scope
   it runs in: the local scope on a machine, the remote too inside CI,
   where the merge point's gate job runs it after the collect. The
   daily child runs the local part; `fm issue.start` keeps running the
   worktree rule first. A local run never writes the remote store.
   Landed 2026-09-10, #413: `fm janitor` is footman's `maintenance`
   family renamed, its default task the sweep, unattended when stdin
   is no terminal or `--no-input` is set; the workshop's sweeper runs
   the store's janitor (`_state.sweep`) over `_series.DECLARED`; a
   series declares an `age`, a family a `stale_after` and its
   `current` keys; the coverage family's current keys are every check
   leg of the contract's runners and gate Pythons with every stored
   unit. `fm ci.janitor` and `fm issue.sweep` are gone, and the merge
   point's gate job runs `janitor` after the stamp.
5. **Reading by hand.** `fm store.ls` and `fm store.show`, read-only,
   through the same reader; the coverage pages the deploy publishes
   rendered from the store's union through that reader (#386 folded
   in), so a run that skipped every leg still publishes current pages;
   the docs' scattered descriptions of the record, the store, the
   marks, and the timings become one section, "The state store", in
   `packages/workshop/docs/index.md`. Landed 2026-09-10, #415:
   `fm store.ls` and `fm store.show` in `_store_tasks.py`, over
   `_series.DECLARED`; the deploy's pages pull every stored unit for
   every check leg inside CI when the legs left no data
   (`_python.stored_union`, a miss named and rendered around); the
   docs section. The verbs are named `store`, the module stays
   `_state.py` until the name is ruled. One fix rode along: a listed
   key goes back to its ref verbatim (`Keyed.at`), since the halves
   written before slice 2 spell the leg with a dot and the first
   remote sweep kept them as unreadable.
6. **The speed ratchet.** A `speed/marks` series beside the coverage
   marks, written by the gate job: per check leg and package, the
   summed test time the leg's row already records, which unlike the
   wall does not depend on the worker count or the tail. The mark is
   the median of the leg's last five green runs for the package; it
   ratchets down when a run beats it by more than five per cent, so a
   suite cannot regress slowly, and `fm speed.accept <package>
   <seconds> --reason=<why>` raises it, since a new heavy test is a
   cost someone chose. One run over the mark by more than fifteen per
   cent and twenty seconds warns in the gate job's output, naming the
   ten slowest tests and the ones that grew most against their own
   medians; two consecutive runs over it are red. The ubuntu leg is
   the reference for red; the macOS leg warns only until its variance
   is measured (the same code took 274 s to 431 s on the macOS 3.14
   leg in one day). `fm test` prints the package's summed time beside
   its mark, and `fm ci.timings` gains the marks as rows and movers.
   Landed 2026-09-11, #437, with the margins as constants: `_speed.py`
   holds the marks (`speed/marks`, window 400), `speed.judge` runs in
   the gate job after the collect and before the verdict, the timing
   rows name each leg's twenty slowest tests for the warning, the
   speed plugin sums each package's phases for `fm test`, and
   `fm speed.accept` takes `--leg` (the ubuntu leg when absent).

7. **The coverage store keyed by main** (#417). One record per check
   leg, `coverage/main/<leg>`, one row per unit with its closure
   identity and its lines, written by the merge point's gate job from
   the union it judged: fresh rows for the suites the legs ran, the
   previous record's rows carried for the rest, units that no longer
   exist removed, replaced in place at every merge. The legs' own
   measurements ride their per-run refs beside the timing halves and
   go with them at the collect. A leg skips a suite when main's record
   on that leg holds the unit at the same closure identity, one read
   per leg. The gate job's union is the per-run refs plus the carry;
   the deploy renders the pages from main's record alone. The artifact
   upload and download steps leave both shells, with the Gitea artifact
   actions and the `coverage-data/` handling; the closure-keyed refs,
   their window, and the family's current-keys rule go. Refusals
   first: a leg that ran a suite and could not put its lines on its
   per-run ref is red, as a missing upload is today; a unit that
   neither the run nor main's record supplies is red by name, never a
   smaller union; a pull request's run that would write main's record
   is refused by the writer rule. What it costs: reuse only through
   main, so a follow-up branch sharing a closure with another unmerged
   branch reruns that suite, and a branch behind main reruns the
   suites main changed since; the submit integrates first, so both are
   rare. Sizes: about 370 KB per leg for the record, the same per leg
   on a per-run ref while a run is in flight. Landed 2026-09-11, #417:
   `_coverage_store.RECORD` is the family `coverage/<base>/<leg>`, its
   one base `main`, one row per unit named by the unit's path; a leg
   puts one file, `coverage.json`, on its per-run ref with its scope
   and every unit it measured (`put_run`), the gate job reads the
   run's legs in one listing (`run_legs`) and main's record once per
   leg (`recorded`), and `put_record` refuses any run but a push. One
   consequence the issue did not spell out: a tree the verified
   record proves no longer skips everything. The leg reads the record
   and runs, measured for their lines alone, the suites whose closure
   moved since main's record, under a fifth scope, `measured`, which
   the stamp treats as `verified`; on main after a merge that is the
   changed package and the workspace tests, the checks themselves
   never rerun. The closure-keyed refs are two-part keys under the
   same prefix, so the janitor drops them by the family's current
   keys on the first merge's sweep, no migration code. `Series.put`
   gained `remove`, so the record's stale rows go in the write that
   replaces it. The loop's proofs changed with the wording: main's
   run after the setup squash says `the union of 3 leg(s) and 0
   reused suite(s)` and `main/check-ubuntu-latest-3.14: 3 fresh`,
   and main's run after the member-only squash `2 leg(s) and 1
   reused` with `2 fresh, 1 carried`.

8. **The branch record** (#425). Each branch with a pull request run
   keeps `coverage/<branch>/<leg>`, the same family with the branch as
   the base, replaced in place by its runs with the union each judged:
   fresh rows for what the legs measured, the rest carried from the
   branch's own previous record before main's. A leg skips a suite
   when either record holds it at the suite's current closure. The
   verified row names the branch, so main's run at the merge, finding
   its tree proved, measures nothing and copies the branch's record
   into main's; the measured fallback of slice 7 stays for a unit no
   record holds. The janitor drops a branch's record once the branch
   is gone from origin, by the family's current keys. Landed
   2026-09-11, #425: `RunContext.head_ref`, `Verified.branch`,
   `_coverage_store.branches` and `put_record(base=)` with the two
   writer rules, `_quality.record_bases`, and the union's
   `_record_bases`; the loop's proofs read `main takes
   chore/setup-check's record for the tree it proved` and `0 fresh, 3
   carried` on main's runs, and the pull requests' records are written
   with what each judged.

9. **Branch coverage** (#423). `branch = true` in the rendered
   configuration, rows of arcs, the combined figure judged everywhere,
   forge's and workshop's floors re-based in the same change. Ruled
   2026-09-11, after slice 8. Landed 2026-09-11, #423: `branch =
   true` in the rendered configuration, the record's schema 2 with
   arcs as pairs of line numbers (a row of lines is skipped and the
   unit measured afresh, so the switch migrates itself at the first
   run), `suite_arcs_by_context` and the pull writing arcs, the
   union's percentage the statements and branches together. Floors:
   forge 90 to 87, workshop 85 to 84, from one local run (87.1 and
   84.0 combined); the loop's members hold no branch and stay at 100.

10. **The workspace tests as a unit of the affected engine** (#424). A
    change under `tests/` runs the workspace tests with format, lint,
    and the type checkers over that directory, not the whole gate.
    Ruled 2026-09-11, after slice 9. Landed 2026-09-11, #424: the
    engine answers the workspace suite as a package of its own for a
    change under `tests/`, the scoped gate takes its directory for
    the style and type verbs and runs it as the one suite, the kind
    checks and the floors leave it out, and the loop proves it on a
    tests-only pull request (`affected: tests`, `the union of 1
    leg(s) and 2 reused suite(s)`).

Slices 1 to 3 are a day together; 4 and 5 another; 6 a day of its own,
once the timing rows carry a fortnight of legs at the gate's Python; 7
a day, before 6 or after it.

## Acceptance

- Every row the workshop keeps is a declared series, remote or local,
  and `fm store.ls` lists them all.
- No module outside the store parses a row, stamps a schema, or applies
  a window.
- One verb cleans up, with `--dry-run`, and the daily child runs its
  local part.
- The loop passes with the proofs' lines unchanged, and a full pull
  request run on GitHub judges the same union and skips the same trees
  as before.
- A local run writes no remote series; the janitor on a machine
  touches no remote ref; the merge point's gate job sweeps the remote
  after every collect.
- A package's suite over its speed mark by the margin for two runs is
  red, one noisy run is a warning naming the culprits, and
  `fm ci.timings` shows the marks beside the timings.
- The shells upload and download no artifacts, and the deploy's pages
  after any merge are the union that merge's gate judged.

## Open rulings

- The module's and the verbs' names: `store` here; `state` and
  `series` are the alternatives. Slice 1 built the row layer in
  `_state.py`, where the transport already was, and slice 5 named
  the verbs `store.ls` and `store.show`; the module's rename is one
  commit when ruled.
- Resolved 2026-09-11: coverage keeps one record per leg with the
  units as rows, main's, replaced in place at every merge; the
  closure-keyed refs go with slice 7.
- The local namespace: slice 3 took `refs/workshop-local/`, a
  namespace of its own that a mirror push of `refs/workshop/*` can
  never carry, over `refs/workshop/local/`. One constant to flip if
  ruled otherwise.

## Decision record

- 2026-09-10, Willem: "It feels like we should have only one way of
  doing this, with one cleanup method, one way of getting the data."
  The inventory above was measured the same day: 38 refs on origin,
  two files on the machine, nine parsers, three cleanups.
- 2026-09-10, Willem: local runs do not write the remote store, by
  default; the janitor on a machine looks only at the local scope, and
  the remote is swept in CI. #404 (one janitor) and #386 (the coverage
  pages after a skipped run) fold into this plan. The test speed
  ratchet is slice 6 of this plan rather than an issue of its own:
  "definitely slice, why make more admin".
- 2026-09-10: slices 1 to 5 landed the same day (#407, #409, #411,
  #413, #415). The first remote sweep, on main's run after #414 at
  21:37 UTC: 83 metrics rows, 25 verified rows, 14 coverage refs,
  no marks, and 8 orphaned halves kept as unreadable because their
  legs are spelled with a dot; #415 drops them on the next merge.
- 2026-09-11, Willem: the coverage store is keyed by main, one record
  per leg replaced in place at every merge ("would storing the main
  coverage as a separate series with window 1 so it always gets
  overwritten make sense? To reduce special cases"); the per-run
  lines ride the per-run ref, and the artifacts go. Filed as #417,
  slice 7. Measured the same day: one stored entry is 8 KB to 108 KB
  per unit, about 370 KB per leg per full run. The gate measures
  lines, not branches; a switch to branches is a ruling of its own.
- 2026-09-11: slice 7 landed (#417). Taken by default, one line to
  reverse: on a proved tree the leg measures the suites main's
  record cannot supply, the tests alone, and leaves the scope
  `measured`. The alternative, a full skip on main, would leave the
  record without fresh rows after every merge and the next run red
  by name; the cost is the changed package's suite and the workspace
  tests rerun once on main after each merge.
- 2026-09-11, Willem: the gate's own driver is not measured. Coverage
  is what the tests execute, every process a test starts included;
  the test runner arms the meter in pytest's environment inside CI
  and the shells set nothing. The driver's path differs by scope, so
  measuring it made workshop's number move with the scope (86.12 % on
  a full run against 85.90 % on a measured run of the same tree,
  #421). Ruled after "should we not measure the runner itself
  everywhere?"; landed the same day.
- 2026-09-11, Willem: each branch keeps its own record and main's run
  copies it at the merge ("I thought each branch would store its
  latest and in main merge they'd be copied to the main ref"); the
  measured rerun on main that slice 7 introduced was the wrong reading
  of the issue. Filed as #425, slice 8. Branch coverage (#423) and the
  workspace tests as a unit of the engine (#424) follow, in that
  order.
- 2026-09-11, taken by default in #424: the check counts packages
  only when it decides whether the gate is narrowed. The workspace
  tests ride the subset as a unit beside the packages, and a subset
  of every package plus that unit is the whole gate, which runs those
  tests anyway; comparing the subset's length with the packages'
  would have run the full gate for a widened subset, and inside a
  test that is pytest spawning pytest.
- 2026-09-11: slice 6 landed (#437), on Willem's ruling to build it now
  with the margins as constants (fifteen per cent and twenty seconds
  over the mark warns, five per cent under it ratchets, five green
  runs behind a mark). Instead of asking Willem, I decided two
  things: the ratchet moves on the median of the last five green runs
  beating the mark, not on one run beating it, since one fast run on
  a quiet runner would set a mark the next ordinary run is over; and
  the "tests that grew most" are read from the rows' own top twenty,
  so a test outside every recent row's top twenty is never named as
  grown. A red run between two runs over the mark does not reset the
  pair: the streak counts green runs. The root `tests/` suite is no
  package in the timing rows and is not judged.
