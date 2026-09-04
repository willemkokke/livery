# The kind hierarchy: native kinds through one registry

Status: draft 2026-09-04, awaiting Willem's review. Nothing built.
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

## Phases

### Phase 1: the registry, opened to layers

The kind vocabulary in the package contract, the backend registry
with the three-callable contract and the documented add-a-kind
procedure, per-kind CI contract defaults, the edge-extractor table,
the template kind chain (child renders parent then itself, managed
set unioned), and layer registration: a mounted layer's plugin can
add kinds. Every guard forced first: unknown kind refuses, missing
extractor fails affected open by name, the fake future kind
registers through a test layer and dispatches.

Acceptance:
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
files), the platform-tagged wheel, the python verbs kept whole
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

The conan publish seam: the library publishes to the forge's own
conan registry where the forge has one (gitea does; proven on the
rig: upload, then a clean `conan install` back from it). The wave
gains the identity guard both ways: a native kind's pure-tagged
wheel refuses, a pure kind's platform-tagged wheel refuses, each
naming the kind and the tag. The armed release rehearsal runs the
extended graph.

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
| The extension publishes single-platform wheels from the wave's runner | a per-platform build matrix with artifact collection, its own cut when needed |

## Decision record

- 2026-09-04 (Willem): the scope is a CMake C/C++ library kind on
  conan 2 and a python binary-extension kind depending on it; Maya
  waits. The cross-kind dependency is the hierarchy's proof.
- 2026-09-04 (Willem, confirming the survey): hse has no native
  implementation to port, only the seams; the seams are the port.
- 2026-09-04: unknown kinds refuse rather than fail soft to
  python, a named deviation from hse: a typo that silently builds
  the wrong kind is worse than a stop.
- Carried forward from the 0901/0902 records: the kind chain
  renders parent then child with the managed union; the backend
  seam mirrors it; types contribute tool profiles by discovery;
  linkable C/C++ goes company-wide through conan 2.

## Open

1. **The conan remote per forge kind.** Gitea's conan registry is
   the rig-provable default. GitHub has no conan registry: decline
   honestly (publish target `none` for the library kind there)
   until a remote exists, or name one now. Owner: Willem.
2. **The platform matrix for native wheels.** The gate builds and
   tests on linux, macos, and windows already; the wave publishes
   from one runner, so the extension's published wheel is
   single-platform at first. Accept for now (the temporary table
   row), or pull the matrix build into phase 5. Owner: Willem.
3. **nanobind confirmed.** The 0901 record names nanobind and this
   plan assumes it with scikit-build-core, keeping one CMake
   ecosystem across both kinds. Confirm or name another binding
   layer. Owner: Willem.
4. **The compiler doctor policy.** Phase 2 requires a host
   toolchain and names its absence; nothing installs compilers
   until phase 18. Confirm. Owner: Willem.
