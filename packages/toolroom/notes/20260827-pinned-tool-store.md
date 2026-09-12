# A pinned tool store, shared with hse

**Status: superseded on the bytes engine, 2026-09-12.** The cache, the
tiers, the lock and the promote are `livery.strongroom`; the spec,
the kinds, the pinning and the emission are built over it in
[20260912-tool-store-over-strongroom.md](20260912-tool-store-over-strongroom.md).
This note was the fourth implementation of the same store and the
prompt for the content-addressed store design. Written 2026-08-27 after reading
both repositories side by side. The question was: hse has a mechanism
that installs pinned versions of software; toolroom already does most
of that work in its machinery; should toolroom carry the feature, and
what would it look like?

## The short answer

Yes — and the precedent is the testing seam. In August the devkit's
hand-rolled `FakeTool` moved into the wheel as `toolroom.testing`
([20260821-testing-seam](20260821-testing-seam.md)); hse-sdk now pins
`toolroom~=0.6.1` as a runtime dependency. hse-sdk's provisioning
engine is the same shape of thing: ~2300 lines of generic, footman-free,
Gitea-free code that hse carries only because nobody else offered it.
It should live in toolroom, stdlib-only, and hse-sdk should import it.

toolroom does **not** already have this feature, though. What it has
is the *upstream* half — it knows the forges, enumerates releases, and
picks the right asset for a platform — with no checksums, no versioned
store, and no way to pin. hse has the *downstream* half — sha256 pins,
a `tools/<name>@<version>` store, a lock, PATH emission — with no way
to discover a release except pasting one URL per platform by hand.
Each repo has exactly the half the other lacks.

## What hse has

Engine: `packages/hse-sdk/src/hse/sdk/provisioning/` (model, database,
store, cache, kinds, resolver, emit, events). pydantic is its only
non-stdlib import (`model.py`), and it is hse-sdk's *only* reason to
depend on pydantic.

- **Spec** — one `specs/<name>.json` per tool: `name`, `kind`,
  `pinned`, `min_version`, `versions{ver → definitions{"<platform>-
  <arch>" → {url, sha256, root, exe, paths, env, shims}}}`. Five host
  keys: `macos-arm`, `macos-x64`, `linux-x64`, `linux-arm`,
  `windows-x64`. A url without a sha256 is a validation error. There
  is no "latest"; specs are inert data and can never run a command.
- **Kinds** — `archive`, `binary`, `uv-tool`, `uv-python`,
  `bun-install`, `system-check` (git: use the system one if ≥
  `min_version`, else vendor PortableGit on Windows).
- **Store** — `<HSE_HOME>/tools/<name>@<version>` (directory existence
  is the installed marker), per-item `O_EXCL` lock with a 600 s
  staleness break, atomic promote of a temp build dir, activation
  records nobody reads yet.
- **Cache** (`cache.py`) — every download lands in
  `<HSE_HOME>/cache/downloads/<sha256>/<filename>`: content-addressed,
  so the same bytes dedupe and same-named files never collide, and the
  filename survives so the extractor can read its suffix. A hit is
  re-hashed before use; a corrupt entry is evicted, never served.
  `HSE_MIRROR` (a folder, `file://`, or base URL in the *same* layout)
  is tried before the origin, and a mirror entry is verified exactly
  like an origin download. `DownloadCache(offline=True)` turns a miss
  into an immediate `DownloadError` — but the only caller that sets it
  is the read-only environment probe; `hse sync` has no offline mode.
  **There is no prefetch:** the cache only ever holds what *this* host
  installed, and "populating a mirror is `cp -r`" therefore covers one
  platform per machine. The `uv-tool`, `uv-python` and `bun-install`
  kinds bypass the download cache entirely (uv and bun keep their own
  caches under `cache/uv` and `cache/bun`), with no offline handling.
- **Surfacing** — `read_delta()` is a ~1 ms offline probe; `emit()`
  renders one PATH prepend plus env for POSIX/pwsh/`$CLAUDE_ENV_FILE`
  /`GITHUB_ENV`; a footman `@pre_tasks` hook repairs PATH and runs the
  reconcile when the store is cold; `env.check` is the drift table.
- **Pinning** — `hse tools.add-version <url>`: download one asset,
  hash it, draft a definition, copy `root`/`exe`/`paths` forward from
  the newest existing one. One URL per platform per call, and the
  copy-forward guesses `exe` from the URL (dropped `.exe` on tea's
  Windows draft — open defect in `footman-026-recommendations.md`).

Currently pinned: uv, bun, eclint, prek, tea, ty (five platforms each),
git (Windows PortableGit + floor), cspell and markdownlint-cli2 via a
bun `package.json`.

## What toolroom has

All of it under `src/machinery/`, repo-only, importing `footman`.

- `_drivers.py` — 37 drivers; `Provision(kind, package, repo, floor)`
  says *where* a tool comes from (PyPI, npm, GitHub, GitLab, Gitea,
  docker's static index, kernel.org/OpenBSD listings). **Nothing pins
  a version**; the only constraints are `floor` and `Plugin.since`.
- `_provision.py` — `provision(drivers, prefix)` installs the *latest*
  of everything into one flat prefix. `assets_for(host, repo, tag)`
  hits the real release APIs; `_pick_asset` ranks assets by OS/arch
  alias, archive-over-binary, canonical-over-variant — **for the
  running host only** (`_platform_tokens()` reads
  `platform.system()`). `_download` retries 3×; `_extract_binary`
  handles tar.*/zip/bare. No `hashlib` anywhere: `.sha256`/`.sig`
  sidecars are *excluded* from selection, never checked.
- `_toolfetch.py` — the pinned path exists here: `install(driver,
  release, into)` puts one specific release into a throwaway so
  `fm tools.observe` can read its `--help` and `_discard` it.
  Integrity is behavioural (`_same_release` rejects a binary that
  reports the wrong version).
- The wheel — `Tool.at(path)` is the only injection point a store
  could use; `installed_version()` is a guard, not a resolver; calls
  resolve by bare name through the OS `PATH`.

## The gaps, side by side

| capability | hse | toolroom |
| --- | --- | --- |
| enumerate a forge's releases | — | `releases(driver)` |
| pick the asset for *another* platform | — (by hand) | host-only |
| sha256 pin, verify, fail closed | yes | — |
| content-addressed download cache | yes | by filename, `<prefix>/.cache` |
| mirror tried before the origin | one `HSE_MIRROR`, hand-populated | — |
| a company tier that is kept populated | — | — |
| one cache shared by several stores | — (under `HSE_HOME`) | — |
| fetch every platform's artifacts, no install | — | — |
| install offline from the cache | probe only | — |
| versioned store, lock, atomic promote | yes | flat latest prefix |
| PATH/env emission per shell | yes | `_on_path` ctx only |
| drift probe (`env.check`) | yes | — |
| ships to consumers | as hse-sdk (+pydantic) | repo-only |
| installs via typed surfaces with receipts | `subprocess` | — |

## Requirement: a local cache, and a company cache above it

Added 2026-08-27, same day, in two steps. The minimum: one local
cache per machine, shared by every store on it. Above that: a central
company cache, so the company's internet link being down does not
stop a machine provisioning, and so an artifact leaves the internet
once per company rather than once per machine.

### Three tiers, one layout

Every artifact is looked up in order:

1. the **local cache** — `<cache>/<sha256>/<filename>`, one per user,
   shared by every store (`home`) on the machine;
2. the **company mirrors** — zero or more, each a base URL or a folder
   in the same layout, tried in order;
3. the **origin** — the URL in the spec.

Verification is identical at every tier, so a tier is never trusted,
only consulted; a hit at any tier lands in the local cache. hse's
cache already has this shape — content-addressed layout, the mirror
*is* a copied cache — so the port keeps it and makes the tiers
explicit.

**toolroom knows nothing about how a mirror reaches a machine.** A
mirror is a string: `https://…` or a path. If a company serves its
cache off a mounted share, mounting it is the consumer's business —
hse has mount backends and passes the mounted path in — and toolroom
neither mounts, probes for, nor names a share. The recommended company
tier is plain HTTP precisely so nothing needs mounting.

### The two operations

- **Download to a cache** — `fetch(specs, *, hosts=ALL_HOSTS,
  into=...)`, for every platform or a chosen subset, installing
  nothing. The output is in cache layout by construction and already
  verified: it is a mirror. This is how the company tier is populated.
- **Install from a cache** — `offline=True` means *never the origin*:
  tiers 1 and 2 only. A LAN mirror still answers when the company's
  link is down; a fully disconnected machine fails closed after trying
  its mirrors, naming the `<name>@<version>` and the origin URL that
  would have satisfied it. It never silently falls through.

### What the engine grows

- `Store(home, *, cache=..., mirrors=(), offline=False)` — all
  explicit; the engine defaults none of them and names no env var.
  hse maps `HSE_CACHE` (one per user, independent of `HSE_HOME`) and
  `HSE_MIRROR` (now a list); machinery maps its own.
- `fetch(specs, *, hosts, into) -> list[Outcome]` — stdlib, walking
  every pinned definition for the requested hosts through the same
  verified `DownloadCache.fetch()`. Machinery exposes it as
  `fm tools.fetch [--host ...] [--into DIR]`; hse as `hse tools.fetch`.
- **Mirror failure is cheap and reported.** An unreachable mirror is
  skipped after a short connect timeout (seconds, separate from the
  transfer timeout) and reported through the progress seam; it never
  blocks the origin unless `offline`. Ordering makes "company down"
  the fast case: local → LAN mirror → origin.
- **The shelling kinds get a company tier of their own kind.**
  `uv-tool`: a package index — hse already runs devpi with `root/pypi`
  as a pull-through PyPI cache and `root/hse` based on it; pointing uv
  there is configuration hse carries today (`PACKAGE_INDEX_URL`), and
  the engine only passes the env through. `uv-python`: stop shelling
  `uv python install` for pinned interpreters — python-build-standalone
  publishes tarballs with sha256 sums, so a managed interpreter becomes
  an `archive` spec and rides the three tiers like everything else
  (uv's `UV_PYTHON_INSTALL_MIRROR` is the fallback if that proves
  awkward). `bun-install`: an npm proxy; hse has none today — open
  question 7. All three honour `offline` through the tool's own flag
  and the store's cache dirs (confirm bun's spelling when
  implementing).
- **Machinery adopts the same cache.** `_provision._download` (cache
  by filename, no verification) and toolfetch's throwaway downloads
  move onto the engine's cache: hash after the download, store under
  that sha256, verify on the next hit. The weekly refresh then keys
  `actions/cache` on the cache directory and stops re-downloading
  every release it re-reads.

### Populating the company tier: push, not pull-through

A workflow runs `fetch` for all hosts whenever a spec changes on
`main`, plus a weekly sweep, writing into the mirror's folder. The
origin is then fetched once per artifact company-wide; a machine
reaches the origin only in the window between a spec merge and that
run, and never when `offline`. A pull-through proxy was rejected: the
layout carries no origin URL, so the client would have to pass it,
making the proxy an open fetcher to allowlist and operate — and the
spec merge is already the moment the artifact becomes needed.

What the company *runs* is outside toolroom. For hse: an nginx stack
on the NAS serving the folder, beside the existing `docs` stack, with
the fetch workflow on the NAS runner writing the same folder. That
lives in hse's `infra/`, not here.

## The shape

Three layers, matching the repos' existing invariants.

**1. The engine ships in the wheel, stdlib-only.** A new
`toolroom.store` (name open — see questions) holding the ported
model/store/cache/kinds/resolver/emit. pydantic becomes dataclasses
plus explicit validation raising a `SpecError`; the JSON on disk stays
**byte-compatible with hse's specs** so hse's seven files move without
edits and `package.schema.json` is published from here. The `uv-tool`,
`uv-python` and `bun-install` kinds call `toolroom.uv` / `toolroom.bun`
instead of `subprocess` — so a reconcile hosted by footman gets capture,
dry-run and receipts for every install step for free, and the standalone
path stays UI-silent. Nothing prints; progress goes through the same
callback seam hse has. `HSE_HOME`/`HSE_CACHE`/`HSE_MIRROR` become
constructor arguments (`Store(home, cache=..., mirrors=(...))`); the
wheel names no env var and knows nothing about shares or mounts.

**2. Pinning is a machinery task, because the forges are.** `fm
tools.pin <tool> <version>` uses the driver's `Provision`,
`assets_for()`, and a `_pick_asset(assets, host=...)` that takes the
target host instead of reading `platform.system()`, then downloads all
five assets, hashes them, inspects each archive to fill `root`/`exe`
(fixing the copy-forward bug by construction), and writes
`specs/<tool>.json`. This is the half hse cannot build: `DRIVERS` is
where the knowledge lives, and it stays repo-only.

**3. hse-sdk imports, hse-devkit keeps the pins.** `hse.sdk.provisioning`
becomes a re-export shim over `toolroom.store` (one release of
compatibility, then deleted); pydantic leaves hse-sdk's dependencies.
The specs stay in hse-devkit — consumers own their pins, exactly as
they own their `pyproject.toml`. `hse tools.add-version` keeps working
through a stdlib `draft_from_url()` in the engine, and gains a "paste
the spec `fm tools.pin` produced" path.

### Rejected

- **Ship the specs in the wheel.** Would couple every `hse sync` to a
  toolroom release and reopen the "a toolroom release never waits on
  footman" rule from the other side. Consumers pin; toolroom provides
  the engine and the generator.
- **Put the engine in machinery.** Fixes nothing for hse; machinery
  never leaves this repo.
- **Add `--version` to `fm tools.provision` and stop.** Gives toolroom's
  own CI a pin but no checksums and no store; it is the cheap thing,
  and it is not the feature.
- **Keep pydantic, vendor it.** Zero runtime deps is a hard invariant.
  The model is ~170 lines; dataclasses cover it.

## Phases

Each phase lands on its own, gate green, releasable.

**Phase 1 — `_pick_asset` grows a `host`, `fm tools.pin` writes a
spec.** Machinery only. Refactor `_platform_tokens()` into a
`host_key → (os_aliases, arch_aliases)` table; add `fm tools.pin`;
check `specs/` into this repo for the tools toolroom's own refresh
provisions (uv, bun). Acceptance: `fm tools.pin bun 1.3.14` reproduces
hse's `bun.json` byte-for-byte except ordering. Also covers git:
toolroom's git driver is the kernel.org `man` kind, so PortableGit
needs a second `Provision` source (`git-for-windows/git` on GitHub) or
the pin task takes an explicit repo for the Windows definition — decide
here.

**Phase 2 — the engine, ported.** `toolroom.store` in the wheel:
model, store, cache (content-addressed, mirror-first, `offline`),
archive/binary kinds, lock, promote, emit, `draft_from_url`, and the
new `fetch()` prefetch over all hosts. Port hse-sdk's provisioning
tests (1183 lines) as the acceptance suite, running against
toolroom's engine from the hse side via the existing dev dependency,
plus new tests for: a five-host `fetch` into an empty folder equals a
mirror; an offline install from that folder succeeds; an offline
install with one artifact removed fails closed naming it. Invariants
to enforce in `test_tools.py`: stdlib-only imports, the `.pyi` parity
rule extended to the new module, no stdout.

**Phase 3 — the shelling kinds go through the bridge.** `uv-tool`,
`uv-python`, `bun-install`, `system-check` rewritten on
`toolroom.uv`/`toolroom.bun`/`toolroom.git`, each honouring
`offline` through the tool's own flag and the store's cache dirs;
conformance under `answers()` so the tests never spawn, asserting the
offline argv is what gets handed. This is also where the `Tool.at()`
story gets an API: `store.tool("bun")` returns the typed surface bound
to the store's binary.

**Phase 4 — toolroom dogfoods it.** `refresh.yml` provisions uv and
bun from `specs/` through the store instead of `fm tools.provision
--strict` latest — the gather runners become reproducible — and
`_download`/toolfetch move onto the engine's cache with
`actions/cache` over it, keyed on the specs. `fm tools.fetch` lands
here with a recorded-HTTP fixture (open question 5). Docs page under
`docs/`; CHANGELOG entry; minor bump (0.7.0: new public module).

**Phase 5 — hse migrates.** In the hse repo: shim `hse.sdk.provisioning`
over `toolroom.store`, move the specs' loader to it, drop pydantic,
point `tools.add-version` at `draft_from_url`, run its own suite. Bump
toolroom's floor to 0.7. One devkit release moves the fleet.

**Phase 6 — the company tier, in hse.** No toolroom code: a
`toolcache` stack in hse's `infra/` (nginx over a folder in cache
layout, the `docs` stack's pattern), a Gitea workflow on the NAS
runner that runs `hse tools.fetch` for all hosts on spec changes and
weekly, `HSE_MIRROR` set fleet-wide through the devkit's env cascade,
`uv-tool` pointed at devpi, managed pythons re-pinned as `archive`
specs, and the npm proxy from question 7. Listed so the plan is
complete end to end.

## Open questions

1. **The module's name.** `toolroom.store` reads well against
   `toolroom.testing`; `toolroom.provision` collides with the machinery
   task's meaning ("latest of everything"). Leaning `store`.
2. **Home directory.** The engine takes `home` explicitly; who defaults
   it? Proposal: the engine has no default, hse passes `HSE_HOME`, and
   toolroom's machinery passes `footman.data_dir() / "toolroom"` as
   today. A `TOOLROOM_HOME` would be a third opinion nobody asked for.
3. **Node tools.** hse pins cspell/markdownlint through a
   `package.json` content-hash (`cfg-<sha>`), so `env.check` can't
   version-check them. Should `fm tools.pin` cover the `node` kind by
   writing that `package.json`, or does `bun-install` stay a
   hse-devkit concern? Undecided; not blocking phases 1–4.
4. **Eviction and lock heartbeat.** Both open in hse today; port as-is,
   do not design them in passing.
5. **Where the five-platform tests run.** `fm tools.pin` and
   `fm tools.fetch` download five assets each; CI should run them
   against a recorded fixture, not the network. The `answers()` seam
   covers the `uv`/`bun` calls but not `urllib` — a small
   recorded-HTTP fixture is needed, and toolfetch would benefit from
   it too. A folder mirror is itself a fixture for the *install*
   side: offline tests need no HTTP at all.
6. **Cache location — decided.** `Store(cache=)` is separate from
   `home`: one per user, shared by every store on the machine. hse
   moves its default out from under `HSE_HOME` (today a second home
   means a second cache); the spelling of that default is hse's.
   Cache eviction stays out of scope with store eviction (question 4).
7. **An npm proxy for `bun-install`.** The company tier for the node
   tools needs a registry that caches upstream; devpi does not speak
   npm and Nexus is legacy. Two options: a verdaccio stack on the NAS
   (pull-through, small, bun honours the registry setting) — the same
   pattern as devpi, keeps `bun-install` a real install; or vendoring —
   `fetch` runs `bun install` per host on CI and stores the resulting
   `node_modules` as an archive under its sha256, true offline with no
   service but per-platform optional dependencies to check. Leaning
   verdaccio. Either way, only cspell and markdownlint-cli2 depend on
   it; nothing in phases 1–5 waits.
8. **Reachability of the company tier.** hse's NAS sits behind a
   reverse proxy that already exposes devpi publicly. Whether the tool
   cache is exposed read-only the same way — so toolroom's GitHub-hosted
   refresh and remote workers could use it — or stays on the LAN is an
   hse infra call. toolroom's CI is fine either way through
   `actions/cache`; and a public mirror is no trust question, since
   every tier is verified.
9. **Spelling a mirror list.** The engine takes a sequence. Whether
   hse's `HSE_MIRROR` becomes `os.pathsep`-separated or numbered is
   the env cascade's decision, not the engine's.

## Sizing

hse's engine is 2315 lines with 1183 lines of tests; the stdlib port
loses the pydantic scaffolding and gains the bridge-based kinds, so
expect roughly the same. Phase 1 is a few hundred lines on top of
`_provision.py`. Phases 1 and 2 are independent and could run in
parallel; 3 needs 2; 4 needs 1 and 3; 5 needs a release; 6 is hse
infrastructure and can start as soon as 5 gives it `tools.fetch`.
