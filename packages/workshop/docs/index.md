# livery.workshop

The devkit: what every repository in the livery ecosystem runs.

- The task surface: `fm check` and its family, dispatched by each
  package's declared type in `livery.toml`.
- The layer model: the workshop is the base layer, an operator's
  overlay may follow, and the instance always wins. One list in the
  root `livery.toml` is the whole of discovery.
- The content channel: skills, hooks, configuration, and the managed
  `CLAUDE.md` fragment, materialised by `fm sync`.
- Templates: rendered projects and packages, published per release as
  a version-tagged artifact repository.

The forge lane belongs to `livery.forge`; the workshop orchestrates
local, git, and forge steps and never hands a raw forge verb to a
user.
