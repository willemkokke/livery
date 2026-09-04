# Changelog

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
