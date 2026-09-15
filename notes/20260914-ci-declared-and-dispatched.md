# CI declared, contributed, and dispatched on command

Status: ruled 2026-09-14. Phase 1 landed 2026-09-15 (livery#584), phase 2
landed 2026-09-15 (livery#587), phase 3 landed 2026-09-15 (livery#589) with
one acceptance item open, phase 4 landed 2026-09-15 (livery#593) with one
acceptance item open, phase 5 landed 2026-09-15 (livery#595). The plan is
complete but for one open line, the GitHub wave; the live GitLab line
closed 2026-09-15 with the GitLab lane of the loop. Runs
before [the tool record and its index][record-plan], whose phase 8 declares
a point through the mechanism phase 5 here delivers; that plan carries no
other CI work.

## What this is

Three strands of one thing: **no workflow is written by hand, and what CI
runs is data.**

- **Dispatched.** The full gate runs in CI on command.
- **Declared.** The point vocabulary is closed today, and each
  forge-and-point pair is its own emitter, a hand-written YAML string in
  Python. Opening it means one renderer per forge driven by declarations,
  so a new point is data rather than a function, and no emitter survives,
  the release's included.
- **Contributed.** A package declares a point, so a layer brings its CI
  with it, beside the checks it registers through the kind seam and the
  tools its kinds require.

Functional equivalence is the bar for a rendering, never byte identity. A
re-rendered workflow may differ in every comment and in the order of its
keys; it must trigger on the same events, run the same jobs with the same
needs and filters, and call the same verbs.

## Where things stand

Verified against the tree at `69f18cc` on 2026-09-14, and brought up to
date where phase 1 moved it.

- `_points.py` declares the four builtin points in `DECLARED`, a tuple
  of `Point` (`gate`, `merge`, `nightly`, `release`): each its workflow
  file, its events, its dispatch inputs and its jobs, a `Job` stating in
  the workshop's words what it needs. `contributed(root)` reads the
  `[[ci.point]]` tables of the workspace's packages, refusing bad ones
  at load, and `points(root)` is the whole set, verified as one; every
  reader (`jobs_of`, `entries_for`, `run_point`, `point_runs`,
  `dispatch_flow`, the renderers) takes it. `POINTS`, `WORKFLOWS`,
  `EVENTS`, `DISPATCHABLE` and `INHERITS` stay the builtin four's. What a
  job runs is `BUILTIN`, a tuple of `Entry`, the contributed entries, plus
  the root contract's
  `[[ci.schedule]]` entries; `run_point` spawns each entry as a child of
  `fm ci.run --point=<p> --job=<j>`, the one command every rendered gate,
  merge and nightly job calls. A job's identity is its point and its
  name. The task addresses live in the entries, never in the YAML.
- `_ci_generate.py` renders every point from the declarations:
  `_actions_workflow` walks the points that share a file for GitHub and
  Gitea, `_gitlab_document` walks every point into the one GitLab
  document, and each job goes through the step helpers (`_rung_step`,
  `_setup_uv_step`, `_docs_requirements_step`, `_enter_step`). No function
  is named for a point. `generate(root)` returns every generated file by
  path; `fm template.apply` writes them and `fm template.check` reports
  drift. There is no `fm ci.generate`.
- **The release point is on the points shape.** Its jobs `wheels`,
  `publish` and `templates` are `ci.run` jobs whose entries are
  `release.wheels`, `workflow.release.publish` and `release.templates`, each
  at `--ref={ref}`, the dispatch input `run_point` reads through
  `dispatch_inputs` (the event payload on GitHub and Gitea, the pipeline
  variables on GitLab). The wave decides prebuilt for itself inside CI
  from a platform member's collected `dist/` (`collected_wheels`), the
  templates verb reads the wave at the ref and skips one that did not
  release the publisher (`publisher_in_wave`), and the driver pin is
  `fm release.driver`, green on an empty input. The `members` output and
  the commit-title regex are gone.
- **GitLab's document is on the points shape for every point.** One
  job per declared job, calling `fm ci.run --point=<p> --job=<j>` through
  `setup.sh`, with rules on the point's events and on `$FORGE_WORKFLOW`;
  the deploy renders as GitLab Pages' own `pages` job, and the wave's
  `publish` job checks out the `ref` variable and rewrites origin with
  the push token before its call. The document sets `workflow: name` to
  `$FORGE_WORKFLOW`, and the GitLab backend fills `Run.workflow` from
  the pipeline listing's `name`, so `point_runs` tells a dispatched gate
  pipeline from a dispatched wave. The clock is a pipeline schedule:
  the forge protocol's `pipeline_schedules` capability
  (`Repository.schedule`), which GitLab implements and GitHub and Gitea
  decline by name, and `fm workflow.configure` reconciles one schedule
  per point that runs on the clock, named `workshop: <point>`. None of
  this is proven live: the GitLab lane of the loop does not exist.
- **The e2e loop has one lane.** `fm ci.e2e` refuses `--forge=gitlab` by
  name. GitLab is proven by rendered output in unit tests only. The loop
  proves the Gitea release wave through `_release_act`; the GitHub wave
  is proven by a real release.
- The gate's shell carries `pull_request`, `push` and `workflow_dispatch`
  on GitHub and Gitea. `dispatch_flow` in `_ci_tasks.py` starts the gate
  and the nightly, and refuses the merge point naming the push and the
  release point naming `workflow.release.dispatch`.
- **A dispatched run pays the full gate.** `ci_affected_base` in
  `_quality.py` narrows only on a `pull_request` event and prints
  `affected-legs: a <event> run pays the full gate` on every other, and
  `check` sets the verified record aside on a `workflow_dispatch` event
  the way it does at the nightly point, printing
  `dispatched: the whole gate, the verified record set aside`. The shell
  spells one call on every event and never `--full`.
- `effective_point` promotes `gate` to `merge` on a `push` event only. A
  dispatched gate on `main` runs the gate point's jobs (`check`, `docs`,
  `gate`), and the merge-only jobs (`deploy`, `govern`, `dispatch`) are
  filtered by `if: github.event_name == 'push'`. `ci.verified.stamp` at
  the end of the gate job writes the row for the tree the run proved, a
  full row since no leg narrowed.
- `retired_files` in `_ci_generate.py` names `RETIRED`, the paths earlier
  emissions wrote under other names, and every file in the forge's
  workflow directory that opens with `GENERATED_MARK` and is not in this
  emission: the workflow of a point whose package is gone.
  `fm template.check` reports them and `fm template.apply` deletes them.
- Tests carry their package's name: `test_workshop_points.py`,
  `test_workshop_dispatch.py`, `test_workshop_entry.py` (the render pins
  for all three forge kinds), `test_workshop_shells.py`,
  `test_workshop_composed_release.py` (the Gitea release shell).
- The docs that describe the points are the "Where a test runs" section of
  `packages/workshop/docs/index.md`, the module docstrings of `_points.py`
  and `_ci_tasks.py`, and the task reference the docs build generates.
  Each phase moves them in the same change.

## Sequencing

Phase 1 waits on nothing. Phases 2 to 5 wait on each other in order. The
tool record plan's phases 1 to 7 wait on nothing here; its phase 8 waits
on phase 5.

## Ground-truth contracts (do not violate)

1. **A job's identity is its point and its name, and its one command is
   `fm ci.run --point=<p> --job=<j>`.** What the job runs is the
   schedule's entries. Nothing in a declaration is forge-shaped: a job
   states what it needs in the workshop's words (a full history, the
   repository token, a store write, a profile artifact, an artifact from
   another job), and each forge's renderer says it in that forge's words.
2. **No logic in the emitted YAML.** A rendered job is plumbing: checkout,
   the rung, uv, enter, one `fm` call, and the forge's own steps around
   it that the renderer names. Every decision lives in the verb
   (livery#286). Event filters and the verdict job's `always()` are the
   only conditions.
3. **The declaration survives GitLab.** GitLab renders one pipeline
   document and a run there is a pipeline, so a point becomes jobs with
   rules and a file-shaped declaration is refused at load, never at
   render.
4. **A contributed point declares no permission, no secret and no
   environment.** Its job runs with the ambient job token at the
   repository's default grant. A scheduled workflow with a write token
   on a public repository is a foothold, and that stays a root decision.
5. **A contributed point adds no job to the gate.** The gate is one
   command with one verdict paid on every push. A declaration names its
   own point and nothing else, so a job on the gate is refused by shape.
6. **Nothing on the merge path waits on anything outside the
   repository.**
7. **A declaration that cannot run refuses at load or at render, never
   on the forge.** A task the runner does not mount, a name two packages
   claim, a name that is a builtin point, a cadence that is not one.
8. **Removing a package removes its workflow.** The generated set is
   resolved fresh from the declarations present, and a generated file the
   emission no longer owns is deleted at apply and reported at check.
9. **The properties an emitter enforces are pinned before it is
   replaced.** Per forge and point: the file, the events, the jobs in
   order, each job's needs and event filter, each job's one verb call,
   and which secrets each job carries. A pin is on those properties, not
   on bytes.
10. **No workflow is hand-written in the workshop.** When phase 4 lands,
    `_ci_generate.py` carries no function named for a point and no YAML
    string longer than one step helper.

## Phases

### Phase 1: the gate on command

Landed 2026-09-15 as livery#584. Evidence, each command run on the
worktree after the loop:

- `uv run python -m pytest packages/workshop/tests/test_workshop_points.py
  packages/workshop/tests/test_workshop_shells.py`: 36 passed.
- `uv run fm template.check`: exit 0 after `fm template.apply`
  re-rendered `.github/workflows/ci.yml`, whose diff is the
  `workflow_dispatch:` entry and its comment.
- `grep -c -- '--full' .github/workflows/ci.yml`: 0.
- `uv run fm ci.e2e`: exit 0, printing
  `gate: proven by hand (run 1578: dispatched, followed to green, the
  full gate ran, and the point read back green)`.
- The dispatch scenario replays green on all three forges, with the
  GitLab cassette naming the dispatched pipeline `conf.yml`:
  `uv run python -m pytest packages/forge/tests/test_gitlab_conformance.py
  packages/forge/tests/test_gitea_conformance.py
  packages/forge/tests/test_github_conformance.py -k dispatch`: 3 passed.
- `uv run fm check --fix`: exit 0.

Deliverables:

- `workflow_dispatch` on the gate's shell for GitHub and Gitea.
  `EVENTS["gate"]` gains `workflow_dispatch`, `DISPATCHABLE` gains
  `gate`, and the refusal in `dispatch_flow` names both. The
  `ci.dispatch` help and the docs stop naming the nightly as the one
  point with an entry.
- On GitLab, the rendered document names its pipelines: `workflow: name`
  is `$FORGE_WORKFLOW` on a dispatched pipeline and the event's own
  word otherwise, and the GitLab backend fills `Run.workflow` from the
  pipeline listing's `name`. If the local GitLab's listing carries no
  `name`, the backend reads the pipeline's variables instead and the
  plan records the cost. Either way `point_runs` tells a gate pipeline
  from a wave.
- The shell spells the same `fm ci.run --point=gate --job=check` on
  every event, and no `--full` anywhere. The verb reads the event from
  the runner's environment and pays the full gate on a dispatch, as
  `ci_affected_base` does today; a pin keeps it so.
- The loop proves the dispatched gate the way `_prove_nightly` proves
  the nightly: dispatched on `main`, followed to green, read back with
  `ci.status --point=gate`, and the check leg's log carrying
  `affected-legs: a workflow_dispatch run pays the full gate`.

What this does not cover: a dispatch scoped to named packages, and a
rerun of one red leg. `fm ci.rerun` already re-runs the failed jobs of
the head commit's run.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_points.py
  packages/workshop/tests/test_workshop_shells.py` passes, the refusal
  of the release point and of a point that is not one first.
- `uv run fm template.check` exits 0 after `uv run fm template.apply`,
  and `git status --short` names only the plan note and the three
  workflow files.
- `grep -c -- '--full' .github/workflows/ci.yml` prints 0.
- `uv run fm ci.e2e` exits 0, and its output carries the line
  `gate: proven by hand`.
- On GitLab, the recorded fixture for `runs()` names a dispatched
  pipeline's workflow, proven by the forge's conformance suite,
  `uv run fm test packages/forge`.

### Phase 2: a point is a declaration, and the emitters are pinned

Landed 2026-09-15 as livery#587. Evidence:

- `uv run python -m pytest packages/workshop/tests/test_workshop_points.py`:
  the load refusals first, 22 passed in the file.
- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py`:
  8 passed against the emitters as they are.
- `uv run fm template.check`: exit 0, no workflow file changed.
- `uv run fm check --fix`: exit 0.

What the pins found: the Gitea deploy job checks out without the tags
the GitHub one fetches, so its release view renders the no-tags
fallback. The pin names the difference per forge; phase 3 renders both
from the one declaration and the loop proves the Gitea deploy then.

Deliverables:

- One structure describing a point, beside `Entry` in `_points.py`: its
  name, its workflow file, its events, its dispatch inputs, and its
  jobs. A job carries the facts the emitters read today, in
  forge-neutral words: the matrix (`runners` or none, `gate` Pythons or
  the whole matrix or none), a single runner or the matrix's, when it
  runs (`always`, on the merge point only, or on every run of the
  point), what it needs, how deep its checkout is (full, tags, the last
  two commits, shallow), which token it carries (the job's, the
  repository's when present else the job's, the admin token, none),
  whether it writes the store, whether it uploads the profile trace,
  whether it installs the docs requirements, whether it deploys through
  the pages seam, which artifact it publishes and which it collects, the
  named environment it runs in, the deploy key it materialises, and its
  note, the comment the shell prints above it.
- All four builtin points expressed in it. `WORKFLOWS`, `EVENTS`,
  `DISPATCHABLE`, `INHERITS` and `jobs_of` derive from the declarations.
  The release's jobs are declared with their shape and stay rendered by
  their emitters until phase 4.
- Refusals at load, each naming the point: an event that is not one of
  `pull_request`, `push`, `schedule`, `workflow_dispatch`; a workflow
  file two points name whose events overlap; a job that needs a job the
  point does not have; an artifact collected that no job publishes.
- The pins contract 9 requires, in `test_workshop_render.py`: for each
  of `github`, `gitea` and `gitlab`, the rendered set parsed as YAML and
  asserted on its properties: the files, the events, the jobs in order,
  each job's `needs` and event filter, the one `fm` call per job, and
  the secrets each job names. The emitters pass them before anything is
  replaced.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_points.py`
  passes, the load refusals first.
- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py`
  passes against the emitters as they are.
- `uv run fm template.check` exits 0 with no workflow file changed,
  proven by `git status --short` naming no path under
  `.github/workflows/`.

### Phase 3: one renderer per forge, for the gate, the merge and the nightly

Landed 2026-09-15 as livery#589. Evidence:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py`:
  8 passed against the renderers, with two pins moved deliberately: the
  Gitea deploy fetches tags like GitHub's, and GitLab's jobs are the
  declared ones.
- The grep for `_(github|gitea)_(gate|nightly)\(` in `_ci_generate.py`
  prints 0.
- `uv run fm template.apply` re-rendered this repository's `ci.yml` and
  `nightly.yml`, and `uv run fm template.check` exits 0.
- `uv run fm check --fix`: exit 0.
- The `schedules` conformance scenario recorded against the local GitLab
  and replaying green; `schedules-declined` replaying green on Gitea,
  GitHub and the fake.
- `uv run fm ci.e2e --fresh`: exit 0 on the re-rendered Gitea shells,
  every proof line printed: the setup gate, the verified skip, the
  scoped, prose and tests legs, the release and its receipt, the nightly
  by hand (run 1616) and the gate by hand (run 1617), ending
  `the loop is whole: gate, merge, release, receipt, nightly, and the
  gate on command`. Main's runs ran the merge point's deploy, govern,
  dispatch and janitor through the rendered jobs.
- Closed 2026-09-15 by the GitLab lane of the loop
  (`fm ci.e2e --forge=gitlab`, exit 0): every pipeline on the local
  GitLab runs its jobs through `ci.run`, the job logs carrying
  `gate/check: check (builtin)` and `gate/docs: docs.build (builtin)`;
  the dispatched gate, nightly and contributed point ran as API-created
  pipelines named by `FORGE_WORKFLOW`, and the birth's schedule, run on
  demand, produced a pipeline from source `schedule` whose nightly job
  went green.

Deliverables:

- `_github_gate`, `_github_nightly`, `_gitea_gate` and `_gitea_nightly`
  replaced by one renderer per forge that walks a point's declaration
  and emits its jobs through the step helpers, which stay as they are.
- GitLab rendered from the same declarations: one document, one job per
  declared job with rules on the event and on `$FORGE_WORKFLOW`, each
  calling `fm ci.run --point=<p> --job=<j>` through `setup.sh`.
  `governance-apply` becomes the merge point's `govern` job like the
  other two forges. `release-publish` and `pages` keep their shapes
  until phase 4.
- The clock on GitLab is a pipeline schedule, a project setting and
  never a line in the document. `fm workflow.configure` creates or
  updates one schedule per point whose events include `schedule`,
  carrying `FORGE_WORKFLOW=<point>`, through a `schedules` capability on
  the forge protocol that GitHub and Gitea decline by name since their
  clock is in the file. The capability is recorded against the local
  GitLab and proven by the forge's conformance suite.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py`
  passes against the renderers.
- No emitter is named for the gate or the nightly point, proven by this
  printing 0:

  ```sh
  grep -cE '_(github|gitea)_(gate|nightly)\(' \
    packages/workshop/src/livery/workshop/_ci_generate.py
  ```

- `uv run fm template.apply` re-renders this repository's `ci.yml` and
  `nightly.yml`, and the phase's own pull request is green on the
  re-rendered gate, read through `uv run fm ci.status`.
- `uv run fm ci.e2e` exits 0.
- On GitLab, one API-created pipeline against the local GitLab runs the
  `gate` and `docs` jobs through `ci.run`, proven by the job log lines
  `gate/check: check (builtin)` and `gate/docs: docs.build (builtin)`
  read through `uv run fm ci.logs --point=gate` from a checkout whose
  contract names the local GitLab.

### Phase 4: the release point is data

The release is the point whose shell is least like one `fm` call, so it
lands alone, after the model is proven on the three that fit it.

Landed 2026-09-15 as livery#593. Evidence:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py
  packages/workshop/tests/test_workshop_composed_release.py
  packages/workshop/tests/test_workshop_dispatch.py
  packages/workshop/tests/test_workshop_release_driver.py
  packages/workshop/tests/test_workshop_release_point.py`: passes, the
  skip of a wave without the publisher and the untrusted local `dist/`
  before the acting cases.
- The grep for every point-named emitter in `_ci_generate.py` prints 0.
- `uv run fm template.apply` re-rendered this repository's
  `release.yml`, and `uv run fm template.check` exits 0.
- `uv run fm check --fix`: exit 0.
- `uv run fm ci.e2e --fresh`: exit 0, the wave on the re-rendered Gitea
  release shell through `ci.run`: `release: ci-e2e-loop-loop-echo 0.1.0,
  ci-e2e-loop-loop-native 0.1.0 served, receipts packages/loop-echo/v0.1.0,
  packages/loop-native/v0.1.0 cut`, then the nightly (run 1638) and the
  gate (run 1639) by hand, ending `the loop is whole`.
- Open: the GitHub wave rendered from the declaration is proven by the
  first release after this lands, read through
  `uv run fm ci.status --point=release`.

Deliverables:

- The release's two YAML decisions move into verbs, per livery#286.
  `workflow.release.publish` decides `--prebuilt` for itself from
  whether a platform-wheel member is in the wave and a collected
  `dist/` is present, and `release.templates` decides for itself whether
  the publisher was in the wave by reading the manifest at the ref, so
  the `templates` job runs after `publish` with no condition and the
  `members` output is retired.
- The release's jobs become `ci.run` jobs with entries:
  `Entry("release", "wheels", "release.wheels", ("--ref={ref}",))`,
  `Entry("release", "publish", "workflow.release.publish", ("--ref={ref}",))`,
  `Entry("release", "templates", "release.templates")`. The dispatch
  inputs reach `run_point` through the event payload
  (`inputs.ref`, `inputs.workshop` on GitHub and Gitea, the pipeline
  variables on GitLab), and `{ref}` joins the facts an entry's arguments
  format with. The driver pin stays a step the renderer emits from the
  declaration's `workshop` input.
- `_github_release`, `_gitea_release` and GitLab's `release-publish` and
  `pages` replaced by the renderers of phase 3, walking the release
  declaration. The pages seam is the deploy job's rendering on every
  forge. The GitLab wave's rules become an event filter on
  `$FORGE_WORKFLOW == "release.yml"`, and the commit title regex is
  deleted.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_render.py
  packages/workshop/tests/test_workshop_composed_release.py
  packages/workshop/tests/test_workshop_dispatch.py
  packages/workshop/tests/test_workshop_release_driver.py` passes, the
  refusal of a wave without a manifest at the ref and of a `--prebuilt`
  with an empty `dist/` before the accepting cases.
- No emitter is named for any point on any forge, proven by this
  printing 0:

  ```sh
  grep -cE '_(github|gitea)_(gate|nightly|release)\(|_gitlab_(pipeline|governance)\(' \
    packages/workshop/src/livery/workshop/_ci_generate.py
  ```

- `uv run fm ci.e2e` exits 0, so the Gitea wave and its receipt are
  proven on the real runner.
- The GitHub wave is proven by the first release after the phase lands,
  read through `uv run fm ci.status --point=release`; the plan keeps
  this line open until that run is green.

### Phase 5: a package contributes a point

Landed 2026-09-15 as livery#595. Evidence:

- `uv run python -m pytest
  packages/workshop/tests/test_workshop_points_contributed.py`: 13 passed,
  every refusal forced before the accepting case, then one declaration
  rendered for `github`, `gitea` and `gitlab`, each output carrying
  `ci.run --point=host-audit --job=host-audit` and no `permissions:` or
  `environment:` and no secret beyond the job token in the contributed
  job, then the removal.
- `uv run fm check --fix`: exit 0.
- `uv run fm ci.e2e --fresh`: exit 0, the member's point dispatched by
  hand: `echo-audit: proven by hand (run 1682: the contributed point
  dispatched, its task run, and the point read back green)`, after the
  wave, the nightly (run 1680) and the gate on command (run 1681), ending
  `the loop is whole: gate, merge, release, receipt, nightly, the gate on
  command, and a contributed point`.

Deliverables:

- `[[ci.point]]` in a package's `workshop.toml`: `name`, `task`, `args`,
  `every`, `runners`, `pythons`. The point's events are `schedule` and
  `workflow_dispatch`, fixed; its one job is named after it, runs the
  matrix of `runners` by `pythons` (the newest gate Python when absent),
  checks out shallow, carries the job token, and runs
  `fm ci.run --point=<name> --job=<name> --os=... --python=...`. Its one
  entry is the declared task, with the cadence judged by `due` on the
  runner as the nightly's entries are today.
- `POINTS` becomes the builtin four plus every package's declared
  points, discovered through `discover_packages`. `schedule`,
  `jobs_of`, `entries_for` and `run_point` take the discovered set. A
  root contract's `[[ci.schedule]]` may attach an entry to a contributed
  point.
- Refusals at load, naming the package and the point: a name that is a
  builtin point; a name two packages claim; a name that is not
  `[a-z][a-z0-9-]*`; a cadence that is not one; a task the runner does
  not mount, resolved against the runner's root group the way
  `livery.footman._executor.resolve` walks a path; a permission, secret
  or environment key in the table.
- Removal: `retired_files` returns `RETIRED` plus every file under the
  forge's workflow directory that opens with `generated_header` and is
  not in this emission. `fm template.check` reports it as drift and
  `fm template.apply` deletes it. GitLab's one document regenerates.
- The loop's fixture workspace declares a point on its member package,
  and the loop dispatches it after the nightly.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_workshop_points_contributed.py`
  passes: every refusal forced before the accepting case, then one
  declaration generated for `github`, `gitea` and `gitlab`, each output
  carrying `ci.run --point=<name> --job=<name>` and no `permissions:`,
  `secrets.` or `environment:` in the contributed job.
- `uv run fm ci.e2e` exits 0, and its output carries
  `<name>: proven by hand` for the loop's contributed point.
- Removing the declaring package from a temporary workspace and running
  `uv run fm template.apply` prints `<file> (retired)` and leaves no
  workflow file behind. This is a test in
  `test_workshop_points_contributed.py`, not a loop step.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `WORKFLOWS` and `EVENTS` mapping four points to three files | phase 2 |
| `DISPATCHABLE` as a tuple beside the points | phase 2 |
| `_github_gate`, `_github_nightly`, `_gitea_gate`, `_gitea_nightly` | phase 3 |
| `_gitlab_pipeline` calling `fm check` and `fm docs.build` bare | phase 3 |
| `_gitlab_governance` as its own job | phase 3 |
| `_github_release`, `_gitea_release`, GitLab's `release-publish` | phase 4 |
| the `members` output and the `contains` condition | phase 4 |
| `RETIRED` as a static tuple | phase 5 |

## Decision record

- 2026-09-14, Willem: the CI work leaves the tool record plan and runs
  first. Three strands in one plan: the full gate dispatched on command,
  workflows declared rather than hand-written, and a package contributing
  a point. The tool record plan keeps only the point it declares.
- 2026-09-14, Willem, carried from the tool record handover: the
  mechanism is a workflow template calling one footman task, cadence
  rides the existing seam so no per-forge cron dialect is needed, a
  contributed point declares no permission, secret or environment and
  adds no leg to the gate, and `[ci] wheel-platforms` is not a precedent
  for any of it.
- 2026-09-14, the agent, revising for the ruling: contract 1 is restated
  around the shape the code has. A rendered job's one command is
  `fm ci.run`, and the task addresses are the schedule's entries, so "a
  job's identity is a task address" described a shape that does not
  exist. The forge-neutral job facts in phase 2 are the fields the eight
  emitters read today, listed from their code.
- 2026-09-14, the agent: phase 1's scope decision is withdrawn, since
  `ci_affected_base` already pays the full gate on every event but a
  pull request. Phase 1 adds the trigger and the GitLab run naming.
- 2026-09-14, the agent: the draft's acceptances named `fm ci.generate`
  and `test_points.py`; neither exists. The verbs are `fm template.apply`
  and `fm template.check`, and tests carry their package's name.
- 2026-09-14, the agent: the draft's phase 4 proved a point against the
  local GitLab through the loop; the loop has one lane, Gitea. GitLab is
  proven by rendered pins and, in phase 3, by one API-created pipeline
  against the local GitLab outside the loop.
- 2026-09-14, Willem: a re-rendered workflow need not be byte-identical
  to what the emitter wrote; functional equivalence is the bar. The
  pinning fixtures the revision proposed are withdrawn, and the pins are
  on properties (contract 9).
- 2026-09-14, Willem: no hand-written workflow file survives in the
  workshop. The release point does not keep its emitters; it becomes
  phase 4, its two YAML decisions moving into verbs first, and the
  contributed point moves to phase 5. The tool record plan's phase 8
  now waits on phase 5.
- 2026-09-14, Willem: a dispatched gate is full, and the check verb
  infers the event itself. The shell never passes `--full`; one
  rendered call serves every event. A scoped dispatch is not built.
- 2026-09-14, Willem: the forge protocol grows a schedules capability
  so a scheduled point runs on the clock on GitLab too. The reconcile
  creates the pipeline schedule in phase 3.
- 2026-09-14, Willem: the on-command gate was asked for by another
  session that wanted to dispatch the gate by hand, and the request was
  folded into this note. Phase 1 as written is that request; the
  draft's open question on it is closed.
- 2026-09-14, the agent, at phase 1: a dispatched run sets the verified
  record aside the way the nightly does. A person asked for this tree to
  be proved now, and a skip would answer with what an earlier run said;
  the plan's "full row since no leg narrowed" holds only if the leg runs.
  The narrowing decision is untouched: `ci_affected_base` pays the full
  gate on every event but a pull request and prints so.
- 2026-09-14, the agent, at phase 1: GitLab 18.9's pipeline listing
  carries `name`, null unless the document sets `workflow: name`, so the
  backend reads `Run.workflow` from it and the rendered document sets
  the name to `$FORGE_WORKFLOW`. The conformance driver's seeded
  pipeline names its pipelines the same way, and since every scenario
  seeds that document, every GitLab cassette was re-recorded against
  the local GitLab. The dispatch scenario asserts the run's workflow on
  every forge, through `await_run` returning the run rather than its
  id, so no other cassette changed.
- 2026-09-14, the agent, at phase 1: `fm forge.fixtures.record` dies at
  xdist start-up (livery#585), so the cassettes were recorded by the
  same pytest invocation the verb makes, run directly with
  `FORGE_RECORD=1` and the shared env sourced.
- 2026-09-15, the agent, at phase 3: the renderers differ from the
  emitters in three ways a run can see, each judged equivalent or
  better. A single-runner job runs on the contract's first runner on
  GitHub as it did on Gitea, where the emitter spelled ubuntu-latest.
  The environment rung renders on every job where the committed
  `.repo.env` declares keys, where the Gitea emitter put it on the
  check, verdict and nightly jobs alone; a job without secrets to mount
  renders no step. The Gitea deploy fetches tags, which the pin found
  missing. On GitLab a `legs` matrix is one leg, labelled by the first
  runner and the first gate Python so its rows key the leg the union
  expects, and the `pages` job stays the deploy until phase 4.
- 2026-09-15, the agent, at phase 5, operations: one pass died on the
  loop's docs job, where zensical reported a build finished in 0.03 s
  with eight pages present and left no index page; the same runner had
  built the site green on the setup and member pull requests minutes
  before, and the fresh pass that followed built it green on every
  leg. Not reproduced, not understood; a second sighting gets an
  issue. A pass is not resumable past the verified-skip proof, which
  reads main's newest push run as the setup squash's: fresh is the
  recovery.
- 2026-09-15, the agent, at phase 5: a contributed point's job carries
  the job token as `FORGE_TOKEN`, since a task that reads the forge (a
  scheduled audit filing its own issue) needs one and the job token is
  the repository's default grant; the pin admits that one secret and no
  other. A contributed point runs on the nightly's clock, its cadence
  judged on the runner, so no per-forge cron dialect is rendered. A task
  is proven mounted against the runner's own registry at load, so a
  unit test supplies the seam; under `fm` the layers are mounted.
- 2026-09-15, the agent, at phase 4: the wave trusts a collected
  `dist/` inside CI alone, since a runner's checkout starts with none
  and a person's may hold a stale build. The templates verb reads the
  wave at `--ref` and decides; without `--ref` it publishes as before,
  which is the hand-run recovery. Gitea's release gains the `workshop`
  input and the driver pin its emitter never had. On GitLab the wave
  checks out the `ref` variable first, since the pipeline runs on the
  base branch, where the emitter published `$CI_COMMIT_SHA`, the base's
  tip rather than the squash.
- 2026-09-15, the agent, at phase 3, operations: the loop failed five
  times before its green pass, none on the render. The host carried six
  orphaned Claude Code snapshot shells spinning at 100% since
  2026-09-08 (killed), the GitLab containers idled at several cores
  (stopped; `fm forge.dev.up --profile=gitlab` restores them), the
  Gitea container answered one-row updates in seconds after eleven days
  up (restarted), and the act_runner's restart failed on the daemon's
  side and left it stopped. Each dead pass left its runs on the scratch
  repository for the runner to grind, with nothing left to read them;
  livery#590 asks for a per-forge down and a runner restart, livery#591
  for cancelling the runs a newer push supersedes. The loop's three
  watchers now retry a timed-out read within the `ci` verbs' budget
  rather than failing the pass on the first one.
- 2026-09-15, the agent, at phase 2: the release's jobs are declared
  with the vocabulary the wave needs (an artifact published and
  collected, an environment, a deploy key, the driver pin, a job that
  exists only where wheels or a template artifact do), and the
  emitters still render them; `jobs_of` lists them, with no entries
  until phase 4. A job's step names and comments are not pinned, by
  the functional-equivalence ruling; its checkout depth, its artifact
  upload and GitHub's grants are, since a verb depends on them.

## Open

1. **How a dispatch input reaches a verb through `ci.run`.** Phase 4
   proposes the event payload, which `event_payload` already reads on
   GitHub and Gitea, and the pipeline variables on GitLab, which arrive
   as environment. If the payload's `inputs` are absent on Gitea's
   act_runner, the renderer passes them as `--ref` and `--workshop`
   arguments to `ci.run` instead, the same way it passes `--os` and
   `--python`. Owner: the agent, at phase 4, measured on the loop.

[record-plan]: 20260914-the-tool-record-and-its-index.md
