# CI declared, contributed, and dispatched on command

Status: drafted 2026-09-14, awaiting Willem's ruling. No phase started.
Runs before [the tool record and its index][record-plan], which depends on
the contribution mechanism this plan delivers and carries no CI work of
its own beyond the point it declares.

## What this is

Three strands of one thing: **no workflow is written by hand, and what CI
runs is data.**

- **Declared.** The point vocabulary is closed today, and each
  forge-and-point pair is its own emitter. Opening it means one renderer
  driven by declarations, so a new point is data rather than a function.
- **Contributed.** A package declares a point, so a layer brings its CI
  with it, beside the checks it registers through the kind seam and the
  tools its kinds require.
- **Dispatched.** The full gate runs in CI on command.

## Where things stand

- `_points.py` maps four points (`gate`, `merge`, `nightly`, `release`)
  to three files through `WORKFLOWS`, and their triggers through
  `EVENTS`. `DISPATCHABLE` is `("nightly",)`.
- `_ci_generate.py` is 1089 lines, with a hand-written emitter per
  forge-and-point pair: `_github_gate`, `_github_nightly`,
  `_github_release`, `_gitea_gate`, `_gitea_nightly`, `_gitea_release`,
  and `_gitlab_pipeline` plus `_gitlab_governance` for GitLab's single
  document. The boilerplate is already factored into helpers:
  `_rung_step`, `_setup_uv_step`, `_docs_requirements_step`,
  `_pin_driver_step`, `_enter_step`.
- The gate's shell carries `pull_request` and `push` and no
  `workflow_dispatch`, so `dispatch_flow` refuses the gate point, naming
  what starts it. There is no way to ask the forge to run the whole gate.
- `[ci] affected-legs = true`: a pull request's legs run the scoped gate
  against its base, and a push or a change outside the packages pays the
  full gate.
- `RETIRED` in `_ci_generate.py` deletes generated files the emitter no
  longer owns, which is how a removed workflow leaves no residue
  (livery#523).

## Sequencing

Phase 1 needs open questions 1 and 2 answered before it starts; both are
Willem's. Phases 2 to 4 need only the plan ruled. The tool record plan's
phases 1 to 7 do not wait on any of this; only its phase 8 waits, on
phase 4 here.

## Ground-truth contracts (do not violate)

1. **A job's identity is a footman task address, and nothing else in a
   declaration is forge-shaped.** That is what lets one declaration render
   for three forges: the task name survives the translation unchanged, and
   every other part of a job is the emitter's words for that forge.
2. **No logic in the emitted YAML.** A rendered file is plumbing:
   checkout, uv, enter, one `fm` call. Every decision lives in the verb
   (livery#286).
3. **The declaration survives GitLab.** GitLab renders one pipeline
   document, so a point becomes jobs with rules inside it. Anything
   file-shaped in a declaration breaks there and is refused at that level
   rather than at rendering.
4. **A contributed point declares no permission, no secret and no
   environment.** A scheduled workflow with a write token on a public
   repository is a foothold, and that stays a root decision.
5. **A contributed point adds no leg to the gate.** The gate is one
   command with one verdict paid on every push. An instance contributes
   tests and thresholds (`[qa] coverage-floor`), a kind registers named
   checks through the kind seam, and points live outside the gate.
6. **Nothing on the merge path waits on anything outside the repository.**
7. **A declaration that cannot run refuses when the files are generated**,
   never on the forge at run time: a task no layer mounts, a point name
   two packages claim, a cadence that is not one.
8. **Removing a package removes its workflow.** The generated set is
   resolved fresh from the declarations present, and what the emitter no
   longer owns is deleted through `RETIRED`.

## Phases

### Phase 1: the gate on command

Deliverables:

- A dispatch entry on the gate's shell for GitHub and Gitea, and the
  equivalent trigger in GitLab's pipeline, so `fm ci.dispatch
  --point=gate` starts it and follows it to its verdict.
- What a dispatched gate scopes to, decided and stated: a dispatch is a
  deliberate act, so it pays the full gate rather than the affected legs,
  and the run says which it ran.

Acceptance:

- `uv run fm ci.dispatch --point=gate` starts a run and returns its exit
  code, against the local Gitea through `fm forge.dev.up`.
- `uv run fm ci.e2e` exits 0.
- The dispatched run's log names the full gate, not a scoped one.

### Phase 2: a point is a declaration

Deliverables:

- One structure describing a point: its name, its triggers, its jobs,
  each job's matrix, and the task each job runs. `Entry` widened from
  attaching a task to an existing job into defining one.
- The four builtin points expressed in it, `WORKFLOWS` and `EVENTS`
  derived from the declarations rather than mapping them.
- Refusals at load: an unknown trigger, a cadence that is not one, a
  point name that is not a filename, a task no layer mounts.

Acceptance:

- `uv run python -m pytest packages/workshop/tests/test_points.py`
  passes, refusals first.
- `uv run fm ci.generate` writes files byte-identical to the checked-in
  ones for all three forge kinds, proven by `git status --short` being
  empty. The declarations reproduce what the emitters wrote.

### Phase 3: one renderer per forge

Deliverables:

- The per-point emitters replaced by one renderer per forge driven by
  declarations, keeping the step helpers as they are. GitLab renders its
  single document from the same declarations.
- `_ci_generate.py` carries no function named for a point.

Acceptance:

- `uv run fm ci.generate` again writes byte-identical files, proven by
  `git status --short` being empty.
- No emitter is named for a point, proven by
  `grep -cE '_(github|gitea)_(gate|nightly|release)' <the generator>`
  returning 0, where the generator is
  `packages/workshop/src/livery/workshop/_ci_generate.py`.
- `uv run fm ci.e2e` exits 0.

### Phase 4: a package contributes a point

Deliverables:

- `[[ci.point]]` in a package's `workshop.toml`, the same structure
  phase 2 defined.
- The limits refusing at generate: a declared permission, secret or
  environment; a job on the gate; two packages claiming one name; a task
  no layer mounts.
- Removal through `RETIRED`.

Acceptance:

- One declaration renders for all three forges and every rendering calls
  the same `fm` task, proven by
  `uv run python -m pytest packages/workshop/tests/test_points_contributed.py`,
  which generates against `github`, `gitea` and `gitlab` and asserts the
  task address in each output. Every refusal is forced before the
  accepting case.
- `uv run fm ci.e2e` exits 0 with a contributed point declared, so the
  point is proven against the local Gitea and GitLab rather than against
  the emitter's own output.
- Removing the declaring package and running `uv run fm ci.generate`
  leaves no workflow file behind, proven by `git status --short`.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `WORKFLOWS` and `EVENTS` mapping four points to three files | phase 2 |
| the per-point emitters in `_ci_generate.py` | phase 3 |
| `DISPATCHABLE` as a tuple beside the points | phase 2 |

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

## Open

1. **What the other session hit.** The need for an on-command full gate
   came from a session not in front of this note. Phase 1 is written from
   what the code shows: the gate's shell has no dispatch entry, so
   `dispatch_flow` refuses the point. If the need was something else, a
   scoped dispatch of one package's legs or a rerun of a failed leg,
   phase 1 changes shape. Owner: Willem.
2. **Whether a dispatched gate may be scoped.** Phase 1 takes a dispatch
   as deliberate and therefore full. The alternative is an argument
   naming packages, which is useful and is also a way to get a green
   verdict that proves less than it appears to. Owner: Willem.
3. **Whether the release point stays hand-shaped.** It is the most
   involved of the four and the one whose steps are least like a single
   `fm` call. If phase 3 cannot express it as declarations without
   contorting them, it keeps its emitter and the plan says so rather
   than bending the model. Owner: the agent, at phase 3.

[record-plan]: 20260914-the-tool-record-and-its-index.md
