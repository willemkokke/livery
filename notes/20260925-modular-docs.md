# Modular docs: a package's section is the package's, and the site assembles

Status: ruled 2026-09-25 with phase 5 out; phase 1 in progress. Tracks
livery#647 and folds in livery#268. The direct downloads plan
(`notes/20260925-direct-downloads.md`) completed first, as ruled.

## What this is

The site is built as one tree. Every docs verb regenerates every
package's pages into the site tree, renders every package's nav into
the one root `zensical.toml` (638 lines, 512 of them the emitter's nav
block), and the drift gate judges that whole file. So a new public
verb in one package rewrites that package's `docs/nav.toml` and the
root config, and a person reviewing the change reads the root
config's diff to find the one package that moved.

Three things are wanted, in the order the issue names them:

1. **One package's docs update without touching the others.** A
   package owns its section end to end: authored pages, generated
   pages, and the section's nav. The site assembles the sections. What
   the assembly owns is the smallest thing that has to know every
   package: the root pages, the package list, the site identity.
2. **Tests localised to the code they cover, where it pays.** The
   affected gate scopes a source edit to its package and dependents,
   and a test edit to its file. A docs page reaches no test at all
   today, so the examples on it run only in CI. Which layout, and what
   it does to the gate, pytest's import mode and the coverage floors,
   is settled here.
3. **The URL scheme, once** (livery#268). `_generated` appears twice in
   every package URL, the runner bakes those URLs into every `--list`
   link, and the machine sections (Changelog, Coverage, API) render
   under the Tasks block in the sidebar. Deep links break when a URL
   moves, so the scheme is decided in this plan and then kept.

## Where things stand

Measured on main at 403527f3, 2026-09-25.

- `fm docs.build` (`livery.workshop._docs`, 1670 lines): declared
  generators run first (`docs.task-reference` for footman, forge and
  workshop; `footman.pages`; `docs.python-coverage`;
  `tools.index.build`; `tools.docs`), then the mount copies every
  package's `docs/` to `docs/_generated/packages/<name>/`, then
  changelog, API and coverage pages are written into the site tree,
  then release pages, then zensical builds strict. Everything under
  `docs/_generated/` and `packages/*/docs/_generated/` is gitignored.
- The committed footprint of a docs change: the package's
  `docs/nav.toml` (its `# nav:begin tasks` block is the task
  reference's) and the root `zensical.toml` (its `# docs-nav:begin`
  block carries every package's whole nav). Both are drift-gated.
- URLs today: `https://docs.willem.net/livery/_generated/packages/
  forge/_generated/tasks/forge/`. The runner's template in the
  rendered root `pyproject.toml` is
  `docs_url = ".../_generated/tasks/{slug}/"`, an alias tree the task
  reference writes to `docs/_generated/tasks/<slug>.md`. Releases
  publish at `_generated/releases/`, API pages at
  `_generated/api/<package>/<module>/`, the tool index at
  `_generated/index/` (`pointer.json`, `build.json`, `objects/`,
  `refs/`, `index/`, `strongroom.json`).
- The nav emitter appends Changelog, Coverage and API after the
  authored entries; the authored nav ends with the tasks block, so the
  sidebar reads Tasks, Changelog, Coverage, API at one level.
- zensical reads one `docs_dir`. Its `INHERIT` key exists and its own
  source calls it a bandaid to be replaced, so nothing here builds on
  it. The scoped preview (`fm docs.build --package`) already assembles
  a config into a gitignored directory and hands it to zensical with
  `--config-file`; that is the pattern the assembly step uses.
- Tests: `packages/<name>/tests/`, 181 files, 4144 tests (footman
  2187 in 61 files, workshop 1089 in 69, toolroom-bench 339,
  strongroom 176, toolroom 161, forge 97, toolroom-store 95), plus
  8 workspace files in `tests/`. Modules are named by their path
  (`--import-mode=importlib`); every package's `tests/` is on
  `pythonpath` in the rendered root `pyproject.toml`; a helper module
  carries its package's name or the session refuses
  (`livery.workshop._pytest_layout`).
- The gate classifies a package path by its first segment
  (`livery.workshop._backends._python.classify`): `tests/` is a test
  or test support, `src/` is source, the rest is configuration. A
  `.md` is prose and reaches no gate but the site build. So an edit to
  a footman docs page runs none of footman's 23 docs-example tests
  (`packages/footman/tests/test_docs_examples.py`, one parametrised
  case per page with a python block) until CI runs the full suite.
- The wheel-side copy: `materialise_module_docs` refreshes
  `livery/<package>/_docs/` from the package's docs for the runner's
  offline pages. It copies the tree as it is on disk.

## Ground-truth contracts (do not violate)

1. **One rendered site per workspace stays the only deploy artifact.**
   Strict build, every link and every nav entry resolves, in the
   `docs` job of every gate run and the `deploy` job of every merge.
   The docs toolchain plan ruled this; nothing here reopens it.
2. **A package's committed footprint for a docs change is under its
   own directory.** After phase 3 a new verb, a new page or a new
   module in package X changes files under `packages/X/` only. A test
   pins it by making each kind of change in a seeded workspace and
   listing what moved.
3. **The URL scheme is decided once and pinned.** After phase 1 no
   published URL contains `_generated`, and a test over the built site
   and the agent files refuses one. The scheme below is the contract;
   a later change is a breaking change to every deep link and to
   every runner that baked a `docs_url`.
4. **The published tool index path is a consumer contract.** A
   consumer names the published index by URL. It moves once, in phase
   1, with the store's documentation and the bench's writer in the
   same change.
5. **Generated files stay generated.** No hand edit inside an
   emitter-owned block or file; the drift gate keeps refusing one.
   Everything the build writes is gitignored or drift-gated, never
   both silently.
6. **A test file is a test file wherever it lives.** The gate's
   classification, the coverage measurement, the wheel and the API
   pages all agree on what a test is. A layout change that leaves one
   of the four reading tests as source stops for the human.
7. **Everything through `fm`.** No raw `zensical`, no hand-assembled
   config outside the build verb.

## The design

### The URL scheme

Published paths under the site URL:

| Content | Today | After phase 1 |
| --- | --- | --- |
| A package's authored page | `_generated/packages/<name>/<page>/` | `packages/<name>/<page>/` |
| A package's task reference | `_generated/packages/<name>/_generated/tasks/<group>/<task>/` | `packages/<name>/tasks/<group>/<task>/` |
| A package's API page | `_generated/api/<name>/<module>/` | `packages/<name>/api/<module>/` |
| A package's changelog, coverage | `_generated/packages/<name>/changelog/` | `packages/<name>/changelog/`, `packages/<name>/coverage/` |
| The runner's `docs_url` alias | `_generated/tasks/<slug>/` | `tasks/<slug>/` |
| The release view | `_generated/releases/` | `releases/` |
| The tool index | `_generated/index/` | `tools/` (open question 1) |

`_generated` stays a name on disk, never in a URL: the mount copies
`packages/<name>/docs/` to `docs/packages/<name>/` and rewrites the
package-internal `_generated/` prefix away on copy, in the pages'
paths and in the section nav. The runner's `docs_url` template, the
agent files (`llms.txt`) and the store's documentation of the index
URL all follow in the same change.

### The section nav

Every machine section is a marker block the author may place in
`nav.toml`, the way the tasks block already is:

```toml
# nav:begin changelog
# nav:end
```

Blocks are `tasks`, `changelog`, `coverage` and `api`. A block the
author did not place is appended after the authored entries in the
fixed order Changelog, Coverage, Tasks, API. The emitter fills a
block in place and refuses a block it does not know. A package
without a `nav.toml` gets its pages enumerated, index first, and the
machine sections appended in the same order.

### The section, complete per package

Every generated page of a package lands in the package's own
generated tree, `packages/<name>/docs/_generated/`: the task
reference already does, and the changelog, the coverage page and the
API pages move in from the site tree. The generators that write them
become declared generators like the others, so a package's section is
the union of its `docs/` and what its declared generators write, and
nothing else in the build knows a package's contents.

The section's nav is emitted complete per package into
`packages/<name>/docs/_generated/nav.toml`: the authored `nav.toml`
with every marker block filled and every path rewritten to the
published scheme. It is gitignored, so the drift gate over a package
shrinks to what a person authored: the `nav.toml` entries name real
pages, and every authored page is named. The tasks block moves out of
the authored file into the emitted one with the rest, so a new verb
changes nothing committed.

Open question 3 asks whether the tasks block should stay committed
instead, so a reviewer sees a package's task surface change in the
diff.

### The assembly

The root `zensical.toml` shrinks to the site identity, the theme, the
extension set and the asset lists, rendered from the workspace
contract by the emitter (`zensical_config`, written by the same render
that writes the workflows) and drift-gated as today. It carries no
nav. `livery.workshop._llms` reads the nav from that rendered config
today; it reads the assembled one after this phase.

The build assembles the config zensical reads into a gitignored
directory: the root config's body, then a nav of the root `docs/`
pages, then one section per package from its emitted `nav.toml`,
then the release view. It hands that file to zensical with
`--config-file`, as the preview does now. The package list is read
from the workspace at build time; nothing committed enumerates it.

The mount stays a copy into the site tree because zensical reads one
`docs_dir`. It is rebuilt per package: a package whose section digest
(the tree of its `docs/` and its generated pages) is unchanged is
left in place. The whole-tree rebuild remains behind `--full` for a
recovery.

### Tests, and the docs examples

Two separate things, ruled separately.

**The docs examples run when their page changes.** This is a gate
rule, not a relocation. A `.md` under `packages/<name>/docs/` reaches
its package's docs-example tests, selected to that page: the
harness gains a `--docs-page` option (repeatable, a path relative to
the package's `docs/`) that narrows its page parametrisation, and
the affected gate passes the changed pages through. A change to the
harness file runs it whole, which the gate already does for a test
file. A source change already runs the package's suite. Prose
elsewhere (`notes/`, the root `docs/`, a `README.md`) reaches no
test, as today.

**Tests in-package.** The layout that keeps contract 6 is
`src/livery/<package>/_tests/`: a regular package beside the code,
`test_*.py` modules and their helpers inside it. What it buys:

- Test modules import by their real name
  (`livery.footman._tests.test_lifecycle`), so two packages' test
  modules never collide, the `pythonpath` list in the rendered root
  `pyproject.toml` goes, and the helper-name refusal in
  `livery.workshop._pytest_layout` goes with its rule.
- A helper is an ordinary relative import, no naming convention.
- The tests and the code they cover move together in a rename.

What it costs, each a line in phase 5:

- The gate's classification reads a path under `src/` as source; it
  learns `src/**/_tests/` as test or test support, or the move widens
  every test edit to the package's dependents.
- uv_build ships everything under the module root; the build backend
  table excludes `_tests` from the wheel, and a test over the built
  wheel pins it.
- Coverage measures `src/`; the run omits `*/_tests/*`, or every
  package's floor rises by the test files' own lines.
- The API pages walk the module tree; `api_modules` skips `_tests` as
  it skips `_docs`.
- The layering lint reads imports under `src/`; a test that imports a
  dependent package for a fixture now reads as a layering violation.
  The lint learns the same exclusion, or the test moves.
- 181 files move. `git log --follow` survives a rename; a plain
  `git log <old path>` does not.

What it does not buy: a narrower gate for a source edit. A module's
tests cannot be selected from a source change without an import graph
inside the package, and none of this builds one. The relocation is a
layout decision, taken for the imports and the two rules it removes,
not for speed. Phase 5 is therefore ruled on its own (open question
4).

## Phases

Each phase lands alone, gate-green, through `fm submit --armed`.
Phases 1 and 2 are small and go first because they change published
URLs, and every day the old URLs live is another deep link that
breaks. Phase 3 is the modular build. Phase 4 is the gate rule. Phase
5 waits for its ruling.

### Phase 1: the URL scheme

Deliverables:

- The mount copies to `docs/packages/<name>/`, rewriting the
  package-internal `_generated/` prefix away in paths and nav.
- API pages at `packages/<name>/api/`, release pages at `releases/`,
  the alias tree at `tasks/`, the tool index at its ruled path.
- The `docs_url` template in the project template renders
  `tasks/{slug}/`; `fm template.apply` moves the rendered root
  `pyproject.toml`.
- `livery.workshop._llms` writes the new URLs.
- The store's documentation and the bench's index writer name the new
  index path.

Acceptance:

- `fm docs.build` green, strict.
- `grep -r _generated site/ --include='*.html' -l` prints nothing;
  the same over `site/llms.txt` and `site/llms-full.txt`.
- `fm --list` prints a task link whose path is `tasks/<slug>/`.
- A test in `packages/workshop/tests/` pins every row of the scheme
  table against a seeded workspace's build.

### Phase 2: the section nav's marker blocks

Deliverables:

- `nav:begin changelog`, `coverage`, `api` blocks, filled in place;
  unplaced blocks appended in the fixed order Changelog, Coverage,
  Tasks, API; an unknown block refused with its name.
- The seven `nav.toml` files stay as they are; the fixed order alone
  fixes the sidebar.

Acceptance:

- Emitter tests: a placed block fills in place; an unplaced block
  appends in order; an unknown block names itself in the refusal.
- The built footman section's sidebar reads authored pages,
  Changelog, Coverage, Tasks, API, checked by a test over the
  assembled nav.

### Phase 3: the section is the package's, and the site assembles

Deliverables:

- The changelog, coverage and API generators write into the owning
  package's `docs/_generated/`, declared like the others.
- The section nav emitted complete per package into
  `packages/<name>/docs/_generated/nav.toml`; the tasks block leaves
  the authored file (subject to open question 3).
- The root `zensical.toml` rendered without a nav; the build assembles
  the config zensical reads into a gitignored directory; the agent
  files read their nav from the assembled config.
- The mount rebuilt per package by section digest; `--full` rebuilds
  whole.
- The drift gate over a package's docs judges the authored `nav.toml`
  both ways and nothing generated.

Acceptance:

- Contract 2's test: in a seeded workspace, a new verb, a new page and
  a new public module in package X each leave `git status` showing
  files under `packages/X/` only.
- `fm docs.build` twice: the second run copies no package's mount and
  says so.
- `wc -l zensical.toml` is under 130 lines and the file has no `nav`
  key.
- `fm check --full` green; the docs and gate jobs green in CI.

### Phase 4: a docs page reaches its examples

Deliverables:

- The docs-example harness (footman's; no other package has one)
  takes `--docs-page`, repeatable, narrowing the page
  parametrisation. A package without a harness selects nothing for a
  page edit, and gains the rule the day it has one.
- `affected_from_paths` maps a `.md` under `packages/<name>/docs/` to
  that package's docs-example test file with the page as selection;
  the reflex passes it through to pytest.

Acceptance:

- Edit one footman page, `fm check --junit-xml=...`: the report has
  that page's cases and no other test.
- Edit the harness file: the report has every page's cases.
- Tests over `affected_from_paths`: a package docs page selects; a
  root docs page, a note and a `README.md` select nothing.

### Phase 5: tests in-package (ruled out)

Ruled out on 2026-09-25 (decision record); kept as the record of what
was weighed. Deliverables, had it been ruled in:

- `packages/<name>/tests/` moves to `src/livery/<name>/_tests/`, one
  package per commit so each rename is reviewable.
- The gate classifies `src/**/_tests/`; the build backend excludes it
  from the wheel; coverage omits it; `api_modules` and the layering
  lint skip it.
- The `pythonpath` list and the helper-name refusal are deleted with
  their tests.

Acceptance:

- `fm check --full` green on every leg.
- A test unpacks each built wheel and finds no `_tests` directory.
- The coverage record for each package moves by less than the
  epsilon after the relocation.
- `grep -n pythonpath pyproject.toml` prints nothing.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `MOUNT = "docs/_generated/packages"` | phase 1 |
| `docs_url = ".../_generated/tasks/{slug}/"` in the template | phase 1 |
| The 512-line nav block in the root `zensical.toml` | phase 3, deleted |
| The tasks block in the authored `nav.toml` | phase 3, or kept by open question 3 |

## Decision record

- 2026-09-25: drafted. The examples-run-on-page-change ask is met by a
  gate rule (phase 4), not by moving tests; the relocation (phase 5)
  is presented with what it buys and costs and ruled on its own.
- 2026-09-25, Willem: phase 5 is out. Measured on the tree, 150 of 182
  test files import their package's public face, and the tests are
  written per behaviour, not per module: a mirror of the source tree
  under the tests would select by a convention the imports do not
  back, and the relocation narrows no gate. What it bought was three
  naming rules that already work. A narrower gate for a source edit,
  if ever wanted, comes from per-test coverage contexts, a plan of its
  own.
- 2026-09-25, Willem: the tool index publishes at `tools/` for now; it
  is expected to become a permanent URL of its own later, no longer a
  section of the docs. No redirects for the old URLs. The tasks block
  is emitted with the other machine sections, not committed.

## Open

None. Every question raised at drafting is in the decision record.
