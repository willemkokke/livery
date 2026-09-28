# The extensible gate: checks opened to layers, the vocabulary bound

Status: phase 0 approved and landed 2026-09-05 (issue #227), pulled
ahead of the docs prepass so that plan's emitter tests pin the bare
`fm` spelling. Contract 7's pinning tests landed 2026-09-28 (commit
94096846). Phase 1 landed 2026-09-28 (issue #860, pull request
#866); phase 2 landed the same day (issue #867, pull request #869,
its ordering fix #870); phase 3c was built the same day (issue #874)
and phase 8's first change, the publishing opt-out, too (issue #871).
Phases 3, 3b, 4 to 8 are drafted and await Willem's review: phase 4 split
into 4 and
4b because its two halves block on different things, and the
revision of 2026-09-28 added phase 3b (the part and channel
registries, which 4b waits on), phase 3c (the regions a repository
owns in its managed files, ruled generic that day) and phase 8 (the
house layer, which that day's base-and-house ruling calls for),
folded the day's other
rulings into the design, contracts 10 to 12, phases 3 and 4, and
the open list, and corrected one claim of the 2026-09-09 record. The
design section below is written to graduate into
`packages/workshop/docs/` after review; everything else is working
record.

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

The seam also has to separate two things one wheel ships today.
The workshop's defaults and livery's house convention travel
together: the base layer's fragments carry the voice rules imported
from hse, and the base template's `pyproject.toml` carries four
type checkers. A project that wants the workshop without livery's
opinions has no layer to leave out. The ruling of 2026-09-28 is
that the base registers a small check set and a house layer adds
the rest, so the proof that the seam works is a house layer that is
thin and a base that gates green without it.

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
its name, its kind, its dependency edges, its coverage floor,
whether it publishes.
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

**The base operates; languages and houses are layers.** The
workshop's base layer carries the engine and what operates it: uv
and the entry contract, the root project and its `tasks.py`, the
render and the drift gate, the gate's walk, the abstract base kind
with the changelog engine, and the workspace's own checks (render
drift, provenance, layering). It registers no language. Each
language is a layer derived from the base: `livery.workshop.python`
carries the python package kind, its template, its checks
(formatting, lint, one type checker, the tests with their coverage
floors) and its tools; `livery.workshop.cpp` carries the C++ kinds
the same way. A house is a layer on top of the languages it wants:
a second and a third type checker, a docstring convention, a voice,
each a registration or content, listed by the project that wants
it. The base is abstract, the way the base kind is: it heads
every stack, carries what every layer needs, and is used by nobody
alone. A workspace listing the base alone registers no kind and can
create no package; it is not a thing anyone uses. What the base is
for is the boundary: the core stays independent of every language
and every layer, and the proof is static, in the core's own gate.
The layering lint refuses an import from the core into a language
layer, dependencies pointing down from a layer into the core and
never back, and a vocabulary test refuses a package kind's or a
check tool's name in the core's own modules; the interpreter that
operates the workshop is the base's own. The conformance suite's
base-alone birth
proves only that the engine renders and gates with nothing listed,
its rendered `tasks.py` judged by the drift gate byte for byte.

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
- the configuration the render manages for it, one fragment per
  rendered file it has something to say in: its table in
  `pyproject.toml`, its settings in `.vscode/settings.json`, its
  id in `.vscode/extensions.json`. A fragment belongs to the check
  and to the package kind together, because one tool wants
  different defaults under different kinds (clang-tidy under a
  binding kind, under a plain C++ library, under a future engine
  kind), and it resolves down the kind chain the way a kind's tools
  and managed files do: the nearest kind's fragment wins, and a
  kind without one inherits its parent's. One owner, several files,
  and the configuration exists exactly while the check is
  registered: unregistering the check removes every fragment from
  the next render, and a file the render wrote for that check alone
  is removed with it, only when the bytes on disk are the bytes the
  render wrote. An edited one is a local override, kept and named,
  the rule the sync verb already applies to delivered content;
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
re-registering the role without it. A layer whose verbs need a
tool with no check involved declares the tool at the same mount,
the way it registers a kind or a check, and the derived profile
takes it beside what the kinds and the contracts require. A
narrowed gate is always visible: every skipped check prints its
name and the reason, and a layer that narrows a role is named in
the gate's output, so a lighter gate is a legible brand decision,
never a silent one.

A fragment is contributed, never applied afterwards. The render
composes the base template with each registered check's fragment
for that file, in check-name order so two machines write the same
bytes, and the drift gate judges the result. A pass that rewrote
the rendered file instead would move the truth from the template
to the template plus the passes, and every rewrite of TOML or of
JSON with comments either loses the prose or dictates how it may
be written, which these files carry on purpose.

**Where a tool composes its own configuration, the render uses
that.** Tools differ in how they find their configuration, and the
render follows each one rather than building a second mechanism. A
tool that reads one file per project (basedpyright, mypy, ty,
pyrefly, pytest, coverage) gets its section in the shared rendered
file, and what varies by package inside it is written the way the
tool itself spells variation: an execution environment, a module
override, a per-path override table. A tool that searches upward
from each file it judges (ruff, clang-format, clang-tidy, git's
ignore rules) gets a file where it looks, so a kind's configuration
lands in the package, and a package's own additions have two
homes, the managed file's own region or the tool's own inheritance
(`extend` for ruff, `InheritParentConfig` for clang-tidy and
clang-format, `include` for CMake presets), never a merge of ours.
The check record says which shape its tool has, and the render
emits accordingly.

**Two ways into a managed file, and they are different.** A
layer's section composes at render: a fragment, contributed the way
a check's is, ordered and byte-stable; the forge layer's
`.forge.dev.env` rule in the root `.gitignore` is one, written today
into the base template where it does not belong. A repository's own
region is state that must survive a render, and it is a feature of
every managed file, not of one format: a pair of marker comments the
render writes, with the repository's lines between them. The render
reads each region from the committed file as an input, the way it
reads the answers, and writes it back in place, so the drift gate
still compares whole bytes, and an edit outside a region, or a
removed marker, is drift like any other. The root `pyproject.toml`
carries the repository's own tables and its own dependencies this
way, the root `.gitignore` its own rules, `tasks.py` its own tasks,
and a package's managed files the same. What a region may say is
the format's business: in TOML a tail region adds whole tables and
array entries and never a key inside a rendered table, so a tool's
own `extend-` spelling is used where it has one. A format without
comments still carries the repository's content, appended: the
render knows its own bytes, so the region is the unmarked tail
after them. A format whose grammar closes, a strict JSON object,
takes no tail and uses its own include instead, as CMake presets do
with `CMakeUserPresets.json`. Where a format also composes itself,
a package may use that instead: its own `.clang-tidy` beneath the
managed one, with `InheritParentConfig`. `fm explain` says which
form a managed file carries and where its lines are, and a drift
report says where the repository's own lines belong, so the refusal
teaches the seam instead of only naming the file.

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
10. The base layer is abstract: it carries the engine and what
    operates it, registers the workspace checks alone, ships no
    language kind and no house convention, and is used by nobody
    alone. Each language and each house is a layer depending on
    the base, never the reverse: the core imports no layer and
    names no package kind and no check tool, and the layering lint
    and a vocabulary test refuse both on every change. The
    interpreter, uv and the venv that operate the workshop are the
    base's own and stay named.
11. A check's configuration exists exactly while the check is
    registered, resolved per package kind down the kind chain. The
    render removes a file it wrote for a withdrawn check only when
    the bytes on disk are the bytes it wrote; anything else is kept
    and named.
12. Where a tool composes its own configuration (upward search,
    `extend`, `InheritParentConfig`, `include`), the render uses
    that composition and builds no second one.
13. A managed file may carry content the repository owns: named
    regions between marker comments, or an appended tail where the
    format has no comments. The render reads it from the committed
    file and writes it back in place, so it survives every render;
    everything else, the markers included, is judged as rendered
    bytes. `fm explain` names which form a file carries, and a
    drift report says where the repository's own lines belong.
14. The base templates aim to be generic enough that a house layer
    needs no overlay, so most projects need no template repository.
    A house customises a rendered file through a fragment, a region,
    or content, all of which travel in its wheel; an overlay stays
    the declared exception for what those cannot say, because it
    makes its home the template publisher and costs it an artifact
    repository. What a house needs that no fragment or region can
    say is first treated as a gap in the base template; open item 16
    carries what remains.

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
the registry. The pinning tests of contract 7 are in, against the
current implementation, and are the phase's own regression suite:
a failure there is either a lost property or a change this note
records with its reason. `CiContract.check_verbs` validates
against registered roles, refusing unknown names with the
vocabulary.

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

The pinning tests of contract 7 are in place: the four
`test_the_whole_gate_is_eight_members_in_one_parallel_block`,
`test_the_fixing_gate_rewrites_serially_then_judges_in_parallel`,
`test_the_judges_read_the_tree_the_rewriters_left` and
`test_one_refusing_member_is_the_gate_s_verdict` in
`packages/workshop/tests/test_workshop_quality.py`. The `Backend`
protocol has two members beside `declared_requirements` that this
phase keeps, `module_roots` and `referenced_siblings`: they are
kind knowledge the layering lint reads, not the `check` member the
router used. hse has no registry to port here: its `quality.py`
names its gates in code, so the registry is livery's own, and the
compare against hse is of the gate's composition (rewriters serially,
judges together), which the pinning tests hold.

The first fix-mode check after the swap is livery#829, the layering
lint's `--fix` that writes the `[[depends]]` edge and the native
requirement for a sibling the graph already reaches. It is a
workspace-scoped record with a fix mode, landing in its own change
right after the registry, so the registry's fix mode is driven by a
real check before a fake one.

**Acceptance**

- The pinning tests pass before and after the swap, proven by
  running them at both commits; a test edited to make the swap
  pass is named in the decision record with its reason.
- `fm check --fix` writes the edge and the requirement for a
  reachable sibling reference and still refuses an unreachable one
  (livery#829), proven by that change's own tests.
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

A layer also declares the tools its own verbs need, at the same
mount, so `_tools.requirements()` gains a fourth site beside the
kinds, the packages and the root contract: `tools.lock` resolves
them, the receipts record them, and `fm doctor` and `fm env.check`
name an absence. Two checkouts listing the same layers derive the
same profile, so the declaration is a registration at mount, never
an installed wheel's metadata (contracts 2 and 3). Today a layer
has nowhere to say it.

**Acceptance**

- A test layer declaring a tool with no check sees it among the
  lock's requirements and in the entered environment, and unlisting
  the layer drops it, proven by a forced test of the unlisted arm
  first.
- A test layer drops one checker and adds a fake one; the gate
  output names both moves, proven by a conformance-suite test.
- An installed-but-unlisted plugin appears in `fm doctor` output
  and changes no verdict, proven by a forced test.
- A layer declaring an incompatible API version refuses at mount
  with the version named.

### Phase 3b: the part and channel registries

The one shared, extensible classification the 2026-09-27 rulings
call for, numbered beside phase 3 because it needs nothing from
phase 4 and phase 4b needs it. Two registries that share only their
walk: the **part** registry answers what a path is to its package
(`source`, `test`, `test-support`, `configuration`), the
**channel** registry answers who wrote it and where to edit it
(`rendered`, `generated`, `materialised`, `layer content`, `seed`,
`contract`, `yours`, and the rest `fm explain` prints today). Each
is an ordered list of rules where the first to claim a path wins
and the caller supplies the fallback. Both existing ladders
migrate at once: every kind backend's `classify` becomes that
kind's builtin part rules, and `_provenance.classify` becomes the
channel's builtin rules, each registered through the same channel
that carries kinds and checks. A layer or a kind registers rules
into either. The per-package exception enters the contract in the
narrow shape a coverage floor has: these paths are not what the
kind would assume, on the part axis alone.

The docs job's condition rides along (livery#839): the predicate
"the site reads this path" is answered here, and the docs job skips
on a change the site does not read, leaving the `gate` context
reportable. The predicate is paths under `notes/` alone, narrower
than `is_prose`, since `README.md` and every package's `docs/`
markdown are published and the site build is their only gate.

The leans on open item 9's remaining questions, each a ruling
before this phase starts:

- A part rule is data, a pattern table, where nothing reads state,
  and a callable otherwise. The table renders into documentation
  and the callable keeps promotion to one registry mechanical.
- Rules order by registration, the most specific claim first, and
  a tie refuses at mount naming both rules, so two checkouts of one
  commit order alike.
- `fm explain` prints the part, the channel, and the layer that
  supplied each.
- The single registry over both waits for a third axis. "Does the
  site read it" is the first candidate and is answered here as a
  channel fact until then.

**Acceptance**

- Every kind's `classify` and `_provenance.classify` are gone:
  `grep -rn "def classify" packages/workshop/src` finds only the
  registries' own entry points, and `fm explain <path>` prints part,
  channel and supplier for a rendered file, a seed, a contract, a
  package source and a materialised entry.
- A test layer registers a part rule and a channel rule and both
  answer, with the supplier named; an ambiguous pair refuses at
  mount naming both, proven by a forced test.
- A package declaring the per-package exception is classified by it
  and nothing wider enters the contract, proven by a test that a
  wider key refuses naming the shape.
- A pull request changing only `notes/` skips the docs job and the
  `gate` context still reports, proven by the conformance chain; a
  change to `README.md` or a package's `docs/` still builds the
  site.
- `fm check` green, output unchanged.

### Phase 3c: the regions a repository owns in its managed files

A feature of the render, needing nothing from the phases before it,
so it may land first. Any managed file whose format has a comment
style may carry named regions: a pair of marker comments the
template renders, with the repository's own lines between them. The
render reads each region from the committed file as an input, the
way it reads the answers, and writes it back in place, so
`fm template.apply` preserves it by construction and
`fm template.check` still compares whole bytes. An edit inside a
region is the repository's; an edit outside it is drift; the
markers are rendered bytes, so a removed marker is drift too. A
file without the region yet, rendered before it existed or born
now, renders it empty and gains the markers on the next apply. A
format without comments still carries the repository's content,
appended: the render knows its own bytes, so the region is the
unmarked tail after them, and a changed prefix is drift the same
way. No managed file needs that form today, since every one of them
has a comment style, so the rule exists for the next one. A format
whose grammar closes, a strict JSON object, takes no tail and uses
its own include instead, as CMake presets do with
`CMakeUserPresets.json`. The editor's `.vscode/extensions.json` is
JSON with comments to the editor, like `settings.json`, so it takes
a marked region inside its list for the repository's own
recommendations beside the derived ones.

The first carriers, each with the lines that move into it today:

- the root `pyproject.toml`, two regions. One inside the `dev`
  dependency group's list, for the repository's own dependencies:
  today's `types-pyyaml`, which is this repository's need, since the
  workshop's yaml reads are type-checked here only because the
  workshop is a member. One at the end of the file, for the
  repository's own tables: today's three basedpyright execution
  environments for the imported tests. TOML decides what a tail
  region can say, whole tables and array-of-table entries and never
  a key inside a table the render wrote, so where a tool spells
  extension as its own key (ruff's `extend-per-file-ignores` and
  `extend-select`) the region uses it;
- the root `.gitignore`, one region for the repository's own rules;
- `.vscode/settings.json`, one region inside the object for the
  repository's own editor settings;
- `tasks.py`, one region below the mount for the repository's own
  tasks. The workshop's docs page already promises that anything
  below the plugin line is the instance's, and today's rendered
  file says the opposite in its own docstring; this makes the
  promise true and the docstring follows;
- a package's managed files, `cliff.toml` today and the native
  configurations phase 4 makes managed, the same way, so a package's
  own `.clang-tidy` lines have a home beside the tool's own
  `InheritParentConfig`.

`fm explain` distinguishes the two forms. For a file with marked
regions it names each region and the lines its markers enclose,
and says a line of the repository's own goes inside; for a file
with an appended tail it names the line the render's bytes end on
and says the repository's lines follow it. The drift report adapts
the same way, so a refusal teaches the seam instead of only naming
the file. Today's line, `<file>: differs from its render (the
<layer> layer owns it)`, becomes one of three: a difference outside
a marked region names the region the repository's lines belong in;
a missing or altered marker says the markers are rendered and that
`fm template.apply` restores them; a difference in a tail file's
prefix names the lines the render owns and says the repository's
follow them. A file with no repository-owned content keeps today's
line. The lines pinned in `test_workshop_compose.py` and
`test_workshop_templates.py` follow the new wording in the same
change. Contract 13.

**Acceptance**

- A line added inside a region survives `fm template.apply` and
  passes `fm template.check`; an edit outside it is reported as
  drift; a removed marker is reported as drift; proven by a forced
  test of each arm, the drift arms first.
- The base template names nothing of this repository:
  `grep -n "packages/toolroom\|packages/footman\|types-pyyaml"
  packages/workshop/src/livery/workshop/templates/project/pyproject.toml.jinja`
  finds nothing, and this repository's root `pyproject.toml` still
  carries the three execution environments and `types-pyyaml`,
  inside its regions.
- A workspace born now and a workspace rendered before the regions
  existed both gain the markers with empty content on
  `fm template.apply`, proven by the conformance chain and by a test
  over a marker-less committed file.
- A managed file without comments keeps its appended tail across
  `fm template.apply` and reports a changed prefix as drift, proven
  by a test over a fixture file, since no managed file needs the
  form today.
- `fm explain pyproject.toml` names its two regions with the lines
  their markers enclose, and `fm explain` on a tail-form fixture
  names the line the render ends on; the two outputs are distinct,
  proven by a test that pins both.
- The drift report's three adapted lines each appear for their arm,
  and today's line still appears for a file with no
  repository-owned content, proven by a forced test per arm, the
  marker arm first.
- `fm check` green, output unchanged.

### Phase 4: the check owns its configuration and tool

Everything a check owns that does not depend on what a file *is*.
Split from the claim, which follows, because the two halves block
on different things: this one needs only the registry from phase
2, while the claim needs the part registry of phase 3b. Joined,
the editor work waits on a question it has nothing to do with.

The record gains its configuration and its tool-profile
contribution, moving both out of their current homes. Today every
python check's configuration is in the root `pyproject.toml`
template (`[tool.ruff]`, `[tool.basedpyright]`, `[tool.mypy]`,
`[tool.ty]`, `[tool.pyrefly]`, `[tool.coverage.*]`,
`[tool.pytest.ini_options]`), so one configuration serves every
python package and no kind can differ; four packages carry a
hand-written `[tool.ruff]` stub that `extend`s the root with their
own `per-file-ignores`; and the native checks are the mirror
image, a `.clang-tidy` and a `.clang-format` seeded per package
that the template never rewrites, so a kind varies freely and an
improvement never arrives. The base template also names three of
this repository's packages in basedpyright execution environments,
an instance fact in the core (0903 plan, contract 18) that phase 3c
moves into the repository's own region of the rendered file.

Nothing today composes what goes inside a rendered file: the
"managed union" kinds use is `managed_files(kind)`, a union of
which file names the drift gate judges. This phase builds the
content composition. Three things the record carries, and what the
render does with each:

- **Fragments per rendered file, per kind.** A fragment names the
  rendered file it goes in and the kind it applies to; the render
  resolves each check's fragment for each package down
  `kind_chain` (nearest kind wins, a kind without one inherits its
  parent's) and composes the base template with the registered
  fragments in check-name order, so two machines write the same
  bytes, and the drift gate judges the result.
- **The tool's discovery shape**, one of two, as the design
  section states it. A tool that reads one file per project gets
  its section in the shared rendered file, and per-package
  variation is spelled the tool's own way inside it (basedpyright
  execution environments, mypy module overrides, ty and pyrefly
  per-path override tables; the phase reads each tool's own
  documentation at the locked version before emitting). A tool
  that searches upward from each file gets a managed file where it
  looks: `.clang-tidy` and `.clang-format` become managed renders
  of their check records' kind fragments instead of seeds, the
  first per-package emission and the case the kind ruling names. A
  package's own additions go in the managed file's region (phase
  3c) or ride the tool's inheritance, a deeper file with
  `InheritParentConfig`, never a merge of ours. One trap
  to hold: a `ruff.toml` beside a `pyproject.toml` shadows the
  table inside it, so a managed ruff file per package never sits
  beside a package's own table without extending it.
- **Withdrawal.** A section vanishes from the next render with its
  fragment. A managed file the render wrote for a withdrawn check
  is removed by the render's sweep only when its bytes are the
  bytes the render last wrote; an edited one is kept and named as
  a local override. The record of what was written and its digest
  is the materialiser's manifest, the `.workshop-materialised`
  shape of `_materialise.py`, reused rather than invented twice.

Ruff (format and lint) is the proof: its rendered configuration,
its editor settings, its recommended extension, its version pin,
and its profile entry all derive from its two check records, so
removing the records removes every trace. clang-tidy is the second
proof, for the per-kind and per-package halves: its fragment under
`python-nanobind` and under `cpp-conan` renders each native
package's `.clang-tidy`, and the seeded copies are adopted where
they match the render and named as overrides where they do not.

hse is the reference for a file per check: its devkit wheel ships
`config/ruff.toml`, `mypy.ini`, `ty.toml`, `pyrefly.toml` and
`basedpyright.json`, and an instance carries a thin
`.config/.ruff.toml` that `extend`s the wheel's copy. Livery
renders the configuration per workspace from the registered set
instead of shipping it whole, and the deviation is named here:
a check a layer drops must take its configuration with it, which a
wheel-shipped file cannot do.

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
- A check's fragment differs between two kinds and each package
  renders its own kind's, proven by a test over the conformance
  workspace's nanobind and cpp-conan members' `.clang-tidy`.
- A managed per-package file of a withdrawn check is kept and
  named when edited and removed when unedited, proven by a forced
  test of both arms, the edited arm first.
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

This phase needs the part registry of phase 3b and lands after
it. Building the claim on today's per-kind `classify` instead
would be the second shape this plan then replaces, which is the
argument that retired `kindcheck` inside phase 2. The hand-written
`[tool.ruff]` stubs in four packages are the interim the claim
retires: their `per-file-ignores` derive from the claim, and what
remains in them, the imported sources' docstring carve-outs, is the
tracked content pass's debt, not this plan's.

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
drift, a fragment resolving per kind down the chain, and a
withdrawn check's file removed only when unedited (contract 11).
The workshop's own kinds and checks run the same kit, so the kit
cannot drift from the enforcement. Once phase 8 exists the kit
carries the base-alone case of contract 10 too.

**Acceptance**

- The builtin python, python-nanobind, cpp-conan kinds and every
  builtin check pass the kit, wired into `fm check`.
- A deliberately broken fake plugin fails the kit with the
  violated clause named, proven per clause.

### Phase 8: the house layer, and the base's small set

The proof that the seam separates the workshop from livery's
opinions. A house layer is a member of this workspace and a layer
in its `[workspace] layers` list, after `livery.workshop`. Its
plugin registers the checks the base does not: mypy on its three
platforms, ty and pyrefly under the `types` role, and whatever the
ruling on open item 2 leaves for `typecomplete`. Its
`content/fragments/` carries `interaction-voice.md`,
`documentation-standards.md` and the house half of today's
`CLAUDE.workshop.md`: the four-checker sentence, the docstring
convention, and every other line that states livery's preference
rather than what the workshop enforces; the base's fragment keeps
the rest. The base template's `pyproject.toml` loses `[tool.mypy]`,
`[tool.ty]` and `[tool.pyrefly]`, which arrive as the house's
fragments through phase 4, and the base's `python` kind record
loses the three tools, which arrive through the layer's declaration
of phase 3.

The python package kind and its tooling leave the base too, into
`livery.workshop.python` (ruled 2026-09-28, open item 17), a layer
derived from the base that the house depends on. That extraction
is a plan of its own, sequenced with the C++ layer's, and this
phase does not wait for it. Until it lands the python kind is
still the base's, the house depends on the base, and the base-alone
acceptance below means no house checker and no house fragment;
after it, base-alone means no language check at all, and the
acceptance is run again at that plan's landing.

The house can be born before its name is settled and before
anyone outside this workspace needs it, because a package may opt
out of publishing. That opt-out lands first, in its own change,
needing nothing from the other phases, and every kind obeys it:
`[release] publish = false` in a package's `workshop.toml`, a fact
of the package in the base kind's contract vocabulary, read by the
release wave's per-member step before the kind's publisher runs, so
no backend knows about it. The wave still derives the version,
writes the changelog entry, stamps, builds (the release legs still
prove the artifact) and cuts the receipt tag; it skips the registry
upload and the served probe, prints the skip by name, and writes
the receipt as unpublished. A re-run walks past a tagged opted-out
member the way it walks past a served one. The kind record's empty
`artifact` already says a kind publishes nothing; this is the same
answer for one package of a kind that does. What it does not cover:
a repository outside this workspace that lists an unpublished layer
must reach it by a source uv can install from, a git source or a
private index, which the template does not render. The house ships
no template overlay (ruled 2026-09-28, contract 14): its
configuration is fragments and its voice is content, both in the
wheel. The reason is concrete here: `fm release.templates` names
as publisher the last layer in the stack that ships a template
tree, and publishes that layer's composed tree to the one
`[workspace] templates-artifact` this workspace declares. A house
overlay would make the house the publisher of the base's artifact
at `workshop-templates`, versioned by the house, and every workshop
instance would receive livery's house in its templates. Whatever
the house needs of a rendered file that a fragment or a region
cannot say is first a gap in the base template, fixed in the base;
what that cannot cover either is open item 16, and the house is
born without an overlay until it is ruled.

Needs phases 3 and 4: a check moved out of the base takes its
configuration and its tool with it, and both need the seams those
phases build. Independent of phases 5 to 7. Livery's own gate does
not change, because the house is listed: the same members run and
the same fragments are delivered. What changes is what a project
born without the house gets, and the conformance suite gains that
project.

**Acceptance**

- The base ships no house convention: the workshop package's
  `content/fragments/` holds no voice or documentation fragment,
  and mounting the base alone in a test registers no mypy, ty or
  pyrefly check, proven on the layer's own content and registry.
  The conformance suite's fixture born without the house gates
  green, its rendered `pyproject.toml` has no `[tool.mypy]` table,
  and its gate output names no house member.
- This workspace's gate output names the same members as before the
  phase, proven by the pinning tests of contract 7 unchanged.
- `fm layers` names the house after the workshop, and `fm doctor`
  on a checkout with the house installed and unlisted names it as
  available and changes no verdict.
- `grep -rin "four type checkers"
  packages/workshop/src/livery/workshop/content/fragments/` finds
  nothing.
- An opted-out member of each kind (python, python-nanobind,
  cpp-conan) releases with its tag cut, its changelog written and
  its publisher never called, the skip printed by name, proven by a
  test per kind and by `fm workflow.release --local` on the
  conformance workspace with one such member per kind; a second run
  walks past it.
- This workspace's house member is born with `publish = false`, and
  `fm workflow.release --local` releases the workspace with it
  unpublished.
- The house ships no template tree: `layer_template_tree` answers
  None for it, and `fm release.templates` on this workspace still
  names `livery-workshop` as the publisher, proven by a test.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The design section above | its `packages/workshop/docs/` page, after review |
| The reduced entry contract (phase 0, no tool store) | the shared tool cache's entry contract (0903 plan phase 18) |
| The POSIX-only entry script | a pwsh spelling, at the tool-store port; CI's windows leg runs `setup.sh` under the runner's bash until then |
| The hand-pinned setup-uv version in nightly.yml and release-legs.yml | emitter-derived pins, when those workflows become generated |
| The argv[0] probe deciding which process reconciles | footman's own real-invocation marker, when footman joins the workspace |
| The gate tools still pinned in the rendered dev group beside their store records (mypy, pytest, coverage) | the check record's one tool declaration, with open item 6's ruling on the venv-side remainder (phase 4) |
| Test role builtin wiring (phases 2-4) | the registered test role (phase 5) |
| Windows C++ coverage deferral (phase 5) | a ruling once an MSVC toolchain answer exists |
| `.clang-tidy` and `.clang-format` seeded per package, never rewritten | managed per-kind renders of the clang-tidy and clang-format check records (phase 4) |
| The three basedpyright execution environments naming this repository's packages in the base template | the repository's own tail region in the root `pyproject.toml` (phase 3c); the lines themselves are the tracked content pass's debt |
| `types-pyyaml` in the base template's dev group | the repository's own region inside the dev group (phase 3c) |
| The forge layer's `.forge.dev.env` rule in the base `.gitignore` template | the forge layer's fragment for `.gitignore` (phase 4) |
| The hand-written `[tool.ruff]` stubs in four packages | the claim-derived `per-file-ignores` (phase 4b); the docstring carve-outs inside them are the content pass's |
| The four checkers and the voice fragments shipped by the base | the house layer (phase 8) |

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
- 2026-09-28, the base is small and the house is a layer (Willem:
  the workshop's defaults and livery's house convention need
  rigidly separating; the house is a thin layer on a simpler base,
  and that layer is where anything worth customising happens, so
  nobody forks the templates; four type checkers are the house's
  extremity, not a default). Contract 10 and phase 8. The finding
  behind it: the base's own fragment says it carries only what the
  workshop enforces, and beside it sit two fragments that open by
  saying they are imported from hse's guidance.
- 2026-09-28, a check's configuration exists while the check is
  active (Willem: enabling a check the workshop provides generates
  its configuration; disabling removes it, and only when the file
  that exists is the one we generated). Contract 11. The withdrawal
  semantics already exist in `_materialise.py` and `_sync.py`, which
  record what was delivered, sweep a withdrawn item, and keep a local
  override while naming it; phase 4 reuses them.
- 2026-09-28, a check's configuration varies by package kind
  (Willem: clang-tidy under an engine kind, under cpp-conan, under
  nanobind). A fragment is a property of the check and the kind
  together and resolves down `kind_chain`, which already composes a
  kind's tools and managed files; no second mechanism keyed on
  kind.
- 2026-09-28, a layer declares tools (Willem). `_tools.requirements()`
  gathers from the kinds, the packages and the root contract, and a
  layer is none of them; phase 3 adds the site, as a registration
  at mount.
- 2026-09-28, where a format composes itself, use that (Willem:
  git reads a `.gitignore` per directory, clang-tidy searches up
  and inherits its parent, CMakePresets has `include`, the editor's
  settings already split into managed and local). Contract 12, and
  the per-tool table in the design section. The open question of
  fragments in a shared file against a file per check is answered
  per tool by its discovery shape, not by one rule for all;
  proposed, open item 10.
- 2026-09-28, a managed file may carry a region the repository owns
  where the format forces one file (Willem), the root `.gitignore`
  the case. A layer's section and a repository's region are two
  features: one composes at render, the other is state a render
  preserves. The proposed mechanics, the region read from the
  committed file as an input to the render so the drift gate stays
  byte-exact, are open item 11.
- 2026-09-28, templates aim to be generic enough that customisation
  is a layer's job and forking is never the answer (Willem). The
  overlay's wholesale replace stays the escape hatch it was ruled
  to be (0903 plan, contract 20), and the fragment mechanism of
  phase 4 is what makes a customisation of an existing file an
  addition instead of a fork.
- 2026-09-28, a correction to the 2026-09-09 refinement, from
  reading the tools rather than recalling them: mypy and the
  pyright family read one configuration per invocation and do not
  walk up from a file to the nearest one; ruff, clang-format and
  clang-tidy do. So "per-package emission by tool-native discovery"
  holds for the second group only, and the first group's
  per-package variation is a section inside the one file, spelled
  the tool's way. The structural claim survives in that form and
  phase 4 carries it.
- 2026-09-28, the plan revised on the handover (the agent; the
  additions await Willem's review): phase 3b written out, since the
  2026-09-27 record placed the registries phase before 4b without a
  phase text; phase 8 added for the house layer; livery#829 slotted
  as the first fix-mode check after the swap; open items 10 to 15
  opened with a lean on each.
- 2026-09-28, repository-owned regions are generic, and
  `pyproject.toml` carries them too (Willem: "as generic as
  possible"; the earlier lean of a `.gitignore`-only carrier is
  withdrawn). Contract 13 and phase 3c. Every managed file with a
  comment style may carry named regions; the first contents are the
  three basedpyright execution environments and `types-pyyaml`,
  both this repository's facts sitting in the base template today.
  Open item 15 closes with them: the region is the package rung's
  answer for the repository's own lines, so no instance rung is
  built. The `tasks.py` region makes the docs page's promise about
  the lines below the plugin call true, which today's rendered file
  contradicts. Later the same day (Willem): a format without
  comments still takes the repository's content, appended; the
  render knows its own bytes, so the tail needs no marker.
- 2026-09-28, `fm explain` distinguishes the two forms of
  repository-owned content and the drift report adapts to them
  (Willem). A refusal that only names the file leaves the person to
  find the seam; the report names the region, the marker, or the
  render's last line instead. Phase 3c, contract 13.
- 2026-09-28, a package may opt out of publishing, for internal
  tools, and every kind obeys it (Willem: it should work for all
  package kinds, so it is a setting of the base package kind). A
  fact of the package in its contract, read by the release wave for
  every kind and by no backend; phase 8 lands it first, and the
  house layer is its first user, so no index name is claimed before
  the name is ruled.
- 2026-09-28, the house layer's name is not settled (Willem: not
  sure about the name). Open item 13 stays open, and the opt-out
  lets the birth precede the name.
- 2026-09-28, the base templates are generic enough that a house
  needs no overlay (Willem: strive for that, whatever the house's
  name, so most projects avoid an overlay and the template
  repository it needs). Contract 14. An overlay makes its home the
  template publisher, so the cost is a template repository per
  overlaying layer; here it would also make the house the publisher
  of the base's own artifact. What a house needs that a fragment or
  a region cannot say is a gap in the base template.
- 2026-09-28, the overlay-free house is an aim, not yet a proof
  (Willem: not sure that will work for everything we need).
  Contract 14 states the aim; open item 16 lists what a fragment, a
  region and content cannot do, and the two ways out.
- 2026-09-28, the C++ support becomes a layer of its own, named
  `livery.workshop.cpp` (Willem: sounds good), so a python-only
  project pays nothing for the conan kinds, their five tools, the
  compilers, the conan registry kind and the releases route. The
  spelling follows `livery.toolroom.bench`, a distribution of its
  own inside its parent's namespace, which costs the workshop the
  namespace restructure toolroom had on 2026-09-12. The extraction
  is a plan of its own after this plan's registry phases, and the
  proof that the seams are complete; open item 17 holds what blocks
  it today.
- 2026-09-28, layers declare the layers they depend on (Willem's
  question, on the agent's weaker proposal that the contract's list
  spell out the closure: it saves configuration errors and tracing
  which ones are missing, and he sees no downside). The agent had
  argued only against a parent that activates without a
  declaration; a dependency declared in the layer's own code is
  deterministic per commit, since the lock pins the layer. The
  design is open item 18, with the lean that the list names what
  the project wants and mount brings each listed layer's declared
  dependencies in before it.
- 2026-09-28, the base and the python kind are separate layers
  (Willem, against the agent's lean that they stay one: the base
  contains enough to run the python that operates the workshop; the
  python package kinds and their tooling are separate and derived
  from the base). `livery.workshop.python` beside
  `livery.workshop.cpp`, each a layer depending on the base, and
  the house depending on the languages it wants. Contract 10 and
  the design section restated; open items 12 and 17 carry the sets
  and the extraction.
- 2026-09-28, the base layer is abstract, an organisational
  boundary and not a workspace anyone uses (Willem: a base-only
  workspace registers no packages to create; it is the interface
  that keeps the core language- and layer-independent, separation
  of concerns, like an abstract base class in C++; done from the
  start it would have prevented the coupling the agent measured).
  The proof of the separation is static, in the core's own gate:
  the layering lint refuses an import from the core into a layer,
  and a vocabulary test refuses a language's name in the core. The
  conformance suite's base-alone birth proves only that the engine
  runs with nothing listed. Contract 10 restated; the coupling in
  open item 17 is the retrofit's bill, not a reason against.
- 2026-09-28, the language layers are layers inside the one
  workshop wheel, not distributions (Willem: no need for different
  PyPI packages for a long time, but decoupled enough that it
  could). A layer module imports the core and never the reverse,
  carries its own templates, content and registrations, and is
  activated only by its listing; a split later is a move and a
  distribution name. Open item 17 restated; their templates stay
  in the one tree, so open item 16 narrows to the house's seeds.
- 2026-09-28, the layers list is kept closed under declared
  dependencies by the lint and its fix, never by mount pulling
  layers in (Willem: automated, it removes the objection and is
  more explicit). The layering lint is a workspace check the base
  owns, code and no tool, so contract 10 holds. Open item 18
  restated; the soft form stays open.
- 2026-09-28, phase 1 landed (issue #860): the contract key `type`
  became `kind` across 66 files, discovery refuses the old key with
  the migration line, and the four templates render the new key.
  Two things the phase found. A scripted rename has to know which
  `type=` is a package's: a task's `--type` override and a copier
  question's `type = "str"` are not, and the first gate run caught
  one of each. And the tests write the contract in six spellings
  (a literal, an f-string, a helper, a triple-quoted block, a seed
  helper, a kwarg), every one of which the new refusal caught in
  the first gate run, which is the refusal proving itself before the
  happy path. The descendant chain ran before submit and found a
  defect that had merged that morning: a newborn's tool sync ran the
  `tools.sync` task under `footman.chdir()` inside a task, which
  footman refuses, so every birth failed (livery#864). Fixed in the
  same change with the ladder's first rung, an engine that takes the
  root as a value; nothing on the merge path runs the chain, which is
  why the defect merged green. The chain's next run found a second
  thing: run through the global `fm`, which hands off through
  `uv run --project`, every child `fm` inherits the handoff's loop
  belt and skips its own handoff, so the born home's verbs ran this
  workspace's footman against the newborn's tasks file
  (livery#865, footman's to scope; the chain now scrubs the belt
  from its children's environment). Its third run found the test
  itself behind the product: three reads of a delivered fragment at
  `.workshop/<name>.md`, where fragments have lived in
  `.workshop/fragments/` since 2026-09-27, fixed in the test. Its
  fourth run hung in the child's own `fm sync`, the hang every sync
  on this desk had shown all day (livery#862): `sync` is an
  interactive task that owns the console, and since the same
  morning commit its body called the `tools.sync` task, whose
  console gate waits for a holder that is its own caller. Fixed
  here the same way as the birth, the engine called with the root;
  footman's half, a self-wait with no note, stays open. Five runs
  to green, each finding the next thing, which is what an armed
  test that nothing on the merge path runs looks like on the day it
  is run. The chain then: 1 passed, 0 failed, 0 skipped in 247 s, from the junit report.
- 2026-09-28, phase 2 built (issue #867): `CheckRecord` and
  `register_check` in `_checks.py`, every registered check also a
  hidden task `checks.<name>` that the gate schedules (rewriters
  serially under `--fix`, judges together, a package's checks
  ordered by `after`), `kindcheck`, `run_kind_checks`,
  `CiContract.kind_verbs` and `Backend.check` gone, the cpp-conan
  kind registering clang-format, configure, build, ctest and
  clang-tidy, and the layering lint registered as the base's
  workspace check with a fix mode to come (livery#829). One thing
  changed while building it: the first walk scheduled each check as
  a step on the block, and the pins passed, but the descendant
  chain's child showed what a person sees, no member rows and no
  skip lines, since a green step prints no row and a step's prints
  are captured. A gate member is a task in footman's model, one
  report row and its prints on the console, so each check became a
  task and the fixtures the step shape had touched went back to
  their original lines. Two deliberate edits to pinned tests, each
  with its reason: the eight members are now format, lint,
  typecheck, typecomplete, test, template_check, provenance_check
  and layering, since kindcheck retired and the layering check
  joined; and the cpp-conan contract carries the build and test
  roles, so the `test` role no longer skips by name for a native
  package, ctest being its check, and the skip test says so. The
  router's one line per package became one line per check, as the
  phase named, and the chain asserts the new lines.
- 2026-09-28, phase 3c built (issue #874): `_regions.py` reads a
  managed file's marked regions and splits a tail file into the
  lines the render owns and the rest; the project and package
  renders take the committed regions as an input beside the
  answers; the drift report's line adapts (outside a region, a
  missing marker, a changed prefix); `fm explain` names a file's
  regions with their lines. The carriers: the root `pyproject.toml`
  (a `tables` region, which took the three basedpyright execution
  environments out of the base template), the root `.gitignore`
  (`rules`), `.vscode/settings.json` (`settings`, first inside the
  object so each line ends with a comma), `tasks.py` (`tasks`, below
  the mount, which makes the docs page's promise true), and every
  package's `cliff.toml` (`own`). No managed file needs the tail
  form today; the rule is built and tested on a fixture. Not done,
  deliberately: the region inside the dependency groups (Willem's
  third objection is not ruled), so `types-pyyaml` stays in the base
  template and that acceptance line stays open.
- 2026-09-28, livery#870, found by the chain after phase 2 merged:
  a native package's checks ran out of order in the gate's block,
  build and ctest before configure, because the task prerequisites
  they were declared with do not order tasks called inside a block.
  A check's `after` prerequisites now run through their tasks from
  the check's own body, once per gate by footman's dedup, and
  outside a run, where nothing dedups, the caller orders them.
  Phase 2 merged with the fix uncommitted, since an armed submit
  merges on green; the fix is its own change.
- 2026-09-28, livery#829 built on phase 2: the layering check's fix
  mode. `write_edges` declares every sibling reference the graph
  already reaches, the `[[depends]]` edge in the contract and the
  native requirement through a new `Backend.declare_requirement`
  (pyproject's list for python, the recipe's `requires` tuple for
  conan, the dependency's kind deciding for the extension), at the
  floor the graph carries; a reference nothing reaches stays a
  refusal, and the refusal names `--fix`. `fm checks.layering --fix`
  is the check's own spelling. A written python requirement moves the
  lock, so the fix runs `uv lock` before it judges. The first
  fix-mode check driven by a real tool rather than a fake. Two pins
  follow it: under `--fix` the layering check is the fourth
  rewriter, after provenance, and not a judge, and a scoped run
  whose caller already rewrote leaves it out like format and lint.
- 2026-09-28, the publishing opt-out built (issue #871), phase 8's
  first change: `[release] publish = false` is a fact of the package
  (`Package.publish`), refused when not a boolean; the wave reads it
  before the registry is probed or the kind's publisher runs, so no
  backend knows about it. An opted-out member is built, tagged and
  receipted as unpublished, its skip printed by name, and a re-run
  walks past its tag; the conan target is resolved only for members
  that publish, so an opted-out conan member asks for none.
- 2026-09-28, contract 10's vocabulary test scoped (the agent's
  correction of its own sentence, on Willem's question): the core
  names no package kind and no check tool; the interpreter, uv and
  the venv that operate the workshop are the base's own. The
  python layer adds what a python package needs on top, the
  nanobind layer adds its kind on top of both, as Willem said; the
  objection was to the test's wording alone.

## Open

1. Does `Edge.kind` rename too, or does "edge kind" bound by its
   table satisfy contract 9? Current lean: keep it, bound.
   Owner: Willem.
2. How long do `typecomplete` and the release-path checks stay
   builtin roles a layer cannot drop? The release train leans on
   typecomplete; dropping it may need its own ruling. Since the
   base-and-house ruling the question is also which side of that
   line `typecomplete` sits: the lean is the python layer, because
   it rides that layer's one type checker and judges python
   distributions alone. Owner: Willem, before phase 8.
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
6. The venv-side remainder, now that toolroom's store exists:
   which tools must stay in the lock because they import the
   project's environment (pytest and its plugins, coverage
   certainly), and whether mypy and basedpyright run from the
   store pointed at the venv's interpreter or stay locked beside
   it. The fact today: mypy, pytest and coverage are declared in
   both places, the rendered dev group and the store (a receipt in
   `.workshop/receipts/` for each, beside the venv's copy), so which
   one answers is PATH order after entry. Phase 4's tool-profile
   contribution makes the check record the one declaration, and the
   phase settles this with it. Owner: Willem, with phase 4.
7. A fragment can only add. Two of the three cases are answered
   since 2026-09-28: a kind that wants a different value than its
   parent's replaces the parent's fragment for that check down the
   chain, and a package that wants more than its kind's file rides
   the tool's own inheritance. What remains is a check whose
   fragment must contradict a line the base template itself wrote.
   The lean is splitting the base template until the line is a
   slot nobody else owns, because the whole-file override forfeits
   every later base improvement for that file (0903 plan, contract
   20). Owner: Willem, with phase 4.
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
   Owner: Willem. Blocks phase 4b; phase 3b states a lean on each
   question and starts on the rulings.
10. Fragments in a shared rendered file, or a file per check. The
    proposal (design section, contract 12): neither by rule; the
    check record names its tool's discovery shape and the render
    follows it, a section for a tool that reads one file per
    project, a file where the tool looks for one that searches
    upward. The per-tool assignment: basedpyright, mypy, ty,
    pyrefly, pytest and coverage are sections; ruff, clang-format,
    clang-tidy and the ignore rules are files. ty reads `ty.toml`
    or `[tool.ty]` and pyrefly reads `pyrefly.toml` or
    `[tool.pyrefly]`, both checked against the locked versions'
    `--help`, so either shape is available and the section is chosen
    because their per-path variation is a table inside the one file.
    Owner: Willem, before phase 4.
11. Resolved 2026-09-28: the region a repository owns is a feature
    of every managed file, read from the committed file as an input
    to the render and written back in place (contract 13, phase
    3c); a format without comments takes an unmarked tail. What
    stays open inside it: the marker shape, one per comment style or
    one text in every style; whether the root
    `pyproject.toml` carries regions beyond the two named (the dev
    group's list and the tail); and whether a region inside a list
    is allowed in every format that has lists, or only where the
    format tolerates an empty one. A layer's section is the fragment
    mechanism of phase 4 and needs nothing new. Owner: Willem, with
    phase 3c.
12. The sets, member by member, since the 2026-09-28 ruling that
    the base operates and languages are layers. The base: the
    workspace checks (render drift, provenance, layering) and
    nothing that reads a language. The python layer: ruff format
    and ruff lint, basedpyright as the one type checker (the editor
    answer and `typecomplete` ride it), pytest with the coverage
    floors. The house: mypy on three platforms, ty, pyrefly, the
    docstring convention, the voice. Two questions inside it: which
    ruff rule set the python layer selects (the lean: today's
    selection minus `D`, with the google docstring convention the
    house's), and whether the docstring convention's sentence in
    `CLAUDE.workshop.md` moves with it. Owner: Willem, before
    phase 8.
13. The house layer's name and home. Not settled (Willem,
    2026-09-28: not sure about the name). The constraints: the layer
    is livery's, so its import and distribution names carry livery,
    and the instance-visible words speak workshop; the family's rule
    holds, that a name is an identifier and no sentence depends on
    the metaphor. Candidates, each with what it collides with:
    `livery.house` (house style; vague alone); `livery.brand` (the
    word the plans and code already use for what sits above the
    base, and a born project's "brand's name"; near-redundant, since
    a livery is a brand's paint); `livery.standards` (what the layer
    holds, the documentation standards and the checkers; corporate);
    `livery.household` (the family's register, the house's people
    and their order, what a footman serves; long);
    `livery.customs` (a company's established practices; collides
    with customisation in a sentence); `livery.canon` (the accepted
    body of rules; collides with "canonical" in code prose);
    `livery.opinions` (the plan's own word, facts in the contract
    and opinions in layers; unusual as a package name);
    `livery.warden` (the wardens enforce a livery company's
    ordinances, which fits the checks; reads as an agent). Its home
    is this workspace
    (0903 plan, contract 19), listed second in `[workspace] layers`.
    The publishing opt-out lets the birth precede the name: the
    member is born with `publish = false`, and the name is ruled
    before the first release lifts it, so no index name is claimed
    early. Owner: Willem, before the first release of the house.
14. How a layer declares a tool: a registration at mount beside
    `register_kind` and `register_check` (the lean, since the
    profile then derives from the registry and two checkouts agree
    by construction), or a `[tools] requires` table in the layer's
    own contract. Owner: Willem, before phase 3.
15. Resolved 2026-09-28: the repository's own override of a
    check's configuration lives in the managed file's region (phase
    3c), so the three basedpyright execution environments move there
    and no instance rung is built. The package rung of the
    2026-09-09 record, a package contributing to another file than
    its own, is designed when such a case exists.
16. Whether the house can do without an overlay (Willem,
    2026-09-28: not sure that will work for everything we need).
    What a fragment, a region, or content cannot do today: add a
    file the base template does not render (a house's own seed, a
    brand's `og-card.png` and palette, a second workflow); replace a
    seed wholesale (a brand's README or LICENSE seed, which the 0903
    plan calls the cheap, ordinary customisation); contribute a
    copier question. Each of those is an overlay, and an overlay
    makes its home the template publisher. Two ways out, for
    ruling. First, the designed one: the house's home is a child
    workspace of its own (0903 plan, contract 19: a child created
    with one added layer becomes that layer's home), which
    publishes its own composed artifact, so the base's at
    `workshop-templates` stays pure; the cost is a repository and a
    gate of its own, and the house leaves this workspace. Second,
    a layer contributes files and seeds through the registry at
    mount, the unioned-registries rung of the 0903 plan's contract
    20, which inherits fully and needs no template repository, and
    the overlay keeps only the wholesale replace and the questions.
    The lean is the second, designed when the first such file is
    real, with the house born here without an overlay until then;
    a wholesale seed replace the house turns out to need is what
    decides for the first. Since the language layers live in the
    workshop's one wheel and one template tree (open item 17), the
    question is the house's alone: its seeds carry livery's
    identity, which contract 18 of the 0903 plan keeps out of the
    workshop's artifact, so a house seed means the house's own
    home and artifact, and everything short of a seed means none.
    Owner: Willem.
17. The language layers, `livery.workshop.python`,
    `livery.workshop.cpp` and `livery.workshop.nanobind`, each
    derived from the base (ruled 2026-09-28: the base contains
    enough to run the python that operates the workshop; the python
    package kind and its tooling are separate; nanobind adds its
    kind on top of both). Ruled the same day: they are layers, not
    distributions. Each is a module inside the one workshop wheel,
    listed in `[workspace] layers` by its import path and activated
    only by that listing, and decoupled enough to become a
    distribution of its own the day a cadence or an owner differs:
    a layer module imports the core and never the reverse, imports
    a sibling layer only through a declared dependency, and carries
    its own `templates/`, `content/` and registrations, so a later
    split is a move and a distribution name. `layer_entries` today
    derives a distribution from the import path by dots to dashes,
    which names nothing for a submodule; the lean is to resolve the
    distribution from the installed metadata of the import path's
    top-level package, the table form staying for the odd case.
    Because the layers share the workshop's one wheel, their
    templates stay in the one template tree and the one artifact,
    organised by layer, so no layer needs an overlay and open item
    16 narrows to the house's seeds. The python layer takes the
    `python` kind record and backend, the `package-python` template
    and its layer variant, the python checks and their tools (ruff,
    basedpyright, pytest, coverage; uv stays in the base, since it
    operates the workshop), the coverage measurement and the test
    runner's python half, and the claim over the root's own
    `tests/`. The C++ layer takes the cpp-conan kind, its tools and
    host tools, the conan registry kind and the releases route; the
    nanobind layer takes the python-nanobind kind and the wheels
    matrix, depending on both. What the extraction faces today: the
    python backend is imported directly by six modules,
    `_quality.py` alone reaching into it fourteen times, and conan
    is named outside the backends in six modules, about sixty
    references, each a call site to route through a registry, most
    of them phase 2's; the engine tests for the python kind by name
    in five modules beyond the registry (`_docs.py`,
    `_packages.py`, `_registries.py`, `_release.py`,
    `_templates.py`, eleven sites), so "joins the uv workspace" and
    "is a python distribution" must become facts of the kind record
    before the name leaves the base; and the coverage store, the
    leg and union verbs and the floors are written against
    coverage.py's data files, which phase 5 turns into the kind's
    answer, so the python extraction follows phase 5 as well as
    phases 2 to 4. Each lands as its own plan after those phases,
    python first, since the house depends on it. Each extraction's
    acceptance is the static proof of contract 10: the layering
    lint's rule that the core imports no language layer, and the
    vocabulary test, both green after the cut. Owner: Willem, for
    the sequencing.
18. Layer dependencies. A layer declares, in its plugin at mount,
    the layers it depends on, and the contract's list is kept
    closed under those declarations (ruled 2026-09-28: automated,
    it removes the objection and is more explicit). The list stays
    the whole truth a reader and a reviewer see: `fm sync` and
    `fm check --fix` append a missing dependency before its
    dependent, with a comment naming who requires it, the same
    shape as the layering lint's fix that writes a `[[depends]]`
    edge (livery#829); the layering lint, a workspace check of the
    base, refuses a list that lacks a dependency or orders one
    after its dependent, naming the fix; and mount mounts in list
    order, refusing at a dependency listed after its dependent so a
    hand-edited list never runs half-wired. Contract 3 keeps its
    wording: the list is the only activation channel, and the lint
    is what keeps it complete. `fm layers` names who requires each
    layer; a cycle refuses naming the ring; a declared dependency
    that is not also a dependency of the layer's wheel refuses at
    the lint, since the import would fail anyway. One form the
    design still needs: a soft dependency beside the hard one. A
    house with opinions on two languages must not drag both
    languages into every project that lists it, so a layer also
    names layers it mounts after when they are listed, contributing
    its registrations for that language only then; the hard form
    is for a layer that cannot mount without the other. Without the
    soft form the alternative is one house per language. Owner:
    Willem, for the soft form.
