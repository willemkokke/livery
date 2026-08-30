# The livery monorepo

Plain-markdown docs, deliberately unrendered: once `livery.workshop`
is functional its docs toolchain renders these same files into the
site, so they are written as that site's seed, never as scratch.

The working record lives in `notes/`: plans, the development
workflows, and their decision records.

The gate is `uv run fm check`: format, lint, typecheck, and tests in
parallel. CI runs the same command. Nothing on the merge path waits on
anything outside the repository.

Layout: one package per `packages/<name>/`, discovered by its
`livery.toml`. Dependencies point only downward, enforced by the
workspace contracts test.

| Package | Docs |
| --- | --- |
| `packages/forge` | [index](../packages/forge/docs/index.md) |
