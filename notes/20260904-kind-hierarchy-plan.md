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
plan is now built. Open 6 (the newborn first-derive version,
issue #218) and Open 7 (compile-time conan consumption) remain.
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

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| No toolroom conan handle; conan runs through a deliberate `footman.run` with stated env | a typed handle when toolroom grows one, or when toolroom migrates into this repository |
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
7. Open (2026-09-05): the extension's dependency on the library is
   declared and lint-agreed, and the release train orders it, but
   the extension's own CMake does not yet consume the conan
   package at compile time (toolchain generation into the
   scikit-build configure, profiles per matrix leg). Its own cut,
   to be planned when the first extension needs a symbol from the
   library. Owner: Willem to sequence.

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
