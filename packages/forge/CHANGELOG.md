# Changelog

## Unreleased

- The protocol draft: `Forge`, `Repository` (with the `pr`, `checks`,
  `issue`, and `release` groups), and `Registry`, with the value types
  they speak and `ForgeError` carrying the server's own words.
  `cancel_run(run, *, force=False)` is required everywhere, `force`
  being the first capability probe.
- `livery.forge.testing`: the verified `FakeForge` with deterministic
  fault injection (`Faults`), the conformance suite (`SCENARIOS` over
  a per-backend `ForgeDriver`), and the HTTP record and replay layer
  (`Cassette`, `RecordingOpener`, `ReplayOpener`) with secrets
  scrubbed at record time.

## 0.0.1 — 2026-08-31

- `Unsupported`: the exception a backend raises when the server
  predates an operation or a capability is declined by name.
- Package skeleton: the `livery.forge` PEP 420 namespace module, the
  package contract (`livery.toml`), and the stdlib-only runtime rule
  under test. Reserves the distribution name and proves the release
  train end to end.
