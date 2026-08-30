# Bootstrapping livery: the forge first

Status: phase 0 in progress on the `setup` branch, 2026-08-31. The
first CI run already proved the gate green on three OSes and the armed
squash merge fired, but the merge was rolled back: main is the
reservation commit again, and the squash merge lands when phase 0
acceptance is complete. Remaining: the PyPI pending publisher, the
`packages/forge/v0.0.1` tag, the accepted merge.

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
2. **All three backends before the protocol freezes.** The protocol is
   derived from hse's Gitea client, and every backend it has not met
   may reshape it: Gitea (ported, against the local container), GitHub
   (the forge this repository runs on), and GitLab (the odd one out,
   above) all land in phase 2, and the protocol may move until all
   three pass the same conformance suite; then it is frozen.
3. **Three things the list did not have that harden immediately:**
   `livery.toml` at the root and in the package from day one (the
   contract is cheap now and every later tool discovers packages by it);
   the path tag grammar (`packages/forge/v0.0.1`) in the very first
   release workflow, never `v*`; and a placeholder release of
   `livery-forge` in phase 0, which reserves the PyPI name (checked free
   2026-08-29) and proves the whole release train before any code
   exists — the same defensive registration `livery` itself got.
4. **Skills: three, not a set.** hse's skills are studio-flavoured;
   adapting them all now is waste. Phase 0 carries `create-plan`,
   `execute-plan` and `phase-audit` (the discipline this very plan
   runs under), hand-adapted; the rest arrive as workshop content
   later. The post-edit ruff hook comes now (imports stripped between
   saves is a lesson already paid for); the session-env hook does not
   (there is no pinned tool store yet — plain uv is the environment).
5. **Release verification now, inline and small.** footman's
   tag-equals-pyproject-equals-`__version__`-equals-changelog check,
   generalised for path tags, lives as a step in the release workflow
   for now and is harvested by the workshop later. Not deferred:
   an unverified train teaches wrong habits from release one.
6. **Deliberately deferred to the workshop phase:** the docs site,
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

## Open

1. GitHub fixtures: a test organisation or a scratch repository under
   `willemkokke`.
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
