# Changelog

## [0.2.0] - 2026-09-11

### Added

- The armed suite rehearses the release path on the true graph
- The docs site's rendered config and build verb
- The API reference, every docstring published
- Changelog pages and the paginated release view
- The docs gate, drifted links red before CI says so
- The publish seams, declared in the contract per forge kind
- The docs seeds and the gradient add-back
- The registry ladder and the kind registry, opened to layers
- The C/C++ library kind on conan 2
- The binary-extension kind through the chain
- The cross-kind dependency
- Publish and the identity guard across kinds
- The chain add-back for both kinds
- Issue bodies through fm
- The entry contract and the bare fm spelling
- Package-owned nav for the docs site
- The docs generator seam
- The docs config surface and the theme
- Llms.txt and llms-full.txt for the site
- The coverage seam
- The per-package task reference
- Toolroom joins the workspace
- Footman joins the workspace
- The version rules speak as the ecosystem's own
- The train carries a migrated line's first release
- The release branch carries its own set manifest
- Fm ci.e2e drives the whole loop against the local forge
- The server blocks red merges on every forge
- Receipt tags are protected on every forge
- The post-edit hook never removes an import mid-edit
- Every gate leg runs profiled and uploads its trace
- The CI state store on refs/workshop/*, with compare-and-swap writes and a janitor
- Timing rows on the state store, and fm ci.timings
- CI as points on the gitea emitter, fm ci.run per job
- The merge point dispatches the release wave, fm workflow.release.dispatch
- The nightly point, fm release.replay, and the schedule seam's first entry
- [ci] python-versions, matrices as contract config; a one-leg loop
- Contract keys are kebab-case; one loader refuses underscores, the render verbs migrate
- The wheels matrix reads wheel-platforms; the loop builds a nanobind wheel through the docker socket
- [ci] affected-legs, the check legs run the scoped gate against the pull request's base
- Workshop/verified, the gate stamps the tree it proved green and a run of the same tree skips
- A cancelled run names the run that superseded it, and the watchers follow
- The gitea lane meters its check legs and unions them in the gate job
- A skipped suite's floor is judged from the workshop/coverage store
- The leg splits one pooled test run by test context instead of one process per suite
- The GitHub emitter runs the check legs and the gate job through the points shell
- The auto-ratchet floor mode, fm coverage.accept, and a coverage row on every gated run
- A diff confined to notes and markdown affects no package, so its legs skip the gate
- A test declares the CI points it runs at, and the nanobind wheel build runs at the nightly point only
- The submit's gate is the gate the CI legs run
- The site's own root files affect no package
- A narrowed green run on a verified base stamps its tree in full
- The GitHub shell carries the merge point's jobs, and the legs start at once
- The timings record every job of the run and the run's own wall
- The submit's gate skips a tree this machine's check already proved
- The runner's directories are swept through a plugin surface
- Every test runs apart from the live runner state, and the registry stays clean
- The check legs run the newest Python; the nightly runs the matrix
- Every series reads and writes its rows through the store
- The coverage store and the per-run halves are keyed series
- The gate record and the diagnostics are local series of the store
- One janitor sweeps the runner's directories and the store
- The state store is read by hand, and the pages read it after a skip
- The coverage record is keyed by main, one row per unit per leg
- Each branch keeps its own coverage record, copied to main's at the merge
- The gate judges statements and branches, and the record holds arcs
- The workspace tests directory is a unit of the affected engine
- Dispatch and read the nightly, ride out dropped connections, merge notes by union, judge the task nav
- The test speed ratchet, slice 6 of the state store plan
- A re-dispatched wave may name a released driver, and recovery reaches older squashes

### Fixed

- Prepare refreshes uv.lock with the stamp
- Ci.rerun re-runs the branch's verdict, read back
- The changelogs are the manifest and recovery handles its leftovers
- Tie the submit verdict to the pushed head
- The newborn's first release is v0.0.0 everywhere
- One release number per distribution line
- The isolated leg answers for its own environment
- The leg runs serial, and the history ships in the wheel
- Re-preparing an unpublished release is recovery, not a fault
- The emitted title check speaks footman's grammar, and a red title cancels the matrix
- The docs deploy fetches the receipt tags
- The scoped gate hands its steps to the block
- The context-rename heal skips a forge that names no contexts
- Lint and format take paths and --safe-fix; the hook calls the verbs
- The metrics collect looks a pull request's run up under the event's head
- The completion test heals no real project; the dev build keeps timestamps
- Classify takes the emitters' path set once instead of rendering per path
- The units a leg ran come from its marker, the legs trace with ctrace, and the workspace's tests are a stored unit
- The gate's own driver is not measured, only the tests
- Six frictions of the daily loop, and workshop's floor at 83
- The ecosystem rung carries PyPI's upload endpoint
- The dev plugin imports livery.toolroom, and the registration gate's test states both cases
- The train recovers an uncut wave only for the set that names it
- The registration gate's test reads the wheel case off the module's own file

### Changed

- The post-mortem's process rules enter the conventions
- Integrate and the wip park through their paces
- The docs seeds move to a shared package-base template
- The task reference documents the advertised tree
- The footman floor rises to 0.52.1
- The kind backend owns its gate composition
- The connection surfaces its credential
- The remaining quick wins on the leg's profile
- The test suite seeds git once and renders once
- The floor on forge names the co-released 0.3.0
- The floor on copier names 9.18.2, the locked version

## [0.1.0] - 2026-09-04

### Added

- The template source is the contract's call
- The other forges' CI variants, and the release legs
- Phase 7 closes green, and the stalled grace loosens
- Affected, coverage floors, and the 0.1.0 stamp
- Every fm call measured, floors judged on the union
- Fm ci.logs, the first verb the fm-only rule surfaced
- Releases derive their version and changelog from the commits
- Fm check --fix heals mechanical findings before the gate judges
- Abandon, submit.merge, submit --fix, and sync matches the lock
- Git-cliff writes the changelogs, per package, from the template
- The engine plan, and the pre-engine tidy
- The workflow engine: state, decision, abort, diagnostics
- The release train's driver, base gate, and two-leg validation
- The wave publishes, the receipts say when each member is done
- The update family rides the engine and finishes itself
- Dev releases, decided by the branch
- The isolated leg installs the gate's toolchain, aimed starvation
- The entered environment, clean, and the hook family
- The entered shell and the issue family
- Submit --force, a leased push with full disclosure
- No livery home, one shared env file, issues as the way of work
- Sync brings the checkout current, integrate is the merge spelling
- Governance in the workshop - owners, the applied contract, the heals
- Implement gitlab's licence-gated governance
- Phase 9, the branded runner
- Brand-ready runtime on footman 0.50
- Identity-free core on workshop.toml
- The template channel reads the artifact repository
- The environment store and the forge tokens
- Fm new.project, birth end to end
- The layer axis built
- The composed artifact release
- The dummy descendant proof chain

### Fixed

- A half-point coverage grace below the floor
- A running job's missing log is a line, not a crash
- Check --fix runs the fixing gates themselves, hse's shape
- Submit refuses an ambiguous title before pushing
- The changelog works on a private forge
- Close the phase 5 audit gaps
- Close the phase 6 audit gaps
- Close the phase 7a audit gaps
- Close the phase 7b audit gaps; assignment is documentation
- Close the phase 8b audit gaps
- Close the phase 9 audit gaps
- FORGE_ADMIN_TOKEN and zero-approvals default
- No livery-named environment variables, no fallbacks
- Livery is only the workspace name
- Ban every misuse note and speak toolroom
- Released-ness is the tag's, and stranded entries regenerate
- Derive_plans shares prepare's released-by-tag judgement
- The set builds every wheel before any isolated leg
- An explicit version regenerates a stranded entry too
- The isolated leg's toolchain pins skip the members
- Version tests assert the installed metadata, never a literal
- Pyyaml floors at the oldest current-Python release
- Floors on toolchain-shared deps align with the lock
- The dogfood sync test skips outside its checkout
- The deploy-key printf keeps its backslash-n
- The member-keys test clears the rung override
- The merged-PR guard allows a fresh cycle of a reused branch

### Changed

- The workshop's cheap coverage wins, floor ratcheted to 80
- Plan labels and jargon out of the published text
- The pre-0.1.0 roadmap, and the release story recorded
- The task shells lit through the resolution seam
- The workshop floor ratchets to the union's 84.5
- Fallbacks before happy paths, and the copy manifest learns content
- The workshop floor ratchets to the union's 87.4
- Migrate to footman 0.49
- The copier floor and pin move to 9.18.1

## 0.0.2 — 2026-08-31

- The whole dev loop: the quality family dispatched by contract, the
  layering lint, and the layer walk (`fm layers`).
- The content channel: `fm sync` materialises fragments, skills, and
  hooks from every mounted layer, and manages the CLAUDE.md stub.
- The template channel: `templates/` with the project and
  package-python kinds, `fm template.check` in the gate,
  `fm template.apply`, and `fm new.package`.
- The forge lane: `fm submit` (verify onto the remote; `--armed`
  lands it), `fm status`, `fm ci.rerun/watch/cancel`, `fm doctor`,
  `fm workflow.abort`, `fm workflow.merge-now`.
- The release train: `fm release.prepare` and `fm release.verify`,
  and the template snapshot publication (`fm release.templates`).
- The update wave: `fm update` bumps floors, refreshes content and
  render, and submits the result.

## 0.0.1 — 2026-08-31

- The plugin host: the `footman.tasks` entry point and the layer walk
  that mounts every layer named by the workspace contract, in order.
- Package skeleton: the `livery.workshop` PEP 420 namespace module
  and the package contract (`livery.toml`). Reserves the distribution
  name and proves the release train's second path.
