# livery-forge

One interface to GitHub, Gitea, and GitLab. The `Forge`,
`Repository`, and `Registry` protocols are frozen: all three backends
and the verified `FakeForge` pass the one conformance suite in
`livery.forge.testing`, which also carries the deterministic fault
injection and the HTTP record and replay fixtures every consumer
tests against. `livery-forge[github-secrets]` adds sealed-box CI
secrets on GitHub.

Runtime dependencies: the Python standard library, nothing else.

Part of the [livery monorepo](https://github.com/willemkokke/livery).
