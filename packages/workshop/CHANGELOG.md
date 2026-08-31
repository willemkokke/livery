# Changelog

## 0.0.2 — 2026-08-31

- The whole dev loop: the quality family dispatched by contract, the
  layering lint, and the layer walk (`fm layers`).
- The content channel: `fm sync` materialises fragments, skills, and
  hooks from every mounted layer, and manages the CLAUDE.md stub.
- The template channel: `templates/` with the project and
  package-python kinds, `fm template.check` in the gate,
  `fm template.apply`, and `fm new.package`.
- The forge lane: `fm submit` (verify onto the remote; `--armed`
  lands it), `fm status`, `fm ci.rerun/watch/cancel`, `fm doctor`,
  `fm workflow.abort`, `fm workflow.merge-now`.
- The release train: `fm release.prepare` and `fm release.verify`,
  and the template snapshot publication (`fm release.templates`).
- The update wave: `fm update` bumps floors, refreshes content and
  render, and submits the result.

## 0.0.1 — 2026-08-31

- The plugin host: the `footman.tasks` entry point and the layer walk
  that mounts every layer named by the workspace contract, in order.
- Package skeleton: the `livery.workshop` PEP 420 namespace module
  and the package contract (`livery.toml`). Reserves the distribution
  name and proves the release train's second path.
