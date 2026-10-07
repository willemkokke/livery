# Changelog

## [0.6.0] - 2026-10-07

### Added

- A rate-limited forge response names when the budget returns, and a watch waits for it and slows while it runs low by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke

### Fixed

- The descendant chain passes again: it drives the dev containers with the credentials their seed recorded and takes this checkout's code from the checkout index by @willemkokke
- The abort reconcile spawns the runner by its real module, two footman invariant tests scan the real tree again, the push guard tests the branch a push names, and the review's smaller faults go by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

## [0.5.0] - 2026-10-04

### Added

- Every forge refuses a missing credential in one shape, GitLab falls back to glab, and the lane names its own variables by @willemkokke
- A package's docs section is the package's, and the site assembles its config at build time by @willemkokke
- The watch prints every job's move as it happens, stamped with the elapsed time, and names a red job with its failure lines at once by @willemkokke
- A forge release takes assets: upload_asset and assets on the protocol, on GitHub, Gitea, GitLab and the fake by @willemkokke
- A conan member's caches ride its own release, and every declared floor is built against by @willemkokke
- The commit a merge made, and a gate that skips a deleted test by @willemkokke
- The layering lint reads what the sources use, and each kind answers for its own by @willemkokke
- A managed file carries the regions the repository owns by @willemkokke
- The conformance loop's cpp-conan member, its runner on Debian, and the fixes the pass found by @willemkokke
- Every workshop.toml key is declared by the layer that reads it, and a contract holds nothing else: an unknown key, an unlisted layer's key, a wrong type or value refuses on read by @willemkokke
- Every distribution root is a namespace whose public names live in its api module, and the docs layer moves to livery.extensions.docs by @willemkokke
- Extensions replace layers: each declares itself in the workshop.extensions entry point group, and a workspace lists them by name in [workspace] extensions by @willemkokke
- Plugins mount through the project builtin rung: the workshop mounts the listed extensions from its own entry module, and the rendered tasks.py keeps only its comment by @willemkokke
- A tool requirement takes ? and !: an optional tool is locked where it can be served and never refused, and a plugin declares the tools its verbs need by @willemkokke
- Footman scans the installed entry points once per process and shares the scan through installed_entry_points, and a plugin declares its tools in a data module by @willemkokke
- The copier answers move into workshop.toml: identity in [workspace], members from discovery with their own description, dev-extras and template, and the answers files go by @willemkokke
- Tasks.py and each package's cliff.toml are composed by the fragment engine, so copier only births by @willemkokke

### Fixed

- The dev rig's runner image is glibc, node's Debian image with the runner binaries pinned by @willemkokke

### Changed

- The contract key type becomes kind by @willemkokke
- A verb that reaches no forge and no tool store loads neither: the forge, store and strongroom roots serve their names on first use, and the workshop and the bench import the store where they use it by @willemkokke

## [0.4.0] - 2026-09-16

### Added

- The state store reads and writes a series in a fixed handful of git processes by @willemkokke
- A given body updates a reused pull request by @willemkokke
- Livery.toolroom is a namespace, the handles live in livery.toolroom.tools by @willemkokke
- The merged head decides what a merge left behind, and the release act cleans up after itself by @willemkokke
- The loop's verbs at the top level: start, commit, abandon a branch, submit --no-close, and issue.reopen by @willemkokke
- Tests are namespaced by their path, and a helper carries its package's name by @willemkokke
- The gate runs on command, and a GitLab pipeline names its workflow by @willemkokke
- One renderer per forge for the gate, the merge and the nightly, and the clock on GitLab by @willemkokke
- Every GitLab pipeline names its workflow, a merge request pipeline is a pull request run, and the recorder's pytest is a child by @willemkokke
- The GitLab lane is born: fm ci.e2e --forge=gitlab merges the setup PR on the real runner and proves the verified skip by @willemkokke
- The GitLab runner carries the toolchain, the docker socket is opt-in on both lanes, and the members and the three legs prove themselves on merge request pipelines by @willemkokke
- The release act, the points by hand and the clock on GitLab: the loop is whole on both lanes by @willemkokke
- Issue.show prints one issue whole, and the protocol reads an issue's thread by @willemkokke
- Forge.conformance runs the suites in replay or live against the local forges, the cassettes untouched by @willemkokke

### Fixed

- The test HTTP servers stop on a short poll by @willemkokke
- The dev plugin's tests skip where footman is absent by @willemkokke

## [0.3.0] - 2026-09-11

### Added

- The publish seams, declared in the contract per forge kind
- The registry ladder and the kind registry, opened to layers
- Issue bodies through fm
- Package-owned nav for the docs site
- The per-package task reference
- Toolroom joins the workspace
- Footman joins the workspace
- The version rules speak as the ecosystem's own
- The train carries a migrated line's first release
- Fm ci.e2e drives the whole loop against the local forge
- The server blocks red merges on every forge
- Receipt tags are protected on every forge
- Timing rows on the state store, and fm ci.timings
- Contract keys are kebab-case; one loader refuses underscores, the render verbs migrate
- The wheels matrix reads wheel-platforms; the loop builds a nanobind wheel through the docker socket
- Every test runs apart from the live runner state, and the registry stays clean
- The gate judges statements and branches, and the record holds arcs
- Dispatch and read the nightly, ride out dropped connections, merge notes by union, judge the task nav

### Fixed

- Ci.rerun re-runs the branch's verdict, read back
- The dev runner image carries its tools; docker speaks toolroom
- Tie the submit verdict to the pushed head
- Six frictions of the daily loop, and workshop's floor at 83
- The dev plugin imports livery.toolroom, and the registration gate's test states both cases

### Changed

- The connection surfaces its credential

## [0.2.0] - 2026-09-04

### Added

- Split the task surface between the layers and the instance ([#18](https://github.com/willemkokke/livery/pull/18))
- The template channel, and the monorepo as its own instance ([#20](https://github.com/willemkokke/livery/pull/20))
- The other forges' CI variants, and the release legs
- Affected, coverage floors, and the 0.1.0 stamp
- Abandon, submit.merge, submit --fix, and sync matches the lock
- The forge protocol builds its own addresses
- Git-cliff writes the changelogs, per package, from the template
- The workflow engine: state, decision, abort, diagnostics
- The release train's driver, base gate, and two-leg validation
- The wave publishes, the receipts say when each member is done
- The isolated leg installs the gate's toolchain, aimed starvation
- The entered shell and the issue family
- No livery home, one shared env file, issues as the way of work
- Governance in forge - listings, codeowners, approvals, admins bound
- Governance in the workshop - owners, the applied contract, the heals
- Implement gitlab's licence-gated governance
- Identity-free core on workshop.toml
- The layer axis built

### Fixed

- The held-run release works off-machine, and the legs' lessons
- The legs' second round of lessons
- Evidence survives failure, and live names go unique
- Pinned gitea digests, surviving evidence, and gentler sweeps
- Decisive gitea evidence, and a poll budget for slow runners
- Recording scratch goes to the e2e organisation
- The changelog works on a private forge
- Close the phase 7b audit gaps; assignment is documentation
- Close the phase 8a audit gaps
- No livery-named environment variables, no fallbacks
- Livery is only the workspace name
- Ban every misuse note and speak toolroom
- Version tests assert the installed metadata, never a literal
- The simple-index probe reads PEP 503 HTML indexes

### Changed

- The error arms a green conformance run never reaches ([#11](https://github.com/willemkokke/livery/pull/11))
- The cassette recorder moves into forge's dev plugin ([#19](https://github.com/willemkokke/livery/pull/19))
- Plan labels and jargon out of the published text

## 0.1.0 — 2026-08-31

- The protocol, frozen: all three backends and the verified fake pass
  the one conformance suite, so the drafts below are the contract.
- The protocol: `Forge`, `Repository` (with the `pr`, `checks`,
  `issue`, and `release` groups), and `Registry`, with the value types
  they speak and `ForgeError` carrying the server's own words.
  `cancel_run(run, *, force=False)` is required everywhere, `force`
  being the first capability probe.
- `GitlabForge`: the GitLab backend (REST v4, stdlib only), the odd
  one out made real: iids never leak, pipelines are the checks
  answer, `force_cancel` and `required_contexts` are declined by
  name, and the asynchronous behaviours the container taught are
  absorbed at the boundary (see the package's quirks list).
- `ci_secrets` joins the capabilities. Gitea and GitLab support it
  outright; on GitHub it rides the `github-secrets` extra (PyNaCl for
  the sealed-box encryption the secrets API demands), loaded lazily,
  with `supports("ci_secrets")` answering for the running install.
- `GithubForge`: the GitHub backend (REST plus the GraphQL auto-merge
  pair), token resolution `GITHUB_TOKEN` then `gh auth token`.
- `GiteaForge`: the Gitea backend (REST v1, stdlib only), with the
  1.28 server floor probed at `cancel_run` and the one-host token
  rule (`gitea_is_configured_host`).
- `livery.forge.testing`: the verified `FakeForge` with deterministic
  fault injection (`Faults`), the conformance suite (`SCENARIOS` over
  a per-backend `ForgeDriver`), and the HTTP record and replay layer
  (`Cassette`, `RecordingOpener`, `ReplayOpener`) with secrets
  scrubbed at record time. The conformance driver states each push's
  CI outcome at push time (`Outcome`), which is what a real forge can
  actually be made to do.

## 0.0.1 — 2026-08-31

- `Unsupported`: the exception a backend raises when the server
  predates an operation or a capability is declined by name.
- Package skeleton: the `livery.forge` PEP 420 namespace module, the
  package contract (`livery.toml`), and the stdlib-only runtime rule
  under test. Reserves the distribution name and proves the release
  train end to end.
