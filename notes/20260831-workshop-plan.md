# Building the workshop

Status: phase 5 built, 2026-08-31, shipped by its own verb: this
phase's pull request is the acceptance run for `fm ship --armed`.
W3 is a callable flow on the frozen protocol (gate, closes guards,
disarm-before-push, find-or-open, the arming ladder, the classified
verdict, self-heal on 10 and 17, `workflow.abort` and
`workflow.merge-now`), with `fm status`, `fm ci.rerun/watch/cancel`,
and `fm doctor` beside it, all tested in-process on FakeForge with a
real temporary git repository. Phases 1-4 shipped the same day
(PRs #12, #13, #17, #20), the task-surface split between them
(PRs #18, #19). The bootstrap
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

- `ship`: the whole act as one idempotent verb, ported from hse's
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
- **Acceptance:** `uv run pytest packages/workshop/tests -k ship`
  green, including a test per fault mode and a re-entry test per
  verb; `uv run fm ship --help` documents the surface; the livery
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
  run on the monorepo itself is a no-op pull request when nothing
  changed.

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

## Temporary, replaced by

| Temporary piece | Replaced by |
| --- | --- |
| root `tasks.py` still defining verbs after phase 2 | the plugin's task tree, phase by phase, one line left at phase 8 |
| GitHub-only rendered CI (phase 4) | the three forge variants, phase 7 |
| hand-listed `content/` inventory | the docs channel and the workshop's own docs toolchain, after 0.1.0 (open 4) |
| `release-legs.yml` skipping absent accounts | all legs live once open item 7 of the bootstrap plan closes |

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

## Open

1. The `workshop-templates` artifact repository: name confirmed as
   `willemkokke/workshop-templates`? Needs creating plus a deploy
   key secret before phase 6. Owner: Willem.
2. Which skills and hooks ship in the wheel and which stay personal
   (design note's open 7). Proposal in phase 3: the three plan
   skills plus both guards ship; everything else waits for a second
   consumer. Owner: Willem, at phase 3 review.
3. Where the coverage bar and per-package checker set live:
   `livery.toml` or workshop configuration (design note's open 4).
   Decide at phase 8, where coverage lands. Owner: the phase.
4. The docs toolchain (per-package rendered sites) is not in this
   plan; it follows 0.1.0 as its own small plan unless Willem pulls
   it in. Owner: Willem.
5. The bootstrap plan's open item 7 (the three e2e accounts) gates
   phase 7's full acceptance. Owner: Willem, in progress
   (2026-08-31: accounts promised for 2026-09-01).
