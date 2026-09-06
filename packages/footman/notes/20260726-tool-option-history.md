# Tool option history → scheduled refresh → honest auto-release

**Built. Reconciled against the repo 2026-08-02.** Unifies two plans that
were never separate: a JSON history of every tool's option surface, and the
scheduled job that keeps it current and decides when a release is warranted.

Shipped: the format and its chain, five listable tiers including CPython, the
backward walk (`fm tools.prime`), the forward walk (`fm tools.refresh`) and
its matrix split (`gather`/`owed`/`assemble`), the release note the events
write, `tools.prepare-release`, and 30 checked-in chains under
`tool-history/`. The local-first rollout §7 asked for is **done**: the store
was primed on three machines, and 26 of the 30 chains carry Linux, macOS and
Windows in their base observation. The workflow has been driven by hand and
lands green. **The first scheduled run is Monday 2026-08-03** — the cron has
not fired only because no Monday has come round since the workflow landed on
Wednesday 2026-07-29. What remains is configuration rather than design — see §7. Decision 5 was **reversed** in the building; its reasoning is
kept in place rather than deleted, because the way it was wrong is the useful
part.

The thesis: **a stub stops being a snapshot and becomes a rendering.** Today a
stub *is* the record — read from one binary, on one machine, at one moment, and
carrying its version in a header comment. Replace that with a per-tool event
log, and the stub becomes a view of it, the refresh becomes an append, and "do
we need to release?" becomes a question about events rather than version
numbers.

## 1. The file: a base at HEAD, deltas pointing backwards

One JSON file per curated tool, under **`tool-history/`** at the repo root
(`_history/` was the sketch's name; §8 records why it landed outside `src/`).
The **newest** observed release is stored whole; every older one is a delta
describing how to step back to it. `since`/`until` are never stored — they
are derived by walking the chain.

Pointing the deltas *backwards* is what makes the format fit the work:

- **Priming backwards is pure append.** Each older release adds one delta
  against the current oldest; nothing already written is touched. That is
  the dominant workflow, and it is the cheapest possible write.
- **The current version costs no replay** — it *is* the base. "The current
  version matters most" becomes a property of the format rather than an
  optimisation on top of it.
- **Midfill rewrites exactly one entry**: the inserted release's successor,
  recomputed against it. Local, never cascading.
- **A refresh** promotes the new release to base and demotes the old base to
  a delta — two entries touched, and only when something changed.
- **The release gate is "is this delta non-empty"** — no hashing, no
  comparison of surfaces.

Real, from prek 0.4.11 (base) stepping back to 0.4.10:

```jsonc
{
  "tool": "prek",
  "observed_from": "0.4.0",         // a fact: the oldest release actually read
  "base": {
    "version": "0.4.11", "date": "2026-07-24", "extractor": 1,
    "surface": {
      "run/--glob":      { "help": "Run hooks on tracked files matching the specified glob pattern",
                           "type": "str", "neg": "" },
      "run/--all-files": { "help": "Run hooks on all tracked files in the repository",
                           "type": "bool", "neg": "" }
      // …96 more
    }
  },
  "deltas": {                        // each: how to get from the newer release to this one
    "0.4.10": { "date": "2026-07-16", "extractor": 1,
                "drop": ["run/--glob", "/--glob"],
                "revert": { "run/--all-files": { "help": "Run on all files in the repo",
                                                 "type": "bool", "neg": "" } } },
    "0.4.9":  { "date": "2026-07-11", "extractor": 1 },   // empty: nothing changed
    "0.4.8":  { "date": "2026-07-04", "extractor": 1 }
  }
}
```

An empty delta is the common case and says something precise: that release
was **observed** and changed nothing. It is not the same as a release nobody
looked at, which simply is not in the file.

The sketch above is one version behind what is checked in: a real file opens
with `"schema": 1`, every observation carries `platforms`, and decision 7's
`absent` sidecar rides beside the surface. Read a live one —
`tool-history/python.json` walks nine deltas back to an `observed_from` of
3.13.12 — rather than this example, which is kept for the shape of the idea.

### Why not the alternatives

Measured, not guessed — seven prek surfaces really extracted, then projected
to 100 releases with a distinct change every fifth, which is what a live tool
does:

| encoding | 100 releases |
| --- | ---: |
| content-addressed full surfaces | 188 KB |
| per-option intervals + change points | 17 KB |
| **base + backward deltas** | **13 KB** |

Content addressing only dedups *identical* surfaces, and a tool that keeps
changing never repeats one — so it degenerates to a full copy per change.
Across the curated set that is the difference between a store measured in
tens of megabytes and one in hundreds of kilobytes.

Per-option intervals come close on size and read better for one question
("when did `--glob` appear?"), but that is a derived index, and generating it
beats storing it.

### The costs, stated

Reconstructing an *old* surface means replaying the chain rather than reading
a blob — microseconds, but no longer a direct read. And a chain has an
integrity property a flat file does not: a mis-ordered or corrupted insert
breaks everything below it. So the writer owes a verify step that replays the
whole chain and checks it reproduces each observed release, run in CI beside
the refresh.

Queries, all derived:

- `exact(version | date)` -> base, then replay deltas down to it.
- `union(start, end)` -> the base plus every option the deltas record as
  dropped; `union(epoch, now)` is what the shipped stubs render.
- "were events appended" -> the new release's delta is non-empty.

The negation and wrapper tables — the two facts the *runtime* reads — come
from the same surfaces, so `tools.audit`'s hard-failure case keeps its
meaning.

Left to implementation: whether `spellings` is the whole identity, or an
option also needs a stable id for the case where every spelling changes at
once. My reading is no — that is a rename, and renames are remove-plus-add
(decision 2).

## 2. Priming: newest first, to a per-tool budget

The current version matters most, so the prime walks **backwards** from the
newest release and stops at a per-tool count — tweakable by popularity, and
living on the **driver** beside `Provision`, never in the history file. The
file records what was observed; how far we chose to look is a setting, and
the two come apart the moment a prime is interrupted. `observed_from` already
answers "where does this history reach", and answers it truthfully. Scale, from PyPI on 2026-07-26: ruff
416 releases (2022-08 →), uv 297, djlint 202, pytest 192, mypy 139, prek 77.
Across ~26 extractable tools a full prime is thousands of install-and-extract
cycles, so it must be **resumable**: skip what is already recorded, and run it
again until it is done. Rate limits make that a requirement, not a nicety.

The budget makes the floor differ wildly per tool — 100 releases is about six
months of ruff and a decade of pytest — which is why `observed_from` records
what was actually seen. An option present in the oldest release read is
`at or before <that release>`, never `since <that release>`: the file must not
assert history it never looked at. A later, deeper prime lowers one tool's
floor without touching any other.

Patch granularity, not minors. The user-facing claim is "added in 0.4.11", and
priming only `0.x.0` releases would attribute a change to the wrong one. A log
that guesses attribution is worse than one that admits a floor.

## 3. The scheduled refresh

**Shipped as `fm tools.refresh`** (2026-07-27), and rebuilt the same day as
a **parallel gather on footman's own runtime**. It has since split along the
seam a three-OS matrix needs: `tools.gather` observes what one platform has
not yet accounted for, `tools.owed` answers what it *would* read without
installing anything, and `tools.assemble` folds gathered observations into
the store as the single writer. `tools.refresh` remains the one-machine
spelling — gather and assemble in one call — and `tools.prepare-release`
rolls the version and changelog the way the runbook does by hand. Three
phases: listings
fetched concurrently; every (tool, release) observed in parallel through a
hidden `tools.observe` task, a bounded wave at a time; chains assembled
single-threaded from whatever arrived, in whatever order — `insert` makes
arrival order meaningless. Each observation is a real task deliberately: the
task boundary copies the caller's env overlay, so the PATH written around
one extraction is that observation's alone, and the sandbox variables flow
into every subprocess through the same injection every task body gets. No
environment was threaded by hand; the engine leans on what the runtime
already guarantees. (`@pre_task` itself was considered and declined: its
"supply the result, skip the body" channel is reserved and inert, so the
lifecycle feature used here is the per-task env lane, plus `parallel()`,
`fail()`, the futures work-key and the `--json` envelope.) A release that
will not install is a reported **hole**, never the end of a walk — refresh
plans every unobserved release down to the floor, so it fills its own holes
on the next run. A bare call is refused with directions; the tests drive the
engine through `footman.testing.Runner`.

The rebuild also caught an observation-correctness bug: click extraction
imported the tool from the *running process's* environment and stamped it
with the PATH binary's version, so a primed mkdocs 1.4.0 recorded our 1.6.x
surface under 1.4.0's label — nine empty deltas, twice over, for exactly the
two drivers importable in this venv. `_from_click` now requires the entry
point's distribution version to match the binary's; re-primed, mkdocs shows
three real events and zensical two where there had been none.

The sketch below described
this in terms of `tools.audit`, which only ever answered *is a newer version
out*; the job needs *what did it change*, so the walk reads releases rather
than comparing version strings.

```sh
fm tools.provision                                # into .tools-latest, no --clean
fm --json tools.refresh --prefix=.tools-latest    # {"read","events","unreachable","release"}
```

It reads **every** release published since each tool's base, oldest first, and
promotes each in turn. Not a jump to the newest: reading only the last of
three attributes all three releases' changes to it, so a flag that arrived in
0.16.1 would be recorded as arriving in 0.16.3, and the stub would tell a
reader on 0.16.2 that an option they have does not exist. An unchanged release
is still read and records an empty delta.

`--prefix` is not optional, and it matters more here than on `sync`: uv
carries CPython's download index *inside the binary*, so a stale uv reports a
stale newest python and the walk starts too low without saying so. Measured —
uv 0.11.1 tops out at 3.14.3 where 0.11.31 sees 3.14.6. The prefix must
therefore hold **uv itself**, not only the tools; a prefix without it falls
back to the host's uv silently.

Two things the job depends on that were not in the sketch:

- **An index that cannot be read raises** rather than returning an empty
  listing. "Is there anything new" is the exit condition, and a throttled
  registry answering "no" would end the run with "nothing to release". For one
  run that is a week's delay; for a renamed package it is *forever*, while the
  job keeps reporting success over a tool nobody is tracking. It exits 75
  (`EX_TEMPFAIL`), and does not abort a release the tools it *could* read
  justify.
- **A prime keeps its downloads inside its own scratch directory.**
  `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` point there, so cleanup is
  structural rather than a rule to remember, and the interpreters the machine
  actually runs are never candidates for deletion. Each release is discarded
  once its surface is read, which is the difference between peak disk being
  one release and being all of them — a full prime of ruff would otherwise
  stand up 416 environments at once.

**Cadence: weekly** (Willem, 2026-07-27). It costs little on a public repo
and shortens the window in which the docs are behind.

## 4. The release gate: new events, not new versions

**This is the point of unifying the two.** A new tool version does not
necessarily change its command-line surface, and today it always looks like it
does — the version lives *in* the stub header, so every bump produces a diff by
construction. An auto-release driven by `behind` would cut releases for header
lines.

v0.21.0 (2026-07-26) was the first release to retake snapshots under the new
runbook step, and it is exactly the case the job must reason about:

| tool | changed lines | what actually changed |
| --- | --- | --- |
| `prek` 0.4.10 → 0.4.11 | 23 | a real surface change (new `glob` option) |
| `uv` 0.11.31 → 0.11.32 | 2 | the `Read from` header only |
| `djlint` 1.42.2 → 1.42.3 | 2 | the `Read from` header only |

Two of three had nothing to say. With an event log the gate is exact: **were
any events appended?** No events, no release — record the new version in the
history and stop. This also makes the release notes write themselves, since the
events *are* the changelog entry ("prek 0.4.11 adds `--glob`").

## 5. Constraints carried in

- **A partial fetch must not read as drift.** Provisioning pulls from four
  tiers (PyPI via `uv tool install`, bun's own release, GitHub and GitLab
  assets). A rate-limited or flaky fetch leaves the prefix short, and anything
  missing must be *left alone*, never read from the runner's own PATH — that
  guard shipped in #79 (`not in the prefix`), and the job must additionally
  require every non-skipped tier to report `ok` before trusting the result.
- **A snapshot only ever moves forward** (#79): a reading older than the record
  is ignored rather than written backwards.
- **Plugin-sensitive tools.** A bare provision strips pytest's `--cov*`; the
  driver declares `plugins=("pytest-cov",)` so the prefix holds a
  plugin-complete pytest. Any new tool with plugin-provided flags needs the
  same.
- **git and docker** sit on the `system` tier only because their real "latest"
  sources were deferred (git from source; docker via download.docker.com static
  builds). A refresh on a clean CI box has to fetch them properly anyway, so
  those sources fall out of this work rather than being tracked separately.

    **They did, and the tier emptied** (2026-08-03). docker got its own
    `Provision(kind="docker")`, and git went to the **`man`** tier, reading
    kernel.org's manuals rather than a binary — the prediction above held,
    the sources fell out of the work. No driver answers `kind="system"` any
    more: `_HOST_READ` computes to an empty set, and the 36 curated tools
    divide as uv 24, man 4, node 2, and one each of docker, bun, github,
    gitea, gitlab and python. Nothing footman reads comes off the host.
- **`main` is protected**, so the job cannot push a `chore(release)` commit
  directly; it lands through a PR like every other release.
- `.tools-latest/` is gitignored; a full provision is ~27s locally, all 24
  fetchable tools ok (6 shells are hand-written stubs, git/docker are `system`
  — the tier that has since emptied, above).

## 6. Decided (2026-07-26)

1. **The event key is short-if-exists, else long** — shorts are the stabler
   spelling, the reverse of generation's preference (long is friendlier as an
   API). *Both* spellings are recorded on the option and a new reading matches
   on either, so an option that later gains or loses a short extends its
   interval instead of reading as a removal plus an addition.
2. **Renames and verb moves are a removal plus an addition.** Nothing in
   `--help` relates the two spellings, a synthesised "moved" would be a guess,
   and it would surface nowhere that helps a reader.
3. **Help text is state**, so a reworded description is a real change — with
   two noise sources closed first, or the log manufactures releases: extraction
   pins the terminal width (in the prime too, or history disagrees with the
   present), and each event records the **extractor version**, so improving
   `_toolhelp`/`_toolspec` rewrites state without counting as tool events.
   Same mechanism covers a tool flipping between the click and `--help` paths.
4. **Version and state are tracked separately.** Only a state change is due a
   release; the version rides along and is stored as the latest actual version
   for the docs. That is what ends "every bump is a diff": the stub header
   stops being state.
5. **Ordering is by version, with the date breaking a tie.** *Reversed
   2026-07-27; the original is kept below, because its reasoning was sound and
   only its premise was wrong.*

   It first read: order by release date, because version strings cannot order
   themselves across this set — `version_tuple` truncates at build tags, so
   `0.6.0-wk.5` and `0.6.0` compare equal — while every source publishes a
   date.

   The flaw is that a date orders *publication*, and this file answers a
   **version** question: does the build in front of me carry this flag. Three
   curated tools keep more than one series alive at once — cmake 3.31.x beside
   4.x, pytest's 4.6 LTS beside 5.x, and CPython's five, which ship five
   patches on one day. For those the two orders genuinely differ, and a
   date-ordered walk back from 3.14.6 steps to 3.13.14, records every 3.14
   option as dropped, and re-adds them a few entries later. Every interval
   derived from that chain is then wrong: `-X tlbc` would read *added in
   3.14.0, gone since 3.13.12*.

   The premise about version strings was also narrower than it looked.
   Measured across all 24 listable tools, 3,195 of 3,385 version strings are
   plain numeric; 165 are pre-releases, now excluded from chains because an
   alpha is not something to say a flag arrived in; 21 are `.postN`; and
   **four** are anything else — all of them eclint's `-wk.N`, a fork series
   that appears in no other index and never beside a plain `0.6.0`. Ordering
   by `(version_tuple, date)` is total across the whole set: the version
   separates every real comparison, and the date separates two builds of one
   base, which is exactly what `version_tuple` folds together.

   The one caller with no date to fall back on is the "a snapshot only ever
   moves forward" guard, since a fresh reading is stamped today whatever build
   it holds. It declines to move rather than guess — which also fixes a live
   bug, where a tie read as "not older" let a stale checkout promote eclint's
   `wk.3` over the recorded `wk.5`.

   Two consequences worth stating, since decision 3 makes help text state.
   Patch granularity is now real rather than aspirational: CPython's `-I`
   help text changed in **3.13.3**, a patch release, and the chain records it.
   And a reading whose version names no release — ninja's binary answers
   `1.13.0.git.kitware.jobserver-pipe-1` where PyPI ships `1.13.0` — has its
   build tail trimmed, or it matches nothing in its own index and the tool
   cannot be primed at all.
6. **Stubs are the union, never pruned.** A removed option stays in the stub so
   completion works against any version ever; `added in` / `removed in` live in
   its docstring. This inverts nothing: a stub already suggests without
   forbidding (`**flags: Any`), and the tool remains the only judge of what it
   accepts. An `exact(version)` variant falls out of the same file later.
7. **Platforms are a list on the observation, and exclusions are the
   exception.** *Refined into an algebra 2026-07-27, when it was built.*

   A release read on three platforms is one observation of a merged surface;
   storing it per platform would triple a store whose options are almost all
   universal. The observation says who looked — and what the store keeps
   beside it is a **sidecar**, `absent`, keyed `verb\toption` the way deltas
   key their moves, naming the platforms that looked at *that release* and
   did not find each option. The invariant is `absent ⊆ platforms`: observed
   absences only, never inferred ones. Nothing recorded means nothing
   contradicts the option.

   Beside the surface rather than inside it, and that is load-bearing. A tag
   never enters a delta, so platform coverage cannot masquerade as the tool
   changing — the release gate and the changelog stay honest with no
   strip-before-compare anywhere. It also makes a verb-level tag safe:
   `_verb_delta` compares a fixed field tuple, so a tag inside the surface
   would vanish from every delta and replay would stop reproducing what was
   observed.

   Every standing claim is **derived**, exactly as `since` and `until`
   already are. `union()` walks each platform's newest verdict per option:
   present there, the claim drops; missing there, it stands; never observed,
   it was never made. So a sighting clears an absence — nothing means
   cross-platform — a Linux-only week can neither set nor clear a Windows
   claim, and `since`/`until` are withheld where the observations cannot
   support them: a platform's own floor is not a since, and a platform that
   never held an option cannot witness its removal.

   Divergent help text still resolves Linux > macOS > Windows, now against
   *derived* provenance so legs arriving in any order reach the same store —
   a tie-break against weekly churn, nothing more. And `fold()` folds a
   release's several readings before the chain is touched: otherwise an
   option only Linux has would be inserted, dropped when macOS's turn came
   and resurrected by Windows, three real deltas for a release nobody
   changed.
8. **The history lives in the repo, not the wheel.** Generation is a maintainer
   action run from a checkout, and users read the stubs, which already carry
   everything the log is for. Later, and probably after the toolgen quartet is
   spun out as its own library: an auto-download option and local regeneration
   matching your own machine exactly. Keep the store inside the
   `_toolspec`/`_toolhelp`/`_stubgen`/`_drivers` neighbourhood so that spin-out
   stays a `git mv` (see the standalone-stubgen memory).
9. **A stub-only release is a patch bump**, with the CHANGELOG entry written
   from the events themselves.

## 7. Still open

Nothing blocking, and all of it configuration rather than design:

1. ~~The workflow file itself~~ — written, and now a matrix:
   `.github/workflows/refresh.yml` gathers on ubuntu, macOS and Windows and
   assembles on one runner. The PR (pushed with the `REFRESH_TOKEN` PAT, so
   CI runs on it) opens on any tree change and **auto-merges** once the gate
   passes — the gate is the reviewer. Auto-release is built and gated on
   `vars.AUTO_RELEASE`, default off. Rollout is local-first: gather on a real
   Windows and Linux machine, copy the documents back, assemble here, before
   cron is trusted.

   **Rolled out, and waiting on the calendar** (checked 2026-08-02). The
   prime ran on three local machines, which is what the store shows: 26 of
   30 chains hold Linux, macOS and Windows in their base observation, and the
   four that do not are tools one platform cannot install. The workflow was
   then driven by hand seven times — all on 2026-07-29, the day it landed.
   Three failed and four passed, but that reads as a build session rather
   than a flaky job: the three reds produced three fixes committed the same
   day (bash on every leg including Windows, provision-what-is-needed with a
   retry for what merely dropped, and a week with nothing to read counting as
   success), and the last run of the day is green.

   The cron is Mondays 06:00 UTC and the workflow landed on a **Wednesday**,
   so no scheduled run has been due yet. **The first one is Monday
   2026-08-03.**

   It fired at 07:21 UTC and **failed on all three legs in under 32
   seconds** — not the job's fault. It reads `.results[]` out of `fm --json`,
   and `2a415ae` had renamed that key to `items` two days earlier, in the
   window where nothing exercised the pairing. Fixed, and the docs carried
   the same two mistakes; the dispatch that followed ran green end to end
   and opened the first real refresh PR. Nothing tested a jq recipe, which
   is how a change flagged `!` for breaking still reached a Monday morning
   unnoticed; the docs suite now runs them.

   That leaves `AUTO_RELEASE`, unset by design, as the one remaining choice.
2. ~~**The `system` tier**~~ — **deleted 2026-08-03.** git and docker were the
   two tools that read the host; docker fetches its own releases now, and git
   reads kernel.org's manuals on the `man` tier. That left `_HOST_READ`
   computing to an empty set, so the tier went out whole rather than sitting
   there as a rule with nothing to apply to (Willem: *"that was never going
   to be reproducible long term, it just got us started"*).

   Out with it: `_provision.py`'s skip branch, the `kind` docstring's entry,
   `tools.py`'s `!= "system"` guard, and — the only real loss —
   **`_resolve`'s macOS Homebrew keg lookup**, whose sole caller was
   `_HOST_READ`. A keg was preferred over `PATH` so an intentionally
   unlinked build was still the one read; with nothing host-read, there was
   nothing to prefer it for, and `_resolve` is now `shutil.which` on every
   platform. A refresh can speak for every curated tool.

**Budget: ten releases per tool, pre-primed** (Willem, 2026-07-27), and not
revisited until the workflow is actually running in CI — a budget tuned
against a laptop's timings would be tuned against the wrong machine. Ten is
enough to make the interval rendering mean something without a prime being an
afternoon. The two tools that already reach further keep what they have:
depth is a fact about what was read, and there is nothing to gain by throwing
it away to meet a number.

Whether that number wants to differ per tool is exactly what a running job
will answer. The scale it has to answer against, from PyPI: ruff 416
releases, uv 297, djlint 202, pytest 192, mypy 139, prek 77.

Two known imprecisions, both looked at and left alone:

- **A gap inside a chain reads as a `since`.** The floor rule refuses to
  claim a `since` for an option present at the oldest release read; the same
  reasoning applies to a hole *mid-chain* and does not. python-build-standalone
  publishes no 3.11.0, so `-P` renders as "Added in 3.11.1" where it in fact
  arrived in 3.11.0. One release's overclaim, on one tool, at the two points
  an index has a hole (Willem, 2026-07-27: not worth fixing).
- **A tool with no `[Unreleased]` section** gets no release note, and the
  refresh reports that it wrote none rather than inventing somewhere to put
  it.

## 8. Where to start

**Steps 1 and 2 landed 2026-07-27.** `_toolhistory.py` holds the format
(surface serialisation, delta, replay, load/save); `tools.sync` records its
reading and renders the stub from the history; all 26 stubs regenerate
byte-identical through the round-trip. The store sits in `tool-history/`,
outside `src/`, and the built wheel was checked to confirm it does not ship.
**Step 3 landed 2026-07-27** for the PyPI tier: `fm tools.prime` walks
backwards, resumably, and prek's checked-in history carries ten real
releases. Ordering needed one refinement the plan did not anticipate —
same-day releases are common, so the sort is (date, version) rather than
date alone; resolved by index order the walk skipped 0.4.8 and would have
appended it below its own successor on the next run.

**Rendering the union landed the same day**: stubs carry every option ever,
annotated `Added in X` / `Gone since Y` where the chain can prove it, and the
reference pages inherit it because they render from the stub docstrings. An
option present at the floor carries no `since` — the honesty rule, enforced
in code rather than remembered.

**The tiers landed 2026-07-27**: PyPI, npm, GitHub and GitLab (which covers
bun). Real chains on all four — prek 21 releases, cspell 3, gh 3, eclint 3.
`system` (git, docker) stays unlistable until their real sources are wired,
and provisioned interpreters are not tool releases. *Those sources were
wired: gitea, docker and `man` joined the list, and the `system` tier is now
empty — see §5 and §7.*

**The rest landed too** (checked 2026-08-02): the per-tool budget sits on the
driver, the release gate reads the deltas and writes its own CHANGELOG line,
and the scheduled refresh exists as a three-OS matrix. `tool-history/` holds
30 chains against 37 stubs — the seven without one are the hand-written shell
stubs and the two `system`-tier tools. The chain-integrity debt §1 promised
is paid in `tests/test_toolhistory.py`
(`test_replay_reaches_every_release_in_a_chain`, and a second test that
inserts a release at any position and replays again), and the refresh PR's
own body says the gate is what replays every chain.

The sequencing below is kept as history — it is how the build was ordered,
and the reasoning is still the useful part:

1. Seed each tool's `<tool>.json` with a base and no deltas, at the
   version its current stub records — the surface is already extractable, and
   `observed_from` states the floor honestly. A history of one release is a
   valid history, which is the point: the format degrades to "what we know
   today" rather than requiring the prime to mean anything.
2. Switch `_stubgen` to render from the history rather than a live `ToolSpec`.
   Nothing user-visible changes; the stubs should regenerate byte-identical
   apart from the header, which proves the schema against every tool.
3. Only then teach the fetchers to walk backwards, which turns the seed into
   history and needs no change to anything above it.

That sequencing made the risky part optional: the refresh became "append to
a file that already exists", the gate became "is this delta non-empty", and a
tool that is never primed simply has a short history rather than a broken one.
The prime — the expensive, network-bound, most-likely-to-stall part — went
last, which is why none of it stalled.

## Until the job existed

**Superseded 2026-08-02.** This was the interim policy, and the job it was
waiting for exists: `CLAUDE.md`'s Releasing section now says the opposite in
so many words — *"The tool stubs are not a release step. They move on their
own schedule… A release ships whatever is checked in."* Retaking them by hand
is something you may do when a reading looks wrong, not something a release
waits for.

The original, for the record. Stubs are resynced **just before a release**,
not when audit notices (Willem, 2026-07-26), which is step 2 of the Releasing
runbook in `CLAUDE.md`:

```sh
fm tools.provision
fm tools.audit --prefix .tools-latest    # what moved → the CHANGELOG line
fm tools.sync  --prefix .tools-latest
```
