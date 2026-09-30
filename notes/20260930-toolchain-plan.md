# Toolchains: the host's, a floor, or an exact version

Status: written 2026-09-30 from Willem's rulings of 2026-09-29 and
2026-09-30. Phase 1 (the host allowance for store tools) is built in
the base (issue #937, Willem's go of 2026-09-30). Phases 2 to 4 belong to
`livery.workshop.cpp`, so they wait for that layer's extraction (the
extensible gate plan's open item 17 and the plan it names).

## The prompt (Willem)

> We need to be able to configure using the host compilers, or a
> version floor, possibly exact. That exact version needs to be
> installable or an error. We need to look at this holistically and
> design a system of configuring the toolchains that works in nearly
> all cases: detect the host toolchain if allowed, and pick the
> family if multiple are installed; support Unreal 5.7+ compilation,
> ideally by default on GitHub free; record the toolchain used for
> SBOM reasons, which is mostly relevant for detected versions; use
> the system version of any tool on the path by default if allowed,
> so that the smallest amount of tools is installed when there are
> no strict requirements. And this should only be a thing if a cpp
> layer is installed as well. Willem, 2026-09-29 and 2026-09-30

## What exists that this builds on

- The tool store's records have six kinds; `system-check` is the
  machine's own tool, found on PATH by `shutil.which` and held to a
  floor by `--version` (`livery.toolroom.store._engine._supply_system`).
  `docker` and `git` resolve that way today; the receipt says
  `mode: none` and the directory the tool was found in.
- A requirement is `name[>=floor][@hosts]`; a scope token is a
  platform (`windows`, `macos`, `linux`: every locked host of it) or
  a host key (`windows-x64`, `windows-arm`, `linux-x64`, `linux-arm`,
  `macos-arm`, `macos-x64`). The lock resolves a tool on the union of
  its requirements' scopes (`livery.toolroom.store._lock`).
- A kind names what the host must already provide and the store
  never supplies: `host_tools=("cc", "c++")` on the nanobind and
  cpp-conan kinds; `fm doctor` and `fm env.check` name a missing one
  (`livery.workshop._env_tasks.missing_host_tools`).
- The cpp-conan backend detects the compiler through conan's own
  `profile detect`, reads the id and path CMake detected for the gate
  build, and picks the measurer by that id: gcc to gcov, clang and
  apple-clang to llvm-cov, msvc to Microsoft's engine, entered through
  `toolchain_env()` (`livery.workshop._backends._cpp_conan`).
- A warm `fm tools.sync --frozen` takes 1.2 s for 18 receipts and
  spawns no tool; a receipt is present when its directory exists and
  its deployment digest is the lock's. The entry runs the same once
  per CI job; on a cold store the downloads dominate.
- Releases are receipt tags (`<path>/v<semver>`, annotated, the body
  the tag's name); a CI run records its profile under
  `refs/workshop-trace/run/<id>/` and its gate record in the state
  store (`livery.workshop._state`).

## Ground-truth contracts (do not violate)

1. **Three answers and no fourth.** A toolchain resolves as the host's
   (detect among the admitted families, newest first), as a floor
   (the host when it satisfies, else the newest installable record
   that does), or as an exact version (the host on an exact match,
   else that exact record, else a refusal). The refusal names the
   host, the family, the spec, and what would satisfy it: the record
   to add, or for `msvc` and `apple-clang` the installer, since those
   two are never installed by the store.
2. **Unwanted families are never probed.** Detection runs for the
   admitted families only, in their order, and stops at the first
   that resolves. A probe runs only when the receipt is absent or
   stale; the receipt is keyed by the declaration and the probe's
   inputs (the driver's path, size and modification time; for MSVC
   the instance and toolset vswhere reports).
3. **A platform scope covers its hosts; a host key overrides.**
   `[cpp.toolchain.windows]` speaks for `windows-x64` and `windows-arm`;
   `[cpp.toolchain.windows-arm]` beside it wins for that host alone. The
   same vocabulary the lock's scopes use, nothing new to learn.
4. **Verdict tools stay store-first.** A tool a check role reads a
   verdict from (format, lint, typecheck, typecomplete, test) and the
   release train's git-cliff take no host allowance; the lock refuses
   one by name. Build and toolchain tools may take it. `uv` stays the
   entry's, pinned by the lock.
5. **What was used is written down.** Every resolved toolchain and
   every host-resolved tool leaves a receipt: family, version, path,
   the driver binary's digest, the source (`host` or `store`), the
   sysroot's version. A CI leg's receipts ride its run record and the
   release records, per artifact, the receipts of the leg that built
   it, so an artifact's toolchain is readable from its release.
6. **One answer for conan, CMake and the measurer.** The conan
   profile is rendered from the receipt, never detected a second
   time; the measurer follows the receipt's family.
7. **The unreal kind reads the engine.** Its declaration derives from
   `Engine/Config/<Platform>/<Platform>_SDK.json` of the engine the
   project names, never from a table typed by hand; the Linux cross
   toolchain stays UnrealBuildTool's own download.
8. **The lock schema stays additive.** New fields; nothing existing
   changes meaning. A lock written before this plan reads unchanged.
9. **Everything through `fm`.** `fm sync` resolves, `fm doctor`
   explains the resolution, `fm tools.lock` locks the fallbacks.
   Nothing on the merge path waits for a person.
10. **A toolchain is the cpp layer's.** The declaration, the probes,
    the toolchain receipt, the `llvm` and `gcc` records and the
    unreal kind's derivation live in `livery.workshop.cpp`. The
    table is `[cpp.toolchain]`: the cpp layer owns `[cpp]` the way
    the forge layer owns `[forge]` and the docs layer `[docs]`. A
    workspace that mounts no cpp layer has no `[cpp]` table (the
    contract refuses it by name as that layer's), probes nothing and
    installs nothing of it. The host allowance of contract 4 is the
    base's, since it concerns every store tool.
11. **A package overrides, key by key, and several toolchains may be
    active at once.** A package's `[cpp.toolchain]` table wins for
    every key it sets and inherits the rest from the workspace's; a
    package that wants nothing inherited sets `policy`, `prefer` and
    each platform itself. The resolution runs per effective
    declaration, so two packages with different declarations resolve
    to two toolchains in one workspace, each with its own receipt and
    profile. The lock holds one entry per toolchain record and exact
    version, since the store already keeps versions side by side.

## The design

### Families and where each comes from

| Family | Installable by the store | Host only |
|---|---|---|
| `clang` | LLVM's release archives, every host but macos-x64 | |
| `gcc` | xpack's archives on Linux; xpack or WinLibs on windows-x64 | macOS |
| `msvc` | | Visual Studio Build Tools; found by vswhere |
| `apple-clang` | | Xcode or the Command Line Tools; found by xcrun |

LLVM's archives cover linux-x64, linux-arm, macos-arm, windows-x64
and windows-arm at 0.3 to 1.2 GB as zst, and ship lld, llvm-cov,
llvm-profdata, clang-format and clang-tidy. xpack's gcc covers
linux-x64 and linux-arm with binutils and gcov at 150 to 170 MB, and
windows-x64 as MinGW-w64; WinLibs is the second Windows source. On
macOS gcc is Homebrew's or the image's, never an archive. A sysroot
stays the host's on every platform: glibc's headers and crt objects
on Linux, the SDK on macOS, the MSVC headers, STL and Windows SDK for
`clang-cl`. `zig` is a family for a later plan.

### Where it lives

The cpp layer, `livery.workshop.cpp`, as the extensible gate plan
rules it: a python-only project mounts no cpp layer and pays nothing
for compilers. The base keeps the host allowance for store tools and
the receipt shape; the layer registers the `[cpp.toolchain]` table, the
probes, the records and the unreal kind. Phase 1 lands in the base
now; phases 2 to 4 land in the layer once it exists as a package.

### The declaration

In `workshop.toml`; a package's own `workshop.toml` may carry the
same table and overrides the workspace's key by key (contract 11).

```toml
[cpp.toolchain]
policy = "host"                     # host: detect, use what satisfies
                                    # store: install, never detect
prefer = ["msvc", "clang", "gcc"]   # the admitted families, in order

[cpp.toolchain.windows]
msvc = ">=14.44.35211"

[cpp.toolchain.windows-arm]
msvc = ">=14.44.35211"
clang = "==20.1.8"

[cpp.toolchain.linux]
clang = "==20.1.8"

[cpp.toolchain.macos]
apple-clang = ">=15"
```

- `prefer` names the admitted families in order. Absent, the
  platform's default applies: `msvc, clang, gcc` on Windows, `gcc,
  clang` on Linux, `apple-clang, clang` on macOS.
- A family with a spec is constrained; a family without one is
  admitted as it is found. A spec for a family outside the admitted
  list refuses at parse: "clang is constrained and not admitted; add
  it to prefer".
- A spec is `>=version` (a floor) or `==version` (exact). No spec is
  the host's newest of that family.
- `policy = "store"` never probes, except `msvc` and `apple-clang`,
  which have no store and are always probed when admitted. A CI leg
  that wants a reproducible clang names an exact version under this
  policy.

### Resolution, in `fm sync`

For the admitted families in order, for this host:

1. A fresh receipt is reused; nothing runs.
2. The family is probed: `msvc` through vswhere (instances, then the
   toolsets under `VC/Tools/MSVC`), `apple-clang` through `xcrun
   --find clang` and its `--version`, `clang` and `gcc` through PATH
   and the versioned names (`clang-20`, `gcc-15`) and the store's own
   tool directories. Each probe is one function behind a seam, so a
   test fakes the machine.
3. The spec is applied to the candidates, newest first.
4. Nothing satisfies: a floor installs the newest record that does, an
   exact version installs that record, for `clang` and `gcc`; for
   `msvc` and `apple-clang` the refusal names the installer.
5. The first family that resolves wins. The receipt says which, and
   which were probed and why they did not.

The resolution is keyed by the effective declaration: the workspace's
table with a package's keys laid over it. One receipt per distinct
effective declaration, under `.workshop/receipts/toolchain/`, named
by the declaration's digest; each package's contract resolves to its
receipt, and a workspace whose packages all share the declaration has
one. The receipt:

```json
{
  "schema": 1,
  "host": "windows-x64",
  "family": "msvc",
  "version": "14.44.35211",
  "source": "host",
  "driver": {"path": "C:/.../cl.exe", "digest": "sha256:..."},
  "tools": {"cc": "...", "cxx": "...", "ld": "...", "ar": "...", "cov": "..."},
  "sysroot": {"kind": "windows-sdk", "version": "10.0.26100.0"},
  "spec": ">=14.44.35211",
  "probed": ["msvc"]
}
```

The conan profile renders from it: `compiler`, `compiler.version`,
`compiler.libcxx`, `compiler.cppstd`, `os`, `arch`, and
`tools.build:compiler_executables` pointing at the receipt's drivers.
The measurer maps the family as it does today. `toolchain_env()`
enters MSVC from the receipt's instance instead of searching again.

### The host allowance for store tools

```toml
[tools]
host-allowed = ["cmake", "ninja", "conan", "node", "docker", "git"]
```

A kind contributes its own names (`host_allowed` on the kind record,
beside `tools` and `host_tools`). Under the allowance `fm sync`
resolves a tool from PATH as a `system-check` when a copy satisfies
the floor: the requirement's, else the record's own `min_version`,
else any version. It writes the receipt with `source: host`; without
a satisfying copy it installs
the locked one as today. A verdict tool in the list refuses at lock
time by name. The lock records the allowance per requirement
(`allow-host`), so a checkout and CI agree on which tools may vary.

What this does not cover: the stubs in `typings/` render for the
locked version; a host copy of another version may lack a flag, and
the call fails with the tool's own message. That is the accepted cost
of the allowance, and the receipt names the version that answered.

### The record for SBOM

A CI leg pushes its receipts (`toolchain.json` and every
`source: host` tool receipt) with its run record. The release wave's
publish step records, per member and per artifact, the receipts of
the leg that built it, as a row in a `release` series of the state
store keyed by the receipt tag; `fm store.show release
--key=<tag>` reads it. The tag itself stays as it is. A CycloneDX
rendering is a later phase; the data is complete without it.

### Unreal 5.7 and later on GitHub's free runners

The `unreal` kind reads the engine's `Windows_SDK.json`,
`Linux_SDK.json` and `Apple_SDK.json` and derives the declaration:
the preferred MSVC range and the banned ranges, the minimum and
preferred clang, the exact Linux cross toolchain name, the Xcode
window. The hosted images meet 5.7 today: windows-2025 has Visual
Studio 2022 17.14 with the 14.44.35211 toolset and LLVM 20.1.8,
macos-15 has Xcode 16.4 (5.7 wants 15.2 or newer; 5.8 wants 26.0,
which macos-26 has), and Linux uses `v26_clang-20.1.8-rockylinux8`,
downloaded by UnrealBuildTool. Not covered: the engine binaries are
not on the runners; a plugin or project build needs an engine the
project hosts.

## Phases

### Phase 1: the host allowance for store tools

Deliverables:

- `[tools] host-allowed` in the workspace contract and `host_allowed`
  on the kind record; the union is the allowance.
- The lock carries `allow-host` per requirement (additive, schema 1).
  `fm tools.lock` refuses a verdict tool in the allowance by name and
  by role.
- `fm sync` resolves an allowed tool from PATH when a copy satisfies
  the floor (the requirement's, else the record's `min_version`,
  else any), through the store's `system-check` path, and writes the
  receipt with `source: host`; otherwise it installs the locked one.
  A fresh receipt spawns nothing.
- `fm doctor` and `fm env.check` print, per tool, `host` or `store`
  and the version that answered.
- The e2e loop's members declare `cmake` and `ninja` host-allowed on
  one leg, so the loop proves both paths.

Tests, refusals first: the allowance on `ruff` refuses naming the
format and lint roles; a host `cmake` below the floor is passed over
with the version it printed and the floor, and the store's version
serves; an absent host `cmake` falls back to the store install and
the receipt says `store`; a satisfying host `cmake` leaves the store
untouched and the receipt says `host`; a warm sync with a fresh host
receipt runs no probe (the seam counts calls); the lock round-trips
`allow-host` and reads a lock without it.

**Acceptance**

- `fm tools.lock` on a workspace naming `ruff` in `host-allowed`
  exits non-zero and names the roles; proven by a forced fixture.
- `fm sync` on a machine with a satisfying `cmake` on PATH writes a
  receipt with `source: host` and installs no cmake; on one without,
  installs the locked cmake. Proven by the seam-faked tests and by
  the loop's leg.
- `fm tools.sync --frozen` on a warm tree stays within 2 s for this
  repository's receipts, measured by the CI store's metrics.

### Phase 2: the toolchain declaration and its resolution

Deliverables:

- `[cpp.toolchain]` parsed with `policy`, `prefer`, and per-platform and
  per-host family tables; refusals at parse for an unknown family, a
  spec shape that is neither `>=` nor `==`, and a constrained family
  outside the admitted list.
- One probe per family behind a seam: vswhere for `msvc`, xcrun for
  `apple-clang`, PATH and versioned names for `clang` and `gcc`.
- The resolution of "The design" per effective declaration, the
  receipts under `.workshop/receipts/toolchain/`, and the conan
  profile rendered per package from its receipt; `toolchain_env()`
  enters the receipt's instance.
- The lock holds one entry per toolchain record and exact version
  (`llvm` at 20.1.8 and at 23.1.2 side by side when two packages ask),
  an additive widening of "one version per tool" for the cpp layer's
  records alone.
- Records: `llvm` (LLVM's archives, the zst variants, every host LLVM
  ships: linux-x64, linux-arm, macos-arm, windows-x64, windows-arm)
  and `gcc` (xpack: linux-x64, linux-arm, windows-x64). Their layouts
  name the drivers and the coverage readers.
- `fm doctor` prints the resolution: the families probed, the
  candidates, the winner, the spec.
- The cpp-conan kind's `host_tools=("cc", "c++")` goes: the
  declaration replaces it, and `fm doctor` names the family that is
  missing instead of two names on PATH.

Tests, refusals first: an exact `msvc` no instance provides refuses
naming the Build Tools installer; an exact `apple-clang` refuses
naming Xcode; a floor `clang` unmet by the host installs the newest
`llvm` record that satisfies it (store faked); an exact `clang` with
no record refuses naming the record to add; `policy = "store"` probes
no `clang` and `gcc` (the seam counts) and still probes `msvc`; a
constrained family outside `prefer` refuses at parse; a platform
table and a host table for the same host resolve to the host table's
spec; a fresh receipt runs no probe; the profile rendered from a
receipt has the receipt's drivers and version.

**Acceptance**

- The gate's Windows leg builds the cpp fixture with the receipt's
  MSVC and measures with Microsoft's engine; the Linux leg with the
  receipt's gcc and gcov; the macOS leg with apple-clang and llvm-cov.
  Proven by the kind tests on each leg.
- A branch leg with `[cpp.toolchain.linux] clang = "==20.1.8"` builds
  with the store's LLVM 20.1.8 on ubuntu-latest, its receipt saying
  `source: store`; proven by a `[[ci.schedule]]` entry on the branch.
- `fm doctor` on this repository prints the three resolutions per
  host; quoted in the decision record.

### Phase 3: the receipts in the record

Deliverables:

- A CI leg's receipts ride its run record; `fm store.show` reads them.
- The release wave records per artifact the receipts of the leg that
  built it, in the `release` series keyed by the receipt tag.
- The conformance loop's release proves a row per artifact for its
  cpp and nanobind members.

**Acceptance**

- `fm store.show release --key=<tag>` after the loop's release prints
  a row naming the family, version and source per artifact; the loop
  pins the printed lines.

### Phase 4: the unreal kind's declaration

Deliverables:

- The kind reads the engine's three json files and derives the
  declaration; a missing engine path refuses naming it.
- `fm doctor` on an unreal project says which of Visual Studio, Xcode
  or the cross toolchain the host lacks and the version window.

**Acceptance**

- A test engine config tree of the json shape yields the declaration
  the table in this note states for 5.7; the derived spec for a
  banned MSVC range refuses with the engine's own comment quoted.

## Temporary, replaced by

| Temporary | Replaced by |
|---|---|
| `host_tools=("cc", "c++")` on the native kinds | the toolchain declaration (phase 2) |
| conan's `profile detect` in the cpp-conan backend | the receipt's profile (phase 2) |
| the release tag as the only receipt | the `release` series rows beside it (phase 3) |

## Decision record

- 2026-09-29, Willem: toolchains are configured as the host's, a
  floor, or an exact version; an exact version installs or refuses.
- 2026-09-30, Willem: the design covers detection when allowed, the
  family choice when several are installed, Unreal 5.7 and later on
  GitHub's free runners by default, the record for SBOM, and the host
  copy of any tool on PATH by default when allowed.
- 2026-09-30, Willem: `windows` in a declaration means `windows-x64`
  and `windows-arm`; a host key beside it is specified separately.
- 2026-09-30, Willem, on the five rulings asked: the policy splits
  (host-first for toolchains, store-first for verdict tools) and
  unwanted toolchains are not probed during `fm sync`; the default
  family order stands; an unmet floor installs and an unmet exact
  installs or refuses, `msvc` and `apple-clang` only refuse; the
  unreal kind reads the engine's json and the Linux cross toolchain
  stays UnrealBuildTool's download; this plan is its own note with
  the host allowance first.
- 2026-09-30, Willem: this is a thing only when a cpp layer is
  installed as well. Contract 10 places every toolchain part in
  `livery.workshop.cpp`; the host allowance stays the base's, and
  phases 2 to 4 wait for the layer's extraction.
- 2026-09-30, Willem: the table is `[cpp.toolchain]`, under the cpp
  layer's own `[cpp]`, as `[forge]` and `[docs]` are their layers'.
- 2026-09-30, Willem, on open item 1: a host-allowed tool with no
  floor in its requirement is held to its record's minimum. On open
  item 2: a package overrides, because a package may need a really
  specific toolchain, and several toolchains may be active in one
  workspace. Contract 11 and the design carry both; the lock widens
  to one entry per toolchain record and exact version.
- 2026-09-30, phase 1 built (issue #937): `[tools] host-allowed` and
  the kinds' `host_allowed` union into the allowance; the lock entry
  carries `allow-host`, the sites' current word and never a kept one,
  so toggling the list moves the lock; a tool a check reads its
  verdict from refuses naming the check and its role, `uv` and
  `git_cliff` by name, and a download whose executable is not named
  like the tool, since the allowance finds a tool on PATH by its name.
  `fm sync` looks on PATH first for an allowed tool: a copy that
  satisfies the floor (the requirement's, else the record's minimum,
  else any) serves with a receipt saying `host` and the version that
  answered, and its file's identity, so the next sync probes nothing
  while the file is unchanged; a copy that is absent or below the
  floor is passed over with a note and the locked version serves,
  never a refusal, since the allowance is permission and not
  obligation. `fm env.check` prints `host <version>` or `store
  <version>` per tool; `fm doctor` names the host-served ones. The
  loop's workspace allows `cmake` and `ninja`; its runner has neither
  on PATH today, so the pass proves the store path, and the host path
  is proven by the seam-faked tests until a host runner exists (the
  local loop plan's phase 2).
- 2026-09-30, on the cost of probing (Willem's question): a warm
  `fm tools.sync --frozen` is 1.2 s for 18 receipts and spawns no
  tool; a probe runs only for an absent or stale receipt, and a CI
  job probes once at entry. Contract 2 pins it.

## Open

1. Resolved 2026-09-30: the record's own `min_version` is the floor
   for a host-allowed tool whose requirement has none. Decision
   record.
2. Resolved 2026-09-30: a package's `[cpp.toolchain]` table overrides
   the workspace's key by key, and several toolchains may be active
   at once. Contract 11.
3. `zig` as a fifth family: one archive per host, MinGW ABI on
   Windows, no coverage reader inside. Owner: Willem, after phase 2.
4. #930: the clang-format and clang-tidy records' source. The `llvm`
   record of phase 2 carries both tools, at 1 GB per host; the PyPI
   wheels carry them alone at a few MB. Owner: Willem, with #930.
5. The CycloneDX rendering of the release rows. Owner: Willem, after
   phase 3.
