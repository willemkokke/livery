# Bootstrapping livery: the forge first

Status: phase 2 built, 2026-08-31, awaiting Willem's acceptance, the
pull request, and the `packages/forge/v0.1.0` tag. The suite is green
four ways: FakeForge (three capability shapes), the Gitea 1.28
container, the GitLab CE container, and github.com scratch
repositories; 24 cassettes per real backend replay in under two
seconds with no network and no credential, no token on disk, and the
protocol is frozen at 0.1.0. The Gitea slice merged earlier as PR #4;
phase 1 as PR #3. The
protocols, the GitLab mapping (`packages/forge/docs/gitlab.md`), the
verified FakeForge with four fault modes, the shipped conformance
suite (25 scenarios, run against the fake in two capability shapes),
and the cassette layer are in; `fm check` green locally (67 passed, 4
capability-gated skips); three-OS proof follows the push. Phase 0 was
accepted by Willem, 2026-08-31, conditional on the basedpyright venv
fix passing CI; evidence: `fm check` green on three OSes and two
Pythons (run 33342483626); branch protection live;
`packages/forge/v0.0.1` verified and published by trusted publishing
(run 33343851374). Next: phase 2, the compose file and the three real
backends.

## The prompt (Willem)

> We need to set up a temporary development environment in the livery
> repository: skills and CLAUDE.md; running tests and releases; Python
> linting and formatting as per the footman repo, global for now; uv
> based, the only package to start with being `livery.forge`; docker
> compose images for Gitea and GitLab to spin up locally. Only once the
> forge is functional does it make sense to start the workshop and get
> involved with copier templates. This is off-the-cuff — push back if
> I'm forgetting anything or missing dependencies or room for
> improvement. Willem, 2026-08-30

## Pushback, taken and recorded

1. **GitLab stays, precisely because it is the odd one out** (Willem's
   counter-push, taken). Gitea is not a third data point: its API
   deliberately mirrors GitHub's shapes, so a protocol frozen against
   those two is frozen against one and a half. GitLab is the stress
   test: merge requests with per-project `iid`s, pipelines as the
   canonical check state rather than the check-run and commit-status
   duality, auto-merge as merge-when-pipeline-succeeds on the request
   itself, protected branches without named required contexts, groups
   and subgroups rather than a flat owner, and tier-gated features
   (approval rules, external status checks) that the capability system
   must express as "this forge, this tier, unsupported". So the GitLab
   *mapping* joins phase 1 as a paper pass over every protocol method,
   and the backend and its container join phase 2; the freeze requires
   all three. The costs are accepted and stated: the official image is
   x86-64-only (Rosetta here, minutes to boot, 8 GB and up, a
   long-lived dev service rather than per-test), and seeding it to a
   testable state (root password by environment, a PAT minted through
   `gitlab-rails runner`, a group and a project by API) is the
   heaviest of the three seed scripts.
3. **All three backends before the protocol freezes.** The protocol is
   derived from hse's Gitea client, and every backend it has not met
   may reshape it: Gitea (ported, against the local container), GitHub
   (the forge this repository runs on), and GitLab (the odd one out,
   above) all land in phase 2, and the protocol may move until all
   three pass the same conformance suite; then it is frozen.
4. **Three things the list did not have that harden immediately:**
   `livery.toml` at the root and in the package from day one (the
   contract is cheap now and every later tool discovers packages by it);
   the path tag grammar (`packages/forge/v0.0.1`) in the very first
   release workflow, never `v*`; and a placeholder release of
   `livery-forge` in phase 0, which reserves the PyPI name (checked free
   2026-08-29) and proves the whole release train before any code
   exists — the same defensive registration `livery` itself got.
5. **Skills: three, not a set.** hse's skills are studio-flavoured;
   adapting them all now is waste. Phase 0 carries `create-plan`,
   `execute-plan` and `phase-audit` (the discipline this very plan
   runs under), hand-adapted; the rest arrive as workshop content
   later. The post-edit ruff hook comes now (imports stripped between
   saves is a lesson already paid for); the session-env hook does not
   (there is no pinned tool store yet — plain uv is the environment).
6. **Release verification now, inline and small.** footman's
   tag-equals-pyproject-equals-`__version__`-equals-changelog check,
   generalised for path tags, lives as a step in the release workflow
   for now and is harvested by the workshop later. Not deferred:
   an unverified train teaches wrong habits from release one.
7. **Deliberately deferred to the workshop phase:** the docs site,
   coverage high-water marks, the affected engine, `ship`/`status`,
   materialised configuration, and everything copier. The temporary
   gate is plain and whole-repo; it will be slower than the eventual
   one and that is fine at one package.

## Rulings folded in on 2026-08-30

**The Gitea floor is 1.28-dev, and cancellation is first-class.**
Willem's ruling: Gitea's 1.28 development line gains workflow-run
cancellation, so the forge interface is designed against that floor and
`cancel_run` joins the checks group as a required protocol method,
beside rerun — not a capability afterthought. The endpoints are
documented in the [next/1.28-dev API docs][gitea-cancel]: `POST
/repos/{owner}/{repo}/actions/runs/{run}/cancel` ("cancel a workflow
run and its jobs", 409 when the run is already terminal) and a
[`force-cancel`][gitea-force-cancel] sibling that marks jobs cancelled
immediately and discards later runner reports, for runs whose runners
stopped answering. That pair mirrors GitHub exactly (`cancel` and
`force-cancel`); GitLab has plain pipeline cancel only. So the verb is
`cancel_run(run, *, force=False)`: required everywhere, with `force`
the protocol's first genuine capability probe — GitHub and Gitea map it
to their force endpoints, GitLab's backend declines it by name. The
odd one out is already earning its keep in the first verb this ruling
adds. Consequences:

[gitea-cancel]: https://docs.gitea.com/api/next/operations/cancel-workflow-run/
[gitea-force-cancel]: https://docs.gitea.com/api/next/operations/force-cancel-workflow-run/ the compose file pins the
Gitea 1.28 nightly image; the backend probes the server version and an older
server raises `Unsupported`, naming the version, so the method is
first-class in the interface and honest at runtime (the container check
in phase 2 is verification of the documented behaviour, not
discovery); and the studio's
Gitea upgrades to 1.28 or later before the hse switch, which is now a
named precondition of that path. What cancellation buys, from hse's own
scars: superseded pushes cancel their stale runs, a dispatch storm can
be withdrawn, and a starving queue can be relieved by a verb instead of
a wait.

**The issue surface is ruled inadequate as inherited.** Willem,
2026-08-30: the current interface can't query issue text, nor provide
issue text when creating one. hse's calls grew around assignment and
labels; this note's W10 (the body is the work order) and W11 (dedup by
searching for the marker text before creating) show what that misses.
The draft protocol carries `issue.create(title, body, labels,
assignee)`, `issue.get` returning the full body, and
`issue.search(text)` — all three forges have the endpoints; only hse's
wrapper was thin.

## Process rules, learned the expensive way

Willem asked what in the development process we have hit again and
again that this build should fix. Mined from hse's devlog and this
project's own planning trail, each with its provenance; additions
welcome, and these belong in the household's `CLAUDE.md` fragment once
the workshop materialises it:

1. **Nothing on the merge path waits on anything outside the
   repository.** hse's premerge leg starved on an 1800-second wait for
   a queue it shared; its e2e held forty runner-minutes hostage. The
   gate here is local-only from day one; everything that touches a
   forge is a fixture replay on the merge path and a scheduled leg off
   it.
2. **A quirk without a fake fault is a debt.** Every forge quirk
   discovered (a lost schedule, a 405 window, a wedged queue) gets a
   `FakeForge` fault mode and a regression test the same day it is
   understood, plus a line in the backend's quirks list. hse paid for
   its forge-evidence list in twenty-minute e2e runs; here each entry
   reproduces in milliseconds forever.
3. **Failure reasons are printed verbatim, never read as booleans.**
   hse lost eight CI runs phantom-hunting a stamp writer whose reason
   string was being truth-tested — a one-line fix found late. Glue
   code gets footman's taught-error discipline: say what failed, in
   the words of the thing that failed.
4. **Every workflow verb is idempotent and safe to re-run.** hse's
   release-recovery-traps note exists because re-running a
   half-finished release used to be dangerous. `ship`, `release`,
   `update`, the artifact publication: re-running any of them is the
   recovery procedure, and a verb that cannot promise that does not
   ship.
5. **Phases land daily, gate-green, mergeable alone.** hse's plan
   format proved it: nine phases, each merged on its own. No branch
   lives longer than a phase; no phase is bigger than a day or two.
6. **Every plan carries ground-truth contracts.** The monorepo plan's
   "do not violate" list demonstrably prevented regressions across
   nine phases. Plans in this repository carry the same section, and a
   phase that would break one stops for the human.
7. **Notes describe the current state; decisions carry dates in a
   record.** The planning trail for this project went through enough
   reversals that its main note had to be consolidated once already.
   Design churn belongs before build; once code exists, the note is
   updated in the same pull request as the change, or it is wrong.
8. **Dogfood the released thing on a schedule, the in-tree thing by
   default.** hse's e2e only ever tested in-tree pairings, the one
   configuration no consumer runs; the nightly here installs the
   released wheels and replays the suites against them.

## Phase 0 — the repository becomes the workspace

The placeholder becomes the monorepo's skeleton. One PR (or a short
series), gate green at the end.

- **Workspace shape.** Root `pyproject.toml` becomes virtual
  (`[tool.uv] package = false`, `[tool.uv.workspace] members =
  ["packages/*"]`, `[tool.uv.sources]` for first-party names); the
  placeholder `src/` goes. `packages/forge/` is born: `pyproject.toml`
  (uv_build; `module-name = "livery.forge"` for the PEP 420 namespace —
  no `livery/__init__.py` anywhere; fall back to hatchling if uv_build
  fights the namespace), `src/livery/forge/`, `tests/`, `CHANGELOG.md`,
  `README.md`, version `0.0.1` in `pyproject.toml` and `__version__`.
- **Contracts.** Root `livery.toml` (`[workspace]` with
  `layers = ["livery.workshop"]` as a forward declaration, `[forge]
  kind = "github", owner = "willemkokke"`, `[ci] runners`) and
  `packages/forge/livery.toml` (`type = "python"`, `name =
  "livery-forge"`, verbs). A ten-line test asserts every `packages/*`
  has one and that `livery.forge` imports only stdlib — the layering
  lint's seed.
- **The dev loop.** `tasks.py` with footman as the dev dependency:
  `check` (ruff format --check, ruff check, basedpyright, pytest — in
  parallel, exit code the verdict), `format`, `lint`, `typecheck`,
  `test`, and a `forge.dev` group stubbed for phase 2. Ruff and
  basedpyright configuration cribbed verbatim from footman's
  `pyproject.toml`, as the prompt says: footman's rules, global, for
  now.
- **CLAUDE.md and the agent loop.** A temporary root `CLAUDE.md`,
  marked as replaced-by-the-workshop: the gate command, the worktree
  rule for agent sessions, commits and identity (SSH-signed, the
  personal email, conventional prefixes, no attribution trailers), the
  notes convention, plain words, and the layering rule. `.claude/`
  with the three skills and a `PostToolUse` hook running
  `ruff check --fix` on edited Python files.
- **CI and the train.** `.github/workflows/ci.yml`: three OS ×
  Python 3.11 and 3.14 (the floor and the newest; the full ladder can
  wait), one `gate` job as the only required check; branch protection
  on `main` with `gate`, squash-only, delete-on-merge, auto-merge
  enabled. `release.yml` replaced: trigger `packages/*/v*`, the
  package read from the tag's path, the inline verification step, build
  with uv, publish via trusted publishing (environment `pypi`,
  registered on PyPI for `livery-forge`).
- **The docs seed.** `docs/` at the root and `packages/forge/docs/` in
  the package: plain markdown, deliberately unrendered — `index.md`
  (what the package is and how to hold it), `protocol.md` (grows with
  phase 1), `quirks.md` (process rule 2's address: every backend quirk
  discovered gets its line here the day it gets its fake fault).
  Temporary in form, not in content: once the workshop is functional
  its docs toolchain renders these same files per package into the
  site, so they are written as the seed of that site, never as scratch.

- **Acceptance:** `uv run fm check` green on three OS for the empty
  package; `packages/forge/v0.0.1` tagged, verified, published — the
  name reserved and the train proven; branch protection live.
  *(2026-08-31: everything local is done and green — package,
  contracts, dev loop, docs seed, CLAUDE.md + skills + hook, both
  workflows; `uv build --package livery-forge` produces the wheel with
  no `livery/__init__.py` in it. The publish, protection, and
  three-OS CI runs follow the push.)*

## Phase 1 — the protocol and the verified fake

- The `Forge` and `Repository` protocols drafted from hse's call
  inventory (the table in the workshop note) and judged against the
  development workflows note (`notes/20260830-development-workflows.md`) — every
  forge-lane touch in this note gets a verb, every verb keeps an
  workflows note touch, and this note's surfaced gaps (`pr.comment` / `issue.comment`, `release.get` by tag, abort-path branch deletion, and full-text issues — `issue.create` with a body, `issue.get` returning it, `issue.search` over text, per the ruling below) enter the draft: pull requests, arming, checks — with `cancel_run`
  first-class beside rerun, per the 1.28 ruling — releases, issues,
  repository administration, identity. The
  `Registry` protocol beside them, one method.
- **The GitLab mapping pass**: for every draft method, the GitLab
  endpoint and semantics named on paper before anything is
  implemented — `iid` versus number, pipeline versus combined status,
  merge-when-pipeline-succeeds versus scheduled merge, group paths as
  owners, and which operations are tier-gated and therefore
  capability-declared. A day of reading that prevents the corner.
- `livery.forge.testing.FakeForge`: answers from a table, injects the
  quirks deterministically (lost auto-merge schedule, 405 window,
  wedged status queue, slow combined status).
- The conformance suite as data plus a harness, written against the
  protocol, run against the fake — the same suite the real backends
  will run.
- The record/replay HTTP fixture layer over `urllib`: cassettes with
  token scrubbing, replayed in CI, re-recorded by a task. toolroom's
  refresh wants this too; it is written once, here.
- **Acceptance:** the suite green against `FakeForge` on three OS; a
  documented fixture format; quirk injection demonstrated by tests
  that fail without the fake's fault handling.
  *(2026-08-31: built and green locally. The protocols and types are
  the public surface of `livery.forge`; the conformance suite ships
  as `livery.forge.testing.SCENARIOS` over a per-backend
  `ForgeDriver`, and runs against the fake full-capability and
  GitLab-shaped; the four fault modes are injected by
  `livery.forge.testing.Faults`, each with its regression test and
  its `docs/quirks.md` line; the fixture format is
  `packages/forge/docs/fixtures.md`; the GitLab mapping is
  `packages/forge/docs/gitlab.md`. The three-OS legs run on the pull
  request.)*

## Phase 2 — two real backends, then the freeze

- **Compose, and the seeds.** `compose.yaml` with `gitea` pinned to the
  1.28 nightly image (the floor ruling) plus `act_runner` (seeded on boot through Gitea's CLI and API: install
  lock, admin user, token, an org, a repository, branch protection,
  runner registration) and `gitlab` (`platform: linux/amd64`;
  long-lived, started once and kept; seeded by the heavier script:
  root password by environment, a PAT through `gitlab-rails runner`, a
  group, a project, protection). `fm forge.dev up / seed / down`, with
  `--profile` to bring up one forge or all.
- **The Gitea backend**, ported from `hse.sdk.gitea` with `_forge.py`'s
  client precedence and token-scoping rule, developed against
  `http://localhost:3000`; fixtures recorded from the container.
- **The GitHub backend**: REST plus the one GraphQL mutation auto-merge
  needs; token resolution `GITHUB_TOKEN` → `gh auth token`; fixtures
  recorded against a scratch repository (an org or a `willemkokke`
  scratch — open).
- **The GitLab backend**: REST v4 against the local container, the
  mapping pass made real; merge-request vocabulary translated at the
  backend boundary; tier-gated operations declared through
  `forge.supports(...)` so CE tests skip what CE cannot do; fixtures
  recorded from the container.
- All three backends run the identical conformance suite; the protocol
  may still move; when all three pass, **the protocol freezes** and the
  drafts in phase 1 become the contract.
- **Acceptance:** one suite green four ways (fake, Gitea container,
  GitLab container, GitHub scratch); `cancel_run` — plain and forced —
  exercised against the 1.28 container by the suite, and GitLab's
  declined `force` asserted;
  recorded replays green in CI on three OS with no network;
  `livery-forge 0.1.0` released through the train.
  *(2026-08-31: green four ways locally — 166 passed, 18
  capability-gated skips on full replay; `cancel_run` plain and
  forced recorded against Gitea 1.28.0+dev and GitHub, GitLab's
  declined `force` asserted by `cancel-run-force-declined`; the
  freeze is declared and the version is 0.1.0. The three-OS replay
  proof and the release tag follow the push.)*

## Phase 3 — the nightly, and the door to the workshop

- A nightly workflow: the replays, plus live legs against a fresh Gitea
  container, the GitHub scratch repository, and a gitlab.com scratch
  group (the container is the dev loop; gitlab.com is where drift
  happens) — drift detection, off every merge path.
- **Entry criteria for the workshop phase** (a separate plan, in this
  repository, when reached): `livery-forge` 0.1.0 on PyPI with all
  three backends conformant, the fixture harness stable, and the
  compose loop routine. Only then copier, templates, the layer host
  and the migration of footman and toolroom — per the planning hub's
  notes.

## The temporary environment, and what replaces it

| Temporary piece | Replaced by |
| --- | --- |
| plain markdown `docs/` per package | the workshop's docs toolchain, rendering the same files into the per-package site |
| hand-written `CLAUDE.md` | the workshop's managed stub + materialised fragments |
| the three copied skills, the post-edit hook | the workshop's `content/` on `fm sync` |
| ruff/basedpyright blocks cribbed from footman | workshop-shipped configurations, `extend`-chained |
| hand-written `tasks.py` tasks | `plugin("livery.workshop")` and the plugin's task tree |
| hand-written `ci.yml` / `release.yml` | template-rendered workflows (`templates/` at `HEAD`, render gate) |
| the inline release verification step | `fm release.verify` |
| plain uv environment | the tool store over strongroom, `fm sync` |
| the ten-line contract test | the layering lint and per-package CI contract |


## Decision record

- 2026-08-31: hse's interaction-voice and documentation-standards
  guidance is imported at `.claude/guidance/`, @-imported by CLAUDE.md,
  from day one (Willem's ruling). The workflows note is renamed:
  "atlas" was a coined metaphor, the file is now
  `notes/20260830-development-workflows.md`. Existing notes are brought
  to voice as they are next touched.
- 2026-08-31: the history mimics a future graduation (Willem's
  ruling): the skeleton lands as a `setup` branch and pull request,
  merged through branch protection by the armed squash merge that the
  workshop's project-birth workflow will one day perform itself.
- 2026-08-31: the first squash merge is rolled back (Willem: phase 0
  was not yet accepted). `setup` is restored at its original commit,
  main is force-pushed to the reservation commit under a briefly
  lifted protection, and the accepted squash merge is a later pull
  request. Pull request #1 stays in the forge's history.
- 2026-08-31: no archive or graduation tags, in livery or copied from
  hse: hse has none (its "graduation" is template-payload docs being
  removed; its "archive" is an installer kind), and the ancestor idea,
  landmark tags, is one we removed. Tags are release tags only. The
  setup squash commit is the addressable pre-graduation point.
- 2026-08-31: typing arrives in final form (Willem: no clean-up passes
  later). footman's four-checker gate (basedpyright, mypy strict per
  platform, ty, pyrefly), `fm typecomplete` requiring a 100%
  type-complete public API, the public/private rule (underscore
  modules, `__all__` re-exports) pinned by a test, and Google-only
  docstrings with no RST anywhere, enforced by ruff's pydocstyle.
  Docstrings follow the voice guidance; objects are named by full
  import path for cross-package API docs. All of it stated in
  CLAUDE.md.
- 2026-08-31, correcting the archive-tag line above: hse decided the
  other way and shipped it. `_tag_archive_setup` in the devkit's
  release driver cuts an annotated `archive/setup` tag at the setup PR
  head before the first squash merge: idempotent, pushed alone,
  branch-prefix-free so a rename cannot orphan it, CI-clean because
  tag guards skip it, and in its words the only durable record of the
  setup history that squash plus auto-delete would erase. The earlier
  search missed the devkit's own devlog and source. Adopted for
  livery: the accepted graduation cuts `archive/setup` at the setup
  head, pushes it, then squash-merges; the release grammar keeps CI
  clean (`packages/*/v*` triggers, `archive/setup` does not); the
  workshop ports this with the first-release flow.
- 2026-08-31, phase 1 drafting rulings: verb spellings refined from
  the workflows note's draft: `pr.arm_state` is `pr.is_armed`,
  `checks.run_jobs` is `checks.jobs`, `checks.rerun_failed` /
  `checks.rerun` collapse into `checks.rerun(run, *,
  failed_only=True)`, and `pr.find_by_head_sha` stays beside
  `pr.find_by_head` because a merged PR's head branch may be cleared
  while the sha persists (the workflows note is updated in the same
  change). Labels are names everywhere, ensured by `repo.configure`,
  and no separate label verbs ship. Listings are complete or they
  raise: no truncated-result type in the protocol (hse's
  `PagedList.truncated` lesson hardened into the contract). Pull
  request and issue numbers are separate spaces, as GitLab keeps them,
  and the fake models the odd one out.
- 2026-08-31: the conformance suite ships in the wheel
  (`livery.forge.testing.SCENARIOS` over a per-backend `ForgeDriver`),
  so the phase 2 backends and any consumer run the identical suite;
  the repository's harness is a thin parametrisation. The cassette
  re-record task is deferred to phase 2 with the first recorded
  cassette: nothing exists to re-record until a backend does, and the
  task belongs beside the containers it records from.
- 2026-08-31, phase 2 driver-seam rework: `finish_run(run, conclusion)`
  was not implementable by a real backend, because real CI decides its
  verdict from the pushed commit, not from a later call. The driver
  contract is now outcome-at-push (`Outcome`: success, failure, or a
  held `hang` released to success by `settle` or ended by
  `cancel_run`), with `settle` and `await_run` as the only blocking
  points; the fake and every scenario updated in the same change. The
  seeded dispatchable workflow's name, `conf.yml`, is part of the
  driver contract.
- 2026-08-31: GitHub fixtures record against scratch repositories
  under `willemkokke` (Willem's ruling on open item 1): zero setup and
  reversible; org-scoped behaviour stays untested and unused by the
  protocol.
- 2026-08-31, container facts, paid for in the first live run: Gitea's
  nightly images live at `docker.gitea.com` (Docker Hub carries no
  nightly tag), and the act_runner runs with `capacity: 8`, because a
  held conformance run occupies a slot and a one-slot runner deadlocks
  the suite. `fm forge.fixtures.record` is the re-record task.
- 2026-08-31, the GitLab image is architecture-dependent (Willem's
  ruling): `fm forge.dev.up` detects the machine and selects
  `gitlab/gitlab-ce` on amd64 and `yrzr/gitlab-ce-arm64v8` on arm64
  (GitLab's own arm64 omnibus packages, community-wrapped), an
  explicit `LIVERY_GITLAB_IMAGE` beating detection. This serves macOS,
  Windows, and Linux on both architectures; the earlier Rosetta cost
  in the pushback section applies only to the amd64-image-on-arm case
  the detection now avoids. Known costs of the community image, both
  accepted for a dev-only container: third-party packaging (mitigate
  by digest pin, with open item 3) and possible release lag (the
  nightly against gitlab.com is the drift detector). In practice the
  arm image booted healthy in about a minute.
- 2026-08-31, `ci_secrets` joins the capability vocabulary: GitHub's
  secrets API demands libsodium sealed-box encryption, which a
  stdlib-only backend cannot provide, and no workflow stores a secret
  on GitHub because trusted publishing replaces tokens there (the
  workflows note's W1). The GitHub backend declines by name; Gitea
  and GitLab support it. The required-context spelling is driver rig
  knowledge (`required_context()`): Gitea spells the seeded check
  "conf / gate (push)", GitHub "gate".
- 2026-08-31, the GitLab parallel failures, run to ground rather than
  serialised away (Willem's instruction): (1) gitlab-runner defaults
  to `concurrent = 1`, so a held job starved every pipeline; the seed
  now sets 8, like act_runner's capacity. (2) Project deletion is
  asynchronous and the path stays reserved, so delete-then-recreate
  races sidekiq; the driver renames leftovers to a corpse path first,
  which is synchronous. (3) What remains is capacity: one single-node
  CE absorbs about four concurrent writers before its own internals
  time out (Gitaly "4:Deadline Exceeded"), so live and record runs
  cap at four workers while merge-path replay stays fully parallel.
  The backend's client timeout is 120s so "slow" stays
  distinguishable from "unreachable".
- 2026-08-31, the conformance driver grows three observation
  primitives the asynchronous forges demanded: `await_mergeable` (the
  405 window until the mergeability recompute and pipeline
  association land), `await_merged` (a scheduled merge and its branch
  deletion are performed by the forge and only ever observed), and
  settle-after-cancel (cancellation is asynchronous). The runs
  listing asserts descending id order, not push order, because
  pipeline creation order is not push order under load.
- 2026-08-31, GitHub secrets ride an optional extra (Willem's
  ruling): `livery-forge[github-secrets]` installs PyNaCl, the
  repository public key comes from the API, and the backend seals
  values lazily, so `supports("ci_secrets")` answers per install and
  the stdlib-only rule is restated as "stdlib-only at module import
  time, one declared lazy extra". Motivation: trusted publishing
  covers GitHub and GitLab but not Gitea, so a portable release train
  needs the traditional token publish story too, and that story needs
  secrets on every forge; the token train itself is workshop-phase
  release work.
- 2026-08-31, github.com's eventual consistency joins the driver
  contract: issue listings run about nine seconds behind writes
  (measured), so `await_issue` bounds every create-then-list read;
  the direct get is not a listing and stays immediate. Scratch
  conformance repositories are public, because branch protection on
  private repositories needs a paid plan; they carry a
  safe-to-delete description and the `livery-forge-conf-` prefix.
  The held run's release signal on GitHub is a `release-<sha>` tag
  the job polls for with its own token, created through the recorded
  opener so replay stays deterministic.
- 2026-08-31, `merge_now` is idempotent (process rule 4 applied to the
  contract): GitHub answers 200 when merging an already merged pull
  request, Gitea and GitLab answer 405, and re-running a verb being
  its recovery procedure decides the tie toward GitHub's shape. The
  other backends absorb the refusal after verifying the pull request
  really merged; the scenario asserts the re-run succeeds.
- 2026-08-31, three GitHub Actions facts, paid for in the first live
  runs: a push workflow triggers for tag pushes (the seeded workflow
  filters on branches), `GITHUB_TOKEN` reaches a run step's shell
  only through an explicit `env:` block, and run creation for a push
  can be silently dropped under load, so the driver's push verifies
  its run is listed and re-pushes, attempt-counted for replay.
- 2026-08-31, the protocol freeze: all three backends and the fake
  pass the identical suite, so per the phase 2 contract the drafts
  are now the contract and a verb change is a compatibility event.
  Scratch repositories were deleted after the final recording (23).
- 2026-08-31, recording refuses redirects like the live clients do:
  the recorder's default inner opener had been urllib's stock opener,
  which forwards the Authorization header to a redirect's location;
  GitHub's signed log URLs surfaced it as Azure 401s. The refused
  redirect's Location now travels in the cassette so the one
  deliberate follow (job logs, bare) replays too, and 3xx replays
  raise like every recorded refusal. Sealed-box secret bodies are
  recorded as VOLATILE (an ephemeral key never encrypts the same
  bytes twice) and match by method and URL.
- 2026-08-31, server-minted secrets join the scrubbing model: GitHub's
  push protection caught GitLab's per-project `runners_token` inside
  the recorded project JSON, a secret no caller-supplied secret list
  can know in advance. The recorder now takes `scrub_fields`, JSON
  field names whose string values are redacted in every stored
  response body, and the recorded cassettes were rewritten through the
  same function before anything left the machine.
- 2026-08-31, the quirks rule interpreted for backend-level quirks: a
  quirk the backend absorbs at its own boundary (GitLab's
  marked-for-deletion dance, the moved-path redirect route) has no
  surface a FakeForge fault mode could reproduce, so its millisecond
  regression is the recorded cassette that replays the server's
  refusal verbatim; `docs/quirks.md` records which reproduction each
  quirk has.

- 2026-08-31: footman's `hooks.pre-bash` guard is ported into the dev
  loop and wired as a `PreToolUse` hook (Willem's ask, after the piped
  gate incident). It carries both of footman's guards: the pipe guard
  (a footman command piped into tail/head is refused, because the pipe
  replaces the verdict exit code) and the conflicting-push guard (a
  branch that conflicts with origin/main is refused before the push
  can open a CI-less pull request). Both are pinned by
  `tests/test_agent_hooks.py` and replaced by the workshop's content
  channel later, per the replaced-by table.

## Open

1. footman's `check` caching reported green after a uv.lock refresh
   bumped ty to 0.0.75, while a cold CI run failed on the new ty's
   class-scope annotation resolution (and on ruff format drift). The
   cache key misses lockfile-driven tool changes; needs a footman
   issue, and until then a lockfile change warrants a cold run.
2. The CI matrix's breadth now (floor + newest) versus footman's full
   ladder; widen when the forge grows platform-sensitive code.
3. Whether strongroom's step 0 (the spec and vectors, per the store
   note) starts in parallel with phase 1 or waits for forge 0.1.0.
4. The Gitea and GitLab containers' version pins, and how often each
   tracks upstream.
5. Which GitLab tier the capability registry models beyond CE: the
   container is CE, gitlab.com free differs again, and approval rules
   and external status checks are paid.
6. Whether the placeholder `livery` distribution becomes the
   meta-package now or at the workshop phase.
7. PyPI namespace grant for `livery-`: PEP 752 was accepted 2026-06-29 and grants are organization-only, so create the `livery` community organization on PyPI, then apply for the prefix grant when the application form is open (PEP 755). Until the grant lands, the prefix is first-come-first-served.
