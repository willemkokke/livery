# Changelog

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
