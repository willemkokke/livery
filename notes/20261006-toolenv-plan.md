# Toolenvs: toolroom's own command line over the central index

Status: proposed, awaiting Willem's ruling. Written 2026-10-06 from
Willem's rulings of 2026-10-06, a future plan: not started, and
separate from the extensions plan (`notes/20261002-extensions-plan.md`).
Its place in the sequence is open item 1.

## The prompt (Willem)

> Add a command line app to toolroom that exposes its functionality,
> so it no longer depends on footman. Used like virtualenv, or parts
> of uv: multiple toolenvs with different versions of the same tool, a
> shared cache, quick bringup and bringdown. The whole idea is that it
> is a central store, like PyPI, except we host no content, just
> records. Not interested in scripts and plugins, and I want to be
> very aware of supply chain issues. Nothing in this should be Python
> specific; it should port cleanly to Rust. Willem, 2026-10-06

## What exists

Verified in the code on 2026-10-06.

Three distributions, three different relations to footman:

- `livery-toolroom`, the handles: no runtime dependency. footman is
  optional, reached only when a footman context is live, through the
  hosted lane in `_host.py` and `api.py`. That reach into footman's
  privates is issue #1204's, and this plan does not touch it.
- `livery-toolroom-store`: depends on strongroom and the handles and
  imports no footman. The record, the surface, the catalogue
  (`Catalogue.of_records`, `Catalogue.of_index`), the lock
  (`resolve_lock`, `tools.lock`, `tools.graphs/`), supply, views,
  `Store.link` and `Store.delta` all live here.
- `livery-toolroom-bench`: the `fm tools.*` verbs, maintainer tooling
  on footman as its task framework. It stays there.

The store's home is already the shared cache: `tools/<name>@<version>`
is one write-once view per version over strongroom objects, so two
environments wanting different versions of one tool coexist. A view is
made by the materialiser ladder, clone first (clonefile on APFS,
FICLONE on btrfs, XFS and bcachefs), then hardlink, link, copy. The
archive stays in the `urls` namespace, volatile, so a rebuild needs no
network.

A checkout already has one toolenv, hardwired: `_tools.py` in the
workshop binds ensure, link and delta to the checkout root as
`.workshop/bin`, receipts under `.workshop/receipts/`, and the env
`fm env.emit` reads back. That code is the toolenv, one layer too
high, with one place it can live.

Delegated kinds are locked transitively: `tools.graphs/` beside the
lock holds a hashed requirements file per pypi tool, installed with
`uv pip install --require-hashes`, and a `package-lock.json` per npm
tool, installed with `npm ci`. A dotnet tool package carries its
dependencies and the exact version pins the whole. A delegated
install's tree never enters strongroom (`Ensured.tree` is `None`).

Strongroom is a written standard: `spec/` beside the package, one page
per format with golden vectors and conformance scenarios, and the
Python package is the reference implementation. toolroom-store's own
formats, the record, the index, the lock, the graphs, the receipts,
the bin directory's `.links.json` and the env emission, are prose in
`packages/toolroom-store/docs/index.md` with no spec page and no
vectors.

The `[tools]` table is read by the workshop (`requires`, `index`, the
per-tool `mode`) and validated by nobody: `_contract_keys` has no
entry for it. `hosts`, which the lock resolves against, is under
`[workspace]`, a workshop table.

## Prior art: mise

mise is the nearest working system, read on 2026-10-06 from its docs
and its registry on `main`.

- Its registry holds 1013 records, MIT (Jeff Dickey), one TOML each,
  thin: a short name mapped to backends in priority order. The
  per-platform download data lives in the backend's source, the aqua
  registry (MIT, Shunsuke Suzuki) for 667 of the first-choice
  backends, GitHub releases for 186, packslip (a vendor-signed
  sigstore manifest) for 12, http for 15.
- One record of the 1013 carries a `postinstall` script. The rest are
  data over a fixed option vocabulary that maps onto `LAYOUT_KEYS`,
  except `rename_exe`, used nine times, which toolroom lacks.
- Code runs on install only in the plugin backends (asdf shell, vfox
  Lua, 33 records with no other backend, new ones refused), the 13
  core runtimes compiled into mise, and the language package
  installers. This plan takes none of those.
- A record is a rule: versions are listed live from the vendor at
  install time and verification data (checksum file, attestation)
  arrives with the artifact. The index here is a list: versions
  observed on every host, digest fixed once. This plan keeps the list
  and automates the ingestion on the index side (phase 7).
- mise does no deduplication: a full tree per version, the download
  deleted after install.
- mise verifies more links of the chain than toolroom today: cosign,
  minisign, SLSA, GitHub attestations per recipe, and packslip pins
  the signer per tool. toolroom verifies the artifact against the
  record's sha256 and nothing about who published either.

## Ground-truth contracts (do not violate)

1. The index hosts records, never content. A record names where an
   artifact is and how to verify it.
2. A record is data. No hook, no script, no plugin. What a record
   cannot express declaratively, the index does not carry.
3. A published version is immutable: a refresh adds versions and never
   changes an existing version's digest. The index build refuses the
   change.
4. Tools are per machine, never per environment: a toolenv is links
   into the shared store. Bringdown removes links and receipts, never
   a version.
5. A toolenv is always created from a lock. A lock is resolved from
   one file's requirements, or by the workspace from its union of
   sites. The toolenv reads the lock and the graphs beside it.
6. The store imports no footman and no workshop, and knows no workshop
   key. What it reads from `workshop.toml` is the `[tools]` table it
   defines.
7. One implementation of the toolenv, two callers: the `toolroom`
   command and `fm sync`. The command holds no logic the store api
   does not expose.
8. Every format the store writes or reads is specified in `spec/`
   with vectors before a second implementation starts. A rule two
   implementations can disagree on (version ordering, host
   resolution) has vectors.
9. Nothing in the store, the lock, the toolenv or the index requires
   a Python on the consumer's machine. The handles, the stubs and the
   in-process lane are consumers of the index, outside it.
10. Kebab-case keys, no migration code: a wrong spelling refuses,
    naming the right one.

## Phases

### Phase 1: the `[tools]` table is the store's

The store owns the table: its key set, parser and refusals, in
`livery.toolroom.store.api`. The workshop reads the table through that
parser from the root and from each package. `hosts` moves to
`[tools] hosts` in the same table; `[workspace] hosts` refuses, naming
the new key. `index` becomes optional and defaults to the published
index's URL; `index = "records"` stays the authoring site's case.

Deliverables: the parser and its refusals in the store; `_tools.py`
and `_contract_keys` in the workshop reading through it; `hosts` moved
in this repository's `workshop.toml`; the store's docs describing the
table as the one a `toolroom.toml` carries too.

Acceptance:

- `fm check` green.
- A `workshop.toml` with `[workspace] hosts` refuses `fm tools.lock`
  with a message naming `[tools] hosts`; a test pins it.
- A `[tools]` key the schema does not know refuses, naming the key
  and the known set; a test pins it.
- `fm tools.lock` on this repository writes a lock equal to the one
  on `main` before the change (`git diff --exit-code tools.lock`).

### Phase 2: the toolenv in the store, the checkout its first caller

A `Toolenv` at a path: a bin directory, receipts and an env record,
created from a lock and its graphs, refreshed idempotently, removed
whole. The receipt, materialise and emission code of the workshop's
`_tools.py` moves into it. The workshop creates the checkout's toolenv
at `.workshop/` on `fm sync`, and `fm env.emit` adds the workshop's
own variables on top of the toolenv's emission. `fm env.check` reads
the toolenv's receipts.

Deliverables: `Toolenv` in the store api with `create`, `refresh`,
`emit(shell)`, `remove` and `receipts`; the workshop's `_tools.py`
reduced to the declaration sites, the union, the stubs and the CI
emission; the store's docs.

Acceptance:

- `fm check` green.
- `fm sync` twice in a row changes nothing the second time; a test
  pins it.
- The receipts and `.workshop/bin` after `fm sync` are byte-identical
  to those before the change on the same lock (compare with the
  recorded set in the test).
- `fm env.emit posix` and `fm env.emit windows` emit what they emit on
  `main` before the change; the pinning tests from the current
  `_tools.py` suite pass unchanged against the moved code.
- `fm ci.e2e` passes on the local loop, since the loop asserts printed
  lines of sync and env verbs.

### Phase 3: the `toolroom` command

A console script `toolroom` in `livery-toolroom-store`, argparse over
the store api, no footman. Config from the `[tools]` table of
`toolroom.toml` or `workshop.toml` in the directory named, one file
per directory: both present refuses, naming both; `--config` names a
file and wins.

Verbs:

- `toolroom lock [dir]`: resolve `tools.lock` and `tools.graphs/`
  from the file's requirements against the index; `--index` names a
  mirror or a records directory; `--platform` adds hosts.
- `toolroom create <path> | --name <name>`: a toolenv from the lock
  beside the config, or `--lock`; by name under the home's `envs/`.
- `toolroom env <path|name> --shell posix|windows`: the activation.
- `toolroom list`: the toolenvs the home knows and what each links.
- `toolroom remove <path|name>`.
- `toolroom run <path|name> -- <cmd>`: the command under the toolenv,
  for scripts and CI, as `mise exec` is.

Deliverables: `_cli.py` in the store; `[project.scripts]`; the store's
docs with a page written as the command's reference.

Acceptance:

- `fm check` green.
- `fm workflow.release --local` green for `livery-toolroom-store`: the
  wheel installs alone and `toolroom --help` runs from it with no
  footman in the environment.
- In a scratch directory with a `toolroom.toml` naming `ruff` and
  `node`: `toolroom lock`, `toolroom create .toolenv`, then `.toolenv`
  carries `ruff` and `node` links into the home's views; `toolroom
  remove .toolenv` leaves the views; a second `toolroom create` of a
  different lock pinning another ruff version links that version, and
  `toolroom list` shows both. One test drives the sequence with the
  records directory as the index.
- Both config files in one directory refuse, naming both; a test pins
  it.
- `fm sync` and `toolroom create` on this repository's lock produce
  the same receipts and links (the workshop's test compares them).

### Phase 4: the formats as a standard

`spec/` beside toolroom-store, after strongroom's: `record.md`,
`index.md` (the pointer, the trees, the HTTP mapping), `lock.md` (the
lock and the graphs directory), `toolenv.md` (the bin directory, the
links manifest, the receipt, the emission). Vectors under
`spec/vectors/` for the record's canonical form, the lock resolved
from a fixed catalogue and requirement set, version ordering
(`version_key`), host resolution including the refusals, and the
emission per shell. Conformance scenarios under `spec/conformance/`
for create, refresh, remove and offline rebuild. The Python
implementation runs them in its suite.

Acceptance:

- `fm check` green, the vectors and scenarios run by the store's
  suite.
- `fm lint.doclinks` green over the new pages.
- A deliberate change to `version_key` fails a vector; proved once and
  reverted in the same change (the test is the vector).

### Phase 5: the index format before the first external lock

Everything that would be a break for every lock once an outside
consumer locks against the published index:

- A per-artifact `verify` entry in the record: the method and the
  identity (a GitHub attestation by repository, a cosign identity and
  issuer, a minisign public key, a checksum file beside the asset).
  The store checks it before landing and refuses a failed check; an
  absent entry lands with the digest alone and the receipt says so.
- A `rename` layout key: an asset's executable landing under another
  name on PATH.
- A signed pointer: the index build signs the pointer it writes, with
  the repository's SSH key or a sigstore identity (open item 4), and
  the store verifies it against a pinned key, refusing a pointer it
  cannot verify unless `--index` names a local directory.
- Immutability in the index build: a published version whose digest
  would change refuses, naming the version and both digests.
- `THIRD-PARTY-LICENSES` in the index and a `source` field per record,
  for phase 6.

Acceptance:

- `fm check` green.
- `fm tools.index.build` refuses a records change that alters a
  published version's digest; a test pins it.
- A record with a failing `verify` entry refuses `Store.ensure`,
  naming the method and the artifact; a tampered artifact under a
  checksum-file entry refuses; tests force both.
- A pointer with a wrong signature refuses `Catalogue.of_index`; a
  test forces it with a second key.
- The spec pages of phase 4 updated in the same change.

### Phase 6: records from mise and aqua

A bench verb `fm tools.convert` reading mise's registry and the aqua
registry and writing toolroom records for the download records alone:
first backend aqua, github, gitlab, http or packslip, about 880 of
the 1013. Core runtimes, language package backends, asdf and vfox are
left out by rule. Each converted record names its source and what the
converter verified on the way in (the source's checksum or
attestation, or neither); a record converted with no vendor-side
check is marked as pinned on first sight. The converter is repeatable
and tested against the mise record schema it read, since that schema
promises nothing.

Acceptance:

- `fm check` green.
- `fm tools.convert --only=ruff` writes a record `fm tools.verify`
  accepts and `fm tools.lock` resolves on every host.
- The converter refuses a mise record whose first backend is a plugin
  or a language installer, naming the backend; a test pins it.
- `THIRD-PARTY-LICENSES` in the index holds both MIT texts;
  `fm tools.index.build` writes it; a test pins it.

### Phase 7: ingestion automated on the index side

The record carries a discovery rule: the asset pattern per version
range and the checksum or attestation source, as aqua's recipes do. A
scheduled refresh on the host matrix discovers releases newer than
each tool's base, verifies each against the rule, observes the
surface, and publishes what passed. A release that fails is named in
the run and left out. `fm tools.audit` names the lag between a
vendor's newest and the index's.

Acceptance:

- `fm check` green.
- `fm tools.gather` on a fixture index with a new upstream release
  observes it, `fm tools.assemble` folds it in, and the index build
  publishes it; a test drives the sequence with a fake vendor.
- A release whose artifact fails its `verify` entry is left out and
  named; a test forces it.
- The nightly on the matrix is dispatched once by hand and green on
  every host; the run id recorded here.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `[workspace] hosts` | `[tools] hosts`, phase 1 |
| The toolenv code in the workshop's `_tools.py` | `Toolenv` in the store, phase 2 |
| The published index pointer unsigned | the signed pointer, phase 5 |
| Hand-written records alone | converted records (phase 6) and the refresh (phase 7) |

## Decision record

- 2026-10-06, Willem: a toolroom command line, used like virtualenv or
  parts of uv: several toolenvs with different versions of one tool, a
  shared cache, quick bringup and bringdown. Not a per-project copy of
  the tools.
- 2026-10-06, Willem: toolenvs live at any path, and by name under the
  home.
- 2026-10-06, Willem: the command reads its config from TOML;
  `workshop.toml` is valid, and so is `toolroom.toml`.
- 2026-10-06, Willem: the index is a central store, like PyPI, hosting
  records and no content. The authoring site reads its own records.
- 2026-10-06, Willem: a separate, future plan, not a phase of the
  extensions plan.
- 2026-10-06, Willem: scripts and plugins are out; supply chain
  awareness is a design input, not an afterthought.
- 2026-10-06, Willem: nothing Python specific; the design ports to
  Rust.
- 2026-10-06, agent, instead of asking: the bench stays on footman.
  Rewriting its tasks as a command replaces footman with a second task
  framework.
- 2026-10-06, agent, instead of asking: the index stays a list of
  observed versions, not a rule evaluated on the consumer's machine.
  Ingestion is automated on the index side with proof on the way in.

## Open

1. Sequencing (Willem). The plan touches the workshop's `_tools.py`
   heavily in phase 2. The agent's recommendation: land phases 1 and 2
   before the extensions plan's phase 11b moves the kinds out of the
   base, so the kinds' tool declarations move once. Phases 3 to 7 do
   not depend on 11b.
2. Private records over the central index (Willem). A monorepo with an
   in-house tool has nowhere to put its record. Options: (a) central
   index only, an in-house tool is a system tool or a delegated kind,
   which does not cover a private binary with its own layout; (b)
   `index` accepts a list merged by version, which covers it and
   brings the confusion pip's extra-index-url has; (c) `index` accepts
   a list where a name found in an earlier source is taken whole and
   never merged across sources, which covers it and keeps a private
   name private. The agent recommends (c).
3. The `hosts` move is a break for every `workshop.toml` that sets
   `[workspace] hosts` (Willem). Pre-1.0, a feature bump; the refusal
   names the new key.
4. The pointer's signing key (Willem): the repository's SSH key, which
   commits already use, or a sigstore identity bound to the index
   build's workflow, as packslip does. Distribution of the pinned
   public key to clients decides it: a key in the store's source is
   simple and rotates with a release; sigstore needs no key shipped
   and a network at verify time.
5. The admission rule for the central index (Willem): mise's is
   written down, widely used, versions listable, download backends
   preferred. Ours needs one before phase 6 converts 880 records.
6. Delegated trees into strongroom (agent, after phase 3). A venv is
   mostly files shared across versions, so dedup would pay; a venv has
   absolute paths inside it, so a view is not relocatable. Out of this
   plan; measured after phase 3 on this repository's seven graphs.
7. The console script's name (Willem): `toolroom`, in
   `livery-toolroom-store`, so `uv tool install livery-toolroom-store`
   gives it with no footman or workshop.
8. A Rust implementation (Willem): out of scope; phase 4 is what makes
   it a port. Its name, home and the conformance run against the
   shared vectors are a plan of their own.
