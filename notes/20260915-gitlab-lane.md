# GitLab, a first-class citizen again: the loop's GitLab lane

Status: ruled 2026-09-15. Phase 1 landed 2026-09-15 (livery#598); phase 2
in progress (livery#600), its live pass waiting on the `gitlab` hosts
entry; phases 3 to 5 not started.
Closes the two open lines of [CI declared, contributed, and dispatched on
command][ci-plan]: the live GitLab pipeline through `ci.run`, and the
schedules reconcile creating a real pipeline schedule.

## What this is

`fm ci.e2e --forge=gitlab`: the same one-command loop that proves the
CI and release story on the local Gitea, run against the local GitLab.
The rendered shells are the same declarations since the CI plan's
phase 3, so the lane exercises them rather than a second hand-written
set. Where the two forges differ (a pipeline in place of a workflow
run, a merge request in place of a pull request, a project registry in
place of an owner's), the difference lives in the forge backend or the
renderer, never in the loop.

## Where things stand

Verified against the tree at `e3407a3` on 2026-09-15.

- `fm forge.dev.up` brings up GitLab CE with a shell-executor
  `gitlab-runner` registered against `http://gitlab:8929/`, the compose
  service name, and seeds an admin token into the shared env file as
  `GITLAB_URL` and `GITLAB_TOKEN`. No project is seeded.
- The loop (`_e2e.py`) refuses `--forge=gitlab` by name in `_dev_forge`.
  Its shape is Gitea's in these places: `ALIAS_URL`, `LOOP_INDEX` and
  `LOOP_PUBLISH` name Gitea's compose host and its owner-level package
  registry; `_require_host_alias` teaches the `gitea` hosts entry;
  `provision` sets three Actions secrets; `_authenticate_remote` writes
  Gitea's credential; `_completed_run`, `_watch` and `_watch_latest`
  select runs by workflow file name; `_merge_setup` merges a pull request;
  `_release_act` probes Gitea's simple index.
- The GitLab backend declines `registry_url` for every kind: its PyPI
  registry is addressed by project id, so the contract declares it.
- A GitLab pipeline names its workflow only when dispatched
  (`workflow: name: $FORGE_WORKFLOW`); a merge request or push pipeline
  is unnamed, so `point_runs` reads it by event alone and the loop's
  file-name filters would match nothing.
- `run_context` keeps `CI_PIPELINE_SOURCE` verbatim, so a merge request
  pipeline is the event `merge_request_event` and `ci_affected_base`,
  which narrows on `pull_request` alone, pays the full gate on it. The
  scoped, prose and tests legs the loop proves depend on the narrowing.
- The state store pushes through the checkout's remote. On GitLab the
  job token cannot push, and only the rendered `publish` job rewrites
  origin with `GITLAB_PUSH_TOKEN`; the `check`, `gate` and `deploy` jobs,
  which write the store, do not.
- The Gitea act_runner runs on an image with the native member's
  toolchain (`runner.Dockerfile`); the `gitlab-runner` container is the
  stock image.
- GitLab's schedules capability, `ci.dispatch` on GitLab, and
  `workflow.release.dispatch`'s API-created pipeline are written and
  proven by recorded scenarios and pins, never live.

## Sequencing

Phase 1 waits on the plan being ruled and touches no container. Phases
2 to 5 run in order, each a pass of the lane reaching further. Both
lanes run on this machine, one pass at a time.

Open issues this plan folds in, each closed by the phase that names it:
livery#585 (phase 1), livery#590, livery#591 and livery#495 (phase 2),
livery#299 (phase 3). livery#287 is superseded: the file it reports is
retired by the CI plan.

## Ground-truth contracts (do not violate)

1. **One loop verb, one act sequence, both forges.** `fm ci.e2e
   --forge=<kind>` runs the same acts in the same order on either forge.
   A step a forge cannot do refuses by name; nothing skips silently.
2. **A forge's shape lives in its backend or its renderer, never in the
   loop.** The loop speaks the protocol and the workshop's verbs. A
   branch on the forge kind inside `_e2e.py` is a missing seam.
3. **Every GitLab pipeline names its workflow.** One reader of runs
   serves both forges, in the loop and in `ci.status`, `ci.logs` and
   `ci.dispatch`.
4. **A merge request pipeline is a pull request run.** The verbs read
   one event vocabulary; the narrowing, the record's base and the
   title check see no forge.
5. **The lane is live.** No cassette stands in for the local GitLab; a
   pass proves its forge alone.
6. **Secrets are masked variables the seed minted.** Nothing is typed
   by hand; a token the loop needs is minted through the API at
   provisioning and written where the cascade reads it.
7. **Nothing on the merge path waits on anything outside the
   repository.** The local runner is the fleet; gitlab.com's shared
   runners are never used.

## Phases

### Phase 1: one event vocabulary and named pipelines

Landed 2026-09-15 as livery#598. Evidence:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py
  packages/workshop/tests/test_workshop_state.py`: passes, the sources the
  map does not name and a GitHub run first.
- `uv run fm forge.fixtures.record --scenario=schedules-declined
  --backend=gitea`: exit 0, one scenario recorded through the verb, the
  cassette unchanged; livery#585 closes.
- `uv run fm check --fix`: exit 0.

Deliverables:

- The rendered GitLab document names every pipeline: the workflow rules
  set `FORGE_WORKFLOW` to `ci.yml` for a merge request and a push, and
  keep the variable a dispatch or a schedule carries, so `workflow:
  name` is never empty and `Run.workflow` is always the file.
- `run_context` maps GitLab's `merge_request_event` to `pull_request`,
  as the forge backend already maps a pipeline's source into the
  protocol's vocabulary, with the head and base branches it already
  reads from `CI_MERGE_REQUEST_*`.
- On GitLab, every job that writes the store or pushes rewrites origin
  with `GITLAB_PUSH_TOKEN` before its call, rendered from `Job.writes`
  and `Job.pushes`; the variable is documented beside the others the
  document names.
- The pins in `test_workshop_render.py` move for the three, and the
  `run_context` mapping has its unit test with the refusal shapes first.
- livery#585: `fm forge.fixtures.record` runs its pytest as a child again
  rather than in the runner's own process, so xdist never meets the argv
  proxy; a recording of one scenario proves it.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py
  packages/workshop/tests/test_workshop_state.py` passes.
- `uv run fm check --fix` exits 0.

### Phase 2: the lane is born and its setup gate runs

Deliverables:

- `_dev_forge("gitlab")` connects with `GITLAB_URL` and `GITLAB_TOKEN`;
  the refusal goes. The forge-shaped constants become one record per
  forge: the alias URL (`http://gitlab:8929`), the hosts entry
  `_require_host_alias` teaches, the credential `_authenticate_remote`
  writes (`oauth2:<token>`), and the registry addresses.
- `provision("gitlab")` creates the project under the seeded group,
  mints a project access token with `write_repository` for
  `GITLAB_PUSH_TOKEN`, and sets `FORGE_TOKEN`, `FORGE_ADMIN_TOKEN`,
  `GITLAB_PUSH_TOKEN` and `UV_PUBLISH_TOKEN` as masked variables through
  `RepoConfig.secrets`. The contract the birth seeds declares the
  project's PyPI registry, index and publish, by the project's
  URL-encoded path. The dev wheels publish there too, since the setup
  gate installs them; the seeded group is public so the runner reads
  the index without a credential, as it reads Gitea's.
- livery#590: `fm forge.dev.down --profile=<forge>` stops one forge and
  its runner, and `fm forge.dev.restart --profile=<forge>` restarts one
  forge's runner, discarding its jobs.
- livery#591 and livery#495: `fm submit`'s push and the loop's setup
  push cancel the runs still moving for the head they supersede, and a
  re-run of a red run waits until every job of it has completed,
  naming the job it waits on.
- The loop's run selection reads runs by event and commit through
  `point_runs` and `Run.workflow`, the same on both forges.
- `_merge_setup` merges through the protocol's `merge_now`, which on
  GitLab is the merge request.
- livery#590: `fm forge.dev.down --profile=<forge>` stops one forge and
  its runner, and `fm forge.dev.restart --profile=<forge>` restarts one
  forge's runner, discarding its jobs.
- livery#591 and livery#495: the loop's push cancels the branch's runs
  on the commits it supersedes, and a re-run of a red run waits until
  every job of it has completed, naming the job it waits on.

Acceptance:

- `uv run fm ci.e2e --forge=gitlab` prints `setup PR #1: merged; the
  gate is proven` and `verified skip: proven on main's run <n>`, then
  fails by name at the first act phase 3 delivers.
- `uv run fm ci.e2e --forge=gitea` is unchanged: exit 0.

### Phase 3: the members and the three legs on GitLab

Deliverables:

- The `gitlab-runner` runs on the image the act_runner runs on, with the
  native member's toolchain, through the compose file; a fresh
  `fm forge.dev.up --profile=gitlab` builds it.
- livery#299: `fm forge.dev.up --with-docker` mounts the host's docker
  socket into both runners with a docker CLI in the image, so a job
  that builds an image (the container publish seam, a wheel through
  cibuildwheel) runs on either lane; without the flag the seam
  declared `none` skips green.
- The members land through the loop's own `fm submit --armed` against
  merge requests, the scoped, prose and tests legs prove the narrowing
  on merge request pipelines, and the composed skips prove main's runs.

Acceptance:

- `uv run fm ci.e2e --forge=gitlab` prints the three `proven on a ...`
  lines and their `composed skip` lines, then fails by name at the
  release act.

### Phase 4: the release act on GitLab

Deliverables:

- The dev wheels and the wave publish to the project's PyPI registry;
  the probe reads its simple index; the receipts are cut and protected;
  `workflow.release.dispatch` starts the wave as an API-created pipeline
  at the stamping commit, and the recovery arm follows the named
  `release.yml` pipeline.
- The upload credential's form on GitLab's registry, an open question
  below, settled and recorded.

Acceptance:

- `uv run fm ci.e2e --forge=gitlab` prints `release: ... served,
  receipts ... cut`.

### Phase 5: the points by hand, and the clock

Deliverables:

- The nightly, the gate and the contributed point dispatched by hand
  through `ci.dispatch` and read back through `ci.status --point`, on
  GitLab.
- The schedules the reconcile created at birth listed back through the
  protocol, one per point on the clock, and one scheduled pipeline
  observed after a schedule is run on demand through the API.
- The CI plan's two open lines closed with this evidence, in that note.

Acceptance:

- `uv run fm ci.e2e --forge=gitlab` exits 0 and ends `the loop is
  whole: gate, merge, release, receipt, nightly, the gate on command,
  and a contributed point`.
- `uv run fm ci.e2e --forge=gitea` exits 0 on the same tree, run after
  it.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| the refusal of `--forge=gitlab` in `_dev_forge` | phase 2 |
| `ALIAS_URL`, `LOOP_INDEX`, `LOOP_PUBLISH` as Gitea constants | phase 2 |
| the stock `gitlab-runner` image | phase 3 |

## Decision record

- 2026-09-15, Willem: the two lanes run locally, one pass at a time,
  and GitLab is a first-class citizen again. The agent's concern that
  the host could not carry the GitLab containers was raised and
  overruled; the agent had read "run locally" as "run at once", which
  nobody asked for.
- 2026-09-15, Willem: the plan is ruled as written, with the open
  issues folded in as the sequencing section lists them.
- 2026-09-15, the agent, at phase 2: the dev wheels belong to this
  phase, not phase 4: the setup gate installs them from the lane's
  registry, so the registry's read and upload are proven here and
  phase 4 keeps the wave and the receipts. Measured on the local GitLab
  18.9: the project's simple index answers anonymously on a public
  project, `__token__` with the token authenticates as basic auth, and
  the URL-encoded project path addresses the registry as the numeric
  id does. The provisioning ran live twice: the project created then
  reused, four masked variables, the push token minted and the previous
  one revoked by name.
- 2026-09-15, the agent: the loop's run selection by workflow file name
  is kept, and GitLab meets it by naming every pipeline, rather than the
  loop growing a per-forge selection. The same choice serves
  `ci.status` and `ci.logs` under `--point` on GitLab.

## Open

1. **The upload credential's form on GitLab's PyPI registry.** `uv
   publish` sends `UV_PUBLISH_TOKEN` as the password with a fixed
   username; GitLab's registry documents a personal or project access
   token with the account's username, and the `__token__` form is not
   documented. Settled on the local instance in phase 4. Owner: the
   agent.
2. **Which shape the GitLab runner takes.** The Gitea runner's jobs run
   in host mode inside its container, whose image carries node, git,
   bash, curl, the docker CLI, a C++ toolchain and cmake on Alpine, with
   the host's docker socket mounted: the gate leg compiles the native
   member's editable install there, and the wheels job builds through
   the socket. The GitLab runner is the stock Ubuntu image with the shell
   executor, no toolchain and no socket, so both jobs fail on it as it
   stands. The first shape to try is one image for both runners, the
   toolchain image with the `gitlab-runner` binary added and the socket
   mounted; the two base images differ, so either the Alpine image takes
   GitLab's Alpine binary or the image is rebuilt on Ubuntu with the
   same toolchain list. If one image does not work out, the runner is
   registered with the docker executor and every job runs in the
   toolchain image; the runner container then needs only the binary and
   the socket. Settled by trying the first. Owner: the agent, at
   phase 3.
3. **The docs build the loop once saw finish in 0.03 s with nothing
   built.** Recorded in the CI plan; not reproduced. If the GitLab lane
   sees it, it gets an issue with both sightings. Owner: the agent.

[ci-plan]: 20260914-ci-declared-and-dispatched.md
