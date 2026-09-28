# The extensible gate: checks opened to layers, the vocabulary bound

Status: phase 0 approved and landed 2026-09-05 (issue #227), pulled
ahead of the docs prepass so that plan's emitter tests pin the bare
`fm` spelling. Phases 1 to 7 are drafted and await Willem's
review, phase 4 having split into 4 and 4b because its two halves
block on different things. The design section below is written to
graduate into `packages/workshop/docs/` after review; everything
else is working record.

## Why

The workshop's stated goal (0903 plan, decision record): an
agnostic core anybody can extend without losing their own branding
and customisation. The kind registry delivered that for build,
render, and tools. The quality gate is the one axis still closed:
`_quality.py` hard-codes the verb list and `_backends/_python.py`
hard-codes the tools inside each verb. Someone who wants three
type checkers instead of four, a different linter, or coverage for
a C++ package has no seam. They fork.

The same discussion surfaced a naming debt (the package concept
has two names, `type` in the contract and kind everywhere else)
and a boundary ruling (what may be a `workshop.toml` option versus
what must be layer code) that this plan lands together with the
seam, because the seam's vocabulary is wrong to build on an
unbound word.

## The design, as the documentation will state it

This section is the concept written in the present tense, as the
shipped docs will carry it. Review edits happen here; after
review it moves to `packages/workshop/docs/` and this plan keeps
only a pointer.

---

### How the workshop decides, and how you change its mind

The workshop makes decisions so a project does not have to: which
tools gate a merge, how a package builds, what its documentation
looks like. Every one of those decisions is replaceable, and they
are all replaced the same way: by code in a layer, never by a
settings switch.

**Three parts serve many projects, over time.** The workshop does
not stand alone. footman is the task surface (`fm`), toolroom
holds the typed tool handles and the tool store, and the workshop
carries the contracts naming what each project needs. Together,
under a brand's name where a brand exists, they provide the
shared tools, environment, and caches for every project on a
machine: multiple projects, related or not, each resolving the
tool versions its own commits pin, over the whole life of those
projects. Nothing that can be shared is installed per project,
and nothing shared ever couples two projects, because each
checkout resolves its own pinned era from the store.

**Facts live in the contract, opinions live in layers.** A
`workshop.toml` carries facts about one workspace or one package:
its name, its kind, its dependency edges, its coverage floor.
A coverage floor is a fact because it is a measured high-water
mark of that package. "Private members are not documented" is not
a fact about a package; it is an opinion about what documentation
is for, so it lives in the layer that owns the documentation
voice, as code. This boundary is what keeps every instance's gate
reproducible: two checkouts of the same commit always judge the
same way, because nothing outside the committed contract and the
mounted layers can change a verdict.

**Layers activate by being listed.** The `[workspace]` table's
`layers` list is the only activation record. Installing a wheel
never activates anything: an installed but unlisted layer is
visible to `fm doctor`, which names it as available, and it does
nothing else. This is deliberate. If installation activated
extensions, `uv sync` on two machines with different incidental
installs would gate differently, and the gate's core promise,
that `fm check` locally and in CI judge the same committed
bytes, would be gone.

**One command, in any machine state.** `fm check` is the gate's
whole spelling; `uv run` is never required. The entry contract is
the choke point that makes it true: it provisions uv, syncs the
environment against the lock, resolves the environment cascade,
and leaves bare `fm` working, in a shell and in a CI job alike.
Between entries every `fm` command reconciles: an environment
that drifted from the lock re-runs itself through uv before
judging, so the verdict always comes from the locked toolchain.

**A package has a kind.** The kind is the contract's `kind`
value (`python`, `python-nanobind`, `cpp-conan`), and it answers
how the package builds, what its template renders, what tools the
machine needs, and which check roles apply to it. Kinds are
registered records; a layer registers its own kinds at mount, and
an unknown kind refuses naming the registered vocabulary. In
prose the full name is "package kind" at first mention, because
the contract also names an edge kind (`[[depends]] kind`) and a
forge kind (`[forge] kind`); each is bound by its owner.

**The quality gate is a set of checks, grouped by role.** A
*check* is one tool's judgment: ruff's format pass, mypy on
linux, the render drift comparison. A *role* is what a check is
an implementation of: `format`, `lint`, `types`, `build`,
`test`, and the workspace roles such as `render`. The *gate* (`fm check`) is the
conjunction: every applicable check green, the exit code the
verdict. Kinds gate on roles, never on tools: a C++ kind says
"format applies", and whether format means ruff or clang-format
is the check's business, so swapping a tool never touches a kind.
A role is a set, not a slot: every applicable check of the role
judges the files it claims, so one package may be formatted by
two tools at once. `cpp-conan` is the standing example:
clang-format owns its C++ sources while ruff owns its
`conanfile.py`, and a `types` check claiming build scripts joins
the same way, closing the gap where `conanfile.py` today escapes
the type checkers.

Each check is one registered record, and the record is the single
place the tool exists:

- the callable that runs it, and the fix-mode callable when the
  tool can rewrite;
- which files it claims, named as classifications rather than as
  globs: a check says which kinds of file it judges and the kind
  decides what that means for each of its members. Two checks may
  claim one classification under different rules, which is how
  ruff judges a cpp-conan package's `conanfile.py` by one set and
  a python package's sources by another, derived from the claim
  instead of typed into a `per-file-ignores` table by hand. The
  classification is the file's part, from the registry below,
  never a table of the check's own;
- how it narrows under `--affected`: by explicit paths, by a
  package subset, or not at all (the whole is always checked);
- the configuration fragments the render manages for it, one per
  rendered file it has something to say in: its table in
  `pyproject.toml`, its settings in `.vscode/settings.json`, its
  id in `.vscode/extensions.json`. One owner, several files, and
  unregistering the check clears every one of them together;
- the tool it contributes to the derived profile. The tool store
  is machine-wide and shared across projects: each version lives
  in it once, side by side with its siblings, and the entry
  contract puts the versions this checkout's pins name on PATH,
  so an older checkout judges with its era's toolchain and two
  projects on one machine never hold two copies of one version.
  Only a tool that must import the project's environment (pytest
  and its plugins, coverage) rides the lock instead; a tool that
  only reads files never lives in a venv.

Adding a tool is one record in a layer's plugin. Removing one is
re-registering the role without it. A narrowed gate is always
visible: every skipped check prints its name and the reason, and
a layer that narrows a role is named in the gate's output, so a
lighter gate is a legible brand decision, never a silent one.

A fragment is contributed, never applied afterwards. The render
composes the base template with each registered check's fragment
for that file, in check-name order so two machines write the same
bytes, and the drift gate judges the result. A pass that rewrote
the rendered file instead would move the truth from the template
to the template plus the passes, and every rewrite of TOML or of
JSON with comments either loses the prose or dictates how it may
be written, which these files carry on purpose.

**What a file is, and who owns it.** The workshop asks two
questions about a path, and one file answers both. Its **part**:
what this is to the package, one of source, test, test support or
configuration. Its **channel**: who wrote it and where to edit it,
one of a layer's fragment, a rendered file, an emitted one, the
contract, or yours. `packages/forge/workshop.toml` is
configuration on the first and the contract on the second;
`packages/forge/src/livery/forge/_http.py` is source and yours.
Neither answer can be read off the other, so the two vocabularies
stay apart and are never merged into one.

Each is a registry that a kind or a layer registers into, through
the channel that already carries kinds and checks. Two registries
rather than one with an axis parameter: a file's part is a pure
function of its path inside a package, while its channel is read
from the delivery manifest, the emitted set and the template
source, so a single registry over both would answer `object` and
hand every rule a context that is the union of two unrelated
needs. What the two share is the walk, an ordered list of rules
where the first to claim a path wins and the caller supplies the
fallback. Same shape, without paying for that shape in types.

Today each is instead a closed ladder in one module. That is why a
layer can teach the workshop a new package kind but not what a
file of that kind *is*, and why `fm explain` called a tool receipt
a layer fragment until the directory it lived in was split.

Registered, not configured. The layout a kind expects is a fact
about the kind, so it is layer code like everything else here. One
package's local exception is a fact about that package, and the
contract carries it in the same narrow shape it carries a coverage
floor: these paths are not what the kind would assume. Anything
wider than that exception is a policy toggle, and the boundary
above refuses it.

**The editor answers with the gate's checkers.** A checker
configured in `pyproject.toml` is the same program in an editor
as on the command line, so the two agree by construction. A
checker configured somewhere else does not: Pylance reads
`[tool.pyright]`, which this render never writes, so beside
basedpyright it answers on its own defaults and an exclusion the
gate honours comes back as a finding. Which formatter runs is a
fact about the project and the check owns it; when to format is
the person's, and stays in their own settings. Silencing the
second opinion is neither: no check owns it, because it follows
from the whole registered set, that the type checker answering in
the editor is the one whose configuration this workspace writes.
The render derives that from the set, the way it derives the
profile.

**Documentation and coverage follow the same split.** Extraction
belongs to the kind: a Python kind extracts its API through
griffe, a C++ kind through its own extractor, and a binding kind
composes both. Policy belongs to the layer: whether private
members are documented, the voice, the site's tone. Assembly
belongs to the core: the per-package site shape and the publish
path. Coverage likewise: the floor is a contract fact in `[qa]`,
the measurement is the kind's answer (coverage.py for Python,
instrumented ctest for C++), and the floors, the grace, and the
enforcement are one core implementation over every kind's
numbers.

**What this does not cover.** A check runs a tool the layer
ships; the workshop does not sandbox it. Mounting a layer is
trusting its code, which is why activation is a committed,
reviewed byte and never an install-time side effect.

---

## Ground-truth contracts (do not violate)

1. `fm check` is the frozen seam: the bare command, typed in any
   machine state, and its exit-code verdict. `uv run` is never
   required, of a person or of CI. The entry contract is the one
   choke point: it provisions uv, syncs the venv against the
   lock, resolves the environment cascade, and leaves bare `fm`
   working; between entries the per-command reconcile keeps that
   true, re-execing through uv on a venv that drifted from the
   lock. Everything behind the command may change.
2. Two checkouts of the same commit with the same mounted layers
   produce the same verdict. Nothing ambient (installed wheels,
   environment, machine state) may widen or narrow the gate.
3. The `[workspace] layers` list is the only activation channel.
   Entry points may discover, never activate.
4. A narrowed gate prints what was narrowed and by whom. No check
   disappears silently; the skip-by-name discipline extends to
   layer-level narrowing.
5. Facts in `workshop.toml`, opinions in layer code. No check or
   docs policy toggle enters the contract vocabulary.
6. Kinds gate on roles, never on tool names. `CiContract`
   vocabulary validates against registered roles.
7. Fixing checks run serially before judging checks run in
   parallel, and the current gate's observable properties
   (member set, parallelism, skip prints, verdict) are pinned by
   test before any implementation is replaced.
8. Registration records grow additively: new fields carry
   defaults, and a mount-time API version check turns an
   incompatible layer into a sentence, not an `AttributeError`.
9. Bare "kind" never stands alone in published prose: "package
   kind", "edge kind", "forge kind" at first mention in every
   document.

## Phases

### Phase 0: the entry contract and the bare spelling

One choke point owns machine readiness: hse's entry contract,
ported in reduced form (livery has no tool store until the 0903
plan's phase 18). A rendered, template-managed entry script at
the workspace root ensures uv, syncs the venv against the lock,
resolves the environment cascade, and emits the environment: an
eval for a shell, a persisted emission for a CI job. Generated CI
replaces `uv sync --locked` plus `uv run --no-sync fm ...` with
the entry step followed by bare `fm ...` everywhere. Between
entries the pre-tasks reconcile keeps the promise: each `fm`
command compares the lock against the venv's sync receipt and
re-runs itself through uv on drift, so a pull that moved the lock
never judges from stale code. The reconcile lives in the
workshop's own pre-tasks hook; footman changes nothing (its uv
handoff already covers the outside-the-venv case). The entry
contract also pins the outer uv, the one tool that runs before
the lock can speak: today setup-uv installs latest at job time
and the curl legs do the same, so the bootstrap is the one
unpinned link in an otherwise locked chain. The prose sweep
replaces `uv run fm` with `fm` in every fragment, doc, and
template.

**Acceptance**

- The regenerated workflows contain no `uv run`, proven by grep
  over the rendered CI, and the conformance chain is green on
  them.
- A venv synced against an older lock re-runs through uv and
  judges current, proven by a forced test.
- `grep -rn "uv run fm"` over docs, fragments, and templates
  finds nothing.

### Phase 1: the kind binding rename

The contract key `type` becomes `kind`; `Package.type` becomes
`Package.kind`; `requires_pyproject` and every `type_name`
spelling follows. Discovery refuses a contract still carrying
`type` with the one-line migration ("rename `type` to `kind` in
workshop.toml"). The templates render the new key; the docstrings
that today collide ("Type-check every package with its type's
gating checkers") are rewritten under contract 9. Breaking, rides
a minor.

**Acceptance**

- `grep -rn '^type = ' packages/*/workshop.toml` finds nothing;
  `grep -rn '"kind"' packages/workshop/src/livery/workshop/_packages.py`
  finds the reader.
- A contract with the old key refuses with the migration line,
  proven by a test.
- `fm check` green; the conformance chain green.

### Phase 2: the check registry, builtins first

`CheckRecord` and `register_check` beside the kind registry, with
role, scope (workspace or per-package), narrowing behaviour, fix
mode, and exclusivity notes. A frozen `GateContext` (root,
packages, subset, git) is the run signature. The eight current
gate members re-register as the first checks with nothing
special-cased; `check` and `_scoped_check` become one walk over
the registry. Before the replacement, the pinning tests of
contract 7 land against the current implementation and survive
the swap unchanged. `CiContract.check_verbs` validates against
registered roles, refusing unknown names with the vocabulary.

One of the eight is not a check but a router: `fm kindcheck`
(`_quality.py`) reads each kind's `CiContract.kind_verbs` and
calls the kind backend's `check` per package. Under the registry
the walk is the router, so the router retires: the `kindcheck`
task, `run_kind_checks`, `CiContract.kind_verbs`, and
`Backend.check` all go, and the python and python-nanobind
backends lose their no-op `check`. What the router dispatched
becomes check records the cpp-conan kind registers, scope
per-package, narrowing by package subset: `configure` and
`build` under a `build` role, `ctest` under `test`. The `build`
role joins the design section's role list. A kind contributes
checks the same way a layer does, through `register_check`, so
the second list on the kind contract has no reason to exist.
The gate output for a cpp-conan package changes from one
`kindcheck` line to one line per check; the skip-by-name lines
for the roles that do not apply stay as they are. Adding this
before phase 2 would build a second interim shape, so it lands
inside the swap.

**Acceptance**

- The pinning tests pass before and after the swap, proven by
  running them at both commits.
- `fm check` output names the same members and skips as
  before, and `--affected` narrows identically. The one named
  change: a cpp-conan package prints `configure`, `build`, and
  `ctest` as three per-package checks instead of one `kindcheck`
  line, proven by the conformance workspace's gate output.
- `grep -rn 'kindcheck\|kind_verbs\|run_kind_checks'
  packages/workshop/src` finds nothing.
- A test registers a fake check and sees it run, narrow, and
  skip by name.

### Phase 3: layers register checks, doctor discovers

A layer's plugin calls `register_check` at mount, the same
channel as `register_kind`. Re-registering a name replaces it,
which is how a layer swaps or drops a tool; the gate output names
the layer that narrowed (contract 4). Mount checks the plugin
API version (contract 8). `fm doctor` learns entry-point
discovery: installed check or kind plugins that no layer mounts
are listed as available, activating nothing (contract 3).

**Acceptance**

- A test layer drops one checker and adds a fake one; the gate
  output names both moves, proven by a conformance-suite test.
- An installed-but-unlisted plugin appears in `fm doctor` output
  and changes no verdict, proven by a forced test.
- A layer declaring an incompatible API version refuses at mount
  with the version named.

### Phase 4: the check owns its configuration and tool

Everything a check owns that does not depend on what a file *is*.
Split from the claim, which follows, because the two halves block
on different things: this one needs only the registry from phase
2, while the claim needs the part registry whose design is open.
Joined, the editor work waits on a question it has nothing to do
with.

The record gains its configuration fragments and its tool-profile
contribution, moving both out of their current homes. A fragment
names the rendered file it goes in, so one record reaches
`pyproject.toml`, `.vscode/settings.json` and
`.vscode/extensions.json` at once; the render composes the base
template with the registered fragments in check-name order, and
the drift gate judges the result through the same managed-union
mechanism kinds use.

Ruff (format and lint) is the proof: its rendered configuration,
its editor settings, its recommended extension, its version pin,
and its profile entry all derive from its two check records, so
removing the records removes every trace.

Two things belong to the set rather than to any check, and the
render derives them the way it derives the profile: the type
checker that answers in an editor is the one whose configuration
this workspace writes, so the second opinion's language server is
turned off, and the extensions recommended are those of the
registered checks. Closes livery#779.

**Acceptance**

- Unregistering the ruff checks in a scratch workspace leaves no
  ruff configuration in the render, no ruff in the rendered
  editor settings or extension recommendations, and no ruff in
  the derived profile, proven by a test.
- The drift gate catches a hand-edited check-owned fragment,
  proven by a forced test.
- A rendered `.vscode/extensions.json` names only extension ids
  that resolve, proven by a test reading the ids from the
  registered checks.
- A rendered project's editor and its gate answer alike on a file
  the gate excludes, proven by a test over the rendered settings.
- `fm check` green, output unchanged.

### Phase 4b: the check claims its files

The other half, and the one that waits. A check record gains its
claim, named in the part vocabulary rather than as globs, and a
check judges the files its claim reaches. Ruff is the proof
again: the rules it applies to a package's configuration files
differ from the rules it applies to sources, and the rendered
`per-file-ignores` is generated from the claim rather than
written by hand.

This phase needs the part registry, whose design is open item 9,
and lands after the phase that builds it. Building the claim on
today's per-kind `classify` instead would be the second shape
this plan then replaces, which is the argument that retired
`kindcheck` inside phase 2.

**Acceptance**

- Two checks claiming one classification under different rules
  render one `per-file-ignores` table with both, in a stable
  order, proven by rendering twice and comparing bytes.
- A check judges every file its claim reaches and no other,
  proven by a fake check over a fixture package of each kind.
- `conanfile.py` is judged by the checks that claim
  configuration, closing the gap the design section names.
- `fm check` green, output unchanged.

### Phase 5: the test role and coverage measurement by kind

The test role joins the registry, and measurement becomes the
kind's answer: a backend returns per-package path-to-percent for
its run. Python answers through coverage.py, unchanged.
`cpp-conan` runs ctest under llvm-cov and reduces to the same
mapping; its `[qa] coverage_floor` is enforced by the same core
floors, grace, and prose. The CI union step merges per-measurer
(coverage.py combines its files, llvm merges profdata), then one
enforcement over the combined answers. Windows MSVC coverage has
no gcov-shaped answer; it is deferred and the deferral is an open
line here, not a silent gap.

**Acceptance**

- A C++ package below its floor fails `fm coverage.enforce` with
  the same prose Python gets, proven by a forced fixture.
- The CI union job merges both measurers' data and enforces
  once, proven by the conformance chain.
- A kind without a measurer skips coverage by name, never
  vacuously passes.

### Phase 6: the documentation seams

Extraction moves to the kind record (Python's griffe wiring is
the first implementation), policy to layer registration, assembly
stays in `_docs.py`. The proving feature is the one that started
this: a layer that turns off private-member documentation does it
in its plugin, in a few lines, touching no contract vocabulary. A
C++ extractor is out of scope here; the seam ships with Python
proving it and the kind record able to say "no extractor,
documented as absent".

**Acceptance**

- A test layer flips the private-members policy and the rendered
  site reflects it, proven by a docs-build test.
- A kind without an extractor produces a site section naming the
  absence, never an empty page.
- The livery site's content is unchanged by the seam: the site
  is built before and after, the builds are diffed, and every
  difference is named and ruled in the phase record. An empty
  diff is the expected outcome, not the requirement.

### Phase 7: the conformance kit

`livery.workshop.testing` ships the pinning tests a third-party
kind or check plugin must pass: the backend protocol, skip
printing, narrowing behaviour, fix ordering, config-fragment
drift. The workshop's own kinds and checks run the same kit, so
the kit cannot drift from the enforcement.

**Acceptance**

- The builtin python, python-nanobind, cpp-conan kinds and every
  builtin check pass the kit, wired into `fm check`.
- A deliberately broken fake plugin fails the kit with the
  violated clause named, proven per clause.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The design section above | its `packages/workshop/docs/` page, after review |
| The reduced entry contract (phase 0, no tool store) | the shared tool cache's entry contract (0903 plan phase 18) |
| The POSIX-only entry script | a pwsh spelling, at the tool-store port; CI's windows leg runs `setup.sh` under the runner's bash until then |
| The hand-pinned setup-uv version in nightly.yml and release-legs.yml | emitter-derived pins, when those workflows become generated |
| The argv[0] probe deciding which process reconciles | footman's own real-invocation marker, when footman joins the workspace |
| The gate tools pinned in the rendered dev group | toolroom's versioned store, once it exists |
| Test role builtin wiring (phases 2-4) | the registered test role (phase 5) |
| Windows C++ coverage deferral (phase 5) | a ruling once an MSVC toolchain answer exists |

## Decision record

- 2026-09-05, `uv run` is never required and the entry contract
  is the choke point (Willem): bare `fm check` for people and CI
  both. Willem's reasoning: a choke point is needed anyway to
  set the environment variables and provision the tools, so the
  same point guarantees the environment is current and activated.
  hse's entry contract is the reference shape; livery ports it
  reduced (no tool store until the 0903 plan's phase 18) and adds
  the per-command reconcile in the workshop's pre-tasks hook, so
  the one gap footman's uv handoff leaves (the already-inside
  venv that imports but is stale) closes in workshop code, not in
  footman.
- 2026-09-05, the three-part stack (Willem): workshop, toolroom,
  and footman together, potentially branded, provide the shared
  tools, environment, and caches for multiple, potentially
  unrelated, projects, over time. The store's machine-wide,
  versions-side-by-side shape is one consequence; the entry
  contract and the env cascade are others. Sharing never couples
  projects: each checkout resolves its own pinned era.
- 2026-09-05, tools leave the venv (Willem): a tool that only
  reads files never lives in a venv. The gate's tools sit in the
  rendered dev group today only because toolroom has no store
  yet; that home is interim. Willem's reasoning: per-venv copies
  mean one copy of every tool per worktree or checkout once
  hardlinking degrades (a separate or degraded filesystem copies
  wholesale), and one uv cache clear re-downloads and re-queries
  everything from the index. The store is machine-wide and shared
  across projects: it holds each tool version once, versions side
  by side, and two projects pinning the same version share the
  one copy. The entry contract
  puts the versions the checkout's committed pins name on PATH:
  an older checkout resolves the tools in use at its commit and
  judges with its era's toolchain. The lock keeps only what must
  import the project's environment. The store is a new toolroom capability, hse's
  `tool@version` shape, and the 0903 plan's phase 18 (shared tool
  cache) is implemented on it. An earlier lean to collapse kind
  tools into the lock is reversed by this ruling.
- 2026-09-05, the gate opens by registry (Willem, from the
  discussion): the kind-registry pattern applied to quality;
  checks, roles, and the gate as the vocabulary. Configuration is
  code in a layer, never a contract toggle; the private-members
  documentation option was held back on instinct and this
  boundary is the articulated reason.
- 2026-09-05, activation is the layers list (Willem: "I always
  meant to list the active ones, auto activation would break
  every reproducibility guarantee we've ever strived for").
  Entry points discover for `fm doctor`; they never activate.
- 2026-09-05, the vocabulary binds (Willem: unhappy with bare
  "kind", and with "gate" less strongly): the fix is binding,
  not replacement. "Package kind" is the full name and the
  contract key renames `type` to `kind` to anchor it;
  `PackageType` was rejected because the word type already works
  full-time in this codebase for static typing, colliding inside
  single sentences. "Gate" stays, bound as "quality gate" at
  first mention, and earns its place in the check/role/gate
  triad.
- 2026-09-05, a role is a set (Willem's question, answered into
  the design): multiple checks of one role apply to one package,
  each claiming its own files, so cpp-conan keeps ruff on
  conanfile.py beside clang-format on its sources, and a types
  check over build scripts becomes possible without touching any
  kind.
- 2026-09-05, coverage reaches C++ (Willem): the floor is
  already a kind-agnostic contract fact; measurement becomes the
  kind's answer and enforcement stays one implementation.
- 2026-09-05, documentation splits three ways (from the
  discussion): extraction is the kind's, policy is the layer's,
  assembly is the core's.
- 2026-09-10, `kindcheck` retires inside phase 2 (Willem): the
  task is a router over `kind_verbs` and `Backend.check`, the
  kind hierarchy plan's pre-registry way for a kind to supply
  per-package checks. The registry walk is that router, so the
  task, the second list on the kind contract, and the protocol
  slot go together, and cpp-conan registers its three checks
  under `build` and `test`. Not a separate change: doing it first
  would build an interim shape the swap then replaces.
- 2026-09-05, phase 1 waits for nothing (Willem): #218 and #220
  are closed, `fm issue.list` shows only #195 (docs content
  pass) open, so the kind rename starts when the plan is
  approved.
- 2026-09-05, phase 6 acceptance relaxed from byte-identical
  (Willem): the site comparison judges content, and differences
  are named and ruled rather than forbidden outright.
- 2026-09-05, phase 0 approved alone and pulled ahead of the docs
  prepass (Willem): the prepass's emitter tests then pin the bare
  `fm` spelling instead of pinning `uv run fm` and changing later.
- 2026-09-05, the entry spelling ruled (Willem): `setup.sh` at the
  workspace root, leaner than hse's `setup/` directory, which earns
  its keep only when the tool-store port fills it. POSIX only for
  now: the CI windows leg runs it under the runner's bash, and the
  pwsh spelling is deferred to the tool-store port. Resolves open
  item 5's spelling half; the script serves both entry shapes
  (sourced for a shell, `github` for CI), and the per-command
  reconcile alone carries a shell that never sourced it once the
  venv exists.
- 2026-09-05, phase 0 scope widened to the hand-maintained
  workflows: nightly.yml and release-legs.yml take the entry step
  and lose `uv run` with the emitted files, their setup-uv pin
  following uv.lock by hand until they are generated.
- 2026-09-05, phase 0 deviation from hse, named: livery has no
  console script of its own, so the reconcile's am-I-a-real-run
  probe reads the runner's name from `argv[0]` instead of hse's
  `cli.main` marker; the marker shape returns with footman's
  migration.
- 2026-09-05, the reconcile re-runs only when the sync changed
  installed code (the dist-info delta), not on every drift: a
  no-op sync leaves nothing stale, and restarting on it would add
  a process start to every post-pull command for no repair.
- 2026-09-05, phase 0's chain acceptance ran with one deselection:
  the release rehearsal's own drift test fails on main already (the
  rehearsal's probe commit enters the docs-derived zensical nav,
  issue #228, found live during this phase). The branch carries no
  commits, so the rehearsal's clone was byte-for-byte main and the
  fault predates the phase.
- 2026-09-09, five refinements from a fresh derivation (Willem and
  the agent independently re-derived this note's architecture while
  routing the post-edit hook off a hard-coded ruff call, issue #312;
  the re-derivation validated the design and added detail the phases
  fold in):
  - `--safe-fix` is a first-class flag on every task that takes
    `--fix`, documented once as "safe to apply to in-progress
    edits". It applies fixes but withholds the code-removing rules,
    and what it withholds lives only in the lowest linter seam (for
    ruff, `F401`), never named above. `--fix` and `--safe-fix` are
    mutually exclusive with a teaching refusal. Landed minimally in
    #312 for python (`fm lint`/`fm format` gained `*paths` and
    `--safe-fix`, the hook calls the verbs); the check record's
    fix-mode callable (phase 4) generalises it per tool.
  - The config fragment a check owns (phase 4) is overridden the
    way every content fragment already is: whole file, last layer
    wins, through the existing `content/fragments` cascade in
    `_sync.py`. Overriding a tool's config in a layer means shipping
    the whole config and owning all of it, never a merged delta.
    Deep-merge of deltas is a possible later extension and the one
    hard part (TOML merge determinism); start whole-file.
  - Two scaffolding verbs: `fm layer.new` scaffolds a layer, and
    `fm layer.override <file>` copies the currently composed result
    of a file into the layer being edited (an explicit layer
    argument, not an inferred one), so an override starts from the
    real current content. Slots beside phase 3.
  - Per-package enforcement is structural, not a bolted-on check:
    each package's composed config emits into that package's own
    directory, so tool-native discovery (ruff, mypy, and the editor
    all walk up from a file to the nearest config) lands on the
    owning package's config by construction. A file cannot be
    judged by another package's rules. The drift gate adds two
    cheap checks on top: every package's emitted config matches its
    composition, and no source file is orphaned or ambiguous. This
    moves phase 4's "render manages the fragment" from one root
    `pyproject.toml` to per-package emission; the cost is more
    config files, every one derived and drift-checked.
  - A package is a layer on top of its package kind. The cascade
    gains two inner rungs below the workspace layers: the kind (as
    a layer contributing its toolset and default config as
    fragments, which today it does not, it ships only templates),
    then the package (as a layer contributing its own overrides).
    Composition resolves once per package with the package
    innermost, which is what makes per-package emission fall out.
    Unifies this note's role-gating (phase 2) with per-package
    config; the composition-per-package is the structural change
    phases 4 and 5 build on.
- 2026-09-27, a check owns its editor settings too, and knows what
  it claims (Willem: "each tool should know how it should behave
  for which rules and which classification, and it should be able
  to adjust its tool configuration or vscode settings"). Phase 4
  widens rather than gains a phase: a record carries a fragment
  per rendered file, so `.vscode/settings.json` and
  `.vscode/extensions.json` join `pyproject.toml` under one owner,
  and it carries a claim in classifications, so a tool's rules per
  classification are derived rather than hand-written. Of the two
  spellings offered, contributed fragments over post-processing
  the rendered file: the rendered files carry explanatory prose,
  and a rewriting pass either loses it or dictates it, and the
  truth would move from the template to the template plus the
  passes. Two editor facts belong to the registered set rather
  than to any check and the render derives them: which type
  checker answers, and which extensions are recommended
  (livery#779, filed to be fixed by hand and folded in here
  instead).
- 2026-09-27, "no consumer in this repository yet" is not a
  reason to defer (Willem, after the agent argued the editor
  mismatch "bites downstream before it bites here"): this
  repository is where the template is written and a born project
  is the consumer, so an absent local instance is the state before
  the option ships, never evidence of low demand.
- 2026-09-27, path classification is one shared, extensible
  mechanism (Willem: "the classification of fm.explain and for the
  tools should be shared, and configurable", and on the sketch
  below, "that is the goal, but I think it needs refinement
  still"). The direction is ruled and the design is not: what is
  settled is that the axes stay separate (a file is both
  `configuration` to its kind and `rendered` to the channel, and
  merging the vocabularies would lose one of those), that the
  mechanism behind them is one registry a kind or a layer
  registers into, and that the layout a kind expects is layer code
  with only a narrow per-package exception in the contract. What
  is not settled is open item 9. The phase lands before the
  present phase 4, which is where the first new consumer is; the
  numbering is left alone until the design settles, because a
  third of this note's phase references are dated decision-record
  lines that must keep meaning what they meant.
- 2026-09-27, two registries rather than one (Willem: "B for
  now", choosing between one registry taking an axis parameter and
  two that share only their walk). The axes have the same shape
  and nothing else: a part is a pure function of a path inside a
  package, a channel is read from the delivery manifest, the
  emitted set and the template source, and their answers are a
  label and a three-field record. One registry over both would
  type its answers as `object` and hand every rule a context that
  is the union of two unrelated needs, which is paying in types
  for a shape. Each registry keeps its own types and its own
  fallback, and the ordered walk they share is a helper.
  Promotion to the single registry stays mechanical, a dictionary
  key, and earns its keep when a third axis is real rather than
  imagined.
- 2026-09-27, the site build's condition belongs to this plan
  (Willem: fix it as part of the extensible gate plan, and take it
  into account in the classification first). A pull request that
  changes only a note pays a full site build: measured across 299
  runs in the CI metrics store, the `Docs` step has a median of 162s,
  a minimum of 79s and a maximum of 182s, and on one notes-only pull
  request the docs job was 190s while the three check legs finished
  in 25s to 57s and `gate` sat queued 199s waiting for it. The
  unconditional run is not an oversight: `is_site` and `is_prose`
  both justify their files reaching no format, lint, type or test
  gate by the site build running on every run, so narrowing the
  suites is safe because the site build is not narrowed. The gap is
  that `notes/` is the one class of markdown the site does not read,
  so a notes-only change is judged by nothing and still pays the
  build. Filed as livery#839; the classification design accounts for
  it before the skip is written.
- 2026-09-27, the axes are named **part** and **channel**
  (from the discussion). `channel` was already the field
  `fm explain` prints, so it needs no new word. `part` is new and
  avoids two collisions the obvious names walk into: `type` is
  what contract 9 is freeing from the contract, and `source` is
  an answer *on the part axis*, so a `classify_source` returning
  `yours` for a file whose part is `source` reads as a
  contradiction.
- 2026-09-28, phase 4 splits (Willem's question, whether livery#779
  needs the whole plan first). It needed less of it than the
  parking suggested: every one of its deliverables, the language
  server setting, the extension ids, ruff as the formatter and the
  provenance plumbing, is a fragment or a fact about the registered
  set, and none of them classifies a file. So the two halves block
  on different things. Phase 4 keeps the fragments and the
  tool-profile contribution and needs only the registry from phase
  2; phase 4b takes the claim and waits on the part registry and
  open item 9. Joined, the editor work waited on a design question
  it has nothing to do with, against the rule that a phase lands
  alone.

## Open

1. Does `Edge.kind` rename too, or does "edge kind" bound by its
   table satisfy contract 9? Current lean: keep it, bound.
   Owner: Willem.
2. How long do `typecomplete` and the release-path checks stay
   builtin roles a layer cannot drop? The release train leans on
   typecomplete; dropping it may need its own ruling. Owner:
   Willem.
3. The Windows MSVC coverage answer (phase 5 deferral): llvm-cov
   via clang-cl, or documented absence? Owner: Willem, when a
   consumer exists.
4. Where the graduated design page lives:
   `packages/workshop/docs/` under what name, and whether the
   facts-versus-policy boundary also enters the hse-imported
   guidance fragments. Owner: Willem.
5. Resolved 2026-09-05: `setup.sh` at the root, sourcing optional
   (see the decision record). What stays open is the pwsh spelling,
   deferred to the tool-store port. Owner: Willem.
6. The venv-side remainder, once toolroom's store exists: which
   tools must stay in the lock because they import the project's
   environment (pytest and its plugins, coverage certainly), and
   whether mypy and basedpyright run from the store pointed at
   the venv's interpreter or stay locked beside it. The store's
   committed pin home and its update flavor of
   `fm workflow.update` are decided with the store itself.
   Owner: Willem.
7. A fragment can only add. When a check needs a line the base
   template already wrote to say something else, the answers are
   the whole-file override the 2026-09-09 record settles for a
   layer, or splitting the base template until the line is a slot
   nobody else owns. Which one applies to a check rather than a
   layer is undecided, and deciding it late means discovering it
   as a fragment that cannot be written. Owner: Willem, with
   phase 4.
8. Which extensions a registered check names, and what the render
   does with a check whose tool has none. Every id shipped is a
   claim about a marketplace entry that this repository cannot
   verify offline, so the check record either carries a verified
   id or carries nothing and the editor keeps quiet about that
   tool. Owner: Willem, with phase 4.
9. The part and channel registries' remaining shape. The decision
   record settles two registries, their names, their types and the
   shared walk; these are what is left:
   - **Whether a part rule may be data rather than a callable.**
     Nothing about a part reads state, so that axis could take a
     pattern table, which is easier to read and to render into
     documentation. A callable on both keeps promotion to a single
     registry mechanical. Symmetry against legibility, and only
     this axis has the choice.
   - **How rules from two layers order.** Most specific wins is
     easy to say and needs defining over callables. Whatever it
     is, two checkouts of one commit must order alike.
   - **How far the per-package exception reaches**, in the shape a
     coverage floor already has. A vendored tree that is not
     source is the motivating case; "these paths are not what the
     kind would assume" is the bound to hold it to. It applies to
     the part axis; whether a package may say anything about its
     own channel is a separate question, and the lean is no.
   - **Whether both existing ladders migrate at once.** Each
     becomes the builtin rules of its own registry, which is the
     natural shape; doing one first leaves two mechanisms for a
     while and proves less.
   - **What `fm explain` prints** now a path has a part and a
     channel, and whether it names the layer that supplied each.
   - **When the single registry earns its keep.** A third axis is
     the trigger; "is this a documented surface" from the docs
     phase is the nearest candidate, and "does the site read it",
     below, is a second. Naming the trigger now keeps the promotion
     a decision rather than a drift.
   - **Which axis answers "does the site read it"**, which the
     docs job's condition needs (livery#839). Four things constrain
     it. The predicate is paths under `notes/` alone, strictly
     narrower than `is_prose`, which is "under `notes/`, or a
     markdown file anywhere" and so also covers `README.md` and
     every package's `docs/` markdown: those are published and the
     site build is their only gate, so skipping on `is_prose` would
     delete it. The paths it must answer for include workspace-root
     ones (`notes/`, `zensical.toml`, the root `docs/`) as well as
     package ones (`packages/*/docs/*.md`), while a part is defined
     as a pure function of a path inside a package, so this case
     tests that boundary rather than fitting inside it. `gate` is
     the one required context and depends on `docs`, so a skipped
     docs job must leave the context reportable. And the answer is
     not an opinion, so it is not layer code by default: whether
     the site reads a path is a fact about the render.
   Owner: Willem. Blocks phase 4b.
