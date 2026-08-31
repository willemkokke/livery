# livery.forge

One interface to GitHub, Gitea, and GitLab. The protocol is small on
purpose: a verb exists because a development workflow uses it, and a
verb no workflow uses is removed.

- Capabilities, not pretence: where forges differ the interface says
  so (`forge.supports(...)`) instead of papering over it.
- Stdlib-only at runtime, on every platform the ecosystem supports.
- Backends: `_github`, `_gitea` (server 1.28 or later), `_gitlab`,
  plus the verified `FakeForge` every consumer tests against. One
  conformance suite gates all of them, the fake included, and the
  protocol freezes only when every backend passes it.

Where to read next:

- [protocol.md](protocol.md): the verbs, their groups, and the rules
  every method obeys.
- [gitea.md](gitea.md): the Gitea backend, its token rule, and its
  1.28 server floor.
- [gitlab.md](gitlab.md): every protocol method mapped onto GitLab's
  REST v4 API, the odd one out that keeps the protocol honest.
- [fixtures.md](fixtures.md): the record and replay layer that lets
  backend tests gate merges with no network.
- [quirks.md](quirks.md): what the real forges taught us, each entry
  reproduced by a deterministic `FakeForge` fault mode.
