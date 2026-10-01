# The base is an empty shell: what a workspace does not mount costs nothing

Status: written 2026-09-30, rulings on the open items taken the same
day; Willem's go on 2026-10-01. Phases 1, 3, 7 and 11a built
(issues #998, #1000, #1006, #1011, #1018); the others not started. Fourteen phases, each
day; Willem's go on 2026-10-01. Phases 1, 3, 7 and 8a built (issues
#998, #1000, #1006, #1011, #1022); phase 11 waits for a ruling on
its design; the others not started. Fourteen phases, each
gate-green and mergeable alone. It builds on the
extensible gate plan (`notes/20260905-extensible-gate-plan.md`), the
releases plan (`notes/20260927-releases-as-a-target.md`), the local
loop plan (`notes/20260930-local-loop-plan.md`) and the toolchain plan
(`notes/20260930-toolchain-plan.md`). It does not repeat their work:
the python and cpp extractions stay the plans the gate plan's open
item 17 names, and this plan delivers what those extractions need
first. It reverts the extensible gate plan's 2026-09-28 ruling that
the language layers stay inside the one workshop wheel: each layer
ships as its own distribution. It amends the strongroom redesign
plan's contract 3: the codec moves into strongroom.

## The prompt (Willem)

> I want to make sure the core is flexible, fast, empty shell, that
> deal with the core forge operations, codifies the workflow,
> abstracts all common development actions, and can be extended to
> work for every language if we so desire without touching that core.
> Willem, 2026-09-30

> footman is a generic task runner, that should be useful on its own,
> like duty or just. workshop is a footman plugin, that should turn
> footman from a task runner into a full features monorepo project
> runner. [...] a workshop layer is meant to compartementalise and add
> functionality so the end user decides exactly what features the
> monorepo supports, including various language support. features that
> you don't use should not cost anything. Willem, 2026-09-30

> the workflow / release train should work on an interface/protocol
> that kinds can / should implement for language specific publishing.
> Willem, 2026-09-30

> I think the changelog needs to become it's own layer too. Willem,
> 2026-09-30

> importing layers based on the manifest makes sense, especially
> because each layer defines its dependent layers. we need to make sure
> the manifest still is updated if a layer changes. Willem, 2026-09-30

> I ruled layers in wheel before I realised how separate wheels would
> be more performant. This plan reverts that. Willem, 2026-09-30

> Implicit core. I'd like a way for a plugin to contribute a built in
> so the task.py can stay empty by default. (Other than a comment that
> they can add project/package specific tasks here depending on the
> folder they're in) Willem, 2026-09-30

> a project/workspace ALWAYS needs to support python, because it needs
> uv, copier, footman, toolroom and forge in order to create itself.
> The Python kind implements the development of python wheels on top of
> that already existing support. You can configure a new livery project
> that only add the C++ layer on top of the skeleton, but it would
> still have a venv and uv and python and footman etcetera. We're
> creating a tool to create full featured and maintainable development
> environments for a wide ranging diversity of projects. Willem,
> 2026-09-30

> The following things are 100% only for our own house style, and not
> opinions we want to push on anyone else. That would imply that the
> implementation for this live in livery.layers.housekeeping, an
> optional wheel that you add as your last layer if you want to develop
> just like us! livery itself should become a pure pep 420 namespace so
> the python in livery.workshop.layers.python can be a optional wheel,
> but livery.workshop.layers is an empty folder contributed by the
> livery.workshop wheel. cbor should become a subpackage of strongroom.
> to avoid the __init__.py problem, each wheel's public api should live
> in wheel.api instead of wheel. [...] that last message includes the
> wheel naming convention matching the pep 420 layout. but once again,
> that is OUR choice, a third party can make a new layer and publish it
> under any name. Willem, 2026-09-30

> livery.housekeeping was already a layer right? I just moved it to its
> new home where all layers live? .api replaces ONLY the content that
> would have been stored in __init__.py. So it would still be "from
> livery.toolroom import tools" and "livery.strongroom.cbor" does not
> need to be reexported from "livery.strongroom.api". We never want to
> see 2 .apis in one module path. Willem, 2026-09-30

## What exists that this builds on

Measured on main at 1d6b1426 and read on main at 4e6f1e9b.

- **The layer mechanics.** A layer inside the workshop wheel is a
  package under `livery.workshop.layers.<name>`; `livery.workshop` and
  `livery.workshop.layers` extend their path, so another distribution
  can ship a layer there. The `base-imports-no-layer` rule of the
  layering check refuses a base module importing a layer. The docs
  layer is the first (`livery.workshop.layers.docs`); it registers a
  site file and contributes the `docs` and `deploy` jobs to builtin
  points through `livery.workshop._points.contribute_job`.
- **The registries.** Kinds, checks, categories, provenance channels,
  slots, AST rules, prose sections, site files and job contributions
  each have a register call. No layer outside the workshop calls any
  of them, and all of them live in underscore modules.
- **The reach-ins this plan removes.** The vocabulary test
  (`packages/workshop/tests/test_workshop_vocabulary.py`) lists them:
  79 findings in 17 base modules, 9 of them imports of a concrete
  backend (`_checks`, `_e2e`, `_kinds`, `_quality`, `_release`,
  `_release_driver`), the rest literal kind, ecosystem and member
  manifest names. Beyond what it counts, the base also branches on
  `*.whl`, `uv publish` and wheel tags. `KindRecord` defaults to
  `artifact="python"` and `wheel_identity="pure"`. No layer imports a
  backend.
- **The release train.** `Backend` already carries `build`,
  `publish_artifact`, `current_version`, `stamp_version`,
  `declared_requirements` and `declare_requirement`. The rest is by
  name: `bump_set_floors` rewrites floors with a pip and a conan
  regex; `validate_member` calls `_python.run_isolated_test`;
  `release_wheels` calls `_cpp_conan.save_cache` and
  `_python_nanobind.floor_legs` and sets `CIBW_BUILD`;
  `publish_release` picks a target with `artifact == "conan"`;
  `discover_release` reads `.release-manifest.json` and falls back to
  the `CHANGELOG.md` headings the squash touched; the release PR title
  check reads the changelogs; `_cliff.bumped_version` asks git-cliff
  for the next version.
- **Registries of artifacts.** `livery.forge.Registry` is one
  read-only method, `versions(name)`; `SimpleRegistry` implements it
  in the forge, `ConanRegistry` in the cpp-conan backend.
  `Forge.registry_url(kind, owner)` takes `RegistryKind =
  Literal["python", "conan", "container"]`. `_registries.py` keys its
  env names, ecosystem defaults and credential names by the same three
  words.
- **What loading costs.** Best of five, wall clock, workspace venv: a
  bare interpreter 40 ms, `import livery.forge` 60 ms,
  `import livery.toolroom.store` 80 ms, `import livery.workshop._tasks`
  150 ms, loading 61 of the base's 85 modules. `livery.footman` with
  `compose` imports in 1.0 to 1.4 ms (`-X importtime`). The language
  backends are about 4 ms of the workshop's 110; the forge and the
  tool store are about half. Every `fm` call in a workshop repository
  pays this before its task starts.
- **Footman's manifest.** `livery.footman._manifest.sync_manifest`
  builds the command tree's JSON on every run, after importing every
  task module, and rewrites the cache when the tree's hash changes.
  Only completion reads it; no run trusts it.
- **The builtin.** The workshop advertises itself in the
  `footman.builtin` entry-point group. Stock footman mounts only
  `footman.self` and `footman.janitor`; this machine mounts the
  workshop through `[builtins] user` in its footman config, for the one
  `global_only` verb, `fm new.project`.

## Ground-truth contracts (do not violate)

1. **Nothing activates by being installed.** The extensible gate
   plan's contract 3: the `[workspace] layers` list activates layers,
   and closure stays the lint's and its fix's, never the mount's. The
   workshop itself activates because the workspace's root manifest
   names it as a direct dependency, which the lock pins: a declared,
   locked fact, never a transitive install.
2. **A layer's declarations are attributes of its plugin module.**
   The extensible gate plan's contract 15. A cached manifest is derived
   from them and never a second source: deleting every cache changes no
   verdict and no tree.
3. **A stale cache never parses a command line wrongly.** Dispatch
   compares the imported task's signature with the cached spec before
   binding, and rebuilds on a difference. A stale cache may cost a
   rebuild; it never costs a wrong run.
4. **Footman imports no workshop and reads no `workshop.toml`.** Every
   footman change here is generic: a footman user without the workshop
   gets the laziness and none of the workshop's vocabulary.
5. **The base imports no layer and names no package kind and no check
   tool.** The extensible gate plan's contract 10. Python is the
   base's runtime, not a kind: every workspace creates and runs itself
   with the interpreter, uv, the venv, the root `pyproject.toml` and
   `uv.lock`, copier, footman, toolroom and the forge, whatever it
   builds, and the base names all of them. The base formats and lints
   every python file in the workspace, `tasks.py` included. The python
   kind develops python packages on top of that support: the package
   template, a package's `pyproject.toml`, wheel publishing, and the
   type checkers and tests of its packages.
6. **Re-running a release is its recovery, and a receipt is the tag
   plus the proof.** The releases plan's contracts 2 and 3. No phase
   here loosens the walk-past or the probe.
7. **`[release] publish = false` stays above every protocol.** The
   wave reads it before any kind's publisher runs, as it does today.
8. **An unknown kind, ecosystem or layer refuses by name.** Nothing
   falls back to python.
9. **Records grow additively and carry defaults.** The extensible
   gate plan's contract 8, under `WORKSHOP_API_VERSION`.
10. **The forge imports only the standard library at module import
    time**, and the forge owns the release transport with no new
    capability for it (Willem, 2026-09-27).
11. **Pinning tests before replacement.** A property the replaced code
    enforces is named and pinned before the code moves.
12. **A layer's import path does not change when it ships apart.**
    `livery.workshop.layers.<name>` stays the name a workspace lists
    and code imports; only the distribution that carries it changes.
13. **The base requires no layout and no naming of a layer.** A layer
    is an import path and a distribution, spelled in its list entry;
    its declarations are read from the module its `footman.tasks`
    entry point names. Our conventions (namespaces without
    `__init__.py`, the public API in `api`, the distribution named
    after the import path) are the house layer's, checked by its rules
    in the workspaces that list it. The base protects only its own
    mechanism: nothing may put an `__init__.py` in
    `livery/workshop/layers/`, since one would hide every other
    distribution's layer.

## The design

### What the base keeps

The python runtime the workspace creates itself with: the interpreter,
uv, the venv, the root `pyproject.toml` and `uv.lock`, copier, footman,
toolroom and the forge, and the format and lint checks (ruff) over
every python file in the workspace. A workspace that mounts only the
cpp layer still has all of them. On top of that: the workspace model (contracts,
packages, the affected graph, categories, the layering check), the
layer mount, the registries, the state store, the gate's walk, the
issue and submit family, the reserved-branch workflow engine, the
release train's phases, and the entry contract. It registers no kind.
Everything a language or an optional feature knows about packages
built in it is a registration a layer makes.

### Each layer ships apart

A layer is its own distribution under the namespace
`livery.workshop.layers`, which spans distributions.
A workspace installs the layers it lists and nothing else, so an
unused layer costs no install, no dependency and no import. The base
is `livery-workshop`; the docs layer is the first to move out, the
changelog layer is born apart, and the python and cpp extractions of
the gate plan's open item 17 land as distributions.

### Our house style is a layer

The conventions we develop by are ours, not requirements of the
workshop, and they live in the house layer,
`livery.workshop.layers.housekeeping`, where every layer lives, an
optional distribution a workspace lists last to develop the way we
do. It carries what the extensible gate plan's phase 8 puts in the
house, and the conventions of our own packages:

- **Namespaces carry no `__init__.py`.** `livery` and every level down
  to a distribution's root package are namespace packages, so
  `livery-workshop` ships `livery/workshop/layers/` as an empty folder
  and `livery-workshop-layers-python` adds `python/` beside it.
- **What a root `__init__.py` held moves to the root's `api` module,
  and nothing else does.** `from livery.footman.api import plugin` and
  `from livery.workshop.api import rewrite_nav_block`; a root whose
  `__init__.py` held nothing gets no `api`. Every other module keeps
  its own path and is imported by it, never re-exported through `api`:
  `from livery.toolroom import tools` stays, and the codec is
  `livery.strongroom.cbor`. A subpackage below the root keeps its
  `__init__.py`. No module path contains `api` twice.
- **A distribution is named after its import path**, dots to dashes:
  `livery.workshop.layers.docs` ships as `livery-workshop-layers-docs`.
  The base's derivation for a bare list entry is the same rule, so our
  entries stay bare; a third party's layer names its distribution in a
  table entry and may call it anything.

### The base is implicit, and `tasks.py` is empty by default

The base is the implicit first layer, the mirror of the instance
being the implicit last: `[workspace] layers` names only what a
workspace chooses, and a list naming `livery.workshop` refuses naming
the rule.

A plugin may contribute itself as a builtin of a project. Footman
mounts, as the project's builtin rung, the `footman.builtin` entry
points of the distributions the project's root manifest names as
direct dependencies, which the lock pins. A transitive install never
activates; a direct dependency that should not mount is excluded by
`[tool.footman] builtin-exclude`. The workshop is a direct dependency
of every workspace, so it mounts without a line in `tasks.py`, and it
names the layers to mount after it from the list.

The rendered root `tasks.py` is a comment and nothing else: that this
file adds tasks for the whole project, and that a `tasks.py` in a
package's directory adds tasks there, the nearer file winning. A
machine may still mount the workshop above every project through
`[builtins] user`, for `fm new.project`; with manifests that costs a
manifest read, not an import.

### The release train's two protocols

A kind implements the package's half. An ecosystem implements the
registry's half, because kinds and registries do not map one to one:
two kinds publish to pypi, and one package may publish to a registry
and its repository's releases (the releases plan).

```python
class Releasable(Protocol):
    """What a kind's backend implements so the train can release it."""

    # Already on Backend today.
    def current_version(self, package: Package) -> str: ...
    def stamp_version(self, package: Package) -> Stamper: ...
    def declared_requirements(self, package: Package) -> dict[str, str]: ...
    def declare_requirement(self, package: Package, dependency: Package, floor: str) -> list[str]: ...

    def version_homes(self, package: Package) -> tuple[Path, ...]:
        """Every file a stamp or a floor bump writes: the snapshot and rollback set."""

    def distributions(self, package: Package) -> tuple[Distribution, ...]:
        """The (ecosystem, name) pairs this package publishes under."""

    def build_plan(self, package: Package) -> BuildPlan:
        """Portable(), built once anywhere, or PerHost(hosts, runtimes), the matrix."""

    def build(self, package: Package, root: Path, *, epoch: int, host: str | None) -> Built:
        """Build into dist/ and write which file each host produced."""

    def check_built(self, package: Package, built: Built, plan: BuildPlan) -> None:
        """Refuse a build that is not what the plan promises."""

    # Optional: a kind without them raises Unsupported and the train
    # prints the skip, the rule CiContract already applies to roles.
    def prove_floors(self, package: Package, root: Path, *, resolution: Resolution,
                     siblings: Mapping[str, Built]) -> dict[str, str]: ...
    def replay(self, package: Package, version: str, target: RegistryTarget) -> int: ...


class Ecosystem(Protocol):
    """One registry protocol: pypi, conan, npm, container, a release store."""

    name: str

    def defaults(self) -> EcosystemDefaults:
        """Env names, default read and publish addresses, credential names."""

    def spell(self, version: SemVer) -> str:
        """The version as this ecosystem writes it: 1.2.0-dev.3 is 1.2.0.dev3 on pypi."""

    def served(self, target: RegistryTarget, dist: str) -> tuple[str, ...]: ...

    def publish(self, target: RegistryTarget, dist: str, version: str,
                files: Sequence[Path]) -> bool:
        """Upload; False when the target already served it."""
```

`Ecosystem` is the workshop's name for the publishing half. It is not
`livery.forge.Registry`, which stays the read-only probe the forge
defines and `SimpleRegistry` implements, as the kind hierarchy plan
ruled on 2026-09-04. An ecosystem's `served` may use a forge
`Registry` underneath.

The releases plan's `_ReleaseRegistry` is the release-store ecosystem:
language-neutral, in the base, reading and writing a release's assets
through the forge's transport.

### Versions and the changelog

The base derives the next version: the commits since the package's
receipt tag that touch its paths, read with the grammar
`livery.workshop._conventional` already holds and `fm commit` already
enforces. The bump policy is the one the rules fragment states. The
release's member list, `.release-manifest.json`, is the only record of
what a release contains; the changelog fallback and the title check
move to it.

The changelog layer, `livery.workshop.layers.changelog`, implements
one protocol and owns git-cliff, `cliff.toml`, and the changelog
pages it contributes to the docs layer through `WORKSHOP_FOR`:

```python
class ReleaseNotes(Protocol):
    def entry(self, package: Package, since_tag: str, version: str) -> str: ...
    def record(self, package: Package, version: str, entry: str) -> list[str]: ...
```

Without the layer a release still versions, publishes and tags, and
its notes are the commit subjects in the release PR body and the
forge release body.

### Loading by manifest

Three pieces, the first two in footman and generic:

- **One cached manifest per plugin.** It holds each task as
  `module:callable` with its CLI spec, the plugin's hooks with their
  modules, its global options, and a data section the plugin fills
  when the manifest is built. It is keyed by every file the build
  imported: a file from an installed wheel by its distribution's name,
  version and `RECORD` digest, a file from an editable install by its
  size and modification time. A task whose options come from a type
  in another package is covered, because that package's file was
  imported when the tree was built. Loading the workshop imports 197
  files, 109 of them editable here; checking those 109 takes 0.17 ms.
  The tree a run sees is the plugins' manifests joined in mount order,
  with the collision checks over data.
- **Dispatch imports one module.** The dispatched task's module is
  imported, its signature compared with the cached spec (contract 3),
  and the manifest of that plugin rebuilt on a difference. An unknown
  name rebuilds the plugins whose keys moved before it refuses. A
  `pre_tasks` hook still runs on every call, so its module is imported
  on every call and must stay small.
- **A plugin may name the plugins that mount after it.** The workshop
  answers with the `[workspace] layers` list and its closure check, so
  `mount_layers()` leaves the rendered `tasks.py`. The kinds, checks and other
  registrations of each layer travel in its manifest's data section as
  records whose callables are references, loaded when used.

The cascade keeps its order: builtins, the user file, the layers, the
root `tasks.py`, the nested files, each nearer rung winning by name.
The root `tasks.py` may shadow a layer's task; `inherited()` reaches
the layer's, and `fm --where` and the listing show the shadowing.
Cascade files are imported on every call, as today; they are the
user's own code.

## Phases

### Phase 1: the base's vocabulary, proved

Built (issue #998). Contract 10 of the extensible gate plan promised a
vocabulary test that did not exist; this phase wrote it as a check that
can only tighten.

Deliverables:

- `packages/workshop/tests/test_workshop_vocabulary.py` scans every
  base module outside `_backends/` and `layers/`. A finding is an
  import of a concrete backend module, or a string literal outside a
  docstring that equals a concrete kind name or `conan` or
  `container`, or holds `pyproject.toml` or `conanfile.py` as a path
  segment. A word inside a longer string is not counted.
- `RUNTIME` lists the base's own python runtime, each entry with its
  count and reason: the root `pyproject.toml` and its fragments, the
  uv workspace the venv and the lock follow, the interpreter and a
  runner's interpreter version, and one false friend, `container` as
  a docs publish seam. 20 occurrences in 14 entries.
- `ALLOWANCE` is the rest: 79 findings in 36 entries across 17
  modules. A count above it refuses; a count below it refuses until it
  is lowered, so the list always says what is left. A runtime count
  above what the scan finds refuses the same way.
- The same test refuses any layer module importing a backend.
- The docs layer's two backend imports were the python coverage
  pages. `KindRecord.coverage_pages` names a kind's renderer, resolved
  from the nearest ancestor like the extractor; the python kind
  registers `render_coverage_pages`, moved from the docs layer into
  `_backends/_python.py` with `_stored_legs`. The docs layer hands each
  package that declares an `htmlcov` report to its kind's renderer,
  and a kind with none states the absence. The generator verb is
  `docs.coverage-pages`, which replaces `docs.python-coverage`.

Acceptance, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 (3m03s; 1482 tests in the workshop's
  suite).
- A new base module importing `_backends._python` refuses:
  `test_a_base_module_importing_a_backend_refuses`.
- A new `"conan"` literal and a member manifest path refuse, naming
  the module: `test_a_new_kind_literal_in_the_base_refuses_naming_the_module`.
- A count above the allowance refuses, and one below it refuses until
  the allowance is lowered: `test_the_allowance_only_falls`; the same
  for the runtime list: `test_the_runtime_list_exempts_only_its_count`.
- A kind with no coverage renderer states the absence:
  `test_a_kind_with_no_coverage_renderer_states_the_absence`.
- `git grep -n "_backends" packages/workshop/src/livery/workshop/layers`
  prints nothing (exit 1), and `test_no_layer_imports_a_backend` keeps
  it so.
- `fm docs.coverage-pages` exits 0 and prints
  `coverage pages for workshop`.

### Phase 2: namespaces without `__init__.py`, public API in `api`

A break for every consumer, taken before 1.0.

The pinning tests come first: for every distribution, the names its
root `__init__` exports today are the names its `api` exports after.

Deliverables:

- A root `__init__.py` with content becomes the root's `api.py`,
  unchanged in what it exports: `livery.footman.api`,
  `livery.forge.api`, `livery.toolroom.store.api`,
  `livery.toolroom.bench.api`, `livery.strongroom.api`,
  `livery.workshop.api`. Footman's lazy `__getattr__` moves with its
  exports. `livery.toolroom` has no `__init__.py` today and gets no
  `api`; `livery.toolroom.tools`, `livery.forge.testing` and
  `livery.footman.tasks` are subpackages and keep their own.
- The docs layer's root holds a docstring only; its `__init__.py`
  goes and it gets no `api`.
- `livery/workshop/layers/__init__.py` and the `pkgutil.extend_path`
  lines go.
- The mount reads a layer's declarations from the module its entry
  point names, and `layer_content` finds a layer's `content/` through
  `importlib.resources`, which reads a namespace package.
- The base's namespace rule refuses any `__init__.py` in
  `livery/workshop/layers/`, in place of today's rule on its shape.
- Every import in the repository, the rendered `tasks.py`, the
  templates, the docs pages and their examples use the `api` modules.
- Each package's changelog states the break and the new import.

Acceptance:

- `fm check` exits 0.
- The pinned exports match, per distribution:
  `test_api_exports_what_the_package_exported`.
- No distribution's root package has an `__init__.py`, and no module
  path contains `api` twice:
  `test_every_distribution_root_is_a_namespace_with_one_api`.
- An `__init__.py` added to `livery/workshop/layers/` refuses naming
  the rule: `test_an_init_in_the_layers_namespace_refuses`.
- `fm workflow.release --local` releases every changed distribution.
- `fm ci.e2e` is green on the develop set, which births a workspace
  from the new template.

### Phase 3: the codec becomes `livery.strongroom.cbor`

Built (issue #1000).

Deliverables:

- The codec is the subpackage `livery.strongroom.cbor`
  (`cbor/__init__.py` exporting `MAX_DEPTH`, `CodecError`, `Value`,
  `decode`, `encode` from `cbor/_codec.py`), reached by its own path:
  strongroom's root does not re-export it, and importing the store
  does not load it. A subpackage, because strongroom keeps every
  module beside its `__init__` underscore-private.
- Its spec is `packages/strongroom/spec/cbor.md` with
  `spec/vectors/cbor.json`, listed in the spec index and the standard
  page; its docs page is `packages/strongroom/docs/cbor.md`, in the
  nav.
- Its tests are strongroom's: `test_strongroom_cbor_codec.py`,
  `test_strongroom_cbor_vectors.py`, and the codec's own pins in
  `test_strongroom_cbor_package.py` (stdlib-only imports, not loaded
  by the store, the public surface, the spec beside the package).
- `packages/cbor/`, its answers entry and every root configuration
  line naming it are gone, and so is the `livery-cbor` distribution.
  Its 0.0.0 release stays on the index as the name's claim.

Acceptance, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 (3m27s, the full gate: the answers file
  changed). Strongroom reads 99.8% here against its floor of 100, the
  CI union's to judge; the lines missed are in `_groups.py` and the
  conformance kit, and the codec is covered whole.
- `git grep -n "livery.cbor\|livery-cbor" -- packages` prints nothing
  (exit 1).
- `fm workflow.release --local packages/strongroom` exits 0 and builds
  `livery_strongroom-0.3.0.dev36+...-py3-none-any.whl`, which carries
  `livery/strongroom/cbor/__init__.py` and `cbor/_codec.py`.
- The codec's pins pass in strongroom's suite, and strongroom's
  vector-file pin now names `cbor.json`.

### Phase 4: the docs layer ships apart

Deliverables:

- `packages/workshop-layers-docs/`, the distribution
  `livery-workshop-layers-docs` carrying `livery.workshop.layers.docs`,
  with its own contract, coverage floor and release; the base drops
  every dependency only the docs layer needs.
- The list entry is the bare import path, since the distribution's
  name is the base's derivation of it.
- The template seeds the docs layer's dependency beside its list entry
  for a new project.
- The local loop's dev wheel set carries each layer's own wheel.

Acceptance:

- `fm check` exits 0; the layering lint proves the base declares no
  dependency on the docs distribution.
- A workspace whose list names no docs layer installs no
  `livery-workshop-layers-docs`, and `fm docs.build` refuses naming the layer
  to list: `test_a_workspace_without_the_docs_layer_installs_none`.
- `fm workflow.release --local` releases the two distributions in
  dependency order.
- `fm ci.e2e` is green on the develop set.

### Phase 5: the kind's half of the release train

Waits for the releases plan's phases 1 and 2 (the target list and the
one release probe), and builds on them.

The pinning tests come first: the wave walks past a served version;
a failed member stops only its dependents; the identity guard refuses
both ways; `publish = false` builds and tags and never uploads; a
prebuilt dist without its collection refuses naming the matrix;
floors move in every dependent's manifest.

Deliverables:

- `Releasable` on the backends: `version_homes`, `distributions`,
  `build_plan`, `build` with its dist manifest, `check_built`, and the
  optional `prove_floors` and `replay`.
- `bump_set_floors` calls `declare_requirement`; its regexes go.
  `rollback_prepare` and the dev release's snapshot read
  `version_homes`.
- `validate_member` calls `prove_floors`; `release_wheels` becomes
  the release point's per-host job, emitted when any member's plan is
  `PerHost`, and calls `build` per host. `_wheels.py`, the
  `CIBW_BUILD` narrowing and `save_cache` move into the kinds that
  need them.
- The prebuilt handoff reads the dist manifest, never a glob of
  `*.whl` or `*.tgz`.
- `KindRecord.artifact` and `wheel_identity` retire into
  `distributions` and `build_plan`; `abstract` kinds declare neither.
- `_update.bump_floors` reads floors through `declared_requirements`,
  so a cpp-conan member no longer reaches `pyproject.toml`; the forced
  case is a test first.

Acceptance:

- `fm check` exits 0; the phase 1 allowance loses every line of
  `_release.py`, `_release_driver.py`, `_publish.py`,
  `_dev_release.py` and `_wheels.py`.
- The pinned properties pass before and after, by name.
- `fm workflow.release --local` releases this workspace's members.
- `fm ci.e2e --scenario=release` is green on the Gitea lane, which
  builds the nanobind wheel per host and the conan member.
- A kind without `prove_floors` prints its skip:
  `test_a_kind_without_floor_proofs_prints_the_skip`.
- `bump_floors` on a cpp-conan member writes its recipe:
  `test_bump_floors_on_a_conan_member_writes_the_recipe`.

### Phase 6: ecosystems are registrations, and the forge administers packages

Deliverables:

- `register_ecosystem(Ecosystem)`, and `pypi`, `conan`, `container`
  and `releases` registered by the kinds that publish to them and by
  the base for `releases`.
- `resolve_registries` reads env names, defaults and credential names
  from the ecosystem's `defaults()`; `_ENV_VARS`, `_ECOSYSTEM` and
  `_CREDENTIAL_VARS` go.
- `livery.forge.RegistryKind` widens from the three-word `Literal` to
  an ecosystem name; each forge backend answers the ecosystems its host
  serves and raises `Unsupported` for the rest, as today. An additive
  forge change, stdlib-only.
- `registry_for` resolves by the ecosystem the distribution names; the
  `artifact != "conan"` refusal goes, and an unregistered ecosystem
  refuses by name.
- The forge administers its hosted packages: `supports("package_admin")`
  and a protocol listing a package's versions and deleting one, on the
  backends whose host allows it. `purge_packages` and
  `purge_gitlab_packages` become its implementations and leave the
  private module; `_e2e` calls the protocol. Deleting a version that
  is already gone is success.

Acceptance:

- `fm check` exits 0; the allowance loses `_registries.py`.
- An unregistered ecosystem refuses naming the registered ones:
  `test_an_unregistered_ecosystem_refuses_naming_the_vocabulary`.
- The ladder's pinned properties from the releases plan's phase 1
  pass unchanged.
- The forge conformance suite passes for the three backends, with
  deletion covered where each host supports it and `Unsupported` named
  where not: `fm forge.conformance`.
- Deleting a version twice succeeds both times:
  `test_deleting_a_deleted_version_is_success`.

### Phase 7: the base derives the version, and the member list is the record

Two changes, mergeable apart: 7a derives the version, 7b makes the
member list the only record.

**7a, built (issue #1006).**

- `livery.workshop._versions.derive_version(root, package)`: the
  commits since the package's newest receipt tag that touch its
  directory, read with the commit grammar. Before 1.0 a break or a
  feature bumps the minor and anything else the patch; from 1.0 a
  break bumps the major, a feature the minor, anything else the
  patch. A `chore(release)` commit earns nothing, and an
  unconventional subject counts as a patch. A package with no receipt
  releases at its `[release] baseline`, else at 0.0.0.
- `derive_plans`, `prepare_release` and the dev release ask it;
  `_cliff.bumped_version` is gone, and git-cliff writes only the
  changelog entry.

Acceptance of 7a, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 (1m55s).
- Fourteen scenarios on throwaway repositories, each asking both
  git-cliff and the derivation, agree:
  `test_the_derivation_agrees_with_git_cliff`. They cover the
  baseline, no baseline, nothing since the tag, a commit outside the
  member, a release commit alone, an unconventional subject, a fix,
  a feature and a break on each side of 1.0, a breaking footer, and
  the strongest of several commits.
- On this repository at d908a3db every member derives what git-cliff
  answered: cbor 0.1.0, footman 0.55.0, forge 0.5.0, strongroom
  0.3.0, toolroom 0.9.0, toolroom-bench 0.2.0, toolroom-store 0.1.0,
  workshop 0.4.0.
- `fm workflow.release --local packages/footman packages/workshop`
  exits 0 and builds `0.55.0-dev...` and `0.4.0-dev...`. The build
  leaves its docs copy in `src/.../_docs/`, which pyrefly then judges;
  filed as #1010.

**7b, built (issue #1011).**

- `discover_release` reads `.release-manifest.json` at the ref and
  nothing else; a commit without a readable list is not a release
  squash and refuses, naming the `--ref` recovery. The changelog
  fallback and `changelog_version` are gone.
- `member_pairs_at(git, ref)` names each listed member from its
  contract at the ref; the release PR's title check and the
  recovery's rebuilt title read it.
- The template publisher reads a ref without a list as one that
  released nothing.
- Every release squash on main since 2026-09-06 carries the list,
  and the pending-wave scan reads only the last 50 commits, so no
  existing history loses its discovery.

Acceptance of 7b, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 (1m52s).
- A squash without a member list refuses, naming the recovery:
  `test_a_squash_without_a_member_list_refuses_naming_the_recovery`;
  a garbled list and a list naming no package refuse too:
  `test_discovery_refuses_a_garbled_member_list`,
  `test_a_member_list_naming_no_package_refuses`.
- The title check passes on a member list and refuses a drifted
  title: `test_the_check_title_task_refuses_a_drifted_title`.
- The versions are 7a's: 7b changes what a release contains, not how
  a version is derived.

### Phase 8: the changelog layer

Two changes: 8a cuts the seam in the base, 8b ships the layer as its
own distribution (Willem, 2026-10-01: a separate wheel from the
start).

**8a, built (issue #1022).**

- `livery.workshop._release_notes`: the `ReleaseNotes` protocol
  (`entry`, `record`, `verify`) and its registry, one provider at a
  time, withdrawn by the layer that registered it.
- `prepare_release`, `verify_release`, the dev release's excerpt and
  the docs layer's unreleased section ask the registered provider.
  With none, the version is stamped, no notes are written or judged,
  and the train prints that no mounted layer records them.
- The git-cliff and `CHANGELOG.md` provider is `CliffChangelog` in
  `_cliff.py`, registered by the base until 8b; `_release.py`
  imports `_cliff` for that registration.

Acceptance of 8a, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 (1m59s); every existing release test
  passes unchanged with the provider registered.
- With no provider, `prepare_release` stamps the version, writes no
  changelog and prints why:
  `test_without_a_notes_provider_prepare_stamps_and_writes_no_notes`.
- One provider at a time, withdrawn by its layer:
  `test_the_notes_provider_is_one_registration_withdrawn_by_its_layer`.

**8b, not started.**

- `packages/workshop-layers-changelog/`, distribution
  `livery-workshop-layers-changelog`, carrying
  `livery.workshop.layers.changelog`: `CliffChangelog` and `_cliff.py`
  move there and the layer registers the provider at mount; the
  base's registration and `_release.py`'s import of `_cliff` go.
- `cliff.toml`, its template and the `git_cliff` tool leave the
  `base` kind for the layer. The base kind manages `cliff.toml` today
  (`managed=("cliff.toml",)`), and no registry lets a layer add a
  managed file to a kind: 8b adds one, or the layer renders the file
  through its template overlay and the base kind stops naming it.
- The tool lock's by-name refusal of a host copy of `git_cliff`
  (`PINNED_TOOLS`) becomes the layer's declaration.
- This workspace lists the layer; the template seeds it for a new
  project.

Acceptance of 8b:

- `fm check` exits 0.
- A workspace without the layer releases with no notes:
  `test_a_release_without_the_changelog_layer_writes_no_notes`.
- `fm docs.build` renders every package's changelog page.
- `fm ci.e2e --layer=livery.workshop.layers.changelog` is green once
  the local loop plan's phase 3b exists; until then an open line.

### Phase 9: the rest of the reach-ins

Deliverables:

- The format and lint records stay the base's and cover every python
  file in the workspace; the typecheck, typecomplete, test and
  examples records move beside the python backend, and the cpp
  records beside the cpp backend, so `_checks.py` registers the
  framework and the base's own python checks;
  `python_members` and the `is_python_kind` filters go.
- The quality verbs dispatch every step through the registered checks
  and the kind: `_quality.py`'s direct `_python` and `_cpp_conan`
  calls go, and so do `suites_of` and `units_of` by name.
- `_e2e.py` asks the ecosystem for its probe; `_sync.py` asks the kind
  for its editable registrations; `_templates.py`'s python defaults
  become the refusal of contract 8.
- `KindRecord` has no python default left.

Acceptance:

- `fm check` exits 0, and the allowance holds only exempt runtime
  lines. The acceptance of the extensible gate plan's contract 10 is
  then the rule itself.
- `fm ci.e2e` is green on the develop set.

### Phase 10: registrations by reference

Deliverables:

- Every record whose field is a callable also takes a reference,
  `"module:callable"`, resolved on first use and cached; the field
  stays additive under contract 9.
- The base's and the docs layer's registrations use references, so
  registering loads no backend and no site code.

Acceptance:

- `fm check` exits 0.
- A reference to a missing callable refuses naming the reference and
  the layer: `test_a_dangling_reference_refuses_naming_it`.
- After `mount_layers()`, `sys.modules` holds no backend and no
  module of the docs layer beyond its plugin module:
  `test_mounting_loads_no_backend_and_no_site_code`.

### Phase 11a: load less by convention

Built (issue #1018), Willem's option A of 2026-10-01; manifest
dispatch, the original phase 11 below, waits to be revisited.

Deliverables:

- `livery.forge`, `livery.toolroom.store` and `livery.strongroom`
  serve their public names from their modules on first use: the
  names stay declared for the checkers under `TYPE_CHECKING`, and a
  module `__getattr__` imports the defining module and keeps the
  value. Importing a root loads none of its modules.
- The workshop's `_tools` and `_devenv`, and eleven of the bench's
  modules, import the store and strongroom inside the functions that
  use them. A name used at import time (a default argument, a module
  constant) or reached by a test as the module's own seam stays a
  module-level import, which now loads only the module defining it.
- The tests that patched `livery.workshop._tools.Store` patch the
  store package's `Store`, the name the functions now read.

Acceptance, with the evidence of 2026-10-01:

- `fm check --fix` exits 0 across forge, strongroom, the store, the
  bench and the workshop (3m06s).
- Importing each root loads none of its modules, one name loads only
  its defining module, every public name resolves:
  `test_importing_the_root_loads_no_module_of_the_package`,
  `test_one_name_loads_only_the_module_that_defines_it`,
  `test_every_public_name_resolves` in each of the three packages.
- Loading the workshop's plugin loads no forge backend and no store
  engine: `test_mounting_the_workshop_loads_no_forge_backend_and_no_store_engine`;
  the bench's likewise: `test_mounting_the_bench_loads_no_store_engine`.
- `fm commit --help` in this repository, fifteen runs each: median
  284 ms before, 255 ms after; 386 modules loaded before, 365 after.
  What still loads of the two is the forge root with its error,
  protocol and type modules (the mounted forge layer's own), and the
  store's record, spec and stub modules with three small strongroom
  modules, for the constants and seams named above.

### Phase 11: footman dispatches from manifests

Deferred (Willem, 2026-10-01: "A, revisit later"); the open item
below names what it must survive.

In footman, generic, with no workshop vocabulary.

Deliverables:

- A manifest per plugin, keyed as "Loading by manifest" states, built
  when absent or its key moved, under footman's cache directory.
- Dispatch imports the task's module alone and checks its signature
  against the spec; a difference rebuilds that plugin's manifest and
  binds again. An unknown name rebuilds the moved plugins before it
  refuses.
- A data section per plugin manifest, filled by a hook the plugin
  names.

Acceptance, refusals first:

- A task whose signature changed binds with the new signature:
  `test_a_changed_signature_rebuilds_before_binding`.
- A task added to an editable plugin is found on first call:
  `test_a_new_task_in_an_editable_plugin_is_found`.
- Deleting the cache changes no tree:
  `test_the_tree_from_manifests_equals_the_tree_from_imports`.
- A warm `fm <task>` of a plugin with ten modules imports that task's
  module and no other of the plugin's:
  `test_dispatch_imports_only_the_tasks_module`.
- `fm check` exits 0 in footman's own gate.

### Phase 12: a plugin contributes a builtin, and names what mounts after it

In footman, generic.

Deliverables:

- The project's builtin rung: the `footman.builtin` entry points of
  the distributions the root manifest names as direct dependencies,
  mounted after the machine's builtins and before the user file;
  `[tool.footman] builtin-exclude` removes one by name.
- A plugin may return the plugins to mount after it, as data in its
  manifest; each mounts under its own identity, in the order given.
- A task in a nearer cascade file shadows a plugin's task by name, and
  `fm --where` and the listing name both.

Acceptance, refusals first:

- A distribution installed transitively mounts nothing:
  `test_a_transitive_builtin_never_mounts`.
- An excluded direct dependency mounts nothing, and the listing says
  who excluded it: `test_builtin_exclude_names_the_exclusion`.
- A plugin naming a plugin that is not installed refuses naming the
  distribution: `test_a_named_plugin_that_is_absent_refuses_naming_it`.
- A root file's task shadows a plugin's, and `inherited()` reaches the
  plugin's: `test_a_root_task_shadows_a_plugin_task_and_inherits_it`.
- `fm check` exits 0 in footman's own gate.

### Phase 13: the workshop mounts itself and its layers from manifests

Deliverables:

- The base is the implicit first layer; a list naming
  `livery.workshop` refuses naming the rule, and `fm layers` prints the
  base first without it being listed.
- The workshop mounts through the project's builtin rung and names the
  listed layers to mount after it; `mount_layers()` goes.
- The rendered root `tasks.py` is the comment alone.
- The layers' registrations travel in their manifests' data sections;
  the base reads them without importing the layer.
- `fm doctor` prints each layer's manifest key and whether it was
  rebuilt.

Acceptance:

- `fm check` exits 0; `fm template.check` passes with the empty
  `tasks.py`.
- A list naming the base refuses:
  `test_a_list_naming_the_base_refuses_naming_the_rule`.
- `fm commit --help` in this repository imports no module of
  `livery.forge` and no `livery.toolroom.store`:
  `test_a_verb_that_needs_no_forge_imports_none`.
- The warm start of `fm commit --help`, best of five, is recorded in
  the decision record beside the 150 ms baseline.
- `fm new.project` births a workspace whose `tasks.py` is the comment
  and whose `fm check` is green, proven in the loop by `fm ci.e2e`.

### Phase 14: our conventions are the house layer's

Waits for the house layer the extensible gate plan's phase 8 creates,
at `livery.workshop.layers.housekeeping`.

Deliverables:

- The house layer registers four rules into the layering check: a
  distribution's root package with an `__init__.py` refuses; a module
  path with `api` twice refuses; an `api` that re-exports a module of
  its own distribution refuses; a distribution whose name is not its
  import path's derivation refuses.
- The base's export test becomes the house's rule; the base keeps only
  contract 13's rule on `livery/workshop/layers/`.
- This workspace lists the house layer last.

Acceptance:

- `fm check` exits 0 here, with the house layer listed.
- A workspace without the house layer accepts a layer distribution
  named `acme-widgets` with an `__init__.py` at its root:
  `test_the_base_accepts_any_layer_name_and_layout`.
- The same workspace listing the house layer refuses both, naming the
  rules: `test_the_house_refuses_a_root_init_and_a_foreign_name`.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The phase 1 allowance of named findings | nothing: phase 9 empties it but for exempt runtime lines |
| The changelog fallback in `discover_release` | the member list alone (phase 7) |
| `KindRecord.artifact` and `wheel_identity` | `distributions` and `build_plan` (phase 5) |
| `plugin("livery.workshop")` and `mount_layers()` in the rendered `tasks.py` | the project's builtin rung and the workshop naming its layers (phases 12 and 13) |
| `{ import = "livery.workshop.layers.docs", dist = "livery-workshop" }` | the bare import path, carried by the layer's own distribution (phase 4) |
| `livery-cbor` 0.0.0 on the index, a distribution no longer built | nothing: kept as the name's claim, a recorded debt (phase 3) |

## Decision record

- 2026-09-30, Willem: footman stays a generic task runner, the
  workshop is its plugin, and a layer adds a feature or a language
  the user chooses to mount. A feature not used costs nothing.
- 2026-09-30, Willem: the release train works through a protocol a
  kind implements for its language's publishing. Phases 5 and 6.
- 2026-09-30, Willem: the changelog becomes its own layer. This
  amends the extensible gate plan's design, which keeps the changelog
  engine in the base kind; phases 7 and 8.
- 2026-09-30, Willem: layers load from a manifest, and the manifest
  follows a changed layer. Answered with a key per plugin and a check
  at dispatch rather than an incremental build: a plugin's rebuild
  costs what every call pays today.
- 2026-09-30, Willem: the workshop is not a builtin of footman. It is
  one on this machine through `[builtins] user`; stock footman mounts
  none of it.
- 2026-09-30, Willem: every workspace supports python, because it
  creates itself with uv, copier, footman, toolroom and the forge. The
  python kind develops python packages on top of that support. A
  workspace with only the cpp layer still has a venv, uv, python and
  footman. Contract 5 and "What the base keeps" state it.
- 2026-09-30, Willem: the formatter and the linter belong to the
  base's python support and run over every python file, `tasks.py`
  included; the type checkers and the tests come with the python kind,
  with the package template and wheel publishing. This amends the
  extensible gate plan's statement that a workspace with the base
  alone runs no language check: it runs format and lint on its own
  python.
- 2026-09-30, Willem: each layer ships as its own distribution. This
  reverts the extensible gate plan's ruling of 2026-09-28 that the
  language layers stay inside the one wheel, taken before separate
  distributions were seen to cost less: an unlisted layer is then
  neither installed nor imported.
- 2026-09-30, Willem: the base is implicit, and a plugin can
  contribute a builtin so the rendered `tasks.py` is empty but for a
  comment. The builtin rung is the project's direct dependencies, so
  activation stays a declared and locked fact.
- 2026-09-30, Willem: the root `tasks.py` may shadow a layer's task,
  shown by `fm --where`.
- 2026-09-30, Willem: the forge administers hosted packages, deletion
  included, because mistakes happen. Phase 6.
- 2026-09-30: the manifest key covers every file its build imported,
  which closes the gap a key over the layer's own files left: a task
  whose options come from a type in another package.
- 2026-09-30, Willem: our conventions are ours and not the
  workshop's. They live in the house layer, an optional distribution
  listed last; a third party publishes a layer under any name and
  layout. Contract 13 and phase 14.
- 2026-09-30, Willem: the house layer is the one the extensible gate
  plan named `livery.housekeeping` on 2026-09-28, moved to where every
  layer lives: `livery.workshop.layers.housekeeping`, distribution
  `livery-workshop-layers-housekeeping`.
- 2026-09-30, Willem: `livery` becomes a pure PEP 420 namespace down to
  every distribution's root, `livery.workshop.layers` is an empty
  folder the `livery-workshop` wheel ships, and what a root
  `__init__.py` held moves to the root's `api`. Only that moves: a
  submodule keeps its own path and is never re-exported, and no module
  path contains `api` twice. Phase 2. This carries out the
  road the extensible gate plan recorded on 2026-09-30 for the docs
  layer.
- 2026-09-30, Willem: a distribution is named after its import path.
  Our layers' list entries stay bare import paths.
- 2026-09-30, Willem: the codec becomes a subpackage of strongroom.
  This amends the strongroom redesign plan's contract 3, which made
  `livery.cbor` the codec's only home. Phase 3.
- 2026-10-01, phase 1: the vocabulary check is a test, not an AST
  rule in the layering check as first written. Contract 10 of the
  extensible gate plan names a test; the findings exist only in this
  repository's source, so a rule would ship the allowance in the
  wheel for a check no other workspace can trigger.
- 2026-10-01, phase 1: the docs layer's backend imports were the
  python coverage pages, not the extractor and the examples runner
  the plan named. They became a field on the kind record, and the
  generator verb `docs.python-coverage` became `docs.coverage-pages`.
- 2026-10-01, phase 3: the codec is a subpackage, not a module,
  because strongroom pins every module beside its `__init__` as
  underscore-private; the path is the ruled `livery.strongroom.cbor`.
  `fm template.apply` could not drop the member: every verb syncs the
  venv from the root `pyproject.toml` first, which still named
  `livery-cbor`, so its lines were removed by hand and the render then
  matched them. Filed as #1005.
- 2026-10-01, phase 7a: the pin compares the derivation with git-cliff
  over scenario repositories, not over this repository's history: a
  CI checkout's history and tags are not guaranteed, and a scenario
  states each rule it pins. The comparison over this repository's
  members was run once and is recorded above.
- 2026-10-01, Willem: phase 11 takes option A now and revisits
  manifest dispatch later. A needed lazy roots in the forge, the
  store and strongroom as well as the workshop's own imports: the
  forge layer's entry point lives inside the forge package, and any
  submodule import ran the store's whole root. The saving is about
  30 ms, not the 55 ms estimated on 2026-09-30: that estimate
  charged the forge and the store for standard-library modules
  footman imports anyway.
- 2026-10-01, phase 8a: without a provider the train writes no notes
  at all. The release pull request's body was never the notes (it
  is the member summary and the Mined-At line), so the plan's
  "commit subjects in the PR body" has nothing to replace.
- 2026-09-30: the ecosystem half is the workshop's `Ecosystem`, and
  `livery.forge.Registry` stays the read-only probe with
  `SimpleRegistry` in the forge, as the kind hierarchy plan ruled on
  2026-09-04.

## Open

1. **Phase 11's design.** Mapping footman's startup on 2026-10-01
   found five facts the phase as written does not survive:
   `tasks.py` is arbitrary python and its mount arguments are not
   recorded, so the tree's shape exists only by running it;
   `pre_tasks` hooks edit the whole live tree before any manifest is
   built, and the workshop's `apply_cascade` may re-exec through
   `handing_off()`; every global lifecycle hook and plugin option
   needs its module imported on every call; the workshop's generated
   verbs are closures no module and qualname can re-import; and the
   `requires_*` gates are evaluated live. The options: (A) load less
   by convention, moving the forge and tool store imports into the
   functions that use them, pinned by a test that `fm commit --help`
   loads neither, with no footman change; (B) manifest dispatch
   redesigned around the five facts; (C) B as a plugin's opt-in. The
   agent recommends A now and B only if A's measurement leaves too
   much. A is built as phase 11a and saved about 30 ms of 284 on
   `fm commit --help`; B and C wait to be revisited. Owner: Willem.
