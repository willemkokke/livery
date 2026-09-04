# Musings

Raw material, dated, appended. Never a plan: a musing graduates by
being written into one.

## 2026-09-04: docs customisation, after phase 17

Two designs discussed with Willem the day the docs toolchain
shipped, both deliberately deferred until phase 17 settles the
layer-registry question.

**Hierarchical docs properties.** Easy knobs like whether the API
reference includes private members, resolved package over workspace
over built-in default: a `[docs]` key in the workspace's
`workshop.toml` sets the default (`api_private = false`), the same
key in a package's own `workshop.toml` overrides it for that
package, and the emitter reads both the way the layering and floor
contracts already do. No new mechanism; a small emitter change when
taken up.

**Layered docs styling.** Layers ship `content/docs/` beside their
fragments: `theme.toml` fragments merged key-by-key in layer order
into the rendered config's theme table; `assets/` composed
same-name-topmost-wins, with `extra_css` listed in layer order so
the CSS cascade agrees with layer precedence; `overrides/` composed
the same way for theme template overrides, wholesale replacement
carrying the overlay `[[replace]]`-with-reason discipline. The
workspace's own committed `docs/assets/` and `docs/overrides/`
compose last, and the contract's `[docs]` knobs win for facts. One
precedence rule throughout: base layer, brand layers in contract
order, the workspace's tree, the contract. A brand restyle is one
layer release through the gradient.

Not covered by either: replacing the generators themselves (a
different release-view or API page shape). That wants phase 17's
registry opened to layers, not a docs-only extension mechanism.
