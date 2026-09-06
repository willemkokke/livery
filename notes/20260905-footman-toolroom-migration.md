# Footman and toolroom join the workspace

Status: phase 1 landed 2026-09-06 (issue #247); phases 2 to 4 not
started. Approved in intent 2026-09-05 (Willem: "lets move them",
both at once); the docs prepass that blocked it completed
2026-09-05. Strongroom's initial plan
follows this one; toolroom's store rework over strongroom is out of
scope here.

Both repositories move into this workspace as packages. The point,
in Willem's words: they end up proper package instances that evolve
with the workshop in the future. Not code parked in a subdirectory,
but template-governed packages that receive every workshop layer
release through the update wave, the same as forge and workshop do.

## Sequencing

1. The docs prepass, its own plan note. Footman's and toolroom's
   docs setups carry features the workshop docs toolchain does not
   have yet (hand-authored per-package navigation at depth,
   generated pages such as footman's errors reference and
   toolroom's per-tool pages, llms.txt, theme overrides). The
   prepass ports the generalisable ones into the workshop layer,
   so both packages arrive as instances of the standard setup
   instead of as exceptions to it.
2. This plan's phases, once the prepass capabilities have landed.

## Ground-truth contracts (do not violate)

1. **Files move at HEAD. History stays in the originals.** No
   filter-repo, no merge of unrelated histories, no imported tags.
   Each import is an ordinary commit through the ordinary issue
   flow. The original repositories are archived read-only after
   their import merges, and they keep the full history, the tags,
   and the old releases. CHANGELOG.md moves as a file, so the
   release record travels without the commits.
2. **Workshop is the product.** Instance-visible names in rendered
   files and on the site speak the workspace. Livery appears only
   in `livery-*` distribution names and `livery.*` import paths.
3. **Both packages become template-governed instances.** Rendered
   files carry the generated header and pass drift. A workshop
   layer release reaches them through the update gradient, never
   through a hand edit of a rendered file.
4. **The release train owns tags and versions.** The first
   workspace release of each package is an annotated train
   receipt cut by `fm release.prepare`. No hand tags, no tags
   copied from the originals.
5. **Both packages stay dependency-free at runtime.** Today both
   declare `dependencies = []`. That property is pinned by test
   before the import lands, and toolroom's first runtime
   dependency remains reserved for strongroom. The shim
   distributions are exempt: each depends on exactly its real
   distribution and nothing else.
6. **The gate is green at every phase cut.** `uv run fm check`,
   nothing on the merge path waits on anything outside the
   repository.

## Phases

### Phase 1: toolroom lands

Toolroom is first: smaller, and it does not run the gate.

Deliverables:

- Toolroom's local main pushed (3 commits ahead of origin at the
  time of writing), so the import point is public before it is
  copied.
- `packages/toolroom/`: `src/`, `tests/`, `docs/`, `notes/`,
  `CHANGELOG.md`, copied at HEAD. Distribution name
  `livery-toolroom`; the import package renamed to
  `livery.toolroom` in the adaptation commits, under the PEP 420
  namespace with no `livery/__init__.py`.
- `packages/toolroom-compat/`: the `toolroom` distribution as a
  thin shim. One re-export of the public surface
  (`from livery.toolroom import *` with the same `__all__`,
  `py.typed`, one forwarder per public submodule,
  `toolroom.testing` included), depending on `livery-toolroom`
  with a version floor. Its version continues the old line past
  0.6.1, so an upgrade of `toolroom` resolves onto the shim.
- `workshop.toml` for the package, rendered files
  (`cliff.toml` and the rest) drift-green, the package in the
  workspace layering graph.
- A disposition list in the import commit body for everything not
  copied. Build outputs (`site/`, `__pycache__/`) are dropped.
  `tool-history/` and the repository-level `CLAUDE.md` are ruled
  at the cut.
- The package's own docs wiring does not come across: its
  `zensical.toml`, docs tasks that call zensical, docs dependency
  group, and docs deploy workflow. Docs are generated only
  through the workshop; the package keeps content, nav, and the
  prepass plan's seam declarations. Applies to footman in phase 2
  the same way.
- The pinning test for `dependencies = []`.
- The origin repository archived read-only after the merge.

Acceptance:

- `uv run fm check` green.
- `uv run fm typecomplete` green for the package.
- `uv run python -c "from livery import toolroom; print(toolroom.__file__)"`
  prints a path under `packages/toolroom/src/`.
- `uv run python -c "import toolroom, livery.toolroom; assert toolroom.__all__ == livery.toolroom.__all__"`
  passes, proving the shim re-exports the whole public surface.
- The docs build renders toolroom's section, generated tool pages
  included, through the prepass capabilities.

### Phase 2: footman lands, and the gate runs on it

The bootstrap flip: the tool that runs this workspace's gate
becomes a member of the workspace. This is the commit most likely
to fight back, so it is its own phase.

Deliverables:

- `packages/footman/` at HEAD, same shape as phase 1.
  Distribution name `livery-footman`, import package
  `livery.footman`, the `fm` entry point moved with it.
  Disposition ruled at the cut for `vendor/`, `launch/`,
  `comparison/`, `scripts/`, `overrides/`, and the
  repository-level `CLAUDE.md`.
- The `footman.new` builtin does not come across: the stock
  scaffolder is deleted at the cut (entry point and module), ruled
  more hassle than it is worth beside the workshop's own `new`
  group (Willem, 2026-09-06). The machine-rung exclusion applied
  the same day (issue #249) is the interim and retires with this
  deletion.
- `packages/footman-compat/`: the `footman` distribution as a
  thin shim, same shape as toolroom's, version continuing past
  0.50.0.
- The workspace resolves `fm` from `packages/footman`, not from an
  external pin.
- The `PreToolUse` hook (`fm hooks.pre-bash`) and shell completion
  verified against the member package.
- The pinning test for `dependencies = []`.
- The origin repository archived read-only after the merge.

Acceptance:

- `uv run fm check` green, executed by the member footman.
- `uv run python -c "from livery import footman; print(footman.__file__)"`
  prints a path under `packages/footman/src/`.
- The shim's re-export assertion, same shape as phase 1's.
- The docs build renders footman's section, the generated errors
  and task reference pages included.

### Phase 3: the instances evolve with the workshop

The proof of the point of the move. A workshop layer change
reaches both packages the way it reaches every other instance.

Deliverables:

- A layer edit (a rendered-file change in the workshop templates)
  delivered to both packages through the update wave.
- The armed chain run over the workspace with both packages
  present.

Acceptance:

- The update run's diff shows the edit arriving in both packages'
  rendered files; a second run no-ops end to end.
- `uv run fm check` green.

### Phase 4: first train releases

Deliverables:

- `fm release.prepare packages/toolroom` and
  `fm release.prepare packages/footman`, entries appended to the
  imported changelogs, versions continuing from 0.50.0 and 0.6.1.
- The `toolroom` and `footman` shim distributions released by the
  train the same way, continuing the same lines, so the old names
  upgrade onto the shims.
- Tags `packages/toolroom/v*` and `packages/footman/v*` as
  annotated train receipts.
- Both packages on the workspace site, release view included, at
  [docs.willem.net/livery](https://docs.willem.net/livery/).
- The old Pages sites taken down, per the decision record.

Acceptance:

- `git tag -v` (or the train's own verification verb) shows both
  tags as annotated receipts.
- The rendered site shows both package sections and their
  changelog entries.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The external footman pin running the gate | the member package, at the phase 2 cut |
| The originals' own CI, Pages sites, and release flows | the workspace gate, the workspace site, the release train |
| hse pinning `toolroom~=0.6.1` from the old distribution | `livery-toolroom`, when hse migrates onto the workshop |
| The `footman` and `toolroom` shim distributions | discontinued when Willem rules (open item 1) |

## Decision record

- 2026-09-04: dist names are `livery-footman` and
  `livery-toolroom` (Willem).
- 2026-09-04: both packages migrate after the docs toolchain
  lands; the docs toolchain shipped the same day (Willem, standing
  ruling).
- 2026-09-05: both migrate at once, not toolroom alone (Willem).
- 2026-09-05: files move at HEAD, without history. The
  repositories are mature, the originals remain as the archive,
  and this matches the `archive/setup` precedent from this
  repository's own graduation. This revises the content-addressed
  store note's "tags, changelogs, docs and workflows intact" to:
  changelogs and docs move as files, tags and workflows stay
  behind (Willem, seconded).
- 2026-09-05: the docs prepass runs first, as its own plan,
  because footman's and toolroom's docs setups are more evolved
  than the freshly landed toolchain (Willem).
- 2026-09-05: versions continue from 0.50.0 (footman) and 0.6.1
  (toolroom). They are continuations, not forks or
  reimplementations (Willem).
- 2026-09-05: the old Pages sites go completely once the
  workspace site serves both packages. No redirects; all docs are
  generated via the workshop (Willem).
- 2026-09-05: the import packages move under the namespace:
  `livery.footman` and `livery.toolroom`, with
  `from livery import footman, toolroom` as a supported spelling.
  The old index names stay alive as thin shim distributions
  released by the train, so nothing is foreclosed, and the shims
  are discontinued whenever Willem has made up his mind (Willem).
  This re-rules the content-addressed store note's dependency
  arrows: strongroom is born `livery.strongroom`, and the arrows
  read `livery.footman -> livery.fabric -> livery.strongroom`
  and `livery.toolroom -> livery.strongroom`.

- 2026-09-06, phase 1 deviations, each ruled at the cut: toolroom
  has no `__all__` (its surface is the stub plus a `__getattr__`
  minting any name as a tool), so the shim forwards attributes and
  mirrors the stub instead of re-exporting a list, and its contract
  tests assert identity per name. The repo's `src/machinery` dev
  package (9000 lines; imports footman internals) moves in as
  `livery.toolroom._machinery` on the `livery.forge._dev`
  exemption. The workshop's own imports sweep to
  `from livery import toolroom` now rather than riding the shim,
  which serves outside consumers only. `livery.toolroom` joins the
  workspace layers so its docs generator verb (`toolroom.pages`,
  the `_docsgen` entry point) mounts. The curated tools' nav
  entries are authored between the standard marker pair; the
  generator does not rewrite the block, because toolroom may not
  import the workshop's rewrite helper (dependencies point only
  downward), and a driver added without its entry goes red in the
  strict build instead. `tool-history/` moves in as package data.
  The template stays brand-neutral: the stubs' mypy override
  generalises to `*._stubs.*`, and the import's docstring and
  test-typing debt lives in the package's own nested ruff config,
  tracked as its own conformance issue.
- 2026-09-06, phase 1: the stub generator's escape layer grows a
  rule (a help token carrying `][` renders as a code span, docker's
  policy format strings being the case), and docker's checked-in
  stub is regenerated through the same pinned path the history
  round-trip test uses.
- 2026-09-06: footman's stock `new` builtin is deleted when footman
  moves in; the workshop's `new` group is the one the ecosystem
  means (Willem). Until then the machine rung excludes it
  ([builtins] discovery_mode with the surface named exactly,
  issue #249).

## Open

1. **Shim discontinuation.** When the `footman` and `toolroom`
   shim distributions stop getting releases and are frozen. No
   deadline; the shims cost almost nothing to carry. Owner:
   Willem.
2. **The docs prepass plan.** Drafted as
   `notes/20260905-docs-prepass.md`; this plan is blocked on its
   phases landing. Owner: Willem approves.
