# footman.tools as a separately installable package

Status: PLAN — nothing built. Decisions marked **open** await Willem's
call. **2026-08-05: the inversion ruling (bottom section) reverses
this plan's mounts-under-footman premise — read bottom-up; the latest
sections supersede.** Sibling plan:
[20260801-tools-separate-repo.md](20260801-tools-separate-repo.md)
explores the same split with the tools dist in its own repository, and
carries the merits comparison between the two. Supersedes the "still thinking — don't build toward it" hold on
the standalone-tools idea (2026-07-29): the plan is now ordered; the
build still is not.

## The ask

`footman.tools` becomes its own distribution: installable separately,
importable under the same name, with hookpoints isolating the
footman-specific parts. The earlier thinking survives intact: the
boundary is **argv-construction vs execution**, and the old worry that
`.opts()` straddles it dissolved when Shape B split the surface —
`.flags()` is the argv lane, `.opts()` is execution policy. The seam
is cleaner than it was when the idea was parked.

## Inventory — what "tools" is today

Five parts, only two of which are the bridge:

1. **The bridge** — `src/footman/tools.py` (868 lines). Imports exactly
   ten footman names, and they map cleanly onto the two lanes:
   - execution: `run`, `Result`, `current`, `Invocation`,
     `_target_cwd`, `_globals` (the `_COLOR` flag table lane)
   - presentation: `color_on`, `real_stderr`, `_colordata`
   Everything else in the file — the kwargs→flags translation, the
   `off` sentinel, subcommand chaining, keyword escaping,
   `installed_version()` — is stdlib-pure and portable.
2. **The typing surface** — `tools.pyi` (219 lines) + `_stubs/*.pyi`
   (generated), the AST parity test, and the generated stubs' anchor
   import `from footman.tools import Result`.
3. **The stub-taking machinery** — `_drivers.py` (764), `_provision.py`
   (680), `_toolfetch.py` (906), `tasks/tools.py` (2448): provision an
   isolated prefix, read each tool's real `--help`, write stubs. These
   are footman *tasks* (`fm tools.provision/audit/sync`) — dev-side
   machinery, not runtime.
4. **The history** — `tool-history/` (per-tool option-event store) and
   `.github/workflows/refresh.yml` (the weekly three-platform gather +
   assemble). The release trigger is "were events appended".
5. **Completion/manifest coupling** — whatever `_manifest` bakes about
   `tools.*` surfaces. **VERIFY at build time**: the exact shape of
   this coupling was not audited for this plan.

## The packaging mechanics

"Namespace package" strictly (PEP 420) is off the table without
killing `footman/__init__.py` — the lazy exports, `main()`, and the
`--complete` fast path all live there, and a namespace package has no
`__init__`. Three real options:

- **(A) The graft.** The new dist ships *only* `footman/tools/` (the
  module becomes a subpackage); footman's own wheel stops shipping it.
  pip happily installs two dists into one package directory as long as
  no file collides, and RECORD bookkeeping keeps uninstalls clean.
  `import footman.tools` works because the directory sits inside the
  regular `footman` package. Risks to verify: editable installs / uv
  workspace dev-mode overlap, and tooling that assumes one dist per
  package dir.
- **(B) The shim.** The new dist ships a top-level `footman_tools`
  package; footman keeps a thin `footman/tools.py` that lazy-imports
  it and teaches installation when it is missing. Boring, robust,
  slightly uglier: two names for one thing, and the stubs' anchor
  import needs the shim to be real enough to re-export `Result`.
- **(C) `pkgutil.extend_path`** — legacy namespace machinery; strictly
  worse than (A) here. Listed to record it was considered.

Lean: **(A)**, because the ask names `footman.tools` and the graft
keeps that name the only name. Fall back to (B) if the editable/uv
verification bites.

## The hookpoint — one executor, injected

The bridge's ten imports collapse into one seam:

```python
class Executor(Protocol):
    def __call__(self, argv: ShowableArgv, /, **policy: Any) -> ResultLike: ...
```

- The bridge builds argv (and the `_show` structured view), merges
  `.flags()` chains, and hands the call to the executor with the
  `.opts()` policy dict. It never imports `footman.context`.
- footman registers its executor at import: `run()` with the full
  work-item pipeline — capture, receipts, review threading, lanes,
  dry-run faking. All ten current imports become that one adapter,
  owned by footman, not by the bridge.
- `ResultLike` is a small protocol (int-ness, `stdout`, `stderr`,
  `ok`) — footman's sealed `Result` satisfies it; the bridge never
  constructs one.
- `installed_version()` stays in the bridge on plain `subprocess`: it
  is deliberately outside the task context today ("so dry-run and
  recording can't lie to it") — that property survives separation
  untouched.

**Standalone-without-footman is NOT in this ask.** `footman.tools`
always mounts under footman, so there is always a host to inject the
executor. The hookpoint still earns its keep: it shrinks the coupling
to one named, versionable contract, and keeps a future re-badge (the
same bridge under a neutral name with a subprocess default executor)
a packaging exercise instead of a surgery. **Open**: whether that
future is wanted enough to shape naming now.

## What moves, what stays

- **Moves** to the new dist: the bridge, `tools.pyi`, `_stubs/`, the
  parity test, the stub-focused tests.
- **Stays** in footman: the executor adapter, the manifest/completion
  baking (it reads installed surfaces at manifest time — VERIFY), and
  the provisioning *tasks* (`fm tools.provision/…` are tasks; tasks
  live where the task runner is). The machinery modules
  (`_drivers`/`_provision`/`_toolfetch`) are dev-side — they can stay
  in footman's repo without shipping in either wheel if the workspace
  build excludes them, or move wholesale later. **Open.**
- **The refresh workflow and `tool-history/`** stay in this repo
  either way — they publish stub PRs wherever the stubs live.

## Repo shape

Same repo, uv workspace, two dists (`footman`, `footman-tools`), one
gate, one CI — the refresh workflow keeps its home and dogfooding
keeps working. A separate repo is the move only if the tools dist
grows its own release cadence pressure (the weekly refresh already
gives it one — see versioning). **Open**, with a lean to same-repo.

## Versioning and deps

- footman keeps **zero runtime deps** — `footman.tools` becomes an
  optional import that teaches `pip install footman-tools` when the
  graft is absent.
- footman-tools: stdlib-only too (the bridge is already), plus a
  declared floor `footman>=X.Y` for the executor contract. The
  contract gets a name and a CHANGELOG-visible stability note; bumping
  it bumps the floor.
- The weekly refresh currently rides footman's release train ("a
  release ships whatever is checked in"). Separated, stub refreshes
  become footman-tools releases — which is arguably the point: tool
  surface updates stop waiting on framework releases. Release
  choreography for two dists out of one repo needs its own runbook
  section (two tags? `tools-vX.Y.Z`? **open**).

## Migration phases (each gate-green, mergeable alone)

1. **Carve in place.** Define `Executor`/`ResultLike` inside footman;
   rewrite the bridge to consume the injected executor; the ten
   imports become the adapter. No packaging change, pure seam work.
   This phase is valuable even if the split never happens.
2. **Workspace split.** New dist directory, graft packaging (option
   A), move the bridge + typing surface, wire the lazy taught error
   for the missing graft. Verify editable/uv dev mode, wheel overlap,
   `verifytypes`, and the four checkers against the split layout.
3. **Stubs and refresh rehome.** Point the refresh workflow's PR at
   the new locations; parity test moves; stub anchor import verified.
4. **Docs + release runbook.** tools-bridge.md and typing pages own
   the install story; CLAUDE.md and the release section learn the
   second dist; hse pins updated after the first tools release.

## Open questions (Willem's calls)

1. Graft (A) vs shim (B) — SUPERSEDED by the 2026-08-05 inversion
   ruling (below): the import name is `toolroom`, no namespace
   surgery. Residual shim question RULED 2026-08-05: **no backwards
   compatibility** — clean break, no courtesy `footman/tools.py`;
   `import toolroom` is the only spelling.
2. Dist name — RULED 2026-08-05: **`toolroom`**, reserved same day on
   both registries (github.com/willemkokke/toolroom; PyPI via a 0.0.1
   placeholder). Sweep below.
3. Same-repo workspace vs separate repo — RULED 2026-08-05:
   **"definitely separate repo"**. The sibling plan is the operative
   one; the reservation repo is the home.
4. RULED 2026-08-05 by the inversion: standalone-first IS the shape,
   not a future re-badge kept merely possible.
5. Machinery modules — RULED 2026-08-05: `fm tools.*` moves
   **wholesale** — task tree, machinery trio, bridge, stubs, and (per
   3) tool-history + the refresh workflow. The entry-point respelling
   (`plugin("footman.tools")` → `plugin("toolroom")`) is accepted;
   footman drops its entry point, the other first-party plugins stay.
6. Two-dist release choreography — RESOLVED by 3: plain `vX.Y.Z` tags
   in each repo; toolroom releases on refresh events, footman on
   framework changes. Residual (sibling plan): refresh auto-tags vs
   human merges+tags.
7. Where does `Argv` live after a split? (New with v0.30.0.) RULED
   2026-08-05: **the twin — each package owns its own; the seam
   speaks stdlib.** See the same-date section. All design opens are
   now ruled.

## 2026-08-05 review — what v0.30.0 moved under this plan

Scope of this section: the note updates, nothing builds; the open calls
above stay open. `.argv` (notes/20260803-tool-command-lines.md) landed
between the plan and now, and it touched exactly the ground this plan
stands on.

- **The construction lane grew a whole surface with zero executor
  involvement.** `.argv` — a property on every tool and verb — builds an
  `Argv` of raw tokens and answers before anything execution-shaped is
  reached; `ArgvTool.__call__` never approaches the seam. In that
  direction the split got *easier*: the build path is portable by
  construction, and it is the surface a standalone re-badge would lead
  with.
- **But `Argv` sits on the execution side of the import graph.**
  `context.py` defines it (deliberately beside `Result`: `run()` accepts
  one, `Result.to_argv()` returns one) and the bridge imports it. After a
  split it is shared vocabulary — the bridge mints it, footman consumes
  it and mints it again from receipts. Three homes on offer: the tools
  dist owns it and footman imports it back (inverting the one-way
  dependency this plan keeps), footman keeps it and the seam grows an
  `ArgvLike` protocol beside `ResultLike`, or a tiny shared-vocabulary
  module both depend on. That is open question 7. `container_error` (the
  container-refusal wording `run()` and the bridge share) is the same
  shape in miniature and lands wherever Argv does.
- **`ResultLike` needs the token tuple.** `Result.to_argv()` re-quotes
  the executed argv from tokens threaded through `Result` construction;
  if the seam's contract doesn't name that field, receipts lose
  `to_argv()` across the split.
- **The generated stubs' anchor import changed.** Verb classes are now
  generic over what a call returns (`class Build(_Tool[_R2])`), so no
  generated stub names `Result` at all — the anchors are
  `Argv as _Argv` and `Tool as _Tool` plus the flag aliases. The
  "what moves" items citing the `Result` anchor should read Argv/Tool.
- **The hand stub imports `typing_extensions`** (PEP 696 `TypeVar`
  default, so a bare `Tool` means `Tool[Result]` in all four checkers).
  Stub-only — never imported at runtime, checkers bundle it — so the
  "footman-tools: stdlib-only" line stays true at runtime and gains this
  footnote.
- **Inventory drift.** The bridge is 1,099 lines (was 868), the hand stub
  277 (was 219). The module-level context imports are nine: `Argv`,
  `Invocation`, `Result`, `_target_cwd`, `color_on`, `container_error`,
  `current`, `real_stderr`, `run` — two newcomers (`Argv`,
  `container_error`), and the fmt-era names came and went inside #297
  without ever landing.
- **`fm tools.restub` helps phase 3.** Every stub re-renders from the
  checked-in history — no tools, no network — so a separate dist's CI
  can regenerate after a renderer change without provisioning anything.

## 2026-08-05 — naming sweep (question 2)

The criterion, set against `footman` itself: a real word with a chance
of being recognised straight away, explained in half a sentence. Two
families tried; recording the sweep so it isn't redone.

- **Household vocabulary is out.** livery, salver, tazza, dumbwaiter,
  bootjack, whiteglove — all free on PyPI, all rejected: salver came
  closest ("the tray a footman presents everything on") but nobody
  gets it without investigating. equerry (free, checked 2026-08-01)
  falls with them.
- **PyPI is strip-mined of good single common words.** Taken:
  quartermaster, toolsmith, jig, chuck, dovetail, wield, bridle, yoke,
  veneer, splice, holster, loadout, kitbag, valet, toolbelt, toolbox,
  toolchest, toolrack, toolshed — the last actively maintained ("Tools
  for data", releases through 2026-02), so PEP 541 reclamation is off,
  and Galaxy's Tool Shed owns the phrase in bioinformatics anyway.
  hilt is free but Google's Android DI library of the same name owns
  the search results.
- **Free and plausible** (checked 2026-08-05): toolroom, toolcrib,
  toolworks, toolstore, ironmonger(y), chandlery, outfitter,
  shadowboard, sheath, handhold, pommel.

**Front runner (Willem, 2026-08-05): `toolroom`.** The machine-shop
sense covers all three lanes in one word — the toolroom is where
precision tools are *made* (stubgen), *kept with their measurements*
(store; calibration records are the provenance stamps), and *signed
out for use* (bridge) — and "toolroom grade" already means
highest-precision. Anyone parses tool+room on sight. Known collision,
flagged when chosen: Toolroom Records, a UK dance label — zero domain
overlap. Runners-up kept warm: toolcrib (the factory checkout cage —
semantically the exact "tool store", clunkier word), shadowboard (the
workshop board with a painted outline for every tool — the most exact
stub metaphor, needs its explaining sentence). A real-word name rather
than `footman-tools` presumes the standalone re-badge is wanted — this
leans question 4 without ruling it. Question 2 stays open until ruled.
(It was — same day; next section.)

## 2026-08-05 — RULED: the inversion — toolroom provides the footman plugin

Willem's call, same day as the naming: spin the no-dependencies story
by structuring the footman integration as a footman plugin that
toolroom provides — and, with the shape below laid out, "it's
definitely the way to go". This REVERSES this plan's stated premise
("`footman.tools` always mounts under footman, so there is always a
host to inject the executor") and supersedes the packaging mechanics
above; graft/shim/extend_path stay as the record of what was nearly
built instead.

The shape:

- **Two zero-dependency packages.** footman never imports, ships, or
  names toolroom — the zero-deps headline keeps no asterisk. toolroom
  is stdlib-only too: the bridge already is, and the default executor
  becomes plain `subprocess`. footman appears only in toolroom's dev
  group (defining and testing the plugin tasks), never in
  `dependencies`.
- **`import toolroom` is the import name.** No graft, no namespace
  surgery. The generated stubs' anchors become
  `from toolroom import Argv as _Argv, Tool as _Tool`; hse eats an
  import rename (pre-1.0; they integrated 0.30.0 same-day).
- **The integration is an entry point toolroom provides** (the
  `footman.tasks` group — "inert until a tasks.py pulls it", the
  philosophy footman's own pyproject already states).
  `plugin("toolroom")` mounts the machinery tasks
  (`fm tools.provision/audit/sync/restub`). The plugin module imports
  footman lazily and only ever runs when footman is loading it, so
  footman is present by definition. The whole coupling is a string in
  metadata; neither dist depends on the other.
- **The task tree rides the plugin; the executor wiring must not.**
  If `run()`-routing depended on the `plugin("toolroom")` line, the
  same `tools.git(...)` call would produce receipts in one project
  and silently subprocess in another — invisible to `recording()` and
  `--dry-run`, and the forged-receipts arc says lie lanes get bent.
  Instead: **host detection in the bridge** — footman context active
  in this process → route through `run()` (receipts, capture,
  dry-run, recording, lanes); otherwise the subprocess default. The
  detection lives in toolroom: the provider knows its host, and
  footman never learns toolroom's name (the
  footman-doesn't-know-its-caller principle, kept). The Executor
  protocol survives as toolroom's extension point — another host
  registers the way footman is detected.

What this sharpens:

- **Question 7 is now the design knot.** With no footman→toolroom
  arrow, footman cannot import toolroom's `Argv` — yet `run()`
  accepts one and `Result.to_argv()` mints one with toolroom absent.
  Either both sides speak structural `ArgvLike`/`ResultLike` and each
  owns a concrete class, or one side vendors a twin. The policy
  vocabulary `.opts()` passes across the seam is part of the same
  contract, and `ResultLike` still needs the token tuple.
- **The migration phases above are graft-shaped and need redrawing.**
  Phase 1 — carve the executor seam in place — survives untouched: it
  is the prerequisite of every variant and is still where the build
  starts.
- **The reservation exists.** github.com/willemkokke/toolroom
  (public, MIT, placeholder 0.0.1 with py.typed, trusted-publishing
  release.yml mirrored from footman), created 2026-08-05. A
  placeholder serves either answer to question 3, so the repo alone
  rules nothing.
- **The dictionary agreed.** Merriam-Webster's tool room: where tools
  are "made, stored, repaired, and issued" — stubgen, tool-history +
  the stub store, the weekly refresh, the bridge and its plugin: one
  verb each. The toolroom README carries the verb set.

## 2026-08-05 — the remaining rulings

Same day, following the inversion: **"plugin rename is acceptable, no
backwards compatibility, definitely separate repo."** Folded into the
list above: question 1 (clean break, no shim), 3 (separate repo — the
sibling plan is now the operative one for logistics), 5 (`fm tools.*`
moves wholesale, entry-point respelling accepted), 6 (resolved by 3).
One behaviour clarification also recorded while answering "what
changes without the plugin line": detection must key on footman being
PRESENT in the process, not on a task running — `current()` already
hands a fresh default Context outside a run, so
outside-task-but-footman-present bridge calls route through `run()`
today and must keep doing so; and `fm tools.*` is already
entry-point-gated today, so skipping `plugin("toolroom")` costs
exactly what skipping `plugin("footman.tools")` costs now.

**Superseded the same evening, by production** (hse's portability
sweep, fixed in toolroom 0.1.1): presence-keyed detection is
process-wide — footman's pytest plugin auto-loads, so every call in a
test process went hosted, and a truth-telling probe inside
`recording()` would be faked into a cached wrong answer. Detection
now keys on **orchestration**: a live footman context (task body,
`parallel()` worker, `recording()` block). Bare calls take the
standalone lane deterministically whatever the process imported;
inside orchestration the routing cannot be bypassed; and harness
infrastructure that must answer truthfully under `recording()` rides
`.opts(recorded=False)` — the value-read lane executes where a story
step is faked. The clarification above stays as the record of the
step in between.

This plan is now fully ruled except **question 7 — the
Argv/vocabulary home** — plus two logistics residuals in the sibling
plan (subtree export vs clean start; refresh auto-tags vs human).
The build remains unordered.

## 2026-08-05 — question 7 ruled: the twin; the seam speaks stdlib

Ruled the same day ("it is so decreed"), after reading the actual
class: each package is vocabulary-complete on its own, and the seam
between them carries only stdlib shapes — `list[str]` in,
int-with-attributes out. The zero-dependencies philosophy applied to
the type layer.

- `Argv` is fifteen lines of stdlib sugar over `list[str]`
  (`.posix()` = `shlex.join`, `.windows()` = `list2cmdline`),
  designed as "an ordinary `list[str]` everywhere in Python" — which
  is the escape hatch: **no `ArgvLike` protocol is needed anywhere**;
  toolroom's `Argv` flows into `run()` as the list it is.
- footman keeps its `Argv` and `Result.to_argv()` untouched —
  `to_argv()` is a footman-alone receipt feature (`run([...])`
  records tokens with no bridge in sight). toolroom ships its own
  fifteen lines, and the generated stubs anchor toolroom's.
- Equality cooperates across the twins (`list.__eq__` compares
  contents), so `assert cmd == ["git", "push"]` and even
  cross-package `==` behave. The one class-identity dependence —
  `run()`'s taught bare-container wording for a built command —
  re-keys on shape (`hasattr(x, "posix")`: carrying shell renderers
  is what makes it a built command).
- `.posix()`/`.windows()` parity across the twins is locked by
  toolroom's conformance test against a *released* footman — the
  standing integration test the separate repo owns anyway.
- Satellites land the same way: toolroom declares its own return
  contract (int-ness, `stdout`/`stderr`/`ok`, the token tuple) as
  the stubs' annotation, and footman's sealed `Result` satisfies it
  structurally; each side's `to_argv()` mints its own `Argv`;
  `container_error` stays shared *wording*, not shared code — one
  copy per door, voice per package.

Rejected on the way, recorded so they aren't re-proposed: toolroom
owns `Argv` exclusively (amputates footman-alone `to_argv()`);
conditional import — footman's class when detected, twin standalone
(type indeterminism in the stubs); a shared vocabulary dist (a
runtime dependency — zero-deps has no "just a small one" clause);
vendored single-source (more sync machinery than the two one-line
methods it protects, and vendoring buys no class identity anyway).

With this, **every design open in this plan is ruled.** What remains
before a build order: the sibling plan's two logistics calls
(subtree export vs clean start; refresh auto-tags vs human). (Both
ruled later the same day — clean start, and a three-mode configurable
release trigger shipping human-in-the-loop first; see the sibling
plan. Nothing in either plan remains open; the next act is the build
order.)

Related thread, same day: the recording failure-injection request's
interaction with this split — analysed and kept in
[20260805-recording-failure-injection.md](20260805-recording-failure-injection.md)
(verdict: lands footman-side, ideally pre-split; detection and
seam-speaks-stdlib both hold under it).

Ruled later the same day, recorded in the sibling plan: **the lean
install** — the machinery is repo-only in toolroom, so
`pip install toolroom` is the bridge and the stubs, nothing else; the
inversion's user-facing entry point waits for a machinery dist that
external demand has yet to order.

**2026-08-05, end of day: EXECUTED — the thread is complete.**
toolroom 0.1.0 and footman 0.32.0 shipped both halves of the split;
the sibling plan's closing section carries the record.
