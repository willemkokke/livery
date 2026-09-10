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

**Local scope.** A local series lives under `refs/workshop/local/` in
the checkout's git directory: never fetched, never pushed, shared by
the checkout's worktrees, empty in a fresh clone. The remote namespace
never lands locally (a read fetches into `FETCH_HEAD` and creates no
ref; the default refspec brings only heads), so the two scopes share
nothing. A local series' `put` never pushes, by construction and by a
test. The gate record and the diagnostics move onto it, and the data
directory keeps no rows of the workshop's.

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
   starts empty.
4. **One janitor** (#404 folded in). Every declared series enumerated
   for windows, orphans, and age; `ci.janitor`, `maintenance.sweep`,
   and `issue.sweep` folded into `fm janitor`, which sweeps the scope
   it runs in: the local scope on a machine, the remote too inside CI,
   where the merge point's gate job runs it after the collect. The
   daily child runs the local part; `fm issue.start` keeps running the
   worktree rule first. A local run never writes the remote store.
5. **Reading by hand.** `fm store.ls` and `fm store.show`, read-only,
   through the same reader; the coverage pages the deploy publishes
   rendered from the store's union through that reader (#386 folded
   in), so a run that skipped every leg still publishes current pages;
   the docs' scattered descriptions of the record, the store, the
   marks, and the timings become one section, "The state store", in
   `packages/workshop/docs/index.md`.
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

Slices 1 to 3 are a day together; 4 and 5 another; 6 a day of its own,
once the timing rows carry a fortnight of legs at the gate's Python.

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

## Open rulings

- The module's and the verbs' names: `store` here; `state` and
  `series` are the alternatives. Slice 1 built the row layer in
  `_state.py`, where the transport already was; the rename is one
  commit, before slice 5 names the verbs.
- Whether coverage entries keep one ref per leg and package, or one ref
  per leg with the packages as files. The per-key shape keeps writes
  small and concurrent legs apart; the per-leg shape halves the ref
  count.
- Whether the local namespace is `refs/workshop/local/` or a namespace
  of its own, `refs/workshop-local/`, which a mirror push of
  `refs/workshop/*` could never carry.

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
