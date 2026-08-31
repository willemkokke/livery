# livery-workshop

The livery ecosystem's devkit, served as a footman plugin: a
repository's whole `tasks.py` is `plugin("livery.workshop")`.

- The quality family (`fm check`: format, lint, four type checkers,
  type-completeness, tests, the render gate), dispatched by each
  package's `livery.toml` contract.
- Layers: the workspace contract names them, `fm sync` delivers their
  content (guidance fragments, skills, hooks, the managed CLAUDE.md
  stub), and the instance's own files always win.
- Templates: `fm template.check` keeps rendered files byte-identical
  to their source, `fm new.package` renders a member.
- The forge lane, on [livery-forge](https://pypi.org/project/livery-forge/):
  `fm submit` gets a branch onto the remote verified (`--armed` lands
  it), with `fm status`, `fm ci.*`, `fm doctor`, and the workflow
  exits beside it.
- The release train (`fm release.*`) and the update wave (`fm update`).

Part of the [livery monorepo](https://github.com/willemkokke/livery).
