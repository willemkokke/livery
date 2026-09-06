# The plugin-scan memo flake — investigation brief

**ANSWERED AND FIXED, same day (2026-08-14).** Hypothesis 1 was right,
and the polluting test was the flakiest place imaginable:
`test_the_scan_answers_the_same_once_the_modules_are_imported` itself.
Its own module-level-looking `import footman.env_files` /
`import footman.profile` (test_split.py, inside the test body) were the
smoking imports — on a worker where that test was the process's *first*
touch of the modules, the bare import ran the module bodies outside any
proper load's capture, so `_load_entry_point` later found an empty
capture, no `_module_trees` memo, no module-level `Group` to adopt, and
raised — which the scan swallows per-entry-point. The test then blinded
its whole worker: the memo can't heal (the import is spent for the
process), so every later scan test on that worker failed too. Running
the single test alone in a fresh process (`pytest
tests/test_split.py::test_the_scan_answers_the_same… -n0`) reproduced
it deterministically, first try. `--profile` survived in the observed
run because some earlier test on gw9 had already properly loaded
`footman.profile` (memoised) while nothing had yet loaded
`footman.env_files`.

The fix is the note's first direction, with the module's own namespace
as the "side table": a spent module's declarations survive as
module-level names — a `GlobalOption` is stamped with its defining
module (`owner`), and each hook decorator now writes the (kind, item)
pairs it registered on the decorated fn (`registry._CONTRIBUTED`,
wrapper objects included). `compose._reconstruct` rebuilds a
contributions-only tree from `vars(module)`; both doors use it
(`_load_entry_point` for `plugin()`/the scan, `_tree_of_module` for
`include()`). Any sign of tasks or groups (a `Group` in the namespace,
a `@task`-stamped fn) keeps the taught refusal — a task tree cannot be
rebuilt from names alone. One-option-model invariants hold: the scan
still goes only through `_load_entry_point`, reconstruction hands out
the same singletons a real capture would, and the `_mounted`/config
derivation stamps are untouched. Three regression tests pin the spent
state deterministically (delete the memo for an already-imported
module) and all fail on the unfixed code. The rest of this note is the
original brief, kept as written.

A worker-ordering flake in the suite, observed
once locally on 2026-08-14; it will eventually red a CI run. Handoff
for a session to root-cause. **Read
`notes/20260809-one-option-model.md` first** — this is its territory
(the scan, `_load_entry_point`, plugin globals), and that note carries
the invariants the fix must keep.

## The signature

One `fm check` run (local, macOS, `-n auto`) failed exactly four tests,
all in `tests/test_split.py`, all on the same xdist worker (gw9), all
one story — `_split._own_plugin_flags()` answered
`{"--profile": "footman.profile"}` with **no `--env-file`**:

- `test_the_scan_answers_the_same_once_the_modules_are_imported` —
  KeyError `--env-file`
- `test_scanning_does_not_spend_the_import_a_real_mount_needs` —
  `--env-file` not in the scan
- `test_footmans_own_plugins_are_taught_whatever_the_brand_is` —
  KeyError `--env-file` (for every brand key)
- `test_a_third_partys_flag_stays_plainly_unknown` — the vouched set
  came back short

Immediately after: `tests/test_split.py -n0` alone passed, and the full
`fm check` passed on the very next run. Ordering-dependent, low
frequency. `--profile` surviving while `--env-file` vanishes means the
failure is per-module, not per-scan.

## The machinery (verified reads, exact places)

- `_split._own_plugin_flags()` (`_split.py:516`) — scans the vouched
  distributions' `footman.tasks` entry points and maps flag →
  entry-point name. Memoised in `_split._OWN_FLAGS` keyed by brand
  dist. Loads through `compose._load_entry_point`, never a raw
  `ep.load()` — that raw-load mistake was already made once and cost
  "four tests that only failed when the scan happened to run first"
  (the docstring's own words; this flake is at least a cousin).
- `compose._load_entry_point(name)` (`compose.py:187`) — **the only
  place allowed to call `ep.load()`**. A module imports once per
  process; its `GlobalOption` constructions fire inside whichever
  `registry.capture()` is active at that moment, and `_module_trees`
  memoises the tree the one real import produced. A module imported
  *outside* any proper load has spent its declarations: `GlobalOption`
  registers by being constructed, so nothing can be re-scanned from the
  module object afterwards.
- `brand_dist` fixture (`test_split.py:365`) — pins `_app._brand` and
  clears `_split._OWN_FLAGS`, but **cannot** clear `_module_trees` or
  un-import a module; the tests rely on the process's one import having
  fired inside a proper load.
- `Runner` restores `_brand` after every invocation (the fixture's
  docstring records that three macOS jobs once learned this the hard
  way — this terrain has bitten before).

## What is ruled out

- Not the day's playground/gallery changes: none touch plugins,
  captures, or the scan; the same suite was green on full CI at the
  same commit. The ~40 new gallery tests only reshuffled the xdist
  distribution — likely why the dice came up now.
- Not `test_env_files.py` collection: its
  `plugin("footman.env_files")` sits inside a TASKS string executed
  per-test via `Runner`, not at module import.

## Hypotheses (unverified — the work)

1. Some test that can share a worker with the `brand_dist` tests
   imports `footman.env_files` bare (directly or transitively) before
   any proper `_load_entry_point("footman.env_files")` happens in that
   process — spending the import, so the scan's later load captures
   nothing and `_module_trees` memoises an empty tree. Candidate
   grep list (referencing the module, mostly lazily):
   `test_branding.py`, `test_complete.py` (`compose.plugin(...)` at
   line ~1213 — inside what capture?), `test_global_options.py`,
   `test_pytest_plugin.py`.
2. A capture-context leak: `footman.env_files` IS loaded through the
   proper door, but inside some test's own transient capture whose
   tree `_module_trees` then memoises in a state the scan cannot use
   (e.g. contributions attached to a capture that test then discarded).
3. A brand-key interaction: `_OWN_FLAGS` is per-brand-dist but
   `_module_trees` is process-wide; some sequence of brand pins makes
   the scan consult a tree captured under different vouching.

## Reproduction avenues

- The four tests fail together, so drive candidate polluters in front
  of them in ONE process: `pytest tests/<candidate>.py
  tests/test_split.py -n0 -p no:cacheprovider` for each candidate;
  bisect within a file with `-k`.
- Or instrument: a conftest autouse fixture that records
  `"footman.env_files" in sys.modules` before each test and the
  contents of `compose._module_trees` — run `-n auto` repeatedly until
  the flake fires and read which test held the smoking import.
- `pytest --dist=loadfile` vs default scheduling changes the pairing;
  useful to widen or narrow the window.

## Fix directions to weigh (not decisions)

- Make the import unspendable: the two first-party plugin modules
  could register their `GlobalOption`s into a reconstructible side
  table at import, so `_load_entry_point` can rebuild a tree even for
  an already-imported module. Weigh against the one-option-model
  invariants before touching.
- Or make bare imports loud in tests: a conftest guard that fails the
  polluting test (the one that imports a first-party plugin module
  outside `_load_entry_point`) — turns the flake into a deterministic
  failure that names its culprit.
- Or scope `_module_trees` handling so an empty capture is never
  memoised as the answer for a module that plainly defines options
  (refuse-and-remember-nothing beats remember-empty).

## Done looks like

The mechanism named (which test, which import path), a regression test
that fails deterministically on the unfixed code, the fix keeping the
one-option-model invariants, and the four `test_split` tests unflaky
under any worker distribution — plus this note carrying the answer at
the top.
