# Direct downloads: a tool comes from its own release, PyPI only for Python

Status: ruled 2026-09-25 as drafted, with git-cliff on a base kind
(decision record). Phase 1 landed 2026-09-25 (livery#674, PR #675): gh's
newest version carries six artifacts and passes the nine checks on six
hosts. Phase 2 landed 2026-09-25 (livery#676, PR #678): git-cliff 2.13.1
carries six artifacts, passes on six hosts, the lock holds it on three,
and every CI job materialises the lock's tools before the emission.
Phase 3 in flight (livery#677): ruff 0.16.4 and pyrefly 1.3.1 carry six
artifacts, ty, uv and prek gained `windows-arm`, and the venv carries
none of ruff, ty, pyrefly. Phase 4 gate-green on its branch (livery#680). Sequenced before the modular docs plan by Willem's word of
2026-09-25. The tool record plan
(`notes/20260914-the-tool-record-and-its-index.md`) is complete, which
is the precondition: the record, the store, the lock and the six-host
verification point all exist.

## The ruling (Willem, 2026-09-24)

On finding git-cliff reaching the workspace through a PyPI dev
dependency with no Windows ARM wheel, while git-cliff's own release
publishes one:

> I want to always prefer direct downloads, especially for single
> executable files. We should only use PyPI if there is no other
> option. If it is not actually made in Python, it is just a wrapper
> and (slight) overhead.

## Where things stand

Measured on main at 403527f3, 2026-09-25. 38 records under `records/`,
12 tools in `tools.lock`, three locked hosts (`linux-x64`, `macos-arm`,
`windows-x64`).

The tools, by what they are made of and where they come from today:

| Tool | Made of | Record kind today | Own release | Locked here |
| --- | --- | --- | --- | --- |
| ruff, ruff_format | Rust | `uv-tool` | `astral-sh/ruff`, six hosts | yes, `uv-tool` |
| pyrefly | Rust | `uv-tool` | `facebook/pyrefly`, six hosts | yes, `uv-tool` |
| git-cliff | Rust | `uv-tool`, not locked | `orhun/git-cliff`, six hosts | no: a venv dev dependency |
| ty, uv, prek | Rust | `archive`, five hosts | yes | ty, uv |
| cmake | C++ | `uv-tool` | `Kitware/CMake`, linux x64 and arm, macOS universal, windows x64 and arm | yes, `uv-tool` |
| ninja | C++ | `uv-tool` | `ninja-build/ninja`, linux x64 and arm, mac universal, win x64 and arm | no |
| gh | Go | `archive`, no hosts, no artifacts | `cli/cli`, six hosts | no |
| tea, eclint | Go | `binary`, `archive`, five hosts | yes | no |
| basedpyright | Node | `uv-tool` | npm `basedpyright` | yes, `uv-tool` |
| cspell, markdownlint, claude | Node | `bun-install` | npm | no |
| mypy, pytest, coverage, twine, build, djlint, git-changelog, mkdocs, zensical | Python | `uv-tool` | PyPI is the release | mypy, pytest, coverage, zensical |
| bun | Zig | `archive`, five hosts | `oven-sh/bun` | no |
| python | | `uv-python` | | no |
| bash, zsh, fish, pwsh, nu, cmd, git, docker, ssh, ssh-keygen, ssh-keyscan | | `system-check` | | git, docker |

What the code does with them:

- **The bench reads a tool by installing it.** `fm tools.gather`
  installs each new release into a scratch directory through the
  driver's provision tier (`livery.toolroom.bench._toolfetch.install`)
  and reads its help. The `uv` tier is the default; its docstring says
  it covers the Rust and C++ tools because they ship wheels. A forge
  tier (`github`, `gitlab`, `gitea`) picks the release asset for the
  running machine (`_provision._pick_asset`), downloads it, reads it,
  and forgets the URL.
- **No code records an artifact.** The six archive records that carry
  one (ty, uv, prek, bun, eclint, tea) carry it on one version each,
  five hosts, never `windows-arm`; they were written into the records
  by hand with the record format. gh's record has neither hosts nor
  artifacts, so gh cannot be supplied by the store. No module under
  the bench computes a digest.
- **The store supplies by kind.** An `archive` or `binary` is
  downloaded by URL, verified by sha256, staged and collected as a
  tree. A `uv-tool` is `uv tool install`ed into the store's home at the
  locked version. `bun-install` and `uv-python` refuse naming the
  kind.
- **The lock picks the newest version that resolves on every locked
  host.** A downloaded kind resolves on a host when the version has
  that host's artifact; a delegated kind resolves everywhere. A version
  without artifacts is history the lock walks past.
- **This checkout already runs the store's ruff.** The receipt at
  `.workshop/receipts/ruff.json` names
  `~/.local/share/footman/toolroom/uv/tools/ruff@0.16.4`, mode `path`.
  The venv carries a second ruff, ty, pyrefly and git-cliff from the
  rendered dev group; the release train runs `git-cliff` by name from
  PATH, so the venv's copy is the one it gets, and the template carries
  a Windows ARM marker so the venv can sync there without it.
- **The six-host point exists.** `tools.verify-host` runs every two
  weeks on six runners and installs, runs and reads every record of a
  downloaded kind on its host. A tool that moves to `archive` is
  covered by it the day it moves.
- **The refresh runs every two weeks** on the nightly point,
  `fm tools.refresh --submit`, armed when every change only added to a
  surface.
- **An archive's root is declared exactly.** `Layout.root` names the
  directory to hoist; the store refuses an archive without it.
  git-cliff's tarball unpacks to `git-cliff-2.14.2/`, a root that
  changes every version; ruff's to `ruff-aarch64-apple-darwin/`, a
  root that changes per host and never per version.

## Ground-truth contracts (do not violate)

1. **A tool that is not Python never comes from PyPI.** The `uv`
   provision tier is for Python programs alone. A test over the driver
   table pins the tier's members to the named Python tools, so a new
   tool cannot enter through PyPI unnoticed: adding one means editing
   the pinned list in the same change, where the review sees it.
2. **An artifact is recorded by downloading it and hashing it.** Never
   copied from a release's sidecar file, never written by hand. The
   store verifies the same digest on every install, so the hash the
   record carries is the hash of bytes the bench had.
3. **A host is on a version when the release publishes an asset for
   it.** A host without one is absent from that version: not a hole,
   not a refusal, not a reason to stop the refresh. The lock's hosts
   stay the gated three; the six-host point verifies every host a
   version has.
4. **A kind change loses no history.** Flipping a record's axis line
   from `uv-tool` to `archive` keeps every version, date and option
   line. Versions without artifacts stay readable history and never
   lock.
5. **One copy per tool per machine, the store's.** After the phase that
   moves a tool, the venv's dev group does not carry it, and the code
   that runs it goes through its toolroom handle so the run has a
   receipt.
6. **Everything through `fm`.** Refusals tested before happy paths.

## The design

### The refresh records artifacts

On the assembler, after a forge-tier driver's new version is read,
one more step per version: list the release's assets once, pick one
asset per host with a picker that takes the host as an argument
instead of reading the running machine, download each, hash each,
and write `artifacts[host] = {url, sha256}` on the version line. The
ingest verification (`fm tools.verify`) already stages each host's
artifact through the bench's store; the download is shared, so a
version is hashed and verified in one pass.

A host the release has no asset for is left off the version. A host
whose asset downloads but fails the nine checks refuses the refresh
with the check and the host named, as it does today.

Only forge tiers record artifacts. A `uv` tier record stays a
delegated kind with no hosts, as the Python tools are.

### The layout, once per tool

An archive unpacks into a tree, and `Layout.root` names the directory
inside it that is the tool: the store hoists that directory to the
install root and puts `paths` on PATH relative to it. It is matched
exactly. Some tools name that directory after the version: git-cliff
2.14.2 unpacks to `git-cliff-2.14.2/`, 2.14.3 to `git-cliff-2.14.3/`.
The record can say that today only as a layout override on every
version line, each restating the version as a root.

`Layout.root` therefore learns one token, `{version}`, replaced by
the version being staged, mirroring `PACKAGE_VAR` in `env`.
git-cliff's axis line then says `root = "git-cliff-{version}"` once.
A root that changes per host and never per version (ruff's
`ruff-<triple>`) goes in `host_layouts` on the axis line, which
exists already. A root that carries both, cmake's
`cmake-4.4.3-linux-x86_64/`, combines the two. The store still
refuses an archive that has no such root, naming the directory it
looked for after substitution.

The layout is authored once when a tool moves, and the nine ingest
checks prove it on every host on every later version.

### A tool moves

Moving one tool is four edits and one refresh:

1. The driver's `provision` names the forge and the repository.
2. The record's axis line flips `kind` and gains `hosts` and the
   layout.
3. `fm tools.refresh --only=<tool>` records the newest version's
   artifacts through the path above.
4. Where the workspace consumed the tool from the venv, the dev-group
   entry leaves the template and the code runs the tool through its
   handle.

The record's earlier versions, read from PyPI with their dates, stay.
New versions list from the forge, whose tag `read_version` normalises
to the bare number the binary reports.

### basedpyright and the node tier

basedpyright is a Node program that PyPI ships with a bundled Node.
Its tier is `node`, its kind `bun-install`, which the store does not
supply yet. The store learns `bun-install` the way it does `uv-tool`:
`bun add --global` at the locked version into a tool directory of its
own under the store's home, its launchers the entry points, through
the store's own bun, which the lock therefore holds as an `archive`
whenever a `bun-install` tool is locked.

## Phases

Each phase lands alone, gate-green, through `fm submit --armed`. Phase
1 is the mechanism and goes first; every later phase is one refresh
away once it exists. Open question 4 offers git-cliff ahead of phase
1 at the price of one hand-written line.

### Phase 1: the refresh records artifacts, and the root token

Deliverables:

- `_pick_asset(assets, host=...)`: the host's OS and CPU aliases, not
  the machine's; the machine's host is the default.
- The artifact step in the assembler: download, hash, record per host;
  shared with the ingest verification's staging.
- `Layout.root` takes `{version}`; the refusal names the substituted
  root.
- The pinned `uv`-tier list in the drivers' tests, naming the Python
  tools.
- gh's record gains hosts and artifacts on its next refresh, since gh
  is already a `github` tier driver.
- The `_provision` and `Provision.kind` docstrings say what the tiers
  are for now: PyPI for Python programs, a forge for everything with a
  release.

Acceptance:

- Refusals first: a host without an asset is absent and the refresh
  continues; a release the forge lacks refuses naming every tag
  spelling tried; a host whose deployment does not resolve whole
  refuses before anything is written; a `{version}` root the archive
  lacks refuses naming the substituted root; a non-Python driver on
  the `uv` tier fails the pinned-list test. The downloaded bytes are
  landed in the bench's store once, so the verification stages them
  without a second download and a digest cannot differ between the
  two; a later download that serves other bytes is the store's own
  integrity refusal.
- `fm tools.artifacts gh` against the live forge writes six
  `artifacts` on gh's newest version and passes the nine checks on
  all six hosts from this machine. gh's record needed its layout
  authored first (a root that carries both the version and the host
  on the unix hosts, none on Windows), which is the one hand edit a
  move keeps.
- `fm check --full` green.

### Phase 2: git-cliff

Deliverables:

- Driver: `Provision(kind="github", repo="orhun/git-cliff")`.
- Record: `archive`, `root = "git-cliff-{version}"`,
  `entry_points = ["git-cliff"]`, `paths = ["."]`; artifacts on the
  newest version from `fm tools.refresh --only=git-cliff`.
- A base kind joins the registry in `livery.workshop._kinds`, the
  record behind the `package-base` template every package template
  already renders first; `python` and `cpp-conan` name it as their
  parent, so `kind_chain` starts there for every kind. git-cliff is
  the base kind's tool, and `cliff.toml` its managed file, since every
  kind releases; the leaf kinds stop restating both. The lock holds
  git-cliff on the three hosts through `kind_tools` as it does every
  kind's tool.
- `livery.workshop._cliff._run` calls `tools.git_cliff`, so the run
  carries a receipt; the stub renders from the record like every
  locked tool.
- The dev-group entry and its Windows ARM marker leave the template;
  `fm template.apply` moves the rendered `pyproject.toml` and
  `uv.lock`.

Acceptance:

- `grep -n git-cliff pyproject.toml` prints nothing; `fm tools.lock`
  holds git-cliff as `archive` on three hosts.
- `fm release.prepare packages/forge` on a branch with one
  conventional commit derives the same bump as before the move, from
  the store's binary: the run's receipt names a tool directory under
  the store's home.
- The six-host point green on every leg including `windows-11-arm`,
  the host the wheel never covered.

### Phase 3: ruff and pyrefly

Deliverables:

- Drivers: `astral-sh/ruff` and `facebook/pyrefly` on the `github`
  tier; `ruff_format` follows ruff's record kind since it is ruff's
  binary.
- Records: `archive`. ruff's archives are uv's shape: one root per
  host triple (`ruff-x86_64-unknown-linux-gnu/ruff`) and a bare
  `ruff.exe` at the top of the Windows zips, so its `host_layouts`
  copy uv's. pyrefly's archives are the bare binary on every host
  (`pyrefly`, `pyrefly.exe`), so its layout is `entry_points` and
  `paths = ["."]` with no root; its linux assets come in gnu and
  musl, and the picker prefers gnu.
- The ty, uv and prek drivers move to the github tier too, so their
  new versions list from the forge that publishes the artifacts their
  records already carry.
- The dev-group entries `ruff`, `ty` and `pyrefly` leave the template;
  ty is an `archive` in the lock already.

Acceptance:

- `fm check --full` green on the three gated hosts with no ruff, ty or
  pyrefly in the venv (`ls .venv/bin/ruff` fails).
- Every gate run's ruff and pyrefly receipts name the store's tree, not
  a uv tool directory.
- The six-host point green.

### Phase 4: cmake and ninja

Deliverables:

- Drivers: `Kitware/CMake` and `ninja-build/ninja` on the `github`
  tier.
- Records: cmake's `host_layouts` put `bin` on PATH on linux and
  windows and `CMake.app/Contents/bin` on macOS, where one universal
  archive serves both macOS hosts; ninja's archive is the bare binary
  at the top.
- The python-nanobind kind's tools resolve to the moved records.

Acceptance:

- `fm tools.verify cmake` and `fm tools.verify ninja` pass on the
  running host; the six-host point green.
- The nanobind package's configure step runs the store's cmake, by its
  receipt.

### Phase 5: basedpyright to the node tier

Deliverables:

- The store supplies `bun-install` through the locked bun.
- Driver: `Provision(kind="node")`; record kind `bun-install`.
- bun joins the lock as the dependency of any `bun-install` tool; the
  dev-group entry `basedpyright` leaves the template.

Acceptance:

- Refusals first: a `bun-install` lock without bun refuses naming bun;
  a bun that fails the install leaves nothing behind and names the
  exit.
- `fm check --full` green with the type gate's basedpyright supplied
  by the store on three hosts; `ls .venv/bin/basedpyright` fails.

## Out of scope, named

- mypy, pytest, coverage and zensical are both locked as `uv-tool` and
  in the venv's dev group. Two copies of a Python tool is a separate
  question from this plan's, and pytest at least must be the venv's,
  since it imports the packages under test.
- The `uv` dev-group entry: uv is an `archive` in the lock already;
  the venv's copy is there so `uv` resolves inside the environment.
  Untouched here.
- cspell, markdownlint and claude are `bun-install` records no site
  locks; phase 5 gives them a supplier, and locking them is a site's
  decision.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The Windows ARM marker on git-cliff in the project template | phase 2, deleted |
| `ruff`, `ty`, `pyrefly` in the template's dev group | phase 3, deleted |
| `basedpyright` in the template's dev group | phase 5, deleted |
| The `uv` tier docstring's claim that wheels cover the Rust and C++ tools | phase 1, rewritten |
| Hand-written artifacts on six records, one version each | phase 1's next refresh writes the newest version's; the hand-written lines stay as history |

## Decision record

- 2026-09-24, Willem: direct downloads always where a release exists;
  PyPI only for a tool that is Python.
- 2026-09-25: drafted. Sequenced before the modular docs plan by
  Willem's word.
- 2026-09-25, Willem: git-cliff is required by the base every package
  kind derives from: it generates the changelog for every package type
  that can release. The template chain already starts at
  `package-base`; the kind registry gains the matching base record and
  phase 2 declares git-cliff there.
- 2026-09-25, Willem: `{version}` in `Layout.root` is the right
  approach.
- 2026-09-25, Willem: one download per new version per host, shared
  with the verification, is the correct shape: as few downloads as
  possible for the maximum use.
- 2026-09-25: a runner had no receipts before phase 2, since the entry
  script never materialised the lock; every tool the gate ran came from
  the venv. The entry script now runs `fm tools.materialise` before it
  persists the emission, and writes the PATH entries last to first so
  the venv's bin stays ahead of the store's directories, as the shell's
  emission orders them. The locked ty moved to 0.0.73: the gate's ty had
  been the venv's 0.0.75, and the locked 0.0.62 flagged seven calls it
  accepts. The store's home is not cached across runs yet (livery#679).
- 2026-09-25: a verb-bound view of another driver's binary
  (`ruff_format` is `ruff format`) records no artifacts and the verb
  refuses it naming the driver to record; the binary's own record
  carries them. uv's Windows archives carry `uvw.exe`, the windowless
  launcher; the Windows layouts exclude it.
- 2026-09-25: a kind declares a tool by its record's name, which is
  its handle's (`git_cliff`), since that is how the catalogue lists
  it and how the lock and the receipts name it. The hyphen spelling
  stays the binary's and the entry point's.
- 2026-09-25: git-cliff's archives carry two helper binaries beside
  the tool (`git-cliff-completions`, `git-cliff-mangen`); the record
  excludes them, so neither reaches PATH. git-cliff ships a MinGW
  build beside the MSVC one on Windows; the asset picker now prefers
  against `windows-gnu` as it does against `musl`.
- 2026-09-25, Willem: the order is whatever reaches the end result
  quickest. Phase 1 first, then git-cliff: authoring git-cliff by hand
  needs the same layout and six hashes phase 1 produces by code.

- 2026-09-25, phase 4: a macOS app bundle is sealed by its code
  signature. cmake's first layout excluded `ccmake` and `cmake-gui` from
  inside `CMake.app`; Gatekeeper judged the bundle damaged, killed
  `cmake` (exit 137 under conan), and its "Move to Bin" removed the
  store's view. No quarantine attribute was involved. A layout removes
  nothing from under a bundle's seal: the macOS layout names every
  executable in `CMake.app/Contents/bin` as an entry point. A layout
  change for an installed version leaves the machine store's ref pinned
  to the old tree, and no verb clears it yet (livery#682). The asset
  picker learned `universal`, arch-less builds and ninja's `winarm64`;
  ninja's newest release has all six hosts through it.

## Open

None. Every question raised at drafting is in the decision record.
