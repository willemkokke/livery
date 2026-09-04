# The docs toolchain: one rendered site per workspace

Status: approved and executing; Willem's go 2026-09-04. All five
open items ruled the same day (the decision record carries them).
Phase 1 shipped 2026-09-04 (issue #179, PR #180): the rendered
config, the build and serve verbs, the docs dependency group, and
the wheel-side `_docs` materialised at build. Phase 2 shipped
2026-09-04 (issue #181, PR #182): the API reference, every module
public first, mkdocstrings wired with the pinned inventories,
objects.inv published. Phase 3 shipped 2026-09-04 (issue #183, PR #184):
per-package changelog pages with the in-memory unreleased section,
and the paginated release view derived from the receipt tags.
Phase 4 shipped 2026-09-04 (issue #185, PR #186): the docs drift
tests and the required CI docs job on all three forge kinds. Phase
5 shipped 2026-09-04 (issue #187): the publish seams on all three
kinds, the Pages assertion in workflow.configure (proven live:
livery's Pages went from absent to the workflow build type in one
run), and the container seam proven against the rig registry.
This is phase 19 of `notes/20260903-workshop-plan.md`, promoted to
its own plan on Willem's ruling (2026-09-04): docs come before the
kind hierarchy, because nothing ships well undocumented, and the
topic is big enough to plan first. The research base is a same-day
survey of three reference implementations: hse (mkdocs-material,
composed monorepo site, toolchain shipped in the devkit wheel),
footman and toolroom (zensical, rendered config, generated pages,
drift-refusing docs tests).

## What this delivers

One rendered documentation site per workspace. Home from the root
`docs/` tree. One section per package: its `docs/` tree, its API
reference generated from every docstring, its changelog. One
release view for the whole workspace, derived at build time from
the receipt tags and the per-package changelogs. The toolchain is
workshop layer content, so a brand instance gets the same site
with its own name on it, and the dummy descendant receives it
through the update gradient, never through a template re-render.

## Ground-truth contracts (do not violate)

1. **Workshop is the product.** Every instance-visible name in the
   rendered docs config, the emitted CI jobs, and the site itself
   speaks the workspace's own identity. Livery appears only as
   `livery-*` distribution names and `livery.*` import paths on its
   own site.
2. **The docs config is rendered, and an edit there is drift.** The
   workshop renders the site config the way it renders `ci.yml`:
   generated header, drift-checked against the emitters, nav
   maintained between machine markers. Hand customisation goes
   through the contract, not through the rendered file.
3. **The API site documents every module in `src/`**, underscore-
   private included, public sorted before private at every level.
   This is `documentation-standards.md`'s standing promise: a
   docstring is published the moment it is written.
4. **The release view derives; nothing new is committed.** Receipt
   tags and per-package changelogs are the sources. A committed
   root changelog would be a merge choke point and stays banned
   (the 0901 plan's ruling, carried forward).
5. **Docs reach a descendant through the update gradient.** Willem's
   model add-back ruling: the toolchain lands in the workshop
   layer, a descendant receives it by taking the layer's release,
   and the proof runs in the armed chain. No template re-render.
6. **The merge path waits on nothing outside the repository.** The
   docs build may gate; the deploy never does. An instance without
   a configured publish target skips with exit 0, never refuses
   (hse's rule, kept for the same reason: docs must not deploy
   where they cannot be removed).
7. **Everything through fm.** The docs verbs are footman tasks; the
   generator runs through toolroom's typed handle; CI calls the
   same verbs.

## The generator: zensical, a named deviation from hse

hse runs mkdocs-material with a virtual-file stack (gen-files,
literate-nav, a wheel-shipped base config assembled at build time).
footman and toolroom run zensical with one explicit `zensical.toml`
and real generated files on disk, nav rewritten between markers.

This plan proposes zensical, deviating from hse, for three reasons:

- The workshop already owns rendered files with drift checks. A
  single explicit toml the emitters render wholesale is the ci.yml
  pattern applied again. hse's virtual-file machinery exists to
  avoid committing generated state; the workshop's answer to that
  problem is rendering plus `.gitignore`, and it needs no plugin
  stack.
- toolroom ships a typed `zensical` handle, and the build call
  (`tools.zensical.build(clean=True, strict=True)`) is already the
  house shape in footman and toolroom.
- footman and toolroom are the direction of travel; hse's mkdocs
  stack predates zensical.

What zensical does not cover, stated so the choice is honest:
social cards (footman writes OpenGraph tags in a template override
instead), the mkdocs-coverage page, and mkdocs's plugin ecosystem
generally. Its mkdocs compatibility surface is one shim, for
mkdocstrings, which is the one plugin this plan needs. Ruled:
zensical (Willem, 2026-09-04).

## The shape, piece by piece

- **Config**: the workshop renders `zensical.toml` at the workspace
  root from the contract (site name from the workspace identity,
  repo links from the `[forge]` table, nav between markers). A thin
  `[docs]` table in `workshop.toml` carries the instance's own
  facts: site title, optional custom URL, extra nav entries.
- **API reference**: a workshop generator walks every package's
  `src/`, writes one page per module into gitignored
  `docs/_generated/api/`, each page a `:::` directive, private
  modules included and sorted after public at every level (hse's
  sort ported). mkdocstrings-python renders them, Google style,
  `show_if_no_docstring: true`, signatures separated and
  cross-referenced. The site publishes `objects.inv`; inventories
  pull CPython, footman, and toolroom so cross-ecosystem references
  resolve. Cross-package references resolve inside the one site,
  which is what `documentation-standards.md`'s full-import-path
  rule exists for.
- **Changelogs**: one page per package from its `CHANGELOG.md`,
  with an in-memory `[Unreleased]` section from git-cliff at build
  time (hse's generator ported; files on disk never change).
- **The release view**: the workspace's site-wide changelog,
  derived from the receipt tags (annotated train receipts,
  gate-enforced since PR #172) joined with the changelog entries
  they point at, newest first across members. The release train's
  content-based manifest (PR #173) means the changelogs are
  authoritative for what each release contained; the tags are
  authoritative for when. Paginated, because it grows without
  bound (Willem, 2026-09-04): the landing page carries the newest
  releases, older ones land on generated per-year archive pages
  linked from it.
- **Verbs**: `fm docs.build` (generate everything, then a strict
  zensical build), `fm docs.serve` (generate, then live serve).
  Both in a `docs` group; the dependencies ride a `docs` dependency
  group (`zensical`, `mkdocstrings-python`).
- **Gate**: docs drift tests in the workspace suite, ported from
  footman's shapes: every docs page reachable from the nav, every
  internal link and anchor resolving, every `__all__` export
  carrying a docstring. The strict site build runs as an emitted,
  required CI job on every forge kind, never inside `fm check`
  (ruled 2026-09-04): the local loop stays fast, and a red docs
  build parks the merge instead of landing on main.
- **Publish**: a declared seam, not a per-forge hardcode (Willem's
  ruling, 2026-09-04). The contract's `[docs]` table declares a
  publish kind; the emitters render the matching CI step. The seams:
  `pages` (GitHub Pages, GitLab Pages), `container` (the built site
  baked into an nginx image and pushed to the forge's own container
  registry, which all three forges carry; the image is the site,
  deployable anywhere), `ssh` (hse's tar-over-ssh to a configured
  `DOCS_HOST`/`DOCS_USER`/`DOCS_ROOT`, with the key handling that
  never touches `~/.ssh`), and `none`. The forge kind picks the
  default (`pages` on github and gitlab, `container` on gitea); an
  unconfigured seam skips with exit 0 per contract 6. A seam that
  needs repository configuration gets it asserted, never clicked:
  `workflow.configure` gains the Pages assertion (GitHub's API
  enables Pages with the workflow build type) so the `pages` seam
  works on a repository nobody has touched in the forge UI.
- **Docs ride the wheel**: each package's `docs/` tree is embedded
  in its wheel as `<import_pkg>/_docs`. The mechanism is uv_build's
  own rule (a module's data ships, nothing outside it, the phase 14
  precedent): the build verb refreshes `_docs` as real files from
  the package's `docs/` immediately before every wheel build, so
  the wheel can never carry docs older than the tree it was built
  from. `_docs` is machine territory, gitignored, never edited by a
  person: authors write `packages/<name>/docs/` and nothing else
  (underscore folders mean keep out, ruled 2026-09-04). This plan
  ships the bytes only; the consumption side waits for the
  migration or the sparse-checkout phase, when a real consumer
  exists.
- **Seeds**: the project template seeds `docs/index.md`; the
  package template seeds `packages/<name>/docs/index.md`. This
  closes open item 4's residue in the 0903 plan.

## Phases

### Phase 1: the rendered config and the build verb

The `[docs]` contract table, the `zensical.toml` emitter with
header and nav markers, `fm docs.build` and `fm docs.serve` through
`toolroom.zensical`, the `docs` dependency group, the drift check
that refuses a hand-edited rendered config, and the wheel
embedding: the build verb materialises `_docs` inside the module
from `packages/<name>/docs/` before every wheel build, and the
materialised tree is gitignored. Livery's site builds strict from
the markdown that exists today, no API pages yet.

Acceptance:
- `uv run fm docs.build` exits 0 and `site/index.html` exists.
- A built member wheel contains its `_docs` tree, and editing a
  docs page then rebuilding refreshes it; proven by building and
  listing the archive twice.
- `uv run fm template.apply && git diff --exit-code zensical.toml`
  proves the rendered config is stable.
- Editing `zensical.toml` by hand and running the drift check
  refuses, naming the emitter.

### Phase 2: the API reference

The per-module page generator (private included, public first),
mkdocstrings wiring, inventories (CPython, footman, toolroom),
`objects.inv` published. The generated tree is gitignored.

Acceptance:
- `uv run fm docs.build` renders a page for a public module, a
  page for an underscore-private module, and sorts public before
  private in the nav; commands: build, then grep the generated nav.
- A docstring reference to `livery.forge.Forge` in
  `livery.workshop` prose renders as a link; proven by grepping
  the built page's html for the target href.

### Phase 3: changelogs and the release view

Per-package changelog pages with the in-memory unreleased section;
the derived release view over receipt tags and changelogs.

Acceptance:
- The built site carries `packages/forge` and `packages/workshop`
  changelog pages whose newest entries are 0.2.0 and 0.1.0.
- The release view lists livery-forge v0.2.0 and livery-workshop
  v0.1.0 dated from their receipt tags, newest first; proven by
  grepping the built page.
- The view paginates: a fixture workspace with releases across two
  years builds a landing page carrying the newest and a per-year
  archive page linked from it; proven in the generator's tests.
- `git status --porcelain` is empty after a build: derivation
  committed nothing.

### Phase 4: the docs gate

The drift tests (nav reachability, internal links and anchors,
exports carry docstrings) in the workspace suite, and the emitted
CI docs job running the strict build on every forge kind.

Acceptance:
- Each drift test goes red when its invariant is broken and green
  when restored; forced in the tests themselves, fallbacks first.
- The emitted ci files carry the docs job for all three kinds;
  `uv run fm template.apply && git diff --exit-code` stays clean.

### Phase 5: the publish seams

The `[docs]` publish-kind declaration, the seam emitters (`pages`
for github and gitlab, `container` for gitea, `ssh` as the
declared alternative), forge-kind defaults, skip-when-unconfigured,
the Pages assertion in `workflow.configure`, and livery's own
site live at `docs.willem.net/livery/` (ruled 2026-09-04). The
domain rides the account's user site: a `willemkokke.github.io`
repository carries the CNAME, every project site of the account
then serves under the domain, and each package section sits at
`docs.willem.net/livery/packages/<name>/`. The user-site repository
and the DNS record are Willem's one-time acts; livery's configure
assertion only enables Pages with the workflow build type.

Acceptance:
- `uv run fm workflow.configure` on a repository with Pages
  disabled enables it with the workflow build type; proven by the
  forge API answering that Pages is configured afterwards.
- Livery's site serves at `https://docs.willem.net/livery/` after
  a main merge, with `packages/forge/` and `packages/workshop/`
  sections beneath it; proven by curl against the deployed pages.
- The emitted GitLab pipeline carries the `pages` job publishing
  `public/`; proven by the emitter tests over the rendered file.
- On the local rig (gitea kind), the emitted container publish
  builds the site image and pushes it to the rig's registry; proven
  by pulling the image and curling the served site from a local run.
- A workspace declaring `none` emits no deploy step; an `ssh` seam
  without `DOCS_HOST` configured skips with exit 0 and says so.

### Phase 6: seeds and the gradient add-back

The template's `docs/` seeds, and the proof: the dummy descendant
takes the workshop layer's release carrying the toolchain and gains
a working `fm docs.build` through the update gradient.

Acceptance:
- A fresh `fm new.project` carries `docs/index.md`; a fresh package
  carries its own.
- The armed chain (`WORKSHOP_CONFORMANCE_DRIVE=1`) proves the
  descendant's docs build after a gradient update, never a
  re-render; the chain test's output is the evidence.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| Inventories pinned to footman's and toolroom's public sites | intra-site references when footman and toolroom migrate into this repository |
| GitLab kind without a docs deploy emitter | the `pages` seam applied to GitLab's pipeline shape, open item 3's cut |
| The rig's GitLab pages job proven by emitter tests only | a live GitLab Pages serve when a real GitLab workspace exists |

## Decision record

- 2026-09-04: Willem rules docs (this plan) before the kind
  hierarchy (0903 plan phase 17): nothing ships well undocumented,
  and the gradient add-back retires the last never-run-in-anger
  machinery under a low-stakes payload.
- 2026-09-04: plan first, after studying hse, footman, and
  toolroom; the three survey reports are the research base.
- 2026-09-04 (Willem): publishing is a seam, declared in the
  contract, with forge-kind defaults: GitHub Pages, GitLab Pages, a
  container image pushed to the forge registry, ssh, or none.
- 2026-09-04 (Willem): the five open items ruled. Zensical is the
  generator. No extras yet: the core first, social cards and
  llms.txt and the coverage page and executed examples join when a
  need arrives. GitLab's `pages` seam ships in phase 5 with the
  other two kinds. Livery's site lives at `docs.willem.net/livery/`
  (the domain on the account's user site, chosen now so nothing
  pins the default URL; packages sit at
  `docs.willem.net/livery/packages/<name>/`). The
  strict docs build is a required CI job on every forge, never part
  of `fm check`.
- 2026-09-04 (Willem): docs ride the wheel from phase 1; the
  consumption side waits for a real consumer. The mechanism is
  ruled the same day: not hatchling's force-include (the members
  build with uv_build, which ships a module's own data and nothing
  outside it, and refuses symlinked directories, both probed) but
  the phase 14 precedent applied again: the build verb materialises
  `_docs` inside the module as real files from `packages/<name>/docs/`
  before every wheel build. Underscore folders are machine
  territory: a person writes `packages/<name>/docs/`, never `_docs`.
- 2026-09-04, phase 5 cut: the container seam's image build lives
  in `fm docs.publish`, which the emitted gitea job calls; the
  rig's act_runner ships no docker socket by design, so the job
  needs a docker-capable runner and the rig acceptance ran the verb
  locally against the rig registry (pushed, pulled, served,
  curled). The pages seam skips inside the verb: the forge's own
  workflow deploys.
- 2026-09-04, phase 4 cut: nav reachability needs no test of its
  own, because the nav is emitted from the tree and the template
  drift gate refuses a stale render; an orphan page is drift before
  it is anything else. The drift tests cover what the emitter
  cannot know: link and anchor resolution, and exports carrying
  docstrings (source-level assignment docstrings included, which is
  how a TypeAlias documents itself). Both ship as a project seed.
- 2026-09-04, phase 3 cut: the release nav derives from committed
  state alone; a shallow CI clone has no tags, and the drift
  dogfood proved a tag-derived nav renders differently per
  checkout. The landing page links its own year archives at build
  time, and a tagless checkout serves a page saying so.
- 2026-09-04, phase 2 cut: a cross-reference in prose must be
  wrapped, `[livery.forge.Forge][]`; a bare dotted path renders as
  plain text and links nothing (probed against the built site).
  Both guidance fragments now teach the wrapped form, so the rule
  rolls to every workspace with the next workshop release.
- 2026-09-04, phase 1 cut: the config's default title is the root
  project's name, never the checkout directory's, because a
  worktree's directory name would make the same contract render
  differently per checkout and turn the drift gate against itself.
- 2026-09-04 (Willem): zensical is young, so every knob must roll
  out fleet-wide from one workshop release: the site config is
  rendered by the emitters, and the zensical and mkdocstrings
  versions ride the workshop-rendered dependency group. Replacing
  the generator, if it ever comes to that, is one emitter module
  and one dependency line, never a per-project edit.
- 2026-09-04 (Willem): after docs lands, toolroom and footman
  migrate into this repository as workspace members. Their zensical
  sites become sections of the one workspace site and the pinned
  cross-site inventories become intra-site references. The
  migration is its own future plan; this toolchain must simply not
  fight it, and its one-site-per-workspace shape already does not.

## Open

All five ruled 2026-09-04; the decision record carries the rulings.
Kept numbered so earlier references stay readable:

1. The generator: resolved, zensical.
2. The extras: resolved, none now.
3. GitLab's pages seam: resolved, phase 5.
4. The site's home: resolved, `docs.willem.net/livery/` under the
   account-level domain.
5. The docs build's gate: resolved, a required CI job.

Nothing awaits an owner. The plan awaits Willem's go.
