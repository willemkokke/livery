# livery-forge

One interface to GitHub, Gitea, and GitLab. Pre-alpha: the `Forge`,
`Repository`, and `Registry` protocols are drafted, and
`livery.forge.testing` carries the verified `FakeForge`, the
conformance suite every backend must pass, and the HTTP record and
replay fixtures. The real backends arrive next; the protocol freezes
when all of them pass the one suite.

Runtime dependencies: the Python standard library, nothing else.

Part of the [livery monorepo](https://github.com/willemkokke/livery).
