# The kind hierarchy: native kinds through one registry

Status: executing; Willem's go 2026-09-04. Phase 1 shipped
2026-09-04 (issue #202): the registry abstraction with the
resolution ladder and folder targets, both existing users ported,
and the kind registry opened to layers with the chain, the managed
union, and every guard forced. Phase 2 shipped 2026-09-04 (issue
#209, PR #210): the cpp-conan kind end to end, conformance-proven;
see the decision record. Phase 3 built 2026-09-04 (issue #211):
the python-nanobind kind, and with it phase 1's deferred
acceptance closes: the chain render proof runs against a real
child template (test_the_chain_renders_parent_files_under_the_leaf
and test_the_drift_loop_renders_the_chain). Phase 3 merged as PR
#213. Phase 4 shipped 2026-09-04 (issue #215, PR #216): the
cross-kind dependency; see the decision record. Phase 5 shipped
2026-09-05 (issue #217, PR #219): publish and the identity guard;
Open 5 (stamping dispatch) closed with it. Phase 6 built
2026-09-05 (issue #220): the chain add-back; every phase of this
plan is now built. Open 6 closed 2026-09-05 with livery#218. Open 7
(compile-time conan consumption) was sequenced 2026-09-26 as phases 7
and 8 below, ruled the same day; phase 7's first step, the conan store
record, is livery#728.
This is phase 17 of `notes/20260903-workshop-plan.md`, promoted to
its own plan. Willem's scope ruling (2026-09-04): a CMake C/C++
library kind on conan 2 and a python binary-extension kind that
depends on it; Maya waits. The research base is a same-day survey
of hse, whose finding is the frame: hse has the seams for
non-python kinds, lint-enforced and test-pinned, with a documented
add-a-kind procedure, and zero native implementation. Livery ports
the seams and builds the first real kinds through them.

## What this delivers

Two new package kinds, shipped by the workshop and registered
through one opened registry. `package-cpp-conan`: a root kind, a
CMake C/C++ library packaged with conan 2, whose gate runs its own
verbs and skips the python ones honestly. `package-python-nanobind`:
a child kind extending `package-python`, a binary extension whose
wheel is platform-tagged and whose native half links the C/C++
library through conan. The cross-kind dependency is the point: one
graph, one release train, one affected computation across kinds.
The registry is open to layers, so a brand ships its own kinds the
way it ships fragments.

## Ground-truth contracts (do not violate)

1. **Workshop is the product.** Kind names, templates, and emitted
   CI speak the workspace's identity; livery appears only in
   distribution and import names.
2. **hse is the reference for the seams.** The backend contract
   (three callables: build, isolated test, publish), the per-kind
   CI contract with lenient reads and strict lint, the
   edge-extractor table pinned to the kind vocabulary, and the
   fail-open affected guard port from hse's shapes; deviations are
   named in the decision record.
3. **The kind chain renders parent then child** (the 0901 ruling):
   a child kind renders its parent's template, then its own over
   it, same answers; the answers file records the leaf kind;
   `PACKAGE_MANAGED` is the union along the chain. The backend seam
   mirrors it: a child backend overrides what differs and inherits
   the rest.
4. **The gate is honest per kind.** A verb that does not apply to a
   kind skips saying so; it never passes vacuously. A C/C++ package
   has no typecheck leg and says why; the extension keeps every
   python verb and adds its native build.
5. **Types contribute tool profiles** (the 0901 ruling): the
   workspace's required tools are the union over present kinds,
   derived by discovery. A pure-python workspace never sees cmake;
   the first native package pulls cmake, ninja, and conan into the
   profile by existing.
6. **The merge path waits on nothing outside the repository.** The
   first validator library carries no third-party C/C++
   dependencies, so the gate never reaches conan-center; when
   external native dependencies arrive, they arrive locked and
   cached, or they wait.
7. **Native artifacts carry their identity.** The extension's wheel
   is platform-tagged; the publish wave refuses a pure-tagged wheel
   from a native kind and a native-tagged wheel from a pure kind
   (the identity guard the 0903 entry promised).
8. **Everything through fm**; native tools run through toolroom's
   typed handles where they exist (cmake and ninja ship as stubs
   today), and a deliberate `footman.run` with stated env where
   they do not yet.
9. **Fallbacks before happy paths.** The unknown-kind refusal, the
   missing-extractor guard, and the honest skips are tested before
   any kind builds anything.

## The seams, ported from hse

- **The kind vocabulary** lives where livery already declares it:
  `type = "python"` in each package's `workshop.toml`. The
  vocabulary grows to `python`, `python-nanobind`, `cpp-conan`; an
  unknown value refuses naming the known set (hse fails soft to
  python; livery refuses, a named deviation: a typo that silently
  builds the wrong kind is worse than a stop).
- **The backend registry**: one module per kind exposing build,
  isolated-test, and publish callables; dispatch by declared type;
  the add-a-kind procedure documented in the registry module the
  way hse documents it. Opened to layers: a layer's plugin
  registers kinds at mount, the way the provisioning registry in
  hse's sdk is designed for devkit overlay.
- **The per-kind CI contract**: which check verbs, which legs,
  which publish target apply, defaulted per kind, overridable per
  package, read leniently and linted strictly.
- **The edge extractors**: one table pinned so
  `set(extractors) == set(kinds)` by test; `graph.affected` fails
  open to everything, naming the missing kind, exactly hse's guard,
  forced with an injected fake kind.

## The registry abstraction

Ruled 2026-09-04 (Willem), reshaping phase 5 and adding phase 1's
first half: artifact registries get one abstraction before conan
becomes a third bespoke story beside the python env vars and the
docs seam's inline docker host.

- **The handles are protocol-generic, never forge-owned.**
  `livery.forge.SimpleRegistry` is the precedent: it speaks the
  simple protocol to any index. The container handle speaks the OCI
  distribution protocol, so any OCI registry works (Willem,
  2026-09-04: OCI is the definition, not an example); the conan
  handle wraps remote configuration and upload against any conan
  remote.
- **A folder or share is a first-class target for every kind**
  (Willem, 2026-09-04). The declaration rung accepts a path as well
  as a URL: python publishes a dists directory with a simple-layout
  index any `file://` or find-links consumer reads; container
  writes the OCI image layout, which is a directory by
  specification; conan uses `conan cache save`/`restore` with the
  local-recipes-index form for consumption. A workspace can publish
  everything to a share and need no registry server at all.
- **The forge contributes one thing**: the URL of its own hosted
  registry per kind, where it has one. Gitea hosts python, conan,
  and container registries; GitHub hosts container (ghcr) and
  declines conan; GitLab hosts its own. The forge is the default
  provider, never the owner.
- **The resolution ladder**, per artifact kind: the contract's
  `[registries]` declaration wins (any URL); else the forge's own
  registry of that kind; else the ecosystem default where one
  exists (pypi.org for python); else an honest decline, named.
- **Auth stays where it lives**: tool-native credential stores
  (docker login, conan's) with refusals teaching what to set, and
  host-qualified tokens for what the workshop drives itself.
  Addresses in the contract or the committed env; tokens as
  environment facts.
- **The existing users port onto it**: the wave's receipt probe and
  floor probe, publish_wheels' env-var pair, and the docs container
  seam's inline host derivation all become ladder lookups, so
  every artifact kind resolves its registry the same way.

## Phases

### Phase 1: the registries and the kind registry, opened to layers

First the registry abstraction: the `RegistryKind` vocabulary
(python, conan, container), the protocol-generic handles, the
forge surface answering its hosted registry URL per kind across
all three backends and the fake, the resolution ladder reading the
`[registries]` table, and the two existing users ported (the
wave's probes and the docs container seam). Then the kind
registry: the kind vocabulary in the package contract, the backend
registry with the three-callable contract and the documented
add-a-kind procedure, per-kind CI contract defaults, the
edge-extractor table, the template kind chain (child renders
parent then itself, managed set unioned), and layer registration:
a mounted layer's plugin can add kinds. Every guard forced first:
unknown kind refuses, missing extractor fails affected open by
name, the fake future kind registers through a test layer and
dispatches, and an undeclared kind on a forge without it resolves
to the named decline.

Acceptance:
- The ladder's four rungs are each forced by test: a declared URL
  wins over the forge's; the forge's serves when undeclared;
  python falls through to pypi.org; conan on a github-kind
  workspace declines naming the kind.
- The docs container seam and the wave's probes read their targets
  through the ladder; proven by the existing suites staying green
  with the env-var pair now feeding the ladder, not the callers.
- A declared folder target round-trips for each kind: python dists
  land in a simple-layout directory a scratch venv installs from;
  the container image writes an OCI layout a local runtime loads;
  the conan package saves into the folder and restores clean; each
  proven by command in the kind's own phase, the ladder's path
  acceptance forced here with the python case.
- `uv run pytest packages/workshop/tests/test_workshop_kinds.py`
  proves: refusal names the vocabulary; the fake kind dispatches
  build/test/publish through the registry; the extractor pin equals
  the kind set; the chain renders parent-then-child on a fixture.
- `uv run fm check` green with every existing package untouched.

### Phase 2: the C/C++ library kind

`package-cpp-conan`: the template (CMakeLists, conanfile.py, a real
source pair, a ctest unit test), the backend (configure, build,
ctest through the toolroom cmake and ninja handles; conan packages
the result), the honest gate (python verbs skip saying why; the
kind's own verbs run), and the tool profile contribution (cmake,
ninja, conan appear in the derived profile only when the kind is
present; `fm doctor` names a missing compiler instead of failing
mid-build).

Acceptance:
- A fixture package of the kind builds and its ctest passes:
  `uv run fm check` in a conformance workspace carrying one.
- The same workspace's gate output shows the python verbs skipping
  by name, never passing silently; pinned by test.
- A pure-python workspace's derived tool profile is unchanged;
  pinned by test.

### Phase 3: the binary-extension kind

`package-python-nanobind`: a child of `package-python` through the
kind chain (nanobind and scikit-build-core over the parent's
files, ruled 2026-09-04), the wheel built through cibuildwheel from
day one so even a single leg's wheels are manylinux-compliant, the
python verbs kept whole
(typecheck, typecomplete, the isolated legs now installing a
compiled wheel, the 0901 promise), and the identity guard's first
half: the built wheel's tag must be platform-specific for this
kind.

Acceptance:
- The chain renders: the fixture extension package carries the
  parent's managed files and the child's build files; answers
  record the leaf kind; proven by the render tests.
- The built wheel's filename carries a platform tag, never
  `py3-none-any`; proven by building the fixture and listing dist.
- The isolated leg installs and imports the compiled module;
  proven in the armed suite.

### Phase 4: the cross-kind dependency

The extension requires the library: `[[depends]]` in the contract
and the conan requirement stating the same floor, with a drift
check refusing when the two disagree. `graph.affected` crosses
kinds (touching the library marks the extension), the layering lint
validates cross-kind edges, and the release train orders the
library before the extension.

Acceptance:
- Editing the library's source marks the extension affected;
  proven by `fm graph.affected` in the fixture workspace.
- A contract floor and conan requirement that disagree refuse,
  naming both; forced by test.
- `derive_plans` over both orders the library first; pinned in the
  driver tests.

### Phase 5: publish and the identity guard

The conan publish seam through the ladder: the library publishes
to whatever the ladder resolves, the rig proof using gitea's own
conan registry (upload, then a clean `conan install` back), and a
declared folder target round-tripping. The native wheel matrix
(ruled 2026-09-04): per-OS cibuildwheel jobs in the emitted release
workflow, artifact collection feeding the wave, so every platform's
wheels publish together from the first release; cibuildwheel's
linux arm needs a docker-capable runner, the container seam's known
constraint. The wave gains the identity guard both ways: a native
kind's pure-tagged wheel refuses, a pure kind's platform-tagged
wheel refuses, each naming the kind and the tag. The armed release
rehearsal runs the extended graph.

Acceptance:
- On the rig, the fixture library uploads to gitea's conan
  registry and installs back into a scratch profile; proven by
  command in the armed suite.
- The identity guard's refusals are forced both ways in the
  publish tests.
- `WORKSHOP_CONFORMANCE_DRIVE=1` rehearsal green over a graph
  containing both kinds.

### Phase 6: the chain add-back

The dummy descendant gains both kinds through the gradient: the
brand child creates a C/C++ library and an extension depending on
it, its gate runs green with the honest skips, and the tool profile
grows only in that workspace. No template re-render.

Acceptance:
- The armed chain (`WORKSHOP_CONFORMANCE_DRIVE=1`) carries the new
  stage and passes twice (fresh and resumed).

### Phase 7: compile-time consumption of the library

Status: slice 7a landed 2026-09-26 (livery#735, PR #742): conan and the
cmake-conan provider are store records, the extension template
consumes fmt at compile time through the provider, `fm sync` registers
every cpp-conan member editable before `uv sync`, and the fixture's
extension wheel calls `fmt::format` and the library's `version()`
from HEAD in its isolated leg. Slice 7b's first cut landed 2026-09-26
(livery#736, PR #750): the forge protocol uploads, lists, and reads
back release assets, on GitHub, Gitea, GitLab and the fake. Its
second cut (livery#751) carries the route itself, below. Slices 7c
(livery#737) and 7d (livery#738) follow.

The route, on a forge that hosts no conan registry:

- Each wheel-platform leg creates every conan member into its own
  cache and saves it as `dist/<name>-<version>-<host>.tgz`, one file
  per host, collected with the wheels.
- The ladder's conan rung, where the forge hosts no conan registry,
  answers the forge's releases. The wave pushes the receipt tag
  first, because a release is addressed by its tag, then creates the
  release (body: the member's changelog entry) and attaches every
  collected cache. A tag alone is no longer a receipt: the probe
  reads the assets, so a re-run after a half-finished upload
  finishes it.
- A consumer reads the release listing, checks the bytes against the
  digest GitHub reports, and restores the cache into its conan home.
  A forge that reports no digest says so on the line rather than
  passing silently.
- Every declared floor on a conan member is proved in the same leg:
  the floor's cache is restored from its own release, one wheel is
  built for this machine with `--require-override=<library>/<floor>`,
  and the package's tests run against it. A floor equal to the
  version the wave releases is the package the main build already
  linked, and that leg says so.

Slice 7d landed 2026-09-26 (livery#738): the descendant chain's
native stage links the sibling library and a Conan Center package
into the child's extension, and the child's own gate calls both from
the one compiled module. The chain runs again: it had been unable to
start since 2026-09-10, when the suite's isolation plugin took the
config-dir variable its bridge relies on, and twelve stale points
stood between that and a green pass. They are in the decision
record. Two gaps it surfaced are filed rather than patched over: a
newborn seeds no tool index or lock (livery#765), and a native
member's compiled module goes stale when its sources change
(livery#766).

The extension calls a symbol from the first-party library and a
symbol from a third-party conan package, and the build resolves both
the same way in the local gate, the isolated wheel legs, and the
cibuildwheel legs on the three wheel platforms, the manylinux
container included. Conan itself arrives through the tool store
first (livery#728: an `archive` record from conan's own releases, the
cpp backend's refusal naming the store).

The shape, mirroring the python kind:

- In the workspace the extension compiles against the library at
  HEAD through conan's editable mode, conan's own workspace source: the
  sibling's source tree, no package in the cache. The contract floor
  stays the drift guard.
- In the isolated legs the extension builds and tests against the
  library resolved at the floor the contract declares, pinned exactly
  (a conan range resolves to the highest available, so the leg pins
  `name/floor` to starve the way `--resolution=lowest-direct` does).
  The library is published before the extension's wheel builds, the
  order the train already imposes.
- The extension carries a consumer `conanfile.py` with
  `requires = "<library>/[>=floor]"` and the third-party ranges; the
  backend's requirement reader and the layering lint apply unchanged,
  and a requirement naming no member is third-party.
- The toolchain reaches scikit-build-core through a CMake dependency
  provider (cmake-conan's `conan_provider.cmake`, a store download):
  the extension's CMakeLists says `find_package(<name> REQUIRED)` and
  CMake runs `conan install` with the host's detected profile at
  configure time, `CMAKE_PROJECT_TOP_LEVEL_INCLUDES` set by the backend
  for the gate and through `CIBW_ENVIRONMENT` for the legs. The
  consumer side lives in a shared helper, not the extension's backend,
  so a later kind (a cargo kind through `PkgConfigDeps`) reuses it.
- Profiles are `conan profile detect` per host with the leg's settings
  written over it, `build_type=Release` everywhere CI builds; no
  profile files in the template.
- Where the packages come from, per the registry ladder: a declared
  conan remote wins where a workspace has one. On GitHub, which has no
  conan registry, the first-party package for each wheel platform is
  attached to the member's own release by the train's release leg and
  read back through the store's records, the same path as a tool.
  Third-party packages come from Conan Center, built once per profile
  with `--build=missing`, with the conan home cached by the CI lane
  keyed on the lockfile and profile. Conan Center is on the merge path
  the way PyPI already is.
- The lane keeps conan's home on the runner's working drive, beside
  uv's cache and the tool store, and a leg that builds native
  packages restores and saves it: the key is the recipes with the OS
  and the architecture, the restore key their prefix. A third-party
  package compiles once per key and downloads afterwards. Profiles
  are conan's own detection, which writes `build_type=Release`, and
  the dependency provider derives the host profile from CMake inside
  whatever environment builds, so the manylinux container's
  toolchain is the one its packages are built for.

Acceptance:
- The conformance chain's stage that wires both kinds proves the
  extension imports and calls one first-party and one third-party
  function, locally and on the three wheel platforms; the
  pure-python profile is pinned unchanged.
- `fm check` in the fixture workspace compiles the extension against
  the library at HEAD: an edit to the library's header is seen by the
  extension's next configure without a release.
- The isolated leg fails when the floor names a version whose header
  lacks the symbol; forced by
  `test_a_floor_whose_header_lacks_the_symbol_fails_the_leg` at the
  nightly point.
- The manylinux leg builds with the store's conan mounted, no wheel
  install of conan inside the container.

### Phase 8: local development for the native kinds

Status: the presets landed 2026-09-26 (livery#769). Both native
templates render a `CMakePresets.json` with Debug and Release
presets over Ninja, compile commands exported, the conan provider
read from the environment and, for the extension, the venv its
interpreter comes from; a workspace with an extension member carries
nanobind in its dev group, which is what that interpreter answers
with. A fresh fixture of each kind configures, builds and tests from
its preset with no further argument, proven by test.

Open: the format and lint half (livery#770). Where clang-format and
clang-tidy come from is undecided: the ruling names the LLVM static
release the store will carry as a compiler, the store carries no
LLVM yet, and one release archive is 0.9 to 1.8 GB per host for two
binaries of a few megabytes. The issue states both ways to sequence
it.

What a person gets at birth beyond the gate: a `CMakePresets.json`
(configure, build and test presets, Debug and Release, compile
commands exported) that conan's generated `CMakeUserPresets.json`
includes, so VS Code, CLion, Visual Studio and Xcode open the
package with no verb; the Debug preset in the same tree for
debugging the ctest binary, and scikit-build-core's editable install
for debugging the extension in the workspace's Python; `.clang-format`
and `.clang-tidy` in both templates with format and lint as gate
checks for the kinds, the two tools taken from the LLVM static
release the store will carry as a compiler, never as a tool of their
own.

Acceptance:
- A fresh fixture of each kind configures from its preset with no
  further arguments; proven by test.
- A misformatted source and a tidy finding each turn the gate red,
  naming the file; forced by test.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| No toolroom conan handle; conan runs through a deliberate `footman.run` with stated env | the store's conan record (livery#728) and the handle its stub gives |
| Compilers required on the host, named by `fm doctor` | phase 18's tool cache over the type-derived profile |
| Validator packages living in conformance fixtures and the chain | real native members when the first production consumer arrives |
| The armed cibuildwheel leg skips on win32 until MSVC arming on the runner is verified | a verified win32 leg |

## Decision record

- 2026-09-04 (Willem): the three remaining open items ruled:
  cibuildwheel with the full per-OS matrix in phase 5, nanobind
  with scikit-build-core, compilers host-required until phase 18.
  And the standing frame, stated the same day: livery is the
  development environment for every future project; capability is
  never deferred on absent consumers, only on sequencing or named
  risk.
- 2026-09-04 (Willem): the scope is a CMake C/C++ library kind on
  conan 2 and a python binary-extension kind depending on it; Maya
  waits. The cross-kind dependency is the hierarchy's proof.
- 2026-09-04 (Willem, confirming the survey): hse has no native
  implementation to port, only the seams; the seams are the port.
- 2026-09-04 (Willem): registries get one abstraction before conan
  arrives: protocol-generic handles after SimpleRegistry's
  precedent (the container handle's protocol is OCI by definition);
  the forge is the default provider through a per-kind URL surface,
  never the owner; resolution is a four-rung ladder ending in an
  honest decline; and a folder or share is a first-class publish
  target for every kind, so a workspace can publish with no
  registry server at all.
- 2026-09-04: unknown kinds refuse rather than fail soft to
  python, a named deviation from hse: a typo that silently builds
  the wrong kind is worse than a stop.
- 2026-09-04 (Willem): the rendered root pyproject lists the uv
  workspace members explicitly instead of a glob with excludes: the
  roster lives in the answers and the file is rendered from it, so
  the glob only re-derived the same list while letting a stray
  directory join unnoticed.
- 2026-09-04 (phase 2 shape): the cpp-conan CI contract keeps
  format and lint (the conanfile is python and ruff gates it) and
  skips typecheck, typecomplete, and test by name; its own verbs
  are configure, build, and ctest, run per package by the gate's
  kindcheck step through the toolroom cmake handle (ctest rides the
  Ninja generator's test target). The gate needs no conan: the
  dependency-free library configures against the host toolchain,
  and conan enters at packaging (`conan create`, a deliberate
  footman.run) and later at publish.
- 2026-09-04 (phase 2 shape): compilers are host_tools on the kind
  record, probed by `fm doctor` and `fm env.check`, never
  provisioned; a package of a non-python kind has no
  pyproject.toml, joins the roster with a `kind` entry instead of a
  dev requirement, and discovery, the layering lint, and the python
  verbs all read the kind's contract before touching it.
- 2026-09-04 (found by the phase 2 conformance run): copier omits
  an answer that equals its default from the receipt, and
  package_dir defaults to the render destination's basename, so the
  drift and apply loops re-rendered a member under a temp directory
  name whenever the receipt lacked package_dir. Both loops now pass
  package_dir from the directory they judge; pinned by test.
- 2026-09-04 (phase 3 shape): python-nanobind is a full python
  kind through the chain (every checker verb whole, pyproject
  required, the uv workspace and dev group keep it); the leaf
  template renders scikit-build-core and nanobind over the
  parent's files, with a typed stub for the compiled module so
  mypy strict and typecomplete hold. The backend inherits check
  from the python backend and overrides build: cibuildwheel
  through `uv tool run` (floor and cap, no lockfile reaches a tool
  run), CIBW_BUILD pinned to the running interpreter locally, the
  sdist still from `uv build`, and the identity guard's first half
  refusing a none-any wheel by name. cmake and ninja join the
  profile through the kind; nanobind and scikit-build-core arrive
  through the package's own build-system requires.
- 2026-09-04 (found in phase 3): the drift and apply loops
  rendered only the leaf template, so a chained member's
  parent-managed cliff.toml would have been skipped as missing;
  both loops now render the full template chain, parent first,
  exactly as the package was born. Pinned by test.
- 2026-09-04 (found by phase 3's CI): two isolated-leg faults the
  first platform wheel exposed. cibuildwheel's linux run also
  builds a musllinux wheel the host cannot install, so the local
  build verb skips musllinux and the leg sorts its wheel
  candidates; and bare `uv venv` takes uv's default python, so a
  3.11 leg validated inside a 3.14 venv, which a pure wheel never
  noticed and a cp311 wheel refused. The leg's venv now pins the
  running interpreter's version. Both fixes pinned by the armed
  suite across the CI matrix.
- 2026-09-04 (phase 4 shape): the edge extractor seam is a
  callable per backend, `declared_requirements(package)`, a named
  deviation from hse's separate table: the registry is livery's one
  add-a-kind point, so the extractor rides the backend and a pin
  test holds `set(kinds)` to the callables. python reads
  [project.dependencies], cpp-conan parses the conanfile's
  `requires` (attribute and `self.requires(...)` forms, as source,
  never importing conan), and the extension unions both. The
  layering lint judges every build edge in the dependency's own
  ecosystem and its refusal teaches the conan range form
  (`"name/[>=floor]"`); `graph.affected` fails open to everything
  naming an unregistered kind; `bump_set_floors` moves a conan
  range with the pyproject floor and the contract floor, and the
  rollback restores only files a member actually has (one absent
  pathspec refuses a whole `git checkout`).
- 2026-09-05 (phase 5 shape): the wave dispatches per kind. The
  KindRecord carries ``artifact`` (python or conan) and
  ``wheel_identity`` (pure, platform, or none); the identity guard
  refuses both mismatches naming kind and tag before anything
  uploads; floor probes ask each dependency's own registry; the
  conan target resolves once per wave through the ladder, and the
  cpp backend uploads through a re-pointed ``workshop`` remote or
  ``conan cache save`` into a folder target, with
  ``ConanRegistry`` answering the probe from ``conan list`` or the
  saved archive names. The emitted release workflow gains a per-OS
  cibuildwheel matrix (artifact collection feeding the wave's
  ``--prebuilt``) only where a platform-wheel kind lives, decided
  from the roster's kind entries; `fm release.wheels` is the
  matrix job's verb.
- 2026-09-05 (found in phase 5): three seams the first cross-kind
  rehearsal exposed. The isolated leg's dev pins carried
  path-sourced entries a scratch venv cannot parse, so local
  references are dropped from the export; find-links dirs now come
  only from members whose kind builds wheels (a conan member's
  missing dist/ failed the sibling legs); and the conformance
  drive isolates XDG config and the uv cache, because the
  operator's shared env leaked tokens into the fictional forge's
  lookups and a stale cached path wheel resurfaced mid-drive.
- 2026-09-05 (phase 6 shape): the descendant chain gains stage
  5c: the brand child creates the C/C++ library and the extension
  through the brand's own verbs, declares the cross-kind edge with
  the agreeing conan range, gates green with the honest skips and
  the real cmake and ctest, and the derived tool profile carries
  cmake and conan only in that workspace; the resumed pass hits
  the wiring's idempotent refusal. No template re-render.
- 2026-09-05 (found by the chain): the seed headers named the
  template channel on line 1, and a channel URL long enough
  (git+http://... in the chain's child) pushed every linted seed
  past the line width. The label left the seed headers; the
  receipt's ``_src_path`` records the channel exactly. Existing
  seeds keep their old headers: a seed is never re-rendered.
- Carried forward from the 0901/0902 records: the kind chain
  renders parent then child with the managed union; the backend
  seam mirrors it; types contribute tool profiles by discovery;
  linkable C/C++ goes company-wide through conan 2.
- 2026-09-26 (Willem): Open 7 sequenced, after the workflow
  streamlining issues: the kind hierarchy closes before the
  extensible gate plan starts. Rulings: conan comes from its own
  release archives through the store, never a wheel; in the workspace
  the extension builds against the library at HEAD and in the isolated
  legs against the released floor, the python kind's own split; CI
  builds Release only and keeps no debug symbols; a Rust kind later
  consumes the same conan packages through `PkgConfigDeps`, so the
  consumer helper is shared; the integration test is the first time a
  first-party and a third-party symbol link into a python extension.
- 2026-09-26 (Willem): the merge path may depend on a service reached
  with a configured token (an index, a registry, a remote); what it
  may not depend on is a person. The workshop fragment says so from
  this date. Conan Center and PyPI are both such services. No Gitea or
  other self-hosted registry for this repository: a declared remote
  serves a workspace that has one, and the member's own release
  assets, read through the store, serve first-party packages here.
  Third-party binaries are cached by the CI lane, never mirrored into
  release assets.
- 2026-09-26 (Willem): clang-format and clang-tidy come with the LLVM
  static release the store will carry as a compiler; no separate tool
  record for either.
- 2026-09-26 (slice 7c shape): the conan home is keyed by
  `packages/*/conanfile.py` rather than a conan lockfile, because
  this workspace commits none: the recipes carry the requirements,
  and a range that resolves to a newer version inside an unchanged
  recipe reuses the entry and builds that one package. The cache
  step is GitHub's alone, as the tool store's is; the other lanes
  have no cache action. No profile is written over conan's
  detection: detection already answers `build_type=Release`, and
  cmake-conan derives the host profile from CMake, so a container
  build is settled by the container's own toolchain.
- 2026-09-26 (slice 7a shape): CMake reads
  `CMAKE_PROJECT_TOP_LEVEL_INCLUDES` from no environment variable, so
  the provider's record sets `CMAKE_CONAN_PROVIDER` to the file inside
  its installed tree and each consumer maps it: the extension's
  `pyproject.toml` defines the CMake variable from the environment
  through scikit-build-core, the cpp backend and the presets pass it as
  `-D`. cmake-conan's release lists no asset, so the bench's `Provision`
  gained `asset`, a URL template recorded for every host from the same
  file; its record is hand-written (a CMake module has no help to
  read) and the host point checks a driver with no help flag for
  presence alone. Recording it collapsed the store's downloaded kinds (Willem):
  `archive`, `binary` and the `file` kind drafted for the provider
  are one `download`. The artifact's form is sniffed from its URL's
  suffix, `format` in the layout overrides a name that lies, a bare
  download lands under its `file` name, and the layout alone says
  what reaches the outside: entry points and `paths` for a program,
  `env` alone for a file that is never run and gets no stub.
  `fm sync` materialises the tools and registers the editables before
  `uv sync`, since uv's build of a native member runs cmake, conan and
  the provider. The conan install arguments `--build=missing` and
  `--build=editable` live in the template's pyproject, so a sibling is
  built from HEAD wherever it is registered and nothing happens where
  it is not.
- 2026-09-26 (slice 7b shape): on a forge that hosts no conan
  registry the artifact rides the member's own release, so the
  receipt tag is pushed before the assets exist. A tag alone is
  therefore no longer a receipt: the wave's walk-past asks the
  target as well, and a re-run after a half-finished upload
  finishes it. The wave hands each backend the resolved
  `RegistryTarget` whole instead of three loose fields, since the
  conan route needs the workspace root to reach the forge.
- 2026-09-26 (slice 7b shape): a consumer reads the bytes back
  through the forge protocol (`download_asset`) rather than the
  store's fetch. A private repository's asset needs the lane's
  credential and each forge addresses the bytes its own way, so the
  backend that knows the dialect does the read; the fake forge then
  makes the whole path provable with no server. The digest the
  forge reports is checked before the restore, and a forge that
  reports none says so on the line instead of passing silently.
- 2026-09-26 (slice 7b shape): the floor leg pins the library with a
  conan profile carrying `[replace_requires]`, added to the profiles
  the dependency provider already passes. conan 2's `install` has no
  `--require-override`, and a profile composes with the generated
  one rather than replacing it.

- 2026-09-26 (slice 7d shape): the chain's bridge names the
  config-dir variable, not `XDG_CONFIG_HOME`: the suite's isolation
  plugin points that variable at a scratch home and it beats XDG, so
  the bridged tasks file was invisible and every child `fm` mounted
  builtins alone. Its wheelhouse builds every member and pins each to
  its own build, because one member missing, or a floor naming a
  version nobody has published, sends the resolution back to the
  index for an older release that fits and then fails on an import
  the current source made. The workspaces it builds share this
  machine's tool store, which is a cache every checkout here shares.
- 2026-09-26 (slice 7d shape): `POINT_VARIABLE` lives in the state
  module, not beside the pytest plugin that selects on it. A brand's
  tool venv carries the layer and its runtime dependencies, never the
  test toolchain, and one import of a plugin module on a verb's path
  turned every command on that CLI into a ModuleNotFoundError. A test
  pins it: the task tree imports with pytest refused.
- 2026-09-26 (slice 7d shape): the floor on `livery-toolroom-store`
  reads 0.0.0, the version the member carries, in both contracts and
  manifests that name it. It read 0.1.0, a version no release has
  ever served, which made livery-workshop uninstallable anywhere but
  this workspace. The train raises the floor at the store's first
  release.
- 2026-09-26 (slice 7d shape): the armed native tests run at the
  merge point as well as the nightly one. Their marks sat on the
  helper above them, where a mark does nothing, so they ran at every
  point; the nightly point alone would have narrowed the native
  build's proof to one runner, and the merge point's legs are the
  three platforms.

- 2026-09-26 (slice 7d shape): the chain keeps the suite's own
  markers out of the workspaces it builds. It plays a person at a
  workstation, so a child that inherits `CI` behaves as a runner
  (the gate refuses `--fix` there, which the update verb asks for),
  and a child that inherits the coverage configuration writes rows
  for files that live only in its temporary tree. It reads the
  update's branch from origin, where the verb now leaves it under a
  pull request.
- 2026-09-26: `workflow.update.templates` carries the gate's own
  output in its refusal. It said only that the gate was red, and
  the caller is often a script that sees nothing else; finding the
  reason cost a run of the chain each time.

## Open

1. Resolved 2026-09-04 (Willem): dissolved into the registry
   abstraction. Any OCI registry and any conan remote is declarable
   in the contract, a local folder or share included; the forge's
   own is only the default; GitHub plus conan resolves to the named
   decline by derivation, not by ruling.
2. Resolved 2026-09-04 (Willem): cibuildwheel is the native wheel
   builder from day one, and the full per-OS matrix with artifact
   collection lands in phase 5: complete wheels from the first
   release. This is the foundation for every future project;
   capability is not deferred on absent consumers.
3. Resolved 2026-09-04 (Willem): nanobind with scikit-build-core.
4. Resolved 2026-09-04 (Willem): compilers are host-required with
   `fm doctor` naming the missing toolchain per platform; cmake,
   ninja, and conan join the derived profile; compiler provisioning
   waits for phase 18's machinery, a sequencing deferral, not an
   adoption one.

All four ruled. Willem's go, 2026-09-04: phase 1 is in build.

5. Resolved 2026-09-05 (phase 5): stamping, the current-version
   read, and the release build all dispatch through the backend
   protocol; the conan recipe's ``version`` attribute is the
   cpp-conan home, and verify_release judges each kind's own
   homes.
6. Resolved 2026-09-05 (Willem): v0.0.0 is the newborn's first
   release, hse's own practice (its sdk and devkit both released
   at v0.0.0); the seeds now say 0.0.0 in every version home and
   the born changelog entry, and the rehearsal pins the derive.
7. Resolved 2026-09-26 (Willem): sequenced as phases 7 and 8, the
   conan store record first (livery#728). The rulings are in the
   decision record under that date.
8. Open (2026-09-26, slice 7c): the lane evidence. This workspace
   has no member that declares wheel platforms, so no release run
   here emits a wheels leg, and the conan cache's restore cannot be
   read from a log of ours. What is proved: the step's path and key
   by test, the home's placement by test, and the cache action
   itself by the tool store, which every GitHub job here restores.
   What is not: a second wheels leg reusing a third-party build.
   The evidence lands with the first workspace whose release runs a
   wheels leg on a GitHub lane; whoever reads that log first records
   it here. Owner: Willem.

Phase 2 evidence (2026-09-04): `fm check` exit 0 in a conformance
workspace carrying one cpp-conan member (rendered from the
template, gated by its own venv), with the gate output showing
`typecomplete: packages/native skips (cpp-conan kind)`,
`test: packages/native skips (cpp-conan kind)`, and
`packages/native (cpp-conan): configure, build, ctest run;
typecheck, typecomplete, test skip`, and the kindcheck step
building and testing the library through cmake and ninja. The
armed suite renders the template and runs the real cmake, ninja,
and ctest on machines that have them, and forces the red-ctest and
missing-conan refusals; the pure-python profile and gate are
pinned unchanged. The livery gate itself is green with the
kindcheck step quiet.

Phase 6 evidence (2026-09-05): the armed chain
(WORKSHOP_CONFORMANCE_DRIVE=1, tests/test_descendant_chain.py)
carries stage 5c and passed twice, fresh and resumed, on the local
Gitea: both kinds wired by the brand CLI, the child's gate green
with `packages/geometry (cpp-conan): configure, build, ctest run`
in its output, ext coverage at its floor, the profile grown only
in the child, and the resumed pass answering `already exists` from
the wiring refusal.

Phase 5 evidence (2026-09-05): on the rig, the rendered fixture
library uploaded to gitea's conan registry
(http://localhost:3000/api/packages/livery-admin/conan) and a
clean CONAN_HOME installed it back
(test_the_rig_conan_registry_round_trips, armed, green live); the
folder target round-tripped through ``conan cache save`` and
``restore`` (test_the_folder_target_round_trips, green live); the
identity guard's refusals are forced both ways in
test_workshop_kind_publish; and the armed
WORKSHOP_CONFORMANCE_DRIVE rehearsal wired both kinds through the
real verbs, gated green with the honest skips, and
`fm workflow.release --local` derived, stamped, built (conan
create and cibuildwheel), validated, and restored the tree.

Phase 4 evidence (2026-09-04): with a cpp-conan library and a
python-nanobind extension fixture, editing the library's source
marks the extension affected (test_touching_the_library_marks_the
_extension drives the graph.affected engine); a contract floor and
conan requirement that disagree refuse naming both, a missing
conan requirement refuses teaching the range form, and an
undeclared internal conan require refuses as a missing edge;
order_topologically and the driver's member resolution put the
library first; bump_set_floors moves the conan range beside the
other two homes and the lint stays green on the result.

Phase 7 evidence (2026-09-26): the armed descendant chain passes
twice, fresh and resumed, in 2m33s, with the child's own gate
running the extension's four tests: the compiled module answers
from fmt, a Conan Center package, and from the sibling library
built from its source. The six-host tool point stayed green after
the phase's four merges, run 36253981021. The floor leg is forced
by test: a floor naming a version whose header lacks the symbol
fails the pinned build, in
test_a_floor_whose_header_lacks_the_symbol_fails_the_leg. The lane
evidence for 7c is the one line still open, and Open 8 says why.

Phase 3 evidence (2026-09-04): the armed suite rendered the chain
fixture, built it through cibuildwheel (macOS leg), and the wheel
carried a platform tag with the sdist beside it; the isolated leg
installed the wheel into a fresh venv and the rendered tests
imported and called the compiled module. The guard tests force the
none-any refusal and the empty-dist refusal; the chain render
tests prove parent files beneath leaf files with the leaf kind
recorded. The armed leg self-limits: it skips naming the gap on a
host missing compilers, on linux without docker, and on win32
until MSVC arming on the runner is verified.
