# The docs prepass: package-owned docs in the workshop toolchain

Status: approved 2026-09-05 (Willem); phase 1 landed 2026-09-05
(issue #231), phases 2 to 6 not started. Written 2026-09-05 from
side-by-side inventories of footman's and toolroom's docs
machinery. This plan blocks phase 1 of
`notes/20260905-footman-toolroom-migration.md`: when its phases
land, both packages arrive as instances of the standard docs
setup instead of as exceptions to it.

## What the inventories found

The workshop docs toolchain
(`notes/20260904-docs-toolchain-plan.md`, shipped) already covers
the rendered config with nav markers, the API reference with
cross-package inventories, per-package changelog pages, the
release view, the core drift tests, and the publish seams. The
delta is what footman and toolroom built beyond that. The ruling
principle: the workshop absorbs what every package wants, grows a
seam for what stays package-specific, and names what it drops.

| Mechanism (source) | Disposition |
| --- | --- |
| Deep hand-authored nav (footman, ~90 pages; toolroom) | absorbed: package-owned nav, phase 1 |
| Nav segment rewritten between machine markers (toolroom's tool pages) | absorbed into the package nav format, phase 1 |
| Generated pages: errors reference, curated `api.md`, task reference, config/globals/notes tables (footman); per-tool pages, colour page (toolroom) | seam: package-declared generator verbs, phase 2; the generators stay package code |
| pty screenshots and animated shell casts (footman; needs zsh, fish, nushell in CI) | seam, phase 2, with declared system requirements for the emitted docs job |
| Snippets, abbreviation tooltips, mermaid fences, tabbed content, emoji, arithmatex | absorbed: the rendered config's extension surface, phase 3 |
| Theme override with OpenGraph and Twitter card tags; palette and type css; fonts | absorbed as layer content with instance facts, phase 3 |
| Pyodide playgrounds, cast player, vendored CodeMirror (both) | package content; needs only the css/js/asset passthrough, phase 3 |
| `llms.txt` and `llms-full.txt` (footman) | absorbed: one pair per site, phase 4 |
| Task-reference rendering and the `docs_url` linking (footman's renderer, task names hyperlinked in `--list`/`--tree`/`--help`/`--json`) | absorbed: the per-package task reference, phase 6 |
| Coverage page with the htmlcov iframe and CI artifact plumbing (footman) | absorbed: the tool-neutral coverage seam, phase 5 |
| Docs drift tests beyond the already ported set (examples execute, jq recipes run, version pins track, llms cleanliness) | travel as package tests; the ones that read the repo `zensical.toml` re-target the package nav at migration |
| Package-level zensical wiring: each repo's own `zensical.toml`, docs tasks calling zensical, docs dependency group, docs deploy workflow | dropped at migration. Zensical belongs to the workshop (contract 5); a package keeps content, nav, and seam declarations only |

## Ground-truth contracts (do not violate)

1. **Workshop is the product.** Instance-visible names in rendered
   files and on the site speak the workspace.
2. **Authors write `packages/<name>/docs/` and its nav. Every
   generated tree is machine territory**, gitignored, rebuilt by
   `fm docs.build` from a clean slate. Carried from the toolchain
   plan.
3. **The root `zensical.toml` stays rendered and drift-checked.**
   Package nav is authored content merged at render time; a hand
   edit of the rendered root still refuses, naming the emitter.
4. **The strict docs build stays an emitted, required CI job,
   never inside `fm check`.** Carried ruling, 2026-09-04.
5. **Docs are generated only through the workshop, and emitted
   CI run steps call only the runner.** Zensical belongs to the
   workshop; no bare `zensical` invocation anywhere: not in
   emitted CI, not in package tasks, not in docs prose. Packages
   reach the generator only through `fm docs.build` and
   `fm docs.serve`. The wider rule: a rendered workflow's run
   steps invoke the workspace's runner verb (`fm`, or the
   brand's own name for it) and almost nothing else; forge-native
   actions for checkout, runtime setup, artifacts, and deploys
   are the named exceptions, because they are not tool
   invocations. The emitter tests refuse a rendered run step
   calling a tool directly (toolroom's old CI had exactly this
   fault: a bare strict zensical build over gitignored pages it
   never regenerated).
6. **Every capability lands with an in-repo consumer proving it
   before the migration relies on it**, and a second run on
   unchanged input is a no-op.

## Phases

### Phase 1: package-owned nav

Each package owns the nav of its own site section, at depth.

Deliverables:

- `packages/<name>/docs/nav.toml`: the package's nav tree,
  hand-authored. It lives in the docs tree, not in the package
  `workshop.toml`, because a ninety-entry nav is authored content
  and the contract file stays small.
- The config emitter merges each package's `nav.toml` under that
  package's section of the rendered root `zensical.toml`. The
  workspace `[docs]` extra entries keep working unchanged.
- Machine-marker segments inside `nav.toml` (begin/end comment
  markers, toolroom's rewrite shape ported without the baked
  indentation), so a package generator can maintain a nav block
  while the surrounding tree stays hand-authored.
- Nav seeds for the existing packages, and the template's package
  seed gains one.
- The nav drift tests extended per package: a `nav.toml` entry
  naming a missing page is red, a docs page absent from the nav
  is red.
- The scoped dev build: `fm docs.build --package <name>` and
  `fm docs.serve --package <name>` materialise a scoped config
  into a gitignored build directory (the rendered root
  `zensical.toml` is never touched) carrying the workspace chrome
  plus only that package's section, API pages, changelog, and
  generators. Cross-package references resolve outward through
  the published workspace site's `objects.inv`. Scoped output is
  a preview, never published; the workspace site stays the only
  deploy artifact. The scoping is discovery-driven, the same
  mechanism the sparse-checkout phase reapplies later.

Acceptance:

- `uv run fm docs.build` green; the built site carries the
  existing packages' sections per their `nav.toml`.
- `uv run fm template.apply && git diff --exit-code zensical.toml`
  stable, twice.
- Both drift directions forced red and restored in the tests.
- `uv run fm docs.build --package workshop` builds only that
  package's section over the workspace chrome, into the
  gitignored build directory, with `git status --porcelain`
  empty afterwards.

### Phase 2: the generator seam

A package brings its own page generators; the toolchain runs
them.

Deliverables:

- A package's `workshop.toml` `[docs]` table declares generator
  verbs (footman tasks the package ships). `fm docs.build` runs
  each package's generators before the site build.
- The two generated homes, workspace-wide and template-seeded
  into `.gitignore`: `packages/<name>/docs/_generated/` for site
  content, `packages/<name>/_generated/` for snippet sources that
  are included but never published as pages. Ported from
  footman's layout.
- A generator declaration may name system requirements (footman's
  casts need zsh, fish, and nushell); the emitted docs CI job
  installs the union of declared requirements.
- The armed-chain dummy package gains a small generator, the
  in-repo consumer proving the seam end to end through the update
  gradient.

Acceptance:

- The chain run shows the dummy's generated page in its built
  site; a second build leaves `git status --porcelain` empty.
- A declared generator verb that fails turns the docs build red
  with the verb named; forced in the tests.
- The emitted CI docs job carries a declared requirement; proven
  by the emitter tests over the rendered file.

### Phase 3: the config surface and the theme

The rendered config grows the surface both packages need; the
site's branding becomes layer content.

Deliverables:

- The extension set adopted as the standard: `pymdownx.snippets`
  with per-package base paths and `check_paths`, `abbr` with
  `auto_append` aggregating every package's
  `docs/includes/abbreviations.md`, superfences with the mermaid
  custom fence, tabbed content, emoji, arithmatex.
- A duplicate-abbreviation drift test: the same term defined
  differently by two packages is red, because tooltips are
  site-wide.
- A package's `[docs]` table declares `extra_css` and
  `extra_javascript`; the emitter renders them with
  package-scoped paths. Package `docs/assets/` trees pass through
  to the site.
- The theme override as layer content: the `extrahead` block with
  OpenGraph and Twitter card tags computed from contract facts
  (site name, description, an instance-seeded card image), the
  brand-leads-on-home rule ported. Palette and type css as layer
  defaults an instance may override.

Acceptance:

- Grep of the built home page html shows the OpenGraph tags
  carrying the workspace's own name and image.
- A package-declared css file appears in the rendered config and
  in the built site; proven by build plus grep.
- The duplicate-abbreviation test forced red and restored.
- `uv run fm template.apply && git diff --exit-code` stays clean.

### Phase 4: llms.txt for the site

Deliverables:

- `llms.txt` and `llms-full.txt` generated at the site root from
  the merged nav: one line per page with a first-sentence
  description and absolute links, the full variant with snippet
  includes resolved inline. Footman's generator ported into the
  toolchain.
- Footman's llms cleanliness drift test ported with it, its
  historic defect classes forced.

Acceptance:

- `uv run fm docs.build` writes both files; grep shows a page
  line with its description and an absolute URL.
- The cleanliness test forced red on each defect class and
  restored.

### Phase 5: the coverage seam

Coverage stays on the site, displayed as an iframe over the
report's own HTML, and the seam is tool-neutral so a C++
package's report rides it the same way as a Python one.

Deliverables:

- A package's `[docs]` table declares named coverage reports:
  a label and the path where the package's own tooling writes a
  static HTML tree. The toolchain never learns the producing
  tool; coverage.py's htmlcov and a gcovr or llvm-cov HTML tree
  are the same thing to it.
- `fm docs.build` copies each declared tree into the package's
  site section and renders a coverage page per package, one
  iframe per report.
- The absent-report fallback: a declared report that is missing
  renders a page saying so, and the build stays green. Footman's
  CI placeholder trick becomes the mechanism, not a hand step.
- The emitted CI plumbing: check jobs upload coverage artifacts,
  the docs job consumes them, ported from footman's shape.

Acceptance:

- A real Python package's htmlcov displays in the built site,
  and a fixture static HTML tree declared as a second report
  displays beside it, standing in for C++ until a native package
  member exists; proven by build plus grep of the iframe srcs.
- The absent-report fallback forced first: a declared, missing
  report builds green and the page names the absence.
- The emitted CI files carry the artifact plumbing; proven by
  the emitter tests over the rendered files.

### Phase 6: the task reference, per package

Task documentation is a per-package capability with one
mechanism. Every package that provides task groups gets its
tasks documented in its own site section with footman's
renderer; that is what the renderer is for. The workshop's task
documentation is no different from any other package's: the
workspace `tasks.py` only mounts plugins, every task has an
owning package, and the owning package's section is where its
pages render. Nothing is special-cased. Footman is already the
workspace's runner, so the renderer is importable before the
migration.

Deliverables:

- `fm docs.build` renders, for each package providing tasks, an
  index per group and a page per public task, into that
  package's gitignored generated tree, with nav entries in that
  package's section.
- The `docs_url` linking kept: task names in `--list`, `--tree`,
  `--help`, and `--json` output link to their pages in the
  owning package's section of the site. The URL derives from
  the `[docs]` contract's site facts, never hand-configured.

Acceptance:

- The built site carries task pages under the workshop's own
  section and under a second provider's section (forge's dev
  plugin), rendered by the one mechanism; proven by build plus
  grep of both paths.
- `uv run fm --list` output carries a docs link under the
  contract's site URL pointing into the owning package's
  section; proven by running it and grepping.
- `git status --porcelain` empty after a build; a second build
  is a no-op.

## Out of scope, named

- The playground machinery. It is package content and rides
  phase 3's passthrough; the workshop never learns Pyodide.
- Footman's and toolroom's generator internals. They migrate as
  package code and plug into phase 2's seam unchanged.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The dummy package's generator as the seam's only consumer | footman's and toolroom's real generators, at the migration's phases 1 and 2 |
| Abbreviation glossaries aggregated from zero packages | footman's glossary when it migrates |
| The site's seeded OpenGraph card image | an instance-authored card, whenever Willem draws one |
| The fixture HTML tree standing in for a C++ coverage report | a real report when a native package joins the workspace |
| The iframe presentation of coverage reports | kept until ruled otherwise; good enough for now (Willem, 2026-09-05) |

## Decision record

- 2026-09-05: the plan approved and phase 1 started (Willem, "start
  docs pre pass"), after the extensible gate's phase 0 landed the
  bare `fm` spelling.
- 2026-09-05, phase 1: a package without a `nav.toml` keeps the
  enumerated section (index first, then sorted). The seeds cover
  forge and workshop, the package template's docs seed carries one,
  and the enumeration is the birth fallback, not a second
  first-class shape.
- 2026-09-05, phase 1: the scoped preview builds non-strict, into
  `.docs-preview/<name>/`. Chrome pages may link into sections the
  preview does not carry, so a strict scoped build would always be
  red; the workspace build owns strictness, and the preview says
  "never published" in its output.
- 2026-09-05, phase 1: nav drift is enforced in the config emitter
  itself (a missing page and an orphaned page each refuse naming
  the file), so the template drift gate and every docs verb carry
  the check without a separate test seed; the forcing tests live in
  the workshop suite. Entries under `_generated/` are exempt both
  ways, because a generator writes them at build time and the
  strict site build owns them.

- 2026-09-05: the prepass precedes the migration, as its own
  plan, because footman's and toolroom's docs setups are more
  evolved than the freshly landed toolchain (Willem).
- 2026-09-05: drafted from the two inventories; the dispositions
  table above is the ruling on what is absorbed, seamed, and
  dropped.
- 2026-09-05: coverage stays on the site, the iframe over the
  report's own HTML is fine for now, and the seam is tool-neutral
  so C++ coverage can be passed and displayed the same way
  (Willem).
- 2026-09-05: docs are generated only through the workshop. No
  manual zensical invocation anywhere; zensical belongs to the
  workshop (Willem).
- 2026-09-05: emitted CI workflows should almost always only
  call `fm`, or its branded name equivalent. Forge-native
  actions for checkout, setup, artifacts, and deploys are the
  exceptions; a run step invoking a tool directly is drift
  (Willem).
- 2026-09-05: the workshop documents all its public tasks with
  footman's renderer, any package providing tasks appears, and
  the `docs_url` task-to-docs linking stays (Willem). Phase 6.
- 2026-09-05: the workshop's task documentation is no different
  from any other package's. The reference is per package, one
  mechanism, every task documented in its owning package's
  section, nothing special-cased (Willem).

## Open

Nothing open as of 2026-09-05.
