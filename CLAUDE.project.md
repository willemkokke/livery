# livery

The monorepo of the livery ecosystem. Current intent lives in
`notes/`: the workshop plan, judged against the development workflows
note.

## This repository's own facts

- Author identity: Willem Kokke <mail@willem.net>, SSH-signed.
- One tag class beyond releases exists: the annotated `archive/setup`
  tag cut at graduation, freezing the setup history the squash merge
  collapsed.
- A forge quirk without a deterministic reproduction is a debt:
  same-day fault mode or cassette, regression test, and a line in
  `packages/forge/docs/quirks.md`.
- `livery.forge` imports only the standard library at module import
  time; the one declared optional extra (PyNaCl, behind
  `livery-forge[github-secrets]`) loads lazily inside the one
  capability path that needs it. The one module exempt is the dev
  plugin `livery.forge._dev`, which may also import footman: it loads
  only through footman's own `plugin()`. Never create
  `livery/__init__.py`.
- The local forge containers: `fm forge.dev.up` (Gitea and GitLab,
  seeded), shipped by livery-forge and mounted through the layers
  list in `livery.toml`; the e2e accounts and their runbook are
  `notes/20260831-e2e-accounts-runbook.md`.
