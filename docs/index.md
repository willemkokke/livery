# The livery monorepo

Plain-markdown docs, deliberately unrendered: these files are
written as the seed of the rendered per-package site, never as
scratch.

The working record lives in `notes/`: plans, the development
workflows, and their decision records.

The gate is `uv run fm check`: format, lint, typecheck, and tests in
parallel. CI runs the same command. Nothing on the merge path waits on
anything outside the repository.

Layout: one package per `packages/<name>/`, discovered by its
`workshop.toml`. Dependencies point only downward, enforced by the
workspace contracts test.

| Package | Docs |
| --- | --- |
| `packages/forge` | [index](_generated/packages/forge/index.md) |
