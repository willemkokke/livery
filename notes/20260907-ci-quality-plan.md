# CI and quality gating: the plan

Status: ruled 2026-09-07, every open ruling closed in review.
Phase 1's substrate landed 2026-09-07 (issue #289): the loop is
whole, gate to receipt; the evidence lives in the phase's
acceptance bullet, and the one open line there names what rides
the wheels-matrix work instead.
Nothing is started; phase 1 begins on Willem's go.
Absorbs #267 (the speed pass), #286 (no logic in YAML), #270 (token
publishing), #273 (the act names itself), and the structural finding of
the phase-4 post-mortem (notes/20260906-phase-4-post-mortem.md): the
workspace world was fully verified, the release world barely, and the
gap is where every release-day defect lived.

Willem's requests shaping it (2026-09-07): a local CI mode, one
platform, against Gitea and GitLab with a local devpi server, Gitea
first and GitLab once Gitea is in a good state, with token publishing
provisioned on the local Gitea. A minimal number of workflows.
Generalise hse's profile-based CI instrumentation, across CI
invocations. Plus everything distilled from the release day.

## Part 1: the current state

Everything below was read from the code and measured from real runs on
2026-09-06/07. File references are to HEAD (7a0c671).

### The workflow files

Six files under `.github/workflows/`, four emitted, two written by
hand:

| file | origin | triggers | jobs |
| --- | --- | --- | --- |
| ci.yml | emitted | pull_request, push main | release-title, check (3 OS x 2 py), docs, gate |
| release.yml | emitted | pull_request closed on main, dispatch(ref) | publish, templates |
| docs.yml | emitted | workflow_run on ci@main, dispatch | deploy (Pages) |
| governance.yml | emitted | push main, paths-filtered | apply (fm workflow.configure) |
| nightly.yml | by hand | cron 04:17, dispatch | released-wheels-replay (6 legs), file-issue-on-failure |
| release-legs.yml | by hand | dispatch | gitea-local, gitlab-local, 3 cloud legs, coverage-report |

The emitter is `packages/workshop/src/livery/workshop/_ci_generate.py`,
one backend per call, rendered only at `template.apply` / the update
wave / repository birth, never per release. The drift gate
(`template_check`, inside `fm check`) holds the four emitted files
byte-identical to the emitter. The two hand-written files sit outside
that gate and each carries a hand-pinned uv version with a comment
admitting the emitted files derive the same value from `uv.lock`: a
lock bump desynchronises them silently.

That rot is not hypothetical: release-legs.yml is inert today
(found 2026-09-07, livery#287). The suites read `FORGE_LIVE` and
`FORGE_E2E_OWNER`; the workflow still exports the pre-rename
`LIVERY_FORGE_*` names, and its cloud legs write credentials to the
retired `.forge.dev.env` path nothing reads. A dispatch goes green by
replaying cassettes and prints a "Live coverage union" containing no
live execution. The file decayed exactly where the drift gate does
not look.

Run entries per merged PR today: ci on the PR, ci again on main, docs
via workflow_run, and a release run that exists only to skip (its
train_if rejects non-release merges in 1-2 seconds). Five entries, two
heavy.

### Where the time goes, measured

A ci run costs 13 to 16 minutes wall. One representative main run
(34061689914):

| job | seconds |
| --- | --- |
| check windows-latest 3.11 | 793 |
| check ubuntu-latest 3.11 | 461 |
| check windows-latest 3.14 | 454 |
| check macos-latest 3.14 | 331 |
| check ubuntu-latest 3.14 | 165 |
| docs (after check) | 133 |
| gate | 16 |

Inside the Windows 3.11 leg: checkout 7s, setup-uv 15s, enter 7s, the
gate itself 754s. Runner setup is noise; the time is `fm check` on
Windows. The same gate on ubuntu 3.14 is about 130s, which matches a
local worktree run (2m08s). Nobody knows where the Windows 620-second
difference goes, because no leg is instrumented.

### What one change pays

- `fm submit` runs the full gate locally, then CI runs it on six legs,
  then the merge runs it on six legs again on main. A self-heal
  (conflict or behind) re-runs the local gate per heal.
- A release adds: the wait for main's own CI to verdict the base
  (`require_verified_base`), two isolated legs per member (floor and
  highest), the full local gate inside the release submit, six CI legs
  on the release PR, then the publish wave.
- A notes-only diff pays all of it. `affected_packages`
  (`_graph.py:48-80`) returns None, meaning everything, when any
  changed file falls outside every package, and `notes/` is outside
  every package. The affected seam cannot express a prose change
  today.

### The gate's composition

`fm check` (`_quality.py:139-191`) is one `parallel()` block: format,
lint, typecheck (six invocations: basedpyright, three platform mypy
passes, ty, pyrefly), typecomplete, tests, kindcheck, the render/drift
gate, the provenance gate. `--fix` serialises the three rewriters
first.

The affected seam already exists and is real: `fm check --affected`
computes the dependents closure and scopes format, lint,
basedpyright/mypy paths, typecomplete, and test dirs. Deliberate
limits: ty and pyrefly always run whole, and scoped mode skips the
render gate. CI does not use it anywhere.

### The emitters, by backend

The GitHub lane is the mature one. The Gitea lane diverges: no
workflow_dispatch recovery entry on its release workflow, unpinned
`actions/checkout@v4`, no coverage metering or artifact, and its
templates job lacks the `contains()` guard the GitHub one has, so the
template artifact would publish on every release wave. The GitLab lane
collapses to one python with no OS matrix, ignores `[ci] runners`, and
triggers its release wave on a commit-title regex instead of the
merged branch.

Dead code: the wheels matrix (`_wants_wheel_matrix`,
`_ci_generate.py:317-331`) reads `answers["packages"]`, a key
`_facts()` never emits, so no real generation ever produces the wheels
job; only synthetic test dicts reach it. A platform-wheel member would
silently get a wheel-less release today.

Decision expressions in the emitted YAML: the census is in #286. Five
are movable into verbs; the one defensible survivor is the event
filter class (which branch, which event).

### The local forge stack

Further along than assumed. `fm forge.dev.up` (packages/forge,
`_dev/`) brings up a digest-pinned Gitea 1.28 with Actions enabled and
an act_runner in host mode (labels ubuntu-latest, capacity 8), and a
GitLab CE with a registered shell-executor gitlab-runner (concurrency
8). Seeding mints the admin user, an all-scope token, the `livery`
org/group, and runner tokens, and writes credentials key-by-key to
`.repo.shared.env` in the runner's config directory. No repositories
are seeded; suites make their own. Housekeeping fault: compose.yaml
and two docstrings still name the retired `.forge.dev.env` path.

`release-legs.yml` already drives release legs against these local
containers from CI, by hand, dispatch-only.

### Publishing and registries

`publish_wheels` (`_publish.py:253-280`) already speaks
`uv publish --publish-url <url> --token <token>`; the token is
`os.environ.get("UV_PUBLISH_TOKEN")` at both call sites. The GitHub
lane overrides this with trusted publishing (environment `pypi`,
id-token); the Gitea lane already emits `UV_PUBLISH_TOKEN` from
secrets, with the comment "Gitea has no trusted publishing".

Registry resolution (`_registries.py:76-125`) is a ladder: env cascade
(`PYTHON_REGISTRY_URL` / `PYTHON_PUBLISH_INDEX`), then `[registries]`
in workshop.toml, then the forge's own package registry, then
pypi.org. But the isolated legs read a different surface entirely:
`[[tool.uv.index]]` from the root pyproject (`_index_args`,
`_python.py:368-385`). One private index today means configuring two
places. And the wave's served-probe builds `SimpleRegistry` without a
token (`_release_driver.py:738`) although the class accepts one, so an
authenticated index would probe anonymously and time out.

Secrets: `fm env.set KEY --scope=ci` writes repository Actions secrets
through the forge protocol on all three backends (GitHub via the
PyNaCl extra, Gitea plaintext PUT, GitLab masked variables). The
provisioning verb for a local publish token already exists.

devpi appears nowhere in source or config, only in notes; hse runs one
externally.

### Instrumentation today

Every duration on screen is footman's report (per-task line, `took
XmYYs`); workshop adds none of its own. footman persists exactly one
number per invocation shape, the wall total, in a machine-local
`.times.json` that is cold on every CI leg. `fm --json` already emits
per-task duration, queue wait, lane waits, sections, and per-step
timings; nothing consumes it.

`footman.profile` is a complete plugin: `--profile` writes a Chrome
trace with a track per worker, a slice per task, steps, lane waits,
author sections/marks/streams (including retroactive spans built for
CI check windows), and per-test slices via the `FM_PROFILE_DIR`
fragment protocol that footman's pytest plugin speaks natively. livery
does not mount it, so `fm --profile check` does not work here today.

hse mounts it fleet-wide and uploads one trace artifact per profiled
leg (via the gitea fork of upload-artifact, with `if-no-files-found:
ignore`, both learned the hard way). Across runs hse aggregates
nothing: no download, no merge verb, no store. What hse does have,
built for other metrics and directly reusable, is the stamp transport
(`hse-devkit/_stamp.py`): a JSON file on an ordinary git ref, written
from inside a CI job without a worktree via hash-object/mktree/
commit-tree/force-push, commits carrying `[skip ci]` and their own
identity. Its two consumers (e2e freshness, coverage high-water mark)
encode the rules a cross-run store needs: only the gated run class
writes, absent data is not regression, fail open loudly, and never
rewrite the file from a state you could not read.

## Part 2: the improvements

### The rulings this plan builds on

- No logic in YAML if it can be avoided; it all lives in tasks
  (Willem, 2026-09-06, #286).
- Token-based publishing suits monorepos better than trusted
  publishing (Willem, 2026-09-06, #270).
- Local CI mode against Gitea and GitLab; Gitea first, GitLab once
  Gitea is in a good state (Willem, 2026-09-07). The request named
  a local devpi; superseded the same day by the forge's own
  registry (decision record).
- A minimal number of workflows (Willem, 2026-09-07).
- Measure the loop end to end and attack the biggest waits first
  (Willem, 2026-09-06, #267).
- Enforced serial legs are not a win; explicit parallelism is the
  shape (Willem, 2026-09-06).
- Develop locally: nothing reaches GitHub until tested exhaustively
  on local Gitea and GitLab; the only public iterations hunt
  GitHub's own quirks; Gitea first, because a clean environment is
  cheapest to stand up there (Willem, 2026-09-07).
- Matrices are parametrised through the contract: the development
  workspaces run python 3.14 on linux only, with docs generation
  stubbed, and the keys are ordinary workshop.toml config with
  derived defaults (Willem, 2026-09-07).

### Phase order, and why

Ruled by Willem 2026-09-07: develop as locally as possible. Nothing
reaches GitHub until it has been tested exhaustively on the local
forges, and the only public iterations are the ones that hunt
GitHub's own quirks.

1. The local Gitea loop: the development substrate for everything
   after. Gitea only, because a clean environment is cheapest to
   stand up there.
2. Instrument the loop, inside it.
3. The new shape, on the Gitea emitter only, so the flux is never
   ported three times.
4. The speed mechanics, proven in the same loop.
5. GitLab reaches the same state, still locally.
6. The GitHub port: livery flips, the quirks are hunted, the live
   numbers are attacked.

Each phase lands gate-green and mergeable alone, per the working
conventions.

### Phase 1: the local Gitea loop, the development substrate

Everything later in this plan is developed inside this loop, ruled
by Willem 2026-09-07: nothing reaches GitHub until it has been
tested exhaustively on the local forges, and the only public
iterations are phase 6's GitHub quirk hunt. Gitea only to begin
with: it is the cheapest environment to stand up cleanly, and
iterating on one backend while the shape is still moving avoids
paying to keep three aligned.

The post-mortem's structural fix. The release act, on real emitted
YAML, on a real runner, against a real index, from one command.

- No devpi (ruled by Willem 2026-09-07). The loop publishes to the
  local Gitea's own package registry through the ladder's forge
  rung, which already resolves it: the Gitea backend's
  `registry_url("python", owner)` returns
  `{host}/api/packages/{owner}/pypi` today. No new container, no
  new credential class, and a shipped rung that was an untested
  fallback becomes exercised code. The passthrough task either index
  was assumed to force is measured unnecessary: Gitea validates the
  token and ignores the basic-auth username, so uv's plain
  `--token` form uploads as-is. A probe wheel published and read
  back proved the circle.
- The loop's CI is linux-only for now (ruled by Willem 2026-09-07):
  the containered act_runner is the whole runner fleet. Linux wheel
  legs run in the loop against the dummy platform-wheel member; a
  macos or native runner waits for a real platform-wheel member
  that needs those platforms.
- Publishing is a kind seam (ruled by Willem 2026-09-07, landed
  2026-09-07): `publish_artifact` is a `Backend` protocol member
  and the wave dispatches through it, picking only the resolved
  target for the kind's artifact. `uv publish` is the python kind's
  implementation (the nanobind kind delegates to it), and the conan
  kind wraps its recipe upload. A future kind brings its own
  publisher and registry rung without the wave changing shape.
- One index seam, broadened by Willem 2026-09-07: the contract
  expresses the registry topology, and the render emits it. The
  first slice landed 2026-09-07 and the loop proves it every pass:
  `[registries.python]` (`url`, plus `prerelease` carrying uv's
  policy value) renders into the root pyproject as the
  `[[tool.uv.index]]` entry and the `[tool.uv]` prerelease line,
  through `registry_injections` beside the other contract-fed
  render inputs, so a re-render can never unwire the index again.
  The loop measured the unwire live twice: birth's resume
  re-rendered pyproject and the e2e's surgery re-applied, two
  commits per run; then a member render (`new.package` re-renders
  the roster) stripped the wiring after the surgery had already
  passed, and the next lock silently resolved the workshop back to
  the released PyPI index. Two resolution facts the loop forced,
  both load-bearing: uv's first-index strategy resolves a package
  served by the declared index from that index alone, and no
  prerelease mode is declared, uv's default ruling (the decision
  record's prerelease entry carries the ruling and the
  measurements). Still open in this seam:
  reads as an ordered list (hse's caching mirror with a private
  overlay), rendering `publish`, and per-platform index pins (the
  pytorch case); what the contract cannot say still needs a durable
  seam in the rendered pyproject rather than a hand edit the next
  render reverts. The isolated legs' `[[tool.uv.index]]` surface
  and `resolve_registry` read one declaration. The served-probe
  carries the token `SimpleRegistry` already accepts, which the
  authenticated local registry forced immediately.
- Provisioning: a verb births (or reuses, idempotently) the repo on
  the seeded Gitea org, pushes HEAD, asserts protection and context,
  and writes `UV_PUBLISH_TOKEN` (the registry credential) and the forge
  token as Actions secrets through the existing
  `RepoConfig.secrets` path. This is the "token based publishing
  provisioned on the local gitea instance" requirement.
- The rehearsal verb (working name `fm ci.e2e`,
  `--forge=gitea` first): run the real workflows on the local
  instance through act_runner, the release act publishing to the
  local registry,
  receipt tags on the local repo, verdicts followed, idempotent
  re-run as the recovery procedure. Absorbs what release-legs.yml
  did by hand.
- Gitea emitter parity while it is finally exercised: the dispatch
  recovery entry, pinned checkout, the templates `contains` guard
  moved into its verb (phase 3 shape), and an explicit decision on
  coverage metering for that lane. Two parity findings already
  measured and fixed on first contact (2026-09-07): Gitea reports
  Actions statuses as `<workflow> / <job> (<event>)`, so a bare
  required context could never match and no protected merge could
  ever pass; `required_context_string` now spells protection per
  forge, pinned. And the emitted gitea governance job carries no
  ambient-token env, so `workflow.configure` refused in-workflow;
  the loop provisions `FORGE_ADMIN_TOKEN` and the emitter gap is
  phase-3 work. Also measured: the emitted-workflows-versus-
  installed-workshop version skew fails exactly as predicted (the
  docs job's "no task named"), and eating the dev wheels cures it;
  the full gate on the loop's fixture-scale workspace runs in 4 to
  6 seconds inside the container. And a third parity finding
  (2026-09-07): Gitea's arm is the same merge endpoint as the
  immediate merge, so its whole 405 family refuses both. That
  family grew into the classified merge-hold state machine (the
  decision record's 2026-09-08 entry), the fake's
  `merge_405_window` fault fires on both paths speaking Gitea's
  real words, and `test_merge_state` pins every documented state
  across the three forges.
- #270's mechanics are proven here: publishing to the local
  registry by token is the same code path. The GitHub flip itself
  is phase 6; whether the pypi environment's approval gate stays is
  Willem's call.
- #273 rides here: the release report names its act (dev, local,
  release) so a rehearsal can never impersonate a release again.
- Housekeeping: the stale `.forge.dev.env` docstrings.
- Acceptance: met 2026-09-07, measured from a clean slate (the
  scratch repository and its registry package deleted, the loop
  workspace removed). One `fm ci.e2e` run birthed the repository,
  proved the gate on the runner (the setup PR merged through
  protection), landed the member through the loop's own armed
  submit, released it through the real release PR and the emitted
  wave (release.yml green on the squash), and measured the result:
  the registry serves ci-e2e-loop-loop-echo 0.1.0 and the annotated
  receipt packages/loop-echo/v0.1.0 points at the squash. A second
  run is idempotent (member already landed, receipt already on the
  loop, exit 0). The broken-member refusal has two measured layers:
  the local gate refuses a red member before any push, naming
  format, lint, and the failing test verbatim, and a red only CI
  can see refuses with the leg's verdict and a diagnostics file
  naming the job (measured live: "ci.yml: docs (failure)", submit
  exit 13). (release-legs.yml, which this supersedes, is deleted in
  the phase-6 file swap.)
- Open in this phase: the dummy platform-wheel member and its linux
  wheel leg in the loop have not run yet; they land with the
  wheels-matrix mechanics rather than blocking the substrate.
- Open in this phase: the workshop coverage floor sits at 85, down
  from 87, because the loop verb's orchestration is live-tested
  only (accepted by Willem 2026-09-08 as a temporary state). The
  e2e orchestration is expected to become the
  initial-infrastructure wizard, and its unit coverage and the
  floor return with that work; the auto-ratchet mode then keeps
  floors climbing on their own.

### Phase 2: instrument the loop

Developed and exercised inside the phase-1 loop. The
`footman.profile` mount is one YAML-free line in tasks.py and may
land on livery at any time; the live GitHub step timings and trace
uploads arrive with the phase-6 port, and the Windows gate
investigation waits there too, since no local Windows runner
exists.

- Mount `footman.profile` in the workspace (hse's tolerant mount
  pattern), so `fm --profile <verb>` works everywhere including CI.
- Every CI leg runs its gate profiled and uploads the trace as an
  artifact, one per leg, `if-no-files-found: ignore` (hse's two
  hard-won upload rules ported with their reasons).
- Generalise hse's stamp transport into workshop as the CI state
  store, ruled by Willem 2026-09-07: refs in a dedicated namespace
  outside `refs/heads`, `refs/workshop/*` (ruled by Willem
  2026-09-07: instance-visible names speak workshop), so a default
  clone or fetch never downloads them. Each ref is replace-only,
  depth one, its tree holding several files (the write reads the old
  tree and rebuilds it with the kept entries plus the new one, so a
  window needs no history). Blobs ride gzipped and windowed; rows
  ride as plain JSON. Concurrency has two rules. Within a run there
  is no contention by construction: each leg writes only its own
  per-run ref, and the run's one gate job is the single writer of
  the shared refs. Across runs, shared refs use compare-and-swap:
  the push carries the expected old value (`--force-with-lease`
  with an explicit sha), the server refuses a stale write
  atomically, and the loser re-reads, merges its rows in, and
  retries. A reader always sees one commit, so there is no torn
  read across the tree's files. Every concurrency loss degrades to
  redundant work (a rerun, a re-stamp), never to a wrong verdict.
  Hygiene, both sides: a read fetches into FETCH_HEAD and creates no
  local ref, so a checkout accrues only unreachable objects that
  git's own auto-gc prunes, and a default clone or fetch never sees
  the namespace at all (a `--mirror` clone does, bounded by the
  windows). On the server the window bounds each ref's reachable
  size, every write orphans its predecessor for the forge's own
  housekeeping to prune (on the compose Gitea that housekeeping is
  ours to keep enabled), and the janitor that sweeps a cancelled
  run's refs also enforces the windows. Port the rulebook: gated
  run class only, fail open loudly, no rewrite from an unread
  state, `[skip ci]`, own identity.
- `workshop/metrics`: a compact row per job per run, window-capped. The
  row is end to end, not gate-only: queue wait and per-step wall
  times lifted from the forge's own run API (checkout, cache
  restore, setup, each named step) beside the per-verb durations
  from footman's ledger. The shell around fm is where two of this
  week's findings hid: the docs deploy running cacheless on every
  `workflow_run` event, and the Windows leg's unexplained 620
  seconds. A number that only starts when fm starts cannot see
  either. Rows carry a schema version and are keyed by package,
  series, and metric; windows and writer classes are declared per
  series, not per store (the gate writes timings and coverage, a
  nightly point may write benchmarks later), so a years-long,
  kilobyte-rows series coexists with a ten-run trace window and a
  new series is a new key, never a migration.
- Traces stay local files for now: in the loop the trace already
  sits in the working directory, so it needs no transport at all.
  Only the metrics rows ride the ref. The blob story
  (`workshop/profiles`, gzip, windows, whether refs replace artifact
  uploads outright) is deferred to phase 6, where a remote runner
  first makes it real; ref transport remains the ruled default
  when it lands.
- A reading verb (working name `fm ci.timings`): per-verb, per-leg
  trend, p50/p90, biggest movers since a base. This is the ledger
  #267's "measure the loop end to end" asks for.
- Acceptance: a trace retrievable for every leg of one run; the
  metrics ref carrying rows from at least two runs; the reading verb
  rendering them; the transport's refusal paths tested before its
  happy path (fallbacks first), the windowed rewrite included.

### Phase 3: fewest workflows, dumbest YAML, CI as points

On the Gitea emitter only. The GitHub and GitLab emitters receive
the settled shape in phases 5 and 6, so the flux is never ported
three times, and livery's own workflows stay untouched until phase
6. The bullets below name the GitHub files for familiarity; they
land as the gitea equivalents first, and the swaps of livery's own
files (deleting docs.yml and release-legs.yml, adopting nightly)
are the phase-6 port, not work here.

The end state, ruled by Willem 2026-09-07: a consumer never thinks
about CI. They reason about a named point in the process and attach a
task or a test tier to it. The emitted YAML becomes a static shell
per forge: each workflow is a trigger, a checkout, an enter, and one
`fm ci.run --point=<name>`, with points such as gate, merge, nightly,
and release. What runs at a point is contract data: workshop's
builtin schedule, plus a `[ci.schedule]` seam where a package or the
workspace attaches its own entries. Scheduling something nightly is a
TOML line. The matrix shape belongs to the point, not the task. What
this does not cover: triggers, permissions, and secrets stay YAML
facts, and a new point shape stays workshop work.

The points, each an entry moment with a fixed execution shape and
its own writer class into the state store:

- gate: on a pull request. The check matrix (every declared runner
  by every python), the docs build, the title check, the verdict
  job. Writes tree verdicts, coverage, and metrics rows.
- merge: on main after a merge. The same gate, collapsed to seconds
  by `workshop/verified` when the squash changed nothing, plus the docs
  deploy. The gate's writer class.
- nightly: on the clock. The wheels replay; live conformance and
  benchmarks later. Its own writer class.
- release: dispatched by the merge point when a merged release is
  unpublished. The publish wave and the templates deploy.

A dispatch is a recovery entry into an existing point, never a
fifth point. The governance fold is ruled (2026-09-07): governance
stops being a workflow, because push-to-main is the merge point, so
`fm workflow.configure` becomes a merge-point task that
self-classifies and exits fast when no contract paths changed,
taking the emitted file target from four to three (ci, release,
nightly).

- Execute #286: move the five movable decisions into verbs. The title
  check runs on every PR and decides for itself; the `!cancelled()`
  family falls away; the gate verdict and the templates-deploy guard
  become fm verbs. Event filters remain the only YAML conditions.
- Fold the docs deploy into ci.yml as a main-only job after the gate,
  deleting docs.yml, the workflow_run trigger, and the cross-run
  artifact download.
- The release wave is dispatched from the merge point (Willem's
  shape, 2026-09-07: an always-run verb that returns green when
  nothing needs doing). The merge point run that exists anyway ends
  with `fm workflow.release.dispatch` (name ruled 2026-09-07): it
  reads the
  manifest at HEAD, and when a merged release is not yet published
  it dispatches release.yml at the squash sha. This deletes
  train_if, the `pull_request: closed` trigger, and the per-merge
  noise run, costs zero extra runner boots, replaces GitLab's
  commit-title regex with the same verb in its main pipeline, and
  orders the wave behind main's own verdict, which the
  verified-tree record makes free for a fresh release squash.
  What decides: the manifest at HEAD names every member and
  version; the verb is green when no manifest exists or every
  member's receipt tag does (the manifest is permanent residue of
  the last release, so presence is not the signal, missing
  receipts are), reports an in-flight wave as green with its run
  id, and otherwise dispatches at the commit that stamped the
  manifest, never at its own sha, so an unrelated merge landing
  before the wave cannot move the wave onto a different tree.
  The verb reads remote state, receipts by `ls-remote` and in-flight
  waves by the forge API, never the local tag store, so a manual
  invocation from any checkout answers the same as CI. Running it by
  hand is therefore the recovery gesture, not a hazard: on a normal
  day it prints green, after a died merge run it dispatches, and the
  duplicate-tolerant wave backstops even a stale double dispatch.
  Why the wave stays a separate dispatched workflow: environment
  rules apply before a job starts, so an inline wave job under the
  pypi environment would demand approval on every merge just to
  say nothing needs doing; the wheels matrix, once wired, only
  makes sense in a workflow that runs solely for releases; and
  recovery needs the dispatch-at-a-ref entry anyway, so inline
  would give one wave two homes. GitLab's dispatch is an
  API-created pipeline: the verb creates one at the stamping sha
  carrying `FORGE_WORKFLOW=release`, the pipeline-class variable
  the emitted workflow rules already gate on, and the wave's jobs,
  `parallel:matrix` wheels included, rule on that class and never
  run in ordinary pipelines. A variable set by the creating call
  is that pipeline's event identity: an event filter, not a
  decision. One
  YAML fact appears: `actions: write` on that job (GitHub's
  no-cascade rule for the ambient token excepts workflow_dispatch).
  The armed engine no longer dispatches; it observes and reports,
  and the release is not done until the wave's run id is confirmed.
  release.yml keeps workflow_dispatch as its only trigger; a hand
  dispatch and the idempotent re-run stay the recovery gestures.
- The token inventory does not grow. The ambient job token covers
  everything same-repo, including both new needs: state-store
  pushes (`contents: write`) and the merge-point dispatch
  (`actions: write`); the wave already runs its forge calls on
  `github.token` today. Provided secrets remain only where the
  ambient token structurally cannot reach: admin API
  (`FORGE_ADMIN_TOKEN`, protection), cross-repository push (the
  templates deploy key and `FORGE_TOKEN`), and publishing off the
  forge (`UV_PUBLISH_TOKEN`). GitLab's `CI_JOB_TOKEN` cannot git
  push, so its existing `GITLAB_PUSH_TOKEN` also carries the store
  writes; its dispatch is an API-created pipeline (the release
  bullet above), and the token class that may create one, a
  project trigger token or the api-scoped token it already
  provides, is a phase-5 fact to verify. The shells declare their `permissions:` blocks
  explicitly, since org defaults are read-only; the block doubles
  as a record of which job may touch what. Also structural:
  pushes to `refs/workshop/*` match no push trigger on any backend, so
  store writes can never cascade into workflows, whatever the
  token.
- Verb visibility follows one rule (ruled 2026-09-07): a verb whose
  only legitimate caller is the emitted YAML is a hidden task, per
  the existing `workflow.configure` precedent
  (`workflow.release.check-title`, `workflow.release.publish`,
  `env.emit`); a verb that doubles as a human gesture stays listed
  (`workflow.release.dispatch`, `ci.run`, `ci.times`,
  `ci.e2e`).
- Adopt nightly.yml as the first proof of the points shape: a
  nightly point whose one declared task is the wheels replay, its uv
  pin derived from the lock like everything else. Adoption ruled
  2026-09-07: the two jobs collapse into one scheduled task whose
  failure path files the issue through fm (the
  `.github/scripts/nightly_issue.py` logic becomes verb behaviour),
  and the replay becomes an ordinary verb a person can also run by
  hand. Delete
  release-legs.yml, inert today (livery#287). Its intent returns as
  scheduled entries: the local live-conformance legs run through
  phase 1's container stack, the cloud legs run with the
  credential-absent skip decided verb-side and named in the report,
  and the live coverage union rides the state store.
- The per-leg coverage upload/download actions leave the YAML with
  the shells: legs hand the gate their data through per-run refs
  (`refs/workshop/run/<id>/<leg>`, race-free because each leg names its
  own ref, read and deleted by the gate job; a janitor verb sweeps
  refs a cancelled run orphaned). One transport on every forge; the
  artifact path needed `actions/upload-artifact` on GitHub, a
  third-party fork of it on Gitea, and a different dialect on
  GitLab. The boundary, recorded not hedged: a fork pull request
  runs with a read-only token and cannot push refs, so a repo that
  accepts fork PRs sets a contract fact and its shells emit the
  artifact plumbing instead, decided at render time, never as a
  YAML condition. The shells declare `contents: write` on jobs that
  write state, the same fact by another name.
- The wheels matrix: wired (ruled by Willem 2026-09-07). `packages`
  with their kinds reach `_facts()`, so the decision is live, and
  the platforms come from `wheel-platforms`. The loop's test
  workspace carries a dummy platform-wheel member, so every
  rehearsal forces the wheels path: build on the linux runner,
  publish to the local registry, install in an isolated leg. More
  platforms are more entries in the same key on real forges; a
  silent wheel-less release for a nanobind member was exactly a
  hidden fallback, and now the fallback has a test forcing it.
- Contract keys are kebab-case (ruled by Willem 2026-09-07), and
  the contract loader gains a consistency check refusing underscore
  keys. The existing snake_case keys (`required_context`,
  `coverage_floor`, `templates_artifact`, and friends) migrate here,
  pre-1.0, rewritten across checkouts by the update wave; new keys
  are born kebab.
- Matrices become contract config with derived defaults (ruled by
  Willem 2026-09-07): `[ci] python-versions` overrides the derived
  floor-plus-newest pair, and a platform-wheel package declares its
  wheel platforms (working key `wheel-platforms`); `[ci] runners`
  already exists. The development workspaces declare the fast
  shape, python 3.14 on linux only, so a local iteration costs one
  leg; livery's production contract keeps the full matrix. The
  wheel keys are defined here because the emitter is open anyway,
  and exercised only when a platform-wheel member exists. The same
  keys serve a consumer whose support surface is genuinely
  narrower. The development workspaces also stub docs generation
  (no declared generators and a minimal docs tree, or a dummy
  generator), so a loop iteration pays no site build; the points
  shape therefore tolerates a workspace whose docs job has nothing
  to do and reports green (ruled by Willem 2026-09-07).
- Target: three emitted files (ci, release, nightly), zero
  hand-written, all under the drift gate, zero decision expressions
  beyond event filters, every job's work reached through
  `fm ci.run --point=<name>`.
- Acceptance: the census in #286 re-run shows only event filters; a
  merged PR creates three run entries, none skipped; drift gate
  covers every workflow file in the repo; a task scheduled through
  `[ci.schedule]` runs at its point with no YAML change.

### Phase 4: the speed mechanics, proven locally

The mechanics are built and proven in the local loop; the attack on
livery's live numbers, the Windows leg included, lands with the
phase-6 port. Targets are ratified against phase-2 baselines, not
guessed. The candidate list, from what is already known:

- The Windows gate: 754s against ubuntu's 130s for the same verbs.
  The trace says whether it is the checkers, the tests, or process
  spawning; then attack that.
- Affected mode in CI: the check legs run the scoped gate against
  the merge base. The render and provenance gates, skipped in scoped
  mode today, get an explicit home (the gate job, or unskipped when
  workflow-owning files are touched).
- Coverage stays global under affected, ruled by Willem 2026-09-07,
  through the CI state store: when a suite runs, its combined
  coverage data is stamped on `workshop/coverage`, keyed by the suite's
  package and the identity of its dependency closure. When affected
  skips a suite, the gate pulls the stamped data instead, and the
  union enforces the same global floors as a full run. The unit is
  the suite that ran, never the package covered, because attribution
  crosses packages (toolroom's tests cover footman lines). The reuse
  is exact, not an estimate: a suite outside the affected closure
  has identical code and identical dependencies, by the same
  argument that lets its run be skipped at all. A miss runs the
  suite fresh, so the store self-heals and needs no backfill. The
  gitea lane has no coverage metering today, so the local loop
  carries no coverage transport at all until this phase decides
  that lane's story.
- Coverage over time, and the floor mode declared per package
  (ruled by Willem 2026-09-07). A per-package percentage row lands
  beside the timing rows on every gated run, so the reader renders
  the trend, not only the latest value. Control stays in the
  contract: `[qa] coverage_floor` is either a literal percentage,
  as today (the shims keep their ruled 100), or the auto-ratchet
  mode (working spelling `"auto-ratchet"`). Under auto-ratchet the
  measured mark lives on `workshop/coverage` and ratchets under hse's
  ported rulebook, plus two rules the hwm never needed: a wobble
  tolerance, and an explicit decrease verb,
  `fm coverage.accept <package> <value> --reason` (ruled
  2026-09-07), that writes a dated, reasoned row, so lowering
  coverage is a visible act with an audit trail instead of a
  silent edit; it refuses without a reason or above the current
  mark, and the refusals are tested first. The tolerance is declared in
  workshop.toml beside the floor (ruled by Willem 2026-09-07;
  working key `coverage-epsilon`, in percentage points), default
  0.5 when absent: enforcement passes at mark minus epsilon, and
  the mark ratchets up only when a run clears it by more than
  epsilon, because xdist scheduling wiggles the percentage
  (livery#263). 0.5 over 0.25 because a wobble-red gate blocks the
  merge path for every PR, while a dip inside the tolerance is
  caught the next time the mark climbs; tighten it per package once
  the flakes are fixed. Either mode is checked against a
  skipped suite's stored measurement without running its tests. A
  local gate that cannot reach origin falls open loudly; CI stays
  the gate of record. The decrease verb and the refusal paths are
  built and tested before the ratchet's happy path.
- The prose class: the gate job always runs and classifies the diff
  itself (fm verb, no YAML logic); a diff confined to `notes/` and
  named prose paths pays markdown lint and the render check only,
  still reporting the one required context. Fixes the
  affected-returns-None hole for prose by construction; a mixed diff
  pays full price.
- The post-merge rerun disappears when the squash changed nothing:
  the gate stamps `workshop/verified` with the tree id it proved green,
  and every run's classifier checks its own tree against the record
  first. A squash of a branch sitting on main's tip produces the
  same tree the PR verified, so main's run reports verified in
  seconds; a squash of a stale branch produces a new tree and pays
  in full. Tree identity, not merge shape, is the test: it covers
  the lock, the workflows, and everything else the repo carries,
  and the same skip reaches reverts and manual re-runs. The docs
  deploy still runs (it publishes, it does not verify), and the
  train's `require_verified_base` reads the same record, so a
  release off a just-merged main starts at once. The record is
  advisory in the safe direction only: a lost stamp reruns the
  gate, never skips it.
- Caching, measured then widened. Today setup-uv restores only uv's
  own cache, one key per leg. That already covers more than it
  looks: every checker rides the lock's dev group, so their installs
  re-link from that cache and the venv rebuild costs seconds (enter
  was 7s on the slowest leg). What starts cold on every leg is
  state: the mypy, basedpyright, ty, and pyrefly incremental caches
  and footman's `.times.json`. Persist those, keyed per leg. The
  footman data directory is not the cache unit: it holds worktrees
  and durable machine state, and its `toolroom` store is empty in CI
  because nothing provisions it there; the gate's tools all arrive
  through the venv. When the entry contract's deferred tool-store
  port moves CI onto the store, the store joins the cache keyed by
  its pinned era, and uv's python directory must be restored beside
  it or the store's interpreter symlinks dangle (POSIX links
  `python` into uv's own store; Windows writes a launcher shim
  because a symlink there needs elevation and a copied `python.exe`
  loses its standard library).
- Partial docs generation: generators scoped by the affected closure,
  untouched packages' `_generated` output reused (#267 item 3, #258's
  casts as the motivating cost). A generator's cache key names all
  its inputs, and a generator may declare a state ref as an input,
  its key then including that ref's tip: a metrics graph changes
  when the store moves, not when the package does, and a key blind
  to the store would freeze it.
- Explicit leg parallelism inside the gate and the release legs
  (the ruled shape; serial was the stopgap).
- The release path's waits: `require_verified_base` rides on main CI,
  so every CI minute saved shortens the train twice.
- Acceptance: each mechanic demonstrated in the rehearsal loop with
  its refusal paths forced first; the live-numbers acceptance (the
  two-minute notes-only merge, the halved median) lands with phase
  6, where the baseline it is judged against exists.

### Phase 5: GitLab reaches the same state, locally

Gated on Willem's ruling that Gitea is in a good state.

- The lane's parity debts: an OS/python matrix honouring
  `[ci] runners`, the commit-title regex replaced by
  `workflow.release.dispatch` in the main pipeline creating the
  `FORGE_WORKFLOW=release` pipeline, the phase-3 verb shapes.
  Facts to verify on the instance: the token class that may create
  a pipeline, and macOS and Windows runner availability, which
  gates whether platform-wheel kinds can declare themselves on a
  GitLab-hosted repo at all.
- The rehearsal verb grows `--forge=gitlab` against the existing
  gitlab-runner and GitLab's own package registry, the forge rung's
  GitLab implementation verified here.
- Secrets provisioned as masked variables through the existing path.
- Acceptance: the same one-command rehearsal, green, on GitLab.

### Phase 6: the GitHub port, the only public iterations

The settled shape lands in the GitHub emitter, and livery's own
workflows flip to it. Iteration in public is allowed here, and only
for what cannot exist locally:

- The port itself: points shells, merge-point dispatch, state
  store, verified trees, the caching keys, the parametrised
  matrices, one emitter at its final shape.
- #270 executes (ruled 2026-09-07): token publishing is the
  default. The pypi environment gains `UV_PUBLISH_TOKEN` and the
  id-token plumbing leaves the default emission. Trusted publishing
  stays a supported choice a workspace declares in its contract,
  rendered at emit time like every backend fact, never the default.
  livery keeps the pypi environment as the secret's scope; approval
  rules are the repo owner's business, and livery sets none. The
  mechanics arrive already proven against the local registry.
- The GitHub quirk hunt: the skip-propagation family, environment
  rules, dispatch behaviour, whatever else only GitHub's runner
  surface can show. Every quirk found lands in this note's ledger
  the day it is found.
- The Windows gate measurement: the first place a Windows runner
  exists. The 620-second question is answered here, by the phase-2
  instrumentation riding the port.
- Acceptance: a release of a real set rides the new train on
  GitHub end to end; a notes-only PR merges within about two
  minutes; the median PR's CI time at least halves against the
  phase-2 baseline; the quirk ledger is written; every skipped
  verb in the cheap paths is skipped by a named rule.

### The scenario map

Drawn 2026-09-07 at Willem's request, as the legibility test of the
target system: 59 distinct states, 12 happy outcomes and 47
divergences or faults, and every red edge exits through one of four
recovery gestures: fix-and-push (the fix rides, never amend), re-run
the same verb (every workflow verb is idempotent), re-dispatch the
wave (the same verb, engine or hand), and `fm workflow.abort`.

What the drawing demands of the reports: the wave prints a
per-member state table (built, identity, published, served, tagged),
because the partial-wave states are the least self-evident part of
the map, and the report is where they become legible. The dispatch
seam simplified once the merge point became the dispatcher: CI is
always watching, so merged-but-undispatched survives only when the
merge run itself died, and the same re-run covers it. The release
still reports done only on a confirmed run id.

```mermaid
flowchart TD
  classDef ok fill:#0e7a5f,stroke:#0a5c48,color:#ffffff
  classDef err fill:#93312f,stroke:#7a2624,color:#ffffff
  classDef rec fill:#a4610f,stroke:#8a500c,color:#ffffff

  subgraph LOCAL["local loop"]
    A0["edit"] --> A1{"fm check --affected"}
    A1 -->|"format or lint red"| A2["fm check --fix"]:::rec --> A1
    A1 -->|"render drift"| A3["fm template.apply"]:::rec --> A1
    A1 -->|"checker or test red"| A0
    A1 -->|"store unreachable"| A4["fall open loudly, CI decides"]:::rec --> B0
    A1 -->|"green"| B0
  end

  subgraph SUBMIT["fm submit --armed"]
    B0["gate, push, PR, arm"] -->|"behind or conflicts"| B1["integrate, re-gate, re-push"]:::rec --> B0
    B0 --> C0
  end

  subgraph GATE["gate point: PR CI"]
    C0{"title verb, check matrix, docs build, verdict"}
    C0 -->|"prose-only diff"| C1["mdlint and render only, context green"]:::ok --> D0
    C0 -->|"leg red"| C2["fix commit rides, never amend"] --> C0
    C0 -->|"flake suspected"| C3["re-run leg, file issue"]:::rec --> C0
    C0 -->|"behind main"| B1
    C0 -->|"green"| C4{"floors vs union, stored suites reused"}
    C4 -->|"floor red"| A0
    C4 -->|"holds, epsilon tolerates wobble"| D0["auto-merge"]
  end

  subgraph MERGE["merge point: main"]
    D0 --> D1{"tree already verified?"}
    D1 -->|"yes"| D2["verdict in seconds, stamp store"]:::ok
    D1 -->|"no: stale-branch squash"| D3{"full gate"}
    D3 -->|"green"| D2
    D3 -->|"red"| D4["main red: fix forward, issue same day"]:::err --> A0
    D2 --> D5["governance task, self-classifies"] --> D6["docs deploy"]
    D6 -->|"deploy red"| D7["redeploy via dispatch"]:::rec
  end

  subgraph ACT["the branch decides the act"]
    R0["fm workflow.release --armed"] --> R1{"branch?"}
    R1 -->|"feature branch"| R2["dev act, named in report"]
    R1 -->|"--local"| R3["local act"]
    R1 -->|"main"| RC{"tree clean?"}
    RC -->|"no"| RD["refuse: clean tree"]:::err
    RC -->|"yes"| R4
  end

  subgraph PREP["prepare"]
    R4{"base verified?"}
    R4 -->|"pending"| R5["wait on main CI or verified tree"]:::rec --> R4
    R4 -->|"main red"| R6["refuse: fix main first"]:::err
    R4 -->|"yes"| R7{"stale workflow branch?"}
    R7 -->|"clean"| R8["re-prepare, stamps skip clean"]:::rec --> R9
    R7 -->|"wrong"| R10["fm workflow.abort"]:::rec --> R0
    R7 -->|"none"| R9{"derive versions, stamp set and manifest"}
    R9 -->|"nothing to release"| R11["refuse, named"]:::err
    R9 -->|"first release, no baseline"| R12["refuse: set release baseline"]:::err
    R9 -->|"ok"| R13{"build and isolated legs per member: floor, highest"}
    R13 -->|"build red, leg red, literal missing, twin mismatch"| R14["rollback, verdict verbatim"]:::err
    R13 -->|"green"| R16["submit release PR"]
  end

  subgraph RPR["release PR"]
    R16 --> S0{"title verb"}
    S0 -->|"wrong grammar"| S1["matrix cancelled fast: fix title"]:::err --> R16
    S0 -->|"ok"| S2{"check matrix on release branch"}
    S2 -->|"red"| S3["fix rides on branch"] --> S2
    S2 -->|"behind main"| S4["re-prepare on new base"]:::rec --> R9
    S2 -->|"second train exists"| S5["refuse: one train at a time"]:::err
    S2 -->|"green"| S6["auto-merge armed"]
  end

  subgraph DISP["merge point dispatches"]
    S6 --> T0{"merge point: gate verdict, then fm workflow.release.dispatch"}
    T0 -->|"not a release merge"| T6["green: nothing to do"]:::ok
    T0 -->|"release merged, main verified"| T1["dispatch wave at squash sha"]
    T0 -->|"main red: no wave"| T5["fix forward, then re-run"]:::err --> T3
    T0 -->|"merge run died"| T2["merged, undispatched"]:::err --> T3["re-run detects and dispatches"]:::rec --> T1
    T1 -->|"dispatch API fails"| T4["retry: not done until run id"]:::rec --> T1
    T1 --> W0
  end

  subgraph WAVE["publish wave"]
    W0{"members from manifest at ref, dependency order"}
    W0 -->|"manifest unreadable"| W1["diff fallback discovery"]:::rec --> W2
    W0 --> W2{"per member: build, identity, publish, probe, tag"}
    W2 -->|"already published or tagged"| W3["walk past"]:::rec --> W2
    W2 -->|"identity mismatch"| W4["refuse before publish"]:::err
    W2 -->|"publish refused"| W5["member fails, dependents stop, rest finish"]:::err --> W6["fix, re-dispatch walks past done"]:::rec --> W0
    W2 -->|"probe timeout or tag push fails"| W7["published, untagged"]:::err --> W6
    W2 -->|"runner dies"| W8["partial wave"]:::err --> W6
    W2 -->|"all receipts cut"| W9["release complete"]:::ok
    W9 --> W10{"templates member shipped?"}
    W10 -->|"deploy red"| W11["re-dispatch templates"]:::rec
    W10 -->|"yes"| W12["templates artifact deployed"]:::ok
    W9 --> W13["docs deploy renders release view"]:::ok
  end

  REH["fm ci.e2e --forge=gitea: this whole graph against local Gitea and its registry, consequence-free"]:::rec
```

### Held open: per-package graphs on the docs site

Willem's direction (2026-09-07), after looking at
github-action-benchmark: at some point, a forge-agnostic equivalent,
fm on top of the state store, rendering per-package graphs over time
(unit tests, integration tests, benchmarks) into the docs deploy.
Not scheduled in this plan. The phases above are shaped so nothing
forecloses it:

- New series are new row keys under the versioned schema, never a
  migration (phase 2).
- Windows and writer classes are per series, so a long benchmark
  history coexists with short trace windows and a nightly benchmark
  point writes without joining the gate's corpus (phase 2).
- The docs build fetches the store with the read token it already
  has, and the partial-docs cache treats state refs as declarable
  generator inputs, so a graphs page rerenders when the store
  moves (phase 4).
- The rendering itself lands later as a `[docs] generators` entry
  plus a `[ci.schedule]` entry for the benchmark runs, both
  existing seams: no new workflow, no new transport.

### Open rulings

None. Every ruling raised in this plan was closed in the review of
2026-09-07; the decision record below carries them all.

## Decision record

- 2026-09-07: plan drafted, awaiting ruling. devpi was the local
  index, named in the request; Gitea's own pypi registry the
  forge-rung fallback. Superseded later the same day (below).
- 2026-09-07, ruled by Willem: the verbs are `fm ci.run` and
  `fm ci.timings` (his spelling: "times" reads as multiply). The
  end-to-end verb lives in `ci.*` (his regrouping: it exercises
  every point, not only the train); its name was `ci.e2e` for
  part of the day and was reopened the same day, since rehearse
  implies operating on the current checkout. The contract
  keys are `[ci] python-versions` and `[ci] runners`. Contract keys
  are kebab-case with a consistency check enforcing it; the
  existing snake_case keys (`required_context`, `coverage_floor`,
  and friends) migrate in phase 3 through the update wave, and
  `coverage-epsilon` is born kebab.
- 2026-09-07, ruled by Willem: "point" is the vocabulary (never
  "stage", which GitLab's pipelines own); the governance fold is
  taken, so the emitted file target is three (ci, release,
  nightly); and the state-ref namespace is `refs/workshop/*`,
  because instance-visible names speak workshop.
- 2026-09-07, ruled by Willem, closing the review: the wheel key
  is `wheel-platforms`, the end-to-end verb is `fm ci.e2e`
  (consumer checkouts do not carry it), and the coverage decrease
  verb is `fm coverage.accept <package> <value> --reason`, listed,
  its refusals built and tested first. No open rulings remain.
- 2026-09-07, ruled by Willem: CI is the metrics ref's only
  writer; local runs read. Local timing measurements are not
  useful, CI trends are the same for everyone; a `local` series
  remains possible later as a new key with its own writer class,
  never a migration.
- 2026-09-07, ruled by Willem: nightly.yml is adopted into the
  emitter as the nightly point's first entry; its issue-filing
  script becomes verb behaviour and the replay a runnable verb.
- 2026-09-07, ruled by Willem: the wheels matrix is wired, not
  felled. Kinds reach the emitter facts, platforms come from
  `wheel-platforms`, and the loop exercises linux wheels against a
  dummy platform-wheel member on every rehearsal, so the once-dead
  branch gains the forcing test the conventions demand.
- 2026-09-07, ruled by Willem: token publishing is the default on
  every backend; trusted publishing remains a supported contract
  choice a workspace may declare, never the default. livery keeps
  the pypi environment as the secret's scope with no approval
  rules.
- 2026-09-07, ruled by Willem: verb visibility. A verb whose only
  legitimate caller is the emitted YAML is hidden
  (`workflow.release.check-title`, `workflow.release.publish`,
  `env.emit`, beside the already-hidden `workflow.configure`); a
  verb that doubles as a human gesture stays listed
  (`workflow.release.dispatch`, `ci.run`, `ci.times`,
  `ci.e2e`).
- 2026-09-07, ruled by Willem: the dispatcher verb is
  `fm workflow.release.dispatch`, in the family of the other CI-run
  train verbs. Manual invocation is the recovery gesture by design:
  the verb reads remote state, so any checkout answers the same as
  CI.
- 2026-09-07, ruled by Willem: no devpi. Each forge's own package
  registry is the loop's index, reached through the ladder's forge
  rung, which stops being an untested fallback; GitLab uses its own
  registry in phase 5 the same way. Also ruled: the loop's CI is
  linux-only for now (the containered runner is the fleet; wheel
  legs and any macos runner wait for a platform-wheel member);
  traces stay local files until phase 6 and only metrics rows ride
  the ref; livery's own workflow-file swaps are phase 6.
- 2026-09-07: phase order originally put measurement first per the
  2026-09-06 "measure the loop end to end" ruling. Superseded the
  same day by the local-first ruling below; measurement is now
  second, inside the loop it measures.
- 2026-09-07, ruled by Willem: local-first development. The plan
  was restructured around it: the local Gitea loop is phase 1, the
  shape iterates on the gitea emitter alone, GitLab follows
  locally, and GitHub is phase 6, the only public phase, for the
  quirks only it can show. Matrices are contract-parametrised so a
  development iteration costs one leg (python 3.14, macos-only
  wheels).
- 2026-09-07, ruled by Willem: the stamp store carries the profile
  artifacts and per-suite coverage, so affected runs still enforce
  global floors. The end state is CI as points: a consumer schedules
  a task or test at a named point in the process and never touches
  CI itself.
- 2026-09-07, ruled by Willem: a package's `[qa] coverage_floor` is
  either a literal percentage or auto-ratchet, declared in
  workshop.toml, so control over the limit stays in the contract.
  Under auto-ratchet the measured mark lives on the ref. Either
  mode is checked against a skipped suite's stored measurement
  without running its tests, and the store also keeps the trend
  over time.
- 2026-09-07, direction from Willem: a forge-agnostic equivalent of
  github-action-benchmark, fm over the state store, graphing tests
  and benchmarks per package on the docs site, is a later
  workstream. The store and docs designs keep it open: keyed
  versioned rows, per-series windows and writer classes, state refs
  as generator inputs.
- 2026-09-07, ruled by Willem: ref transport is the default for CI
  state and blobs. The forge artifact store remains only as a
  contract-selected emission for a repo that accepts fork pull
  requests, whose read-only tokens cannot push refs. The two
  reasons artifacts are the wider default (fork tokens, free
  per-run namespacing and retention) were weighed and do not bind
  this shape; the machinery refs need instead is named in phases 1
  and 2 (compare-and-swap writes, the janitor, a namespace outside
  `refs/heads` so clones never fetch blobs).
- 2026-09-07, ruled by Willem: prerelease resolution stays at uv's
  default; the workshop declares no mode. The default admits a
  prerelease where a requirement names one or where a package has
  only prereleases, which under uv's first-index strategy means
  exactly the workspace's own declared dev index and nothing else,
  and Willem judges that the better default ("if there are only
  dev releases that is fine"). The `[registries.python] prerelease`
  key stays for a workspace that wants a deliberate mode. Measured
  around the ruling: a global `allow` resolved mkdocs 2.0.dev3 from
  PyPI and broke the docs job; `explicit` alone is unsatisfiable
  because the layer floors and the packages' own dependency floors
  (`livery-forge>=0.1.0`) carry no dev bounds.
- 2026-09-07, asked by Willem: `fm forge.dev.up` grows a
  `--with-docker` flag that configures the runner for the docker
  installed in the default place, so the container docs-publish
  seam is testable locally too. The loop's default stays
  `[docs] publish = "none"` for speed; the flag arms the seam when
  it is the thing under test.
- 2026-09-08, ruled by Willem: merge holds are a classified state
  machine, never a retry budget. A refusal classifies (per forge)
  into a state carrying a user-facing message and the forge's
  native words; the categories are in-progress (follow through to
  completion, no arbitrary timeout, the task's own timeout the
  backstop, honest durations feeding footman's estimates),
  recoverable (stop and say what would recover it), terminal, and
  success (discovered merged walks past). `submit.merge` waits in
  every waitable state: merge means merge as soon as possible, even
  unarmed. Every documented forge state is mapped at build time,
  the hard-to-stage ones included; an answer outside the map fails
  loudly with the native words and the map is updated in software,
  never guessed at runtime. GitLab classifies from its published
  `detailed_merge_status`, GitHub from `mergeable_state` when its
  lane arrives; Gitea classifies its 405 prose against the combined
  status, and a required context nothing reports refuses with the
  name mismatch rather than waiting forever. Recording Gitea's real
  405 bodies as cassettes by staging them stays open work.
