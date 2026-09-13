# The tool store over strongroom

Status: phases 0 and 1 landed 2026-09-13 in `packages/toolroom-store/`
(the spec, the home, the engine: `Store.ensure`, `link`, `delta`,
`fetch`, kinds `archive` and `binary`). The package's own tests and
docs page carry them (`packages/toolroom-store/tests/`,
`packages/toolroom-store/docs/index.md`), not the toolroom paths the
phases named. Deferred from phase 1's list: the held ref lock refusal
has no test yet. Phase 2 next, on Willem's go.
Supersedes the bytes engine of
[20260827-pinned-tool-store.md](20260827-pinned-tool-store.md); the
tool store of that note stays and is built here over
`livery.strongroom`.

## Scope

In: `livery.toolroom.store`, the pinned tool store, its own
distribution `livery-toolroom-store`, installable alone, with its
`fm` verbs bundled and loading only through footman's plugin, the way
the docs generator of `livery.toolroom.tools` does. It imports
`livery.toolroom.tools`, `livery.strongroom` and the standard library.
A tool spec format
byte-compatible with hse's `specs/<name>.json`. The `tools/`
namespace of a machine-wide strongroom store: one copy of each tool
version, versions side by side, shared by every checkout and project
on the machine. Install from three verified tiers (local store,
mirrors, origin), `offline`, and `fetch` for every host into a folder
that is a mirror by construction. PATH and env emission as deltas the
workshop's entry contract applies. `fm tools.fetch` in the store's verbs and
`fm tools.pin` in `livery.toolroom.bench`, the machinery's own
distribution, where the forges are. The workshop side: specs delivered to a
workspace, the entry contract putting the pinned versions on PATH, the
per-command reconcile keeping the store warm, `fm env.check` naming
drift, and the gate's file-reading tools leaving the venv. Toolroom's
own release refresh provisioning through the store.

Out: hse's migration, which is hse's plan over the same engine. The
company tier's hosting (nginx, the fetch workflow), which is
infrastructure. An npm proxy for node tools. Retention of old tool
versions beyond what strongroom's sweep gives today. A pwsh entry
script beyond the emission, decided in phase 3. Strongroom itself
changes only where this plan names a gap, in its own issue. The
machinery's move to `livery.toolroom.bench` is its own issue and
lands before phase 2 needs the pin verb there.

## Why strongroom, and what it gives

The 2026-08-27 note planned a content-addressed download cache, a
mirror-first fetch, an `offline` mode, and a `tools/<name>@<version>`
store with a per-item lock and an atomic promote. Every one of those
is now strongroom, proven by its vectors and conformance cases and
released as `livery-strongroom`:

- The download cache is the object store. A spec's `sha256` is the
  object's name, so an archive is landed by its pin
  (`store.land(source, expected=digest)`) and a mismatch fails closed
  before any byte is used.
- The mirrors are strongroom sources in its layout: a folder or an
  HTTP base, each verified, none trusted, consulted in order; `offline`
  means never the origin, and a miss names the digest and the origin
  that would have satisfied it.
- The store at `tools/<name>@<version>` is a write-once ref naming a
  tree, the extracted archive, as the namespace conventions in
  strongroom's spec already publish. The per-item lock is the ref's
  lock; the atomic promote is the compare-and-swap.
- The tool directory a shell runs from is a view of that tree, filled
  by the materialiser's ladder: clone or hardlink on one filesystem,
  copy where nothing better holds, executable bits carried by the
  tree on every platform.
- `fetch` for every host lands every definition's archive into a
  folder store, which is a mirror by construction.
- Reachability decides what stays: every `tools/` ref and every live
  view is a root, and the sweep removes the archive blobs and
  superseded manifests nothing names.

What strongroom does not know and this plan adds: the spec, the
kinds, the extraction, the version pinned per checkout, PATH.

## Ground-truth contracts (do not violate)

1. **The store is machine-wide and shared.** One strongroom store per
   machine holds each tool version once, versions side by side; two
   projects pinning one version share the one copy, and a CI runner
   with a persistent volume shares it across jobs. Never a per-venv or
   per-checkout copy (Willem, 2026-09-05).
2. **A checkout resolves its own committed pins.** The versions on
   PATH for a command are the ones the checkout's specs pin, so an
   older checkout judges with its era's toolchain. Sharing never
   couples projects.
3. **Tools that only read files never live in a venv.** The lock keeps
   what must import the project's environment (pytest and its
   plugins, coverage). Which checkers count is open item 1.
4. **Specs are inert data.** A spec names a kind, a pinned version,
   and per host a URL with a mandatory `sha256`; a URL without one is
   a validation error, and no spec can run a command. The JSON is
   byte-compatible with hse's, so hse's files move without edits.
5. **Every tier is verified and none is trusted.** A hit at any tier
   is checked against the pin; `offline` never reaches the origin; a
   miss fails closed naming `<name>@<version>` and the origin URL.
6. **The engine names no environment variable and no share.** `home`,
   sources and `offline` are constructor arguments; the workshop and
   hse map their own variables. A mirror is a string, and mounting is
   the consumer's business.
7. **Toolroom emits deltas, never mutates a shell.** PATH prepends,
   env and shims come out as a delta; the workshop's entry contract
   and `fm env.emit` apply them, one place per dialect.
8. **Nothing removes an installed version on a clock.** A `tools/` ref
   is write-once. Removal is a deliberate act under a retention rule,
   open item 4, never an age sweep.
9. **Strongroom knows no tool.** Toolroom declares the `tools/`
   namespace and reads its meaning; strongroom enforces the class.
   Any gap found in strongroom is an issue there, never a workaround
   here.
10. **Dependencies point downward.** `livery.toolroom.store` imports
    `livery.toolroom.tools`, `livery.strongroom` and the standard
    library, and footman only through its plugin entry;
    `livery.toolroom.tools` imports nothing of the store; the
    workspace's layering lint pins the edges. Footman stays free of
    toolroom.
11. **Fallbacks before happy paths.** Every refusal (a mismatch, an
    offline miss, a corrupt mirror entry, a lock held, an archive the
    extractor refuses) is forced by a test before the success path.
12. **Windows gates.** The windows-latest leg runs the suite on every
    push (Willem, 2026-09-12, livery#487), and the Windows-only paths
    are also forced through their seams with fakes on the other
    platforms, so a fallback is proven wherever the tests run.

## The design

### The spec

`specs/<name>.json`, hse's shape unchanged: `name`, `description`,
`kind`, `pinned`, `min_version`, and `versions{<ver> → {version,
definitions{<host> → {platform, arch, url, sha256, root, exe, paths,
env, shims}}}}`. Six host keys: `macos-arm`, `macos-x64`,
`linux-x64`, `linux-arm`, `windows-x64`, `windows-arm`. A spec
carries any subset; an install on a host the spec lacks refuses,
naming the hosts it has, and `fm tools.pin` says which upstreams ship
no build for a host. Dataclasses with explicit
validation raising `SpecError`; the two consistency rules (a
`versions` key equals its `version`, a `definitions` key equals its
host) stay. A JSON schema is exported beside the specs.

### The home

`Store(home, *, sources=(), offline=False)`. Under `home`:

- `store/`, the strongroom store, algorithm sha256, namespaces
  `tools` (write-once) and `urls` (volatile, the machinery's download
  cache in phase 5).
- `tools/<name>@<version>/`, the view of the tool's tree.
- `uv/`, `bun/` and their caches for the delegated kinds, as hse lays
  them out.

Toolroom's machinery passes `footman.data_dir() / "toolroom"`, the
same home its flat prefix uses today; hse passes `HSE_HOME`. No
default inside the engine.

### Install

`store.ensure(name, version="")`, the pinned version by default:

1. Read the spec, pick the running host's definition, refuse a host
   the spec does not carry, naming the hosts it does.
2. If `tools/<name>@<version>` names a tree and its view is live and
   whole, done, in about a millisecond and offline. This is the probe
   `env.check` and the reconcile call.
3. Land the archive by its pin: `store.land` through the sources in
   order, the local store first, then each mirror, then the origin
   unless `offline`. A mismatch anywhere fails closed and names the
   tier.
4. Extract into scratch under the store, `root` stripped, `exe`
   renamed for Windows' `PATHEXT` where the spec says; a bare binary
   is placed as is. Collect the directory as a tree, executable bits
   read by the materialiser's ladder.
5. Move the ref: `tools/<name>@<version>` to the tree, write-once,
   previous `None`; a ref already naming the same tree is done, a ref
   naming another tree is refused and reported, never overwritten.
6. View the tree at `tools/<name>@<version>/` and record the view.

Two processes installing one tool serialise on the ref's lock; the
loser re-probes and finds it present. The archive blob is unreached
after step 5 and the next sweep removes it; the mirrors keep it for
other machines.

### The bin directory, and emission

PATH names one directory per checkout, and it never changes: a
gitignored bin directory in the checkout holding one link per pinned
tool binary (every entry the spec's `paths` and `exe` name) and the
spec's `shims` (`node` to bun). `store.link(specs, into)` fills it
from the store's views and removes only links it made; the reconcile
calls it on the next command after the pins change, so an open shell
follows a pin change without a new emission. On Windows a link falls
back to the `.cmd` launcher toolroom already writes for an
interpreter and the node shim, and a tool that finds its siblings
through the path it was launched from gets a launcher that knows the
real directory. Per checkout, not per machine, because a checkout
resolves its own pins and two checkouts with different pins on one
machine would fight over one directory; the store behind it stays
machine-wide.

`store.delta(specs, bin)` returns the one PATH prepend and the specs'
`env` as a delta object; the workshop's `workspace_delta` extends
with it and `emit_lines` renders it, POSIX and pwsh. The GitHub
persistence goes through the same delta. The alternative, one PATH
entry per tool directory, is hse's shape today; it goes stale in an
open shell when a pin changes and grows with the tool count.

### Fetch

`fetch(specs, *, hosts=ALL_HOSTS, into)` lands every definition's
archive for the requested hosts into a strongroom store at `into`,
verified, installing nothing. The folder is a mirror; serving it is
outside toolroom.

### Pinning

`fm tools.pin <tool> <version>`, machinery, forge-aware: the driver's
`Provision` and `assets_for`, a `_pick_asset(assets, host=...)` that
takes the target host instead of reading `platform.system()`, then
the five downloads, hashed, each archive inspected for `root` and
`exe`, and the spec written. `fm tools.pin` runs against a recorded
HTTP fixture in tests, never the network.

### Kinds

`archive` and `binary` go through the store as above. `uv-tool` and
`uv-python` delegate to `toolroom.uv` with the store's directories in
the environment, `bun-install` to `toolroom.bun`, `system-check` to
`toolroom.git` with a floor; each honours `offline` through the tool's
own flag. Their bytes do not go through strongroom. Each `uv-tool`
install gets its own directory, `<home>/uv/tools/<name>@<version>`,
because uv keeps one install per tool name and the store keeps
versions side by side. Interpreters stay with uv: it picks the
python-build-standalone build for the host, verifies it, installs
once per machine under `<home>/uv/python`, and serves offline from
`UV_PYTHON_INSTALL_MIRROR`, a folder `fetch` fills in that layout.
The store would redo all of that for one mechanism's sake and gain
nothing a checkout can use.

### The workspace

Specs are layer content: the workshop renders `specs/<name>.json`
into a workspace for the tools its kinds name, drift-judged like every
rendered file, so a layer release moves every project's pins through
the update wave, and a project adds its own specs beside them. The
entry contract's `fm sync` ensures every pinned tool, the per-command
reconcile ensures on a cold store, `fm env.emit` prepends the tool
directories, and `fm env.check` reports each tool's installed version
against its pin. CI legs restore the store directory from the runner
cache keyed on the specs' digest, so a leg installs nothing the cache
holds.

The dev group loses the tools that only read files, phase by phase:
ruff, ty, pyrefly and git-cliff first, as released binaries. uv's
bootstrap stays in the entry script, pinned by the lock, because the
store's installer needs a python and uv makes the venv that has one.
mypy and basedpyright are open item 1.

## Phases

### Phase 0: the spec format, the namespace, the dependency

Deliverables:

- `livery.toolroom.store`'s model: the spec dataclasses, validation,
  the JSON schema export, `host_key()`; golden spec files under
  `packages/toolroom/tests/specs/` including hse's `bun.json` as is.
- The `tools` namespace declaration and the home layout, with a test
  that opens the store twice and refuses a home of another layout.
- `packages/toolroom-store/`, born with `fm new.package`:
  distribution `livery-toolroom-store`, import
  `livery.toolroom.store`, `[[depends]]` edges on `packages/toolroom`
  and `packages/strongroom` (floor 0.1.0), footman in a `test` extra,
  a `footman.tasks` entry point for its verbs, and a layer entry
  `{ import = "livery.toolroom.store", dist = "livery-toolroom-store" }`
  where a workspace wants the verbs.

Acceptance:

- `uv run fm check` exits 0.
- `uv run python -c "from livery.toolroom.store import Spec"` prints
  nothing.
- `uv run python -m pytest packages/toolroom/tests/test_store_model.py`
  loads hse's `bun.json` and rejects a definition without `sha256`.

### Phase 1: the engine

Deliverables:

- `Store(home, *, sources, offline)`, `ensure`, the probe, `link`,
  `delta`, the progress seam, `fetch`; kinds `archive` and `binary`.
- Tests, refusals first: a wrong `sha256` at each tier; an `offline`
  miss naming the origin; a corrupt mirror entry skipped and the next
  tier tried; a held ref lock; an archive with no `root`; a host the
  spec lacks; then a five-host `fetch` into an empty folder equals a
  mirror, an offline install from that folder succeeds, an offline
  install with one blob removed fails closed naming it, a second
  `ensure` is a probe, `link` removes a link it made and never a file
  it did not, and the Windows launcher fallback is forced through its
  seam.
- `packages/toolroom/docs/store.md`, the page.

Acceptance:

- `uv run fm check` exits 0, toolroom's coverage floor kept.
- `uv run python -m pytest packages/toolroom/tests/test_store.py -k offline`
  passes with the network unreachable (the tests use folder sources).

### Phase 2: pin and fetch as verbs

Deliverables:

- `_pick_asset(assets, host=...)` and the host table in
  `livery.toolroom.bench`; `fm tools.pin` there, writing a spec;
  `fm tools.fetch [--host ...] [--into DIR]` in the store.
- A recorded HTTP fixture for the forge asset listings and the five
  downloads; `fm tools.pin bun 1.3.14` reproduces hse's `bun.json`
  but for key order.
- `specs/` in this repository for uv, bun, ruff, ty, pyrefly and
  git-cliff.

Acceptance:

- `uv run fm tools.pin ruff <version>` against the fixture writes
  `specs/ruff.json` with five definitions and exits 0.
- `uv run fm tools.fetch --into /tmp/mirror` against the fixture lands
  every blob and `fm tools.fetch --into /tmp/mirror` again lands none.

### Phase 3: the workspace runs on the store

Deliverables:

- The workshop renders the specs the kinds name into a workspace and
  judges their drift; `fm sync` and the reconcile ensure them;
  `workspace_delta` carries the store's delta, the checkout's bin
  directory on PATH once; the reconcile relinks it after a pin
  change; `fm env.check` reports per tool.
- The CI leg restores and saves the store directory keyed on the
  specs; the entry script emits the tool directories; the dev group
  loses ruff, ty, pyrefly and git-cliff; `fm check` runs them from the
  store.
- The Windows leg of the gate runs the store's install, view and
  emission for real; the pwsh emission lands here (open item 7).

Acceptance:

- `uv run fm check` exits 0 on every gated runner, windows-latest
  included, with the store directory restored from the cache, and
  `fm env.check` prints every pinned tool at its version.
- `grep -c "ruff\|ty\|pyrefly\|git-cliff" uv.lock` counts only
  transitive mentions, none in the dev group.
- On a second checkout of this repository on one machine, `fm sync`
  installs nothing and its bin directory links the same store views.
- After a pin changes on a branch, the next `fm check` in an open
  shell runs the new version with no new emission.

### Phase 4: the delegated kinds

Deliverables:

- `uv-tool`, `uv-python`, `bun-install`, `system-check` through
  `tools.uv`, `tools.bun`, `tools.git`, offline-aware; one directory
  per `uv-tool` install so versions coexist;
  conformance under `answers()` so the tests never spawn, asserting
  the offline argv handed over.
- `store.tool("bun")` returns the typed handle bound to the store's
  binary through `Tool.at`.
- The ruling on open item 1 applied: whichever of mypy and
  basedpyright leaves the lock goes through `uv-tool`.

Acceptance:

- `uv run fm check` exits 0.
- `uv run python -m pytest packages/toolroom/tests/test_store_kinds.py`
  passes without spawning uv or bun.

### Phase 5: toolroom's refresh on the store

Deliverables:

- `refresh.yml` provisions uv and bun from `specs/` through the
  store; `_download` and toolfetch's throwaway downloads move onto the
  store's `urls/` convention, verified on the next hit, which closes
  the sidecar gap; the flat latest prefix and `fm tools.provision`
  are retired or narrowed to what the release refresh still needs.
- CHANGELOG entry; the release through the train.

Acceptance:

- `uv run fm check` exits 0 and the nightly point is green.
- `fm tools.refresh` in a run of `refresh.yml` reports every download
  as a store hit on its second run.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| ruff, ty, pyrefly and git-cliff in the dev group | phase 3 |
| the flat latest prefix at `footman.data_dir() / "toolroom"` and `fm tools.provision` | phase 5 |
| `_download` caching by filename under `<prefix>/.cache` | phase 5 |
| uv's bootstrap in the entry script | stays until a ruling says otherwise (open item 6) |

## Decision record

- 2026-09-12, Willem: plan the toolroom store, the goal strongroom
  was started for. This note. The 2026-08-27 note's bytes engine
  (cache, tiers, lock, promote) is superseded by strongroom; its
  spec, kinds, pinning and emission stand.
- 2026-09-12, Willem: `windows-arm` is the sixth host key.
- 2026-09-12, Willem asked whether every binary is linked into one
  directory on PATH per checkout, so the PATH never changes; the
  agent recommended yes, and the design above says so. Awaiting the
  ruling with the rest.
- 2026-09-12, Willem: the store is its own distribution with its
  verbs bundled, installable alone; the stub machinery is another,
  named `livery.toolroom.bench`; the handles moved to
  `livery.toolroom.tools` so `livery.toolroom` is a namespace
  (livery#524), and the house style is
  `from livery.toolroom import tools`. The shim distributions are
  gone (livery#521).
- 2026-09-12, Willem asked whether managed pythons should leave uv;
  the agent answered no, with the reasons in the design, and the
  plan keeps them with uv.
- 2026-09-12, the agent, for the ruling: a `tools/` ref names the
  extracted tree, not the archive, as strongroom's namespace table
  already publishes; the archive is landed by its pin and swept once
  the tree is rooted. The alternative, the ref naming the archive
  blob with extraction as the one materialisation, was set aside
  because it forgoes the ladder's zero-copy views and the tree digest
  the evaluator's key may fold in.
- 2026-09-13, the agent: phases 0 and 1 landed (livery#541, livery#549)
  in `packages/toolroom-store/`, the package the 2026-09-12 ruling
  asked for; their tests and docs page live there rather than under
  `packages/toolroom/`. The held ref lock refusal of phase 1's list
  has no test yet and stays open.

## Open

1. **Which checkers leave the lock.** ruff, ty and pyrefly read files
   and ship binaries: they move. mypy reads the venv's packages and
   plugins; basedpyright reads the venv when pointed at its
   interpreter. Proposal: both stay locked through phase 3, and
   phase 4 moves basedpyright as a `uv-tool` pointed at the venv if
   the gate's types agree on both spellings. Owner: Willem.
2. **The specs' home in a workspace.** Proposal above: rendered by the
   layer, drift-judged, project-owned additions beside them. The
   alternative is the specs riding the layer's wheel, which the
   2026-08-27 note rejected for coupling every sync to a release.
   Owner: Willem.
3. **Resolved 2026-09-12.** livery-strongroom 0.1.0, with the groups
   of phase 7, is the floor; released the same day.
4. **Retention of old tool versions.** A `tools/` ref is write-once,
   so a version stays reachable until a retention class exists in
   strongroom, and the sweep never removes a rooted tree. Sizes are
   tens of megabytes per version; the proposal is to leave this until
   the store's size is measured on a machine that has been through a
   year of pins, and to file the retention class in strongroom then.
   Owner: Willem.
5. **The CI cache versus a company mirror.** Phase 3 keys the runner
   cache on the specs; a company mirror is hse's infrastructure and
   arrives through hse's plan. Whether GitHub-hosted legs ever reach
   a mirror is the 2026-08-27 note's question 8. Owner: Willem.
6. **uv's bootstrap.** The entry script installs uv at the lock's
   version through uv's installer; the store cannot supply the tool
   that makes the venv the store's installer runs in. Stays unless a
   standalone bootstrap is wanted. Owner: Willem.
7. **pwsh.** The entry script is POSIX; the pwsh spelling was deferred
   to this port. Phase 3 emits the store's delta in pwsh through the
   existing dialect seam; whether the entry script itself gets a pwsh
   spelling is decided on the Windows leg's evidence. Owner: the
   agent, at phase 3.
