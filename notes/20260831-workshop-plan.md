# Building the workshop

Status: phase 8 shipped 2026-08-31 (PRs #42-#44; coverage floors
live with a half-point grace, workshop ratcheted to 80). 0.1.0 is
deliberately NOT tagged: more of hse ports first (Willem), phase 9
(coverage accuracy) being the first named piece; the stamped 0.1.0
version waits inert until the list closes. The affected engine scopes the gate to
the changed packages' dependents' closure, per-package coverage
floors live in each contract's `[qa]` table and gate the test step,
the bootstrap's replaced-by table is annotated done line by line,
and the workshop docs state the layer model as built. Phases 1-7
shipped 2026-08-31 (PRs #12-#41; all five release legs green in run
33416377299). The bootstrap
plan's entry criteria are met: `livery-forge` 0.1.0 is on PyPI with
all three backends passing the one conformance suite, the fixture
harness is stable, and the compose loop is routine.

## The prompt (Willem)

> I'll create the new accounts tomorrow, in the mean time start
> working on the workshop plan. Willem, 2026-08-31

The design this plan implements is the workshop-and-forge note in the
planning hub (`livery-planning/docs/20260829-workshop-and-forge.md`):
layers, channels, templates as version-tagged artifact repositories,
the `livery.toml` contract, and the task surface. The fitness
yardstick is `notes/20260830-development-workflows.md`: the
workshop's verbs are those workflows, W3 is the unit of reuse, and
recovery is re-entry. This note does not restate the design; it
sequences the build.

## Ground-truth contracts (do not violate)

1. **The gate stays whole and local.** `uv run fm check` runs format,
   lint, the four type checkers, typecomplete, and tests; CI runs the
   same command; nothing on the merge path waits on anything outside
   the repository. Every phase here keeps that true mid-migration:
   the fm surface may grow, never break.
2. **Raw forge verbs never reach a user's hands.** The workshop
   orchestrates local, git, and forge steps; `livery.forge` stays the
   only forge lane. The workshop's logic tests run against
   `FakeForge` in-process (the testing pyramid's third floor); no
   scenario e2e apparatus returns.
3. **Layer discipline.** A later layer adds and overrides; it never
   edits what an earlier layer owns. The instance is the last layer
   and always wins. Discovery is the `livery.toml` layer list and
   nothing else. Fragments add; identity is parameterised.
4. **The importable namespace stays PEP 420**: never create
   `livery/__init__.py`. `livery.workshop` imports downward only
   (forge, footman, toolroom, its declared tools); `livery.forge`
   stays stdlib-only at module import time plus its one declared lazy
   extra.
5. **Typing arrives in final form.** Four checkers gating, 100%
   type-complete public API, underscore modules with `__all__`
   re-exports pinned by test, Google docstrings, no RST. No clean-up
   passes later.
6. **Tags are release tags**, `<path>/v<semver>`, immutable, pushed
   alone; the template artifact is the one repository with bare
   `v<semver>` tags, stamped by the workshop's release with the
   workshop's own number.
7. **Every workflow verb is idempotent; re-running it is the
   recovery procedure.** Each forge touch probes before acting. The
   frozen `livery.forge` protocol is the contract: a verb change is a
   compatibility event, not a convenience.
8. **The monorepo is the workshop's first instance.** Its rendered
   files come from `templates/` at `HEAD` behind a render gate; a
   template change and its consequences land in one pull request.
9. **Process rules 1–9 of the bootstrap plan hold**, the verdict-pipe
   rule included: never pipe a command whose verdict is depended on.
10. **Scratch and e2e surfaces stay out of personal namespaces.**
    Published artifacts (the wheel, `workshop-templates`) are
    products, not scratch, and live where the design says.

## Phase 1 — the package, the plugin host, and the reserved name

`packages/workshop/` is born the way `packages/forge/` was: contract
first, train proven before the code matters.

- `packages/workshop/`: `livery.toml` (`type = "python"`, `name =
  "livery-workshop"`, `[[depends]] path = "packages/forge"` with kind
  and floor `0.1.0`), `pyproject.toml` (uv_build, `module-name =
  "livery.workshop"`, dependencies: `livery-forge>=0.1.0`, footman,
  toolroom), `src/livery/workshop/` with `__init__` and `__version__
  = "0.0.1"`, `docs/` seed, `CHANGELOG.md`, tests pinning the public
  surface.
- The plugin host: `_tasks.py` behind the `footman.tasks` entry
  point (the underscore rule kept; the entry point name is the public
  address), and `_layers.py` reading `[workspace] layers` from the
  root `livery.toml` and mounting each layer's plugin in order, with
  `fm layers` printing the composition. Root `tasks.py` gains
  `plugin("livery.workshop")` while keeping its existing tasks.
- The name: `livery-workshop` released as 0.0.1 through the path-tag
  train (`packages/workshop/v0.0.1`), reserving the distribution
  (checked free on PyPI, 2026-08-31) and proving the train's second
  path.
- **Acceptance:** `uv run fm check` green with the second workspace
  member; `uv run fm --list` shows the surface unchanged but for
  `fm layers`; `uv build --package livery-workshop` produces a wheel
  with no `livery/__init__.py` (`unzip -l`); `packages/workshop/v0.0.1`
  tagged, verified, published (PyPI answers
  `https://pypi.org/pypi/livery-workshop/json`).
  *(2026-08-31: all four proven — gate green, `fm layers` present,
  the wheel namespace-clean with its entry point, and 0.0.1 tagged,
  verified, and published; PyPI answers for the version.)*

## Phase 2 — tasks move into the plugin, dispatched by contract

The dev loop's brain moves from the root `tasks.py` into the wheel,
and starts reading `livery.toml` instead of assuming Python.

- `livery.workshop` grows the task tree: the check family (`check`,
  `format`, `lint`, `typecheck`, `typecomplete`, `test`), `clean`,
  and the `forge.dev` and `forge.fixtures` groups, ported from the
  root `tasks.py`. Each verb resolves the packages by walking
  `packages/*/livery.toml` and dispatches to one backend module per
  declared `type` (`_backends/_python.py` today; the seam is the
  point).
- The layering lint becomes real: edges from `[[depends]]` reconciled
  against the native manifests both ways, dependencies pointing only
  downward, the forge's stdlib rule kept — replacing the ten-line
  seed in `tests/test_workspace_contracts.py`.
- Root `tasks.py` shrinks toward the one-liner; anything left is
  explicitly temporary and listed in the replaced-by table.
- **Acceptance:** `uv run fm check` green and byte-identical in
  meaning (same steps, same scopes) before and after; `uv run fm
  --list` names the same verbs now served by the plugin; the layering
  lint fails on a synthetic violation
  (`uv run pytest packages/workshop/tests -k layering`).
  *(2026-08-31: all three hold, and the root `tasks.py` reached its
  one-line form this phase rather than "toward" it - the whole
  surface moved, hooks and forge.dev included, so nothing temporary
  remains in the file. Two verbs grew beside the moves: `clean`
  (planned surface, nothing to port) and typecomplete deriving each
  package's module from its contract name. The pipe guard and the
  container seeds proven working from the plugin by command.)*

## Phase 3 — the materialiser and the content channel

hse's materialiser, run once per layer, and the household content in
the wheel.

- `_materialise.py` ported from `hse/setup/materialize.py`; `fm sync`
  materialises every layer's `content/` into `.workshop/`
  (gitignored) and merges by name in layer order, the instance's own
  files winning.
- `content/` in the wheel: the base `CLAUDE.md` fragment (only rules
  the workshop enforces, rendered identity from contract values), the
  guidance fragments (`interaction-voice.md`,
  `documentation-standards.md`, imported from hse and already in
  `.claude/guidance/`), the three plan skills, the `pre-bash` and
  post-edit hooks, and the ruff/basedpyright/mypy/ty/pyrefly
  configuration the gate runs, `extend`-chained.
- The monorepo dogfoods it: root `CLAUDE.md` becomes the managed stub
  (`@.workshop/CLAUDE.livery.md` then `@CLAUDE.project.md`), the
  temporary hand-written file retires per its own header, and
  `.claude/` skills and hooks come from `fm sync`.
- **Acceptance:** `uv run fm sync` is idempotent (second run, no
  diff: `git status --short` empty after); the stub imports resolve
  (Claude Code loads the fragments); `uv run fm check` green with the
  hook and skill files materialised, and the pre-bash guard still
  refuses a piped gate
  (`echo '<event json>' | uv run fm hooks.pre-bash; exit 2`).
  *(2026-08-31: all four hold, plus a dogfood test that pins the real
  tree in sync (`test_the_monorepo_is_in_sync`) and six behaviour
  tests: idempotency, guidance-first stub order, materialised links,
  override kept and committed, identical copies reclaimed, stale
  entries pruned.)*

## Phase 4 — templates, and the monorepo as its own instance

The template source at the root, the render gate in the gate.

- `templates/` with one `copier.yml` (`kind`: `project`,
  `package-python`; `_subdirectory`), `_shared/` partials, and the
  two kinds. `project` renders what the monorepo's root actually is:
  `livery.toml`, the one-line `tasks.py`, the managed `CLAUDE.md`
  stub, `pyproject.toml`, `.github/workflows/ci.yml` and
  `release.yml` rendered from contract values (runners, the required
  context spelling, the tag grammar). `package-python` renders the
  `packages/<name>/` shape with `namespace_package` derivation kept
  whole from hse's template.
- The monorepo adopts answers files (root and per package) against
  `templates/` at `HEAD` as a local path source, and `fm
  template.check` — the render gate, hse's pattern — joins `fm
  check`: a pull request whose rendered files drift from `templates/`
  fails.
- `fm new.package` renders `package-python` into `packages/` and
  wires the workspace member.
- The `project` render is the bootstrap: it writes the files that must
  exist before the repo's own `fm` loop can run (`tasks.py`,
  `livery.toml`, `pyproject.toml`), each seeded once and
  instance-owned after. The verb that renders a fresh repository is a
  globally available task, hse's `create.repo` shape: it lives in the
  tool above any repository, so it never depends on the target having
  a `tasks.py`, and nobody invokes copier by hand. The phase decides
  the carrier: hse's `create.repo` with a livery-specific layer, or
  the same shape shipped by livery-workshop.
- **Acceptance:** `uv run fm check` green with the render gate in it;
  a deliberate template drift fails it (edit a rendered file, `uv run
  fm check` exits non-zero, revert); `uv run fm new.package
  --name scratch` renders a package that passes the gate, then is
  removed.

## Phase 5 — ship, status, and the forge-lane verbs

W3 lands as a callable flow on the frozen protocol, tested on the
fake.

- `submit`: the whole act as one idempotent verb, ported from hse's
  `ship_flow` onto `livery.forge`: local gate, closes-link resolution
  with existence probes, disarm-before-push, push, find-or-open with
  title rules, arm per the arming ladder (`--armed` / env / repo
  policy / default, hse's `_arming.py` ported), follow to a
  classified verdict, the self-heal exits, and `workflow.abort` with
  the remote branch deleted.
- `status` and the `ci` group (`ci.rerun`, `ci.watch`, `ci.cancel`)
  over `checks.*`; `doctor` as W12 (whoami, version, capabilities).
- Every path tested in-process against `FakeForge` with a temporary
  git repository: the second run of every workflow tested as
  seriously as the first, the fault modes (lost schedule, 405 window,
  wedged queue, slow status) exercised through ship's own loops.
- **Acceptance:** `uv run pytest packages/workshop/tests -k submit`
  green, including a test per fault mode and a re-entry test per
  verb; `uv run fm submit --help` documents the surface; the livery
  repository's own next pull request is shipped by `uv run fm ship`
  and merges armed.
  *(2026-08-31: 19 tests cover the four fault modes (lost schedule
  retried by read-back, 405 ridden out by `workflow.merge-now`,
  wedged queue timing out with `ci.cancel` as the relief, slow status
  polled through), the re-entries (second ship reuses the PR, second
  abort finds nothing, `with_closes` never duplicates), the closes
  guards, the title rules, and the verdict discrimination. The help
  renders the whole surface with the arming ladder's env rung. The
  dogfood ship is this phase's own PR.)*

## Phase 6 — the release train and the update wave

The train the workshop rides is the train it ships.

- `release.prepare` and `release.verify` replace the inline
  verification step in `release.yml`: tag equals `pyproject` equals
  `__version__` equals changelog, floors resolve to released tags
  (hse's `published` check generalised over `livery.toml` floors).
- The workshop's release publishes the template snapshot: checkout of
  `willemkokke/workshop-templates` with a deploy key, tree replaced
  from the tagged commit, tagged `vX.Y.Z` with the workshop's number,
  idempotent, same-version-different-content refused (W6).
- `fm update` (W7): floor bumps, `sync`, `template.update` per layer
  at `v<installed>`, copier migrations, then *becoming* W3 by calling
  the ship flow.
- **Acceptance:** `uv run fm release.verify` green on a
  release-shaped tree and red with a mismatched changelog (both by
  command); the snapshot publication proven by releasing
  `livery-workshop` and diffing the artifact repository against
  `templates/` at the tag (`git diff --no-index`, empty); `fm update`
  run on the monorepo itself is a no-op when nothing changed: no
  branch and no pull request, one line saying so.
  *(2026-08-31, first half: `fm release.verify
  packages/workshop/v0.0.2` verified after `fm release.prepare`
  stamped it; `packages/forge/v0.1.1` refused naming all three
  disagreements. Seven tests cover verify, prepare idempotency, the
  snapshot's publish/idempotent/immutable triple with the empty
  `git diff --no-index` as an assertion, and floor bumps moving both
  homes. Second half, after the tag: the release run's publish and
  templates jobs both green, PyPI serves 0.0.2, the artifact clone at
  v0.0.2 diffs empty against `templates/` (`git diff --no-index`,
  exit 0), and the update flow on the merged tree prints "nothing to
  update: floors, content, and render all current" and creates
  nothing.)*

## Phase 7 — the other forges' CI, and the release legs

The per-forge knowledge the conformance rigs banked becomes shipped
templates, and the per-release live verification gets its workflow.

- The `project` kind grows `forge = gitea` and `forge = gitlab`
  variants: `.gitea/workflows/` (act_runner labels, POSIX-sh steps,
  token publishing to a configured index) and `.gitlab-ci.yml`
  (rules on `CI_COMMIT_MESSAGE`/variables, `LIVERY_WORKFLOW`
  routing), rendered from the same contract values; the
  required-context spelling per forge is a contract value, not a
  sentence.
- `release-legs.yml` in this repository: workflow_dispatch, run
  before a release tag — the conformance suite live against the
  compose Gitea and GitLab plus the cloud surfaces from the bootstrap
  plan's open item 7 (the GitHub organisation, the private gitlab.com
  group, the gitea.com organisation), skipping cleanly where a
  secret is absent, cleaning its scratch at end of run.
- **Acceptance:** rendering each forge kind produces a repository
  whose CI definition lints (`fm template.check` over all three
  kinds); `release-legs.yml` green via `gh workflow run` for every
  leg whose account exists, and its skips named in the summary for
  the rest.
  *(2026-08-31, first half: the render-all-kinds test parses every
  rendered CI definition and pins the required-context job, the
  per-forge exclusivity, and the contract's `required_context` line.
  Six dispatches later (PRs #29-#35 carrying the lessons), run
  33416377299 is green on all five legs; no account was absent, so
  no skip rows appear, and the below-floor skips on gitea.com are
  named per scenario instead. The decisive last piece was a poll
  budget: CI runners are slower than a laptop, and gitea-local's
  holds were slow, not broken.)*

## Phase 8 — graduation: affected, coverage, and 0.1.0

The bootstrap deferrals come home and the temporary environment ends.

- The affected engine (`graph.affected`, `check --affected`) over the
  `livery.toml` graph, and per-package coverage high-water marks in
  the gate.
- The replaced-by table of the bootstrap plan executed to empty: no
  hand-written workflows, no cribbed configuration blocks, no
  temporary `CLAUDE.md`, the root `tasks.py` at one line.
- `livery-workshop` 0.1.0 through the train, snapshot published; the
  workshop's own docs (`packages/workshop/docs/`) state the layer
  model as built.
- **Acceptance:** `uv run fm check --affected` on a one-package
  change runs that package's closure only (shown by its own output);
  `uv run fm check` green from cold caches; the bootstrap plan's
  replaced-by table annotated done, line by line, in the same change;
  `livery-workshop` 0.1.0 on PyPI.
  *(2026-08-31: the cold-cache gate ran green after `fm clean` plus
  removing the checker caches, coverage floors printing inside it
  (forge 90.7 over 90, workshop 76.2 over 76); the table is
  annotated in this change; five graph tests pin the closure, the
  everything fallback, and both change halves. The --affected demo
  and the 0.1.0 evidence follow the merge.)*

## Phase 9 — coverage accuracy: every fm call measured

0.1.0 waits on this (Willem: it is the only way to see how much of
the task surface, a large chunk of the workshop, is covered). hse's
shape, on today's cheaper machinery.

- Subprocess instrumentation: `[tool.coverage.run]` gains
  `patch = ["subprocess"]`, parallel data files, `relative_files`,
  and a `[tool.coverage.paths]` alias set so Windows and POSIX paths
  merge as one file. pytest-cov retires; the parent measure is
  `coverage run` around the whole `fm check`, and the patch cascades
  through every child: task shells, the out-of-process pytest, its
  xdist workers.
- CI's Gate step runs under that parent measure; each leg combines
  its own parallel files and uploads one `.coverage.<os>-<python>`
  artifact. The aggregating `gate` job downloads all six, combines
  them, and enforces the floors once, on the union: the number that
  counts Windows junction branches and mac lines alike.
- `fm test` locally keeps a quick same-machine enforcement (the
  grace absorbs its platform bias); the combined CI number is the
  authoritative one, and the floors ratchet against it.
- **Acceptance:** the combined report shows the task-shell modules
  (`_quality`, `_submit` shells, `_ci_tasks`) with non-zero
  coverage from the gate's own run; one gate job enforces floors on
  the merged union and fails a deliberate floor raise above the
  measured union (raise, observe red, revert); the per-leg
  enforcement is gone from CI logs.

## Phase 10 — automatic changelogs and versioning

One engine for both: conventional commits drive the changelog entry
and the next version.

- git-cliff configuration ported from hse's template (cliff.toml per
  package), rendering a release's entries from the commits touching
  that package's path since its last tag.
- `fm release.prepare <path>` learns to run without a version:
  derive the bump from the commits (feat is minor, fix is patch, a
  breaking marker is major), stamp it, and write the generated
  entries under the new heading for the human to edit.
- **Acceptance:** on a branch with one `feat:` commit touching only
  the workshop, `uv run fm release.prepare packages/workshop`
  stamps the minor bump and a changelog entry naming that commit's
  subject; `release.verify` passes the result; a hand-passed version
  still wins.
- Gates 0.1.0: yes (the first real release should ride it).

## Phase 11 — the environment: variables, paths, launchers, agents

hse's instance environment, portable: `.repo.env` and
`.repo.env.local` through footman's env cascade, path management,
the shell and editor launchers in the project template, and the
agent environment they share.

- Scope drawn when the phase starts; hse's `setup/` and launcher
  files are the source.
- Gates 0.1.0: yes (template-shaping is cheapest before instances
  exist).

## Phase 12 — the branded runner

The option to replace `fm` in workflows and docs with a branded
footman name, as hse brands its own. A template question carries the
brand through rendered workflows; footman's branding hooks do the
rest.

- Gates 0.1.0: preferred but not blocking; it slips without harm.

## Phase 13 — the shared tool cache (post-0.1.0)

One tool cache between agents. Waits on toolroom moving into this
monorepo and on strongroom progress, both outside this plan's
control; recorded so the dependency is visible.

## Temporary, replaced by

| Temporary piece | Replaced by |
| --- | --- |
| root `tasks.py` still defining verbs after phase 2 | done: the plugin's tree serves every verb, and the file is the seeded one line (fixtures.record moved into forge's dev plugin) |
| GitHub-only rendered CI (phase 4) | done: the three forge variants render from contract values, phase 7 |
| hand-listed `content/` inventory | done as far as this plan goes: the content channel ships and materialises the inventory; the rendered docs toolchain stays post-0.1.0 (open 4) |
| `release-legs.yml` skipping absent accounts | done: all five legs live and green in one dispatch (run 33416377299) |

## Decision record

- 2026-08-31: plan opened on the bootstrap's exit criteria, all met
  by command: 0.1.0 on PyPI, the suite green four ways, the compose
  loop routine, the nightly shipped in its revised
  released-wheels-only form. `livery-workshop` checked free on PyPI
  the same day.
- 2026-08-31: the phase order follows the workshop-and-forge note's
  sequencing with one deliberate inversion: content (phase 3) lands
  before templates (phase 4), because the render gate wants the
  managed stub and materialised configuration to already exist as
  the things the template renders references to.

- 2026-08-31, phase 1 spellings: the plugin module is `_tasks.py`,
  addressed by the entry point, keeping the underscore rule
  (`footman_tasks.py` in the design note was hse's public spelling);
  and the surface gains one verb this phase, `fm layers`, because
  footman refuses to mount a plugin that registers nothing, and the
  layer walk deserves a voice more than a stub deserves existence.
  The layer host's functions (`layer_names`, `mount_layers`,
  `workspace_root`) are the package's public API from day one.

- 2026-08-31, phase 3 scoping: the checker configurations stay in the
  root `pyproject.toml` rather than moving into the content channel
  now. Four checkers' extend-and-discovery behaviours differ enough
  that the move belongs with the template rendering (phase 4 and
  later), where the rendered `pyproject` can reference materialised
  files deliberately; shipping fragments, skills, and hooks first
  keeps the phase day-sized. The stub's import order is guidance
  first (voice, then documentation rules), then the layer fragments,
  then `CLAUDE.project.md`, which always wins.

- 2026-08-31, the task-surface split (Willem: "The local forge
  compose ones should be this repo only. No user of the workshop
  would want them by default."). The workshop base layer keeps only
  what every instance wants. `forge.dev.*` moves to livery-forge as
  the `footman.tasks` entry point `livery.forge`, with the compose
  file shipped as a package resource (its project name is pinned in
  the file, so the move changes no running containers); a workspace
  gets those tasks only by listing `livery.forge` in its layers.
  `forge.fixtures.record` also lives in the plugin, registered only
  in forge's own source checkout: the tests directory it runs is
  derived from the module's location and a wheel install has none, so
  no consumer sees the task and the root `tasks.py` stays at its
  seeded one line (Willem's ruling; an earlier cut had put the
  recorder in `tasks.py`). The layering lint widens for exactly one
  subtree: `livery.forge._dev` may import footman and toolroom,
  because its only loader is footman's own `plugin()` and only a
  workshop workspace mounts layers; livery-forge still declares no
  dependency.
- 2026-08-31, instance-owned files (Willem's questions, resolved).
  `tasks.py` is classed like `CLAUDE.project.md`: seeded once by the
  template, never rewritten, so an instance growing its own tasks
  below the plugin line never conflicts with an update. Global task
  or guidance changes travel in the wheels, never in the seeded
  files. `fm sync` never runs git: a seeded file is left untracked
  for the human to commit. Sync maintains an instance and cannot
  create one; the bootstrap of a new repository (tasks.py,
  livery.toml, pyproject.toml) is the template phase's job, and its
  verb must be stated there.

- 2026-08-31, phase 5 shape. The verdict codes are hse's 10-17;
  exit 18 (required review) is dropped, because the frozen protocol
  carries no review surface, and a review-blocked pull request reads
  as 16 with the message saying what to check. The classifier probes
  conflicts locally (`git merge-tree`) before the arming state, since
  a conflict blocks the merge whatever the arming; behind is
  classified only after an armed grace, because without strict
  protection an armed behind head still merges. `ci.rerun` and
  `ci.cancel` key on the pull request's head sha, not the local one,
  so they work from a checkout that moved on. Arming is verified by
  read-back, never assumed from a 2xx, and an arm that merged on the
  spot is success, not a lost schedule.
- 2026-08-31, forge evidence from the first dogfood ship (PR #21,
  merged armed): a merge in flight consumes the schedule between the
  open-PR read and the arming read, so an armed merge can read as
  "green and not armed". The classifier re-reads the pull request
  before reporting disarmed or stalled; merged wins over any blocker
  derived from stale reads (regression-tested through a stub; fixed
  by PR #22, which the fixed verb shipped and followed to "merged").
- 2026-08-31, phase 7 shape. The scratch owner override is one env
  var, `LIVERY_FORGE_E2E_OWNER`, read by all three conformance
  drivers with their local defaults kept, so the cloud legs are the
  same suite pointed at the e2e accounts; the gitea.com leg mints a
  disposable act_runner per run through the API (capacity 8: held
  runs occupy slots) and removes it after, per the runbook's
  verified design. Cloud legs write `.forge.dev.env` with the cloud
  URL and token, which is the harness's existing seam. Cleanup lists
  and deletes scratch by prefix, runs on every verdict.
- 2026-08-31, the release story as it stands (Willem: record it).
  The github kind releases by trusted publishing to PyPI; the gitea
  and gitlab kinds publish by token (`UV_PUBLISH_TOKEN`) to the
  contract's `publish_index`; a workshop release also publishes the
  template snapshot. Not yet covered anywhere: attestation or
  signing, a non-PyPI index for the monorepo itself, and a portable
  token path on the github kind. `packages/workshop/docs/releases.md`
  states the same for readers.
- 2026-08-31, coverage grace (Willem: hse allowed a margin so a
  0.01% drop is not penalised). The gate fails only more than half a
  point below the floor; the floor stays the declared high-water
  mark and the grace absorbs measurement jitter, printed beside
  every verdict so the enforced numbers are the visible ones.
- 2026-08-31, phase 8 shape. The coverage bar lives in each
  package's own contract (`[qa] coverage_floor` in livery.toml),
  closing open item 3: the floor is part of what the package
  declares about itself, moves in the same review as the code, and
  the template seeds new packages at 100. Floors start at today's
  high water (forge 90, workshop 76). In `--affected` mode ty and
  pyrefly still check their configured whole (their runs cost
  seconds and pin the platform matrix), and the render gate is
  skipped because a package-scoped change cannot touch its inputs;
  everything else takes explicit paths.
- 2026-08-31, the legs gate forge releases only (Willem: live
  interaction should happen "when we actually change something in
  forge and decide to release"). release-legs verifies livery-forge's
  contract against real servers, and nothing else in the workspace
  can invalidate that contract, so the dispatch is required before a
  `packages/forge/v*` tag and skipped for other packages' releases.
  The remaining live interaction is free and development-bound:
  cassette recording when forge's exchanges change (local compose
  plus the github e2e org), and the daily submit verbs' API calls.
  gitlab.com compute, the one metered resource, is spent only by a
  forge release's legs dispatch, about 34 minutes each.
- 2026-08-31, the stalled grace was too tight (livery PR #35 merged
  25 seconds after `fm submit`'s follow declared 16): the green-and-
  armed grace is now eight polls, about two minutes at the default
  interval, before the watch calls an evaluation lost.
- 2026-08-31, what the first legs dispatch taught (run 33404116894,
  all five legs red; every failure became a fix and most a quirk
  entry). The held-run release is now the release-<sha> tag on every
  forge (the compose-exec file touch could never work off-machine):
  the gitea job polls the API with wget (the act_runner image has no
  curl) at GITHUB_API_URL, which required the compose ROOT_URL to
  say gitea:3000 rather than localhost; the gitlab job polls its
  checkout's origin (CI_REPOSITORY_URL carries the unreachable
  external_url) under workflow rules that admit tag pipelines never,
  LIVERY_WORKFLOW dispatches, and push pipelines only (`when:
  always` duplicated runs via MR pipelines). Current gitlab-ce
  demands a sha on merge, so merge_now and arm pin the head sha.
  Live runs delete scratch per scenario (gitea.com's quota trips on
  bursts; single creates pass, proven by probe). The github driver
  waits out workflow-indexing lag before returning a fresh repo and
  retries 5xx cancels. Gitea and gitlab cassettes re-recorded.
- 2026-08-31, the template source is the contract's call (Willem:
  forks and local modification at their own risk must be possible;
  "always keep the ease of use of consuming this development in hse
  in mind"). `[workspace] templates` in livery.toml: a directory
  relative to the root (the monorepo says `templates`) or a git URL,
  defaulting to the published artifact repository. The update wave
  writes the contract's value into the answers' `_src_path` before
  `copier update`, because that is where copier reads its source, so
  livery.toml stays the one authority. hse as a consumer either says
  nothing (stock) or points at its own fork or checkout.
- 2026-08-31, phase 6 shape. The wave's no-op is literal: nothing
  changed means no branch and no pull request, one line saying so
  (the plan's "no-op pull request" wording read as "no pull
  request"). `fm update` refreshes render via the applier where
  `templates/` lives in the workspace, and via `copier update` at
  the installed workshop's own tag everywhere else. The artifact
  repository is public (instances consume it without credentials;
  its whole content is already public in the monorepo), created with
  Willem's gh credentials along with the write deploy key and the
  `WORKSHOP_TEMPLATES_DEPLOY_KEY` secret, closing open item 1. A
  known gap, owned by the global create verb: `fm new.package` still
  requires a local `templates/`, so a wheel instance cannot render a
  member until the verb reads the artifact repository.
- 2026-08-31, the verb is `submit`, armed off by default (Willem:
  "ship is a little misnamed ... it's also verify the current state
  on CI and make sure it's on the remote"; "we need to keep both our
  audiences in mind"). The common human run publishes and verifies;
  landing is the `--armed` flag, one flag for an agent, opt-in for a
  person. The unarmed dogfood run was PR #24 (parked cleanly, exit
  0); the rename itself merges through `fm submit --armed`.
- 2026-08-31, parked is not an error (Willem: "if the prose even
  hints an error or problem, that's no bueno", nobody should have to
  learn the exit codes). A deliberately unarmed ship that reaches
  green finished its job: it prints where the PR is parked and what
  arms or merges it, and exits 0. Exit 11 is reserved for the
  surprising case, an armed ship whose schedule went missing. The
  codes signal deviations from what the verb was asked to do; the
  prose alone must carry the meaning. `fm status` and `fm ci.watch`
  keep answering 11 for a parked PR, with the same neutral prose.
- 2026-08-31, the title default (Willem: hse's recurring mis-title).
  A defaulted PR title is allowed only when it is unambiguous: on
  first open with the branch one commit ahead, that subject is the
  intent; more than one ahead refuses, lists the subjects, and asks
  for --title. Re-ships are untouched, where the default is already
  inert (it never rewrites the PR title or the squash subject).

## Open

1. Resolved 2026-08-31: `willemkokke/workshop-templates` exists
   (public) with the write deploy key and the
   `WORKSHOP_TEMPLATES_DEPLOY_KEY` secret on the monorepo.
2. Which skills and hooks ship in the wheel and which stay personal
   (design note's open 7). Proposal in phase 3: the three plan
   skills plus both guards ship; everything else waits for a second
   consumer. Owner: Willem, at phase 3 review.
3. Resolved 2026-08-31 at phase 8: the coverage bar lives in each
   package's `livery.toml` (`[qa] coverage_floor`). Per-package
   checker sets stay undivided until a package needs one.
4. The docs toolchain (per-package rendered sites) is not in this
   plan; it follows 0.1.0 as its own small plan unless Willem pulls
   it in. Owner: Willem.
5. The bootstrap plan's open item 7 (the three e2e accounts) gates
   phase 7's full acceptance. Owner: Willem, in progress
   (2026-08-31: accounts promised for 2026-09-01).
