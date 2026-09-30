# The extensible gate: checks opened to layers, the vocabulary bound

Status: phase 0 landed 2026-09-05 (issue #227). Contract 7's pinning
tests landed 2026-09-28 (commit 94096846). Phases 1, 2 and 3c landed
2026-09-28 (issues #860, #867 with its ordering fix #870, #874; 3c's leftover on 2026-09-29, #896), phase 3
up to the dependency closure the same day (#876) and the rest of it on
2026-09-29 (#884); phase 3b was built the same day (#886), phase 4's two changes (#888, #890), phase 4b (#892), phase 4c (#894), phase 5's first change (#905), the dotnet kind (#907) and the MSVC measurer with the Windows toolchain environment (#912), host-scoped tool requirements (#917) and the conformance loop's cpp-conan member (#914, the wave's wheels leg open in #931), phase 6's first four slices (#955, the extractor on the kind record; #956, the private-members policy as a slot; #957, layer assets staged from the wheel and the theme slot; #958, examples as files; the docs layer's three changes, #968, #970 and #971), phase 7's first change (#974, the conformance kit's first three clauses) and the gate's one walk (#976) beside the layering
2026-09-29 (#884); phase 3b was built the same day (#886), phase 4's two changes (#888, #890), phase 4b (#892), phase 4c (#894), phase 5's first change (#905), the dotnet kind (#907) and the MSVC measurer with the Windows toolchain environment (#912), host-scoped tool requirements (#917) and the conformance loop's cpp-conan member (#914, the wave's wheels leg open in #931), phase 6's first two slices (#955, the extractor on the kind record; #956, the private-members policy as a slot) beside the layering
check's fix mode (#829) and phase 8's first change, the publishing
opt-out (#871). On 2026-09-28 and 2026-09-29 Willem ruled every open
item the remaining phases waited on; the decision record carries each
ruling, and the design section, the contracts, phases 3 to 9 and the
open list carry their consequences. Next, in dependency order: phase 3's
remainder (the tool declaration, the AST rule registry, contributions
by target), phase 3b (the category and channel registries), phase 4
with 4b and 4c, the small 3c leftover, then phases 5 to 9 and the layer
split as its own plan. The design section below is written to graduate
into `packages/workshop/docs/` after review; everything else is working
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
language is a layer derived from the base: `livery.workshop.layers.python`
carries the python package kind, its template, its checks
(formatting, lint, one type checker, the tests with their coverage
floors) and its tools; `livery.workshop.layers.cpp` carries the C++ kinds
the same way, and `livery.workshop.nanobind`, a kind layer rather
than a language, depends on both. Three more layers derive from the
base and register no language: `livery.workshop.layers.docs` assembles the
site, judges the docs and runs the examples, asking each kind record
for its extractor and its example runner; `livery.workshop.playground`
depends on docs and python and adds the browser sandbox and the
second run of the examples inside it; `livery.workshop.claude` writes
the agent's entry file from the prose fragments the listed layers
deliver. A house, `livery.housekeeping` here, is a layer of opinions:
a second and a third type checker, a docstring convention, a voice,
a theme, each a registration or content, listed by the project that
wants it. It depends on the base alone and contributes to each
language it has opinions on by data: its plugin module names, per
target layer, the module that carries the registrations for that
target, and the mount grafts that module when both are listed. No
mount code branches, the list stays the whole truth, and a house
with opinions on six languages drags none of them into a project
that has one. The base is abstract, the way the base kind is: it heads
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
  classification is the file's category, from the registry below,
  never a table of the check's own. What a check claims and the
  facts it declares beside them, a released version the browser
  installs say, are its inputs, and the record is where the gate
  will one day read them to run a check only when an input moved;
- the options a package may set for it. The record declares each
  with a type and a default, a kind sets defaults, a layer
  overrides, and a package sets them in its contract under
  `[checks.<name>]`: `parallel = false` on the python test check
  passes `-n 0` for that package, and `typecomplete` is one such
  option, on by default. The record decides what a package may
  say, so a fact about the package is offered and a rule set is
  not, which keeps contract 5;
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
tool with no check involved declares it as data on its plugin
module, `WORKSHOP_TOOLS = ("docker>=27",)`, beside its API version
and its dependencies, read at mount like them and never from a
wheel's metadata; the derived profile takes it as a fourth site
beside the kinds, the packages and the root contract, `fm layers`
names each layer's tools, and a layer that lives in the repository
as a member declares it the same way, since it imports the same
way. A
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

**A slot is a hole one layer cuts and others fill.** A line the
base template writes that a layer needs different is never
overridden whole and never deleted by a fragment; the template
splits until that line is a slot, and the records fill it. The
owning layer declares the slot with its composition rule,
`register_slot("python.dev-group", compose=union)`, and any layer
or record contributes, `contribute("python.dev-group",
"mypy>=1.14")`. A list composes as the union in contribution order,
a scalar takes the nearest layer's value, two claims at one level
refuse naming both, and a table applies those rules key by key; the
owner reads the composed value and puts the instance's contract
facts on top itself. The `dev` dependency group and pytest's
`addopts` are the first slots, so a project born without the house
has no mypy in its venv and nobody wrote either sentence; the docs
layer's theme is another, the fonts and schemes a theme layer
contributes. A contribution to a slot nobody declared refuses at
mount naming the layer.

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
carries the repository's own tables this way, the root `.gitignore`
its own rules, `tasks.py` its own tasks,
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
questions about a path, and one file answers both. Its
**category**: what this is to its package, source, test, test
support, configuration, and whatever a layer adds, prose, example,
asset, nav. Its **channel**: who wrote it and where to edit it,
one of a layer's fragment, a rendered file, an emitted one, the
contract, or yours. `packages/forge/workshop.toml` is
configuration on the first and the contract on the second;
`packages/forge/src/livery/forge/_http.py` is source and yours;
`packages/footman/docs/examples/first_task.py` is example and
yours. Neither answer can be read off the other, so the two
vocabularies stay apart and are never merged into one.

Both live in one store keyed by axis, with a typed pair of
functions per axis: `register_categories` and `category_of`,
`register_channels` and `channel_of`. A category rule is a pattern
table a kind or a layer registers, since nothing a category needs
reads state, and the table renders into the documentation; a
channel rule is a callable, since it reads the delivery manifest,
the emitted set and the template source. Rules order by
specificity, the most specific claim first, and two rules of one
specificity claiming one path refuse at mount naming both, so two
checkouts of one commit order alike and nothing is settled by mount
order in silence. The workspace root is a unit of its own, the one
the gate already has for its `tests/`, and the base registers its
rules: notes, the site's files, the readme, the configuration.
The root is never a package, so a `src/` at the root has no rule
and no claim, and the layering check refuses it naming the move
under `packages/<name>/`. A third axis, if one ever comes, is one
more pair of functions over the same store.

Today each is instead a closed ladder in one module. That is why a
layer can teach the workshop a new package kind but not what a
file of that kind *is*, and why `fm explain` called a tool receipt
a layer fragment until the directory it lived in was split.

Registered, not configured. The layout a kind expects is a fact
about the kind, so it is layer code like everything else here. One
package's local exception is a fact about that package, and the
contract carries it in the same narrow shape it carries a coverage
floor, a `[categories]` table keyed by category, `vendored =
["docs/assets/vendor/**"]`: these paths are not what the kind would
assume, on the category axis alone, never the channel. Anything
wider than that exception is a policy toggle, and the boundary
above refuses it.

**A check claims categories, and the docs check is one of them.**
A claim is the relation between a check and the files it reads,
named in categories. The site build claims prose, nav, asset,
example and the root's site files; the examples check claims
example; nothing claims notes, so a change under `notes/` runs
the workspace checks and no more. "Does the site read this path"
is therefore not a third axis and not a fact on the file; it is
the docs check's claim, and the docs check knows the docs layout.

**Prose fragments serve two readers.** A layer's prose, the voice,
the standards, what a kind's package looks like, is a set of
fragments, and the same set writes the agent's entry file and the
site's development section. A fragment is a file in the layer's
content named by the path alone,
`<section>.[<kind>.]<topic>[.<audience>].md`, or a registration in
code with a render that takes the audience. The base defines the
sections and their order, identity, voice, standards, rules,
workflow, gate, verbs, kinds, tools, and a layer adds one at a
declared position. A kind in the name gates the fragment on a
package of that kind, or a kind deriving from it, being present; a
topic is unique across everything delivered, and a collision
refuses naming both files. The audience is `agent` or `human`, and
a name without one serves both; a dynamic fragment renders from
the registries at sync, so the sentence naming the gate's checkers
is derived and true for every project. `fm sync` delivers the
agent-or-both set flat under `.workshop/fragments/`, byte for byte
the source, materialised so an edit is kept and named; an agent
layer writes its entry file from that set, sections in order, the
repository's own fragments last; the docs layer renders the
human-or-both set into the site's development section in the same
order. Without an agent layer nothing writes an entry file.

**Examples are files, tested twice.** A package's examples live
under `docs/examples/` as python files a page includes by snippet,
so an example is code with a category, judged by the format and
lint checks, run by the docs layer's examples check through the
kind's runner, and reported with a file and a line. A prose edit
reaches the site build alone. When the playground layer is listed
it registers a second check over the same category that runs the
examples in the browser's environment, and declares that runtime
as its tool.

**A theme is a layer.** Its files, a palette, fonts, a logo, theme
partials, ship as layer content, staged into the site build and
wired in layer order with the workspace's own css last; its values
are contributions to the docs layer's theme slot; an update is a
wheel bump, never a template re-render.

**Templates compose on demand.** A layer that ships template files
publishes only its own, to a branch of the one templates artifact
named by the layer and tagged with the layer's release version; a
layer inside the workshop wheel publishes nothing, its templates
ride the workshop's series. A project's stack is its layers list,
the tag per layer is the version its lock pins, and the render
fetches each series at that tag, composes them in layer order and
renders the result, the way a home composes its local trees. A
base fix reaches every project by one tag, and an overlay costs a
layer nothing but the files it ships, so a house may replace the
README and LICENSE seeds without becoming the base's publisher.

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
belongs to the docs layer: the per-package site shape, the
development section from the prose fragments, and the publish
path, with nothing language-shaped in it. Coverage likewise: the floor is a contract fact in `[qa]`,
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
    alone. Each language, the documentation, an agent's files and
    each house is a layer depending on the base, never the reverse: the core imports no layer and
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
14. A layer's template files travel in a series of their own in the
    one templates artifact, tagged with the layer's version, and a
    project composes the series its lock pins at render. An overlay
    therefore never makes a layer the base's publisher and costs no
    repository; the base templates still aim to be generic enough
    that most layers ship none, and a house customises a rendered
    file through a fragment, a slot, a region or content first.
15. A layer's declarations are attributes of its plugin module,
    read at mount and never from a wheel's metadata: its API version,
    its dependencies, its tools, and its contributions to other
    layers as a map from target to module. Mount code never branches
    on what is listed; the mount grafts a contribution module when
    both its owner and its target are, and the list, with the
    resolved targets the fix writes into it, is the whole truth.
16. `fm check` runs each source parse once. The layering check
    owns the one traversal, memoised per file and tree, and a kind
    or a layer registers AST rules into it rather than walking
    again; a rule's fix hooks run inside the check's rewrite and its
    judgments inside the check's judge, so the gate's serial-then-
    parallel ordering is implemented once, in the walk.

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

The API version check, the withdrawal a layer may make with its
name on it, the gate's narrowing lines, the dependency closure and
its fix, `fm layers` and the doctor's discovery landed 2026-09-28
(issue #876). Three pieces remain, ruled 2026-09-28 and 2026-09-29:

- **The tool declaration.** A layer declares the tools its own
  verbs need as `WORKSHOP_TOOLS = ("docker>=27",)` on its plugin
  module, beside `WORKSHOP_DEPENDS` and `WORKSHOP_API_VERSION`.
  `_tools.requirements()` gains the fourth site, `layer <import
  path>`; `tools.lock` resolves them, the receipts record them,
  `fm doctor` and `fm env.check` name an absence, and `fm layers`
  names each layer's tools. A layer in the repository is a member
  package and declares the same way.
- **The AST rule registry.** The layering check owns the one
  source traversal, memoised per file and tree, and offers
  `register_ast_rule`: a kind or a layer hands it a rule that
  receives each parsed module once and returns its problems, with
  fix hooks beside. The check's three walks today, the
  terminal-through-the-runner rule, the stdlib-only rule and the
  sibling references, become its first three rules, and
  import-shaped rules read the one import graph the check builds.
  The rules' fix hooks run inside the check's rewrite and their
  judgments inside its judge, so the walk sees one check and the
  serial-then-parallel ordering stays implemented once (contract
  16).
- **Contributions by target.** A layer's plugin module declares
  `WORKSHOP_FOR = {"livery.workshop.layers.python": "livery.housekeeping.python", ...}`,
  a map from target layer to the module carrying the registrations
  for that target. The mount grafts a contribution module when both
  its owner and its target are mounted, whichever mounts later; a
  contribution module is straight-line registrations and no mount
  code branches (contract 15). `fm layers` prints `<layer>: for
  <targets>`. The closure lint's `--fix` writes the resolved targets
  once into the entry's table form, `{ import = "...", for = [...]
  }`; from then on `for` is the truth, the mount follows it, the
  lint refuses a `for` naming an unlisted layer, the fix never
  completes it again, and deleting a name from `for` is a project's
  opt-out from that target's opinions.

**Acceptance**

- A test layer declaring a tool with no check sees it among the
  lock's requirements and in the entered environment, and unlisting
  the layer drops it, proven by a forced test of the unlisted arm
  first.
- The layering check parses each source once: a counting fake
  over the parse proves one call per file with three rules
  registered, and a test layer's AST rule sees every module, its
  fix hook runs inside the check's rewrite, and its refusal names
  the rule, proven by a forced test of the refusal first.
- A contribution module for an unlisted target never mounts,
  proven by the forced arm first; with the target listed it mounts
  after both, `fm layers` names it, and its registrations answer.
- `fm check --fix` writes `for` once; a name deleted from it stays
  deleted on the next fix; a `for` naming an unlisted layer refuses
  naming the fix; proven by tests in that order.
- A test layer drops one checker and adds a fake one; the gate
  output names both moves, proven by a conformance-suite test.
- An installed-but-unlisted plugin appears in `fm doctor` output
  and changes no verdict, proven by a forced test.
- A layer declaring an incompatible API version refuses at mount
  with the version named.

### Phase 3b: the category and channel registries

The one shared, extensible classification the 2026-09-27 rulings
call for, settled in full on 2026-09-28 (open item 9), numbered
beside phase 3 because it needs nothing from phase 4 and phase 4b
needs it. One store keyed by axis, and a typed pair of functions per
axis: the **category** axis answers what a path is to its package
(`source`, `test`, `test-support`, `configuration`, and what a layer
adds: `prose`, `example`, `asset`, `nav`, `generated`), the
**channel** axis answers who wrote it and where to edit it
(`rendered`, `generated`, `materialised`, `layer content`, `seed`,
`contract`, `yours`, and the rest `fm explain` prints today). A
category rule is a pattern table, since nothing a category needs
reads state, and the table renders into the documentation:

```python
register_categories(
    "python",
    [
        ("src/**", "source"),
        ("tests/**/test_*.py", "test"),
        ("tests/**", "test-support"),
        ("**", "configuration"),
    ],
)
```

A channel rule is a callable, since it reads the delivery manifest,
the emitted set and the template source. Rules order by
specificity, the most specific claim first, and two rules of one
specificity claiming one path refuse at mount naming both; a
callable declares its rank. Both existing ladders migrate at once:
every kind backend's `classify` becomes that kind's builtin
category rules, and `_provenance.classify` becomes the channel's,
each registered through the channel that carries kinds and checks.
The workspace root is a unit with rules the base registers, the one
the gate already has for its `tests/`: tests as today, `notes/**`
as notes, the site's files, `README.md`, the configuration files. A
`src/` at the root has no rule and no claim, so the layering check
refuses it naming the move under `packages/<name>/`: a project that
ships one package has one directory under `packages/`. The
per-package exception is a `[categories]` table in the package's
contract, keyed by category, on that axis alone:

```toml
[categories]
vendored = ["docs/assets/vendor/**"]
```

`fm explain` prints the category, the channel, the layer that
supplied each, and the checks that claim the file.

The docs job's condition rides along (livery#839) as the first
claim: the site build claims prose, nav, asset, example and the
root's site files, nothing claims notes, and the docs job skips a
change the site does not read, leaving the `gate` context
reportable. The claim mechanism in full is phase 4b's; this phase
lands the docs check's claim in the narrow form that skip needs.

**Acceptance**

- Every kind's `classify` and `_provenance.classify` are gone:
  `grep -rn "def classify" packages/workshop/src` finds only the
  registries' own entry points, and `fm explain <path>` prints
  category, channel, supplier and claims for a rendered file, a
  seed, a contract, a package source, a materialised entry and a
  root note.
- A test layer registers a category rule and a channel rule and
  both answer with the supplier named; two rules of one specificity
  claiming one path refuse at mount naming both, proven by a forced
  test first.
- A package declaring `[categories]` is classified by it and
  nothing wider enters the contract, proven by a test that a wider
  key, and a channel key, refuse naming the shape.
- A `src/` at the workspace root refuses in the layering check
  naming the move, proven by a forced test.
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

- the root `pyproject.toml`, one region at the end of the file, for
  the repository's own tables: today's three basedpyright execution
  environments for the imported tests. TOML decides what a tail
  region can say, whole tables and array-of-table entries and never
  a key inside a table the render wrote, so where a tool spells
  extension as its own key (ruff's `extend-per-file-ignores` and
  `extend-select`) the region uses it. The repository's own dev
  dependencies take no region (ruled 2026-09-28, open item 11): a
  member declares what its dev loop needs as a `dev` extra of its
  own, `livery-workshop[dev]` carrying `types-pyyaml`, which the
  answers' dev entry already knows how to carry, and the base
  template loses the line. A region inside a list is allowed
  wherever the format allows a comment inside one, its entries
  written with trailing commas, and is built when a carrier
  appears;
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
  finds nothing; the three execution environments live inside the
  root's `tables` region and `types-pyyaml` in the workshop member's
  `dev` extra.
- A workspace born now and a workspace rendered before the regions
  existed both gain the markers with empty content on
  `fm template.apply`, proven by the conformance chain and by a test
  over a marker-less committed file.
- A managed file without comments keeps its appended tail across
  `fm template.apply` and reports a changed prefix as drift, proven
  by a test over a fixture file, since no managed file needs the
  form today.
- `fm explain pyproject.toml` names its region with the lines its
  markers enclose, and `fm explain` on a tail-form fixture
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
  section states it (ruled 2026-09-28, open item 10, with open
  items 6, 7 and 8 as leaned). A tool that reads one file per project gets
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

Two more things the record carries, ruled 2026-09-28:

- **Slots.** A line the base template writes that a layer needs
  different is split out of the template until it is a slot the
  records fill: `register_slot(name, compose, default)` by the
  owning layer, `contribute(slot, value)` by any layer or record,
  composed by the rules the design section states. The `dev`
  dependency group's tool entries and pytest's `addopts` are the
  first slots; the python layer declares both and its check records
  fill them, so the base template writes only `uv`, the members and
  the layer requirements. A whole-file override and a removing
  fragment were argued down (open item 7).
- **Package-settable options.** The record declares the options a
  package may set, each with a type and a default; a kind sets
  defaults, a layer overrides, a package sets them in its contract
  under `[checks.<name>]`; the render writes them where the tool
  reads a file and the check passes them where it invokes. The
  python test record declares `parallel`, and a package that says
  `parallel = false` runs under `-n 0`. `typecomplete` is such an
  option on the basedpyright check family, on by default, off per
  package (open item 2); the release wave follows the gate and the
  receipt records whether the role ran.

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
- A test layer contributes to the `dev` group slot and the rendered
  group carries the line; unlisting the layer removes it; a scalar
  slot claimed twice at one level refuses naming both; proven by
  forced tests, the refusal first.
- A package setting `[checks.test] parallel = false` runs its suite
  under `-n 0` while its siblings run under `-n auto`, and a package
  turning `typecomplete` off skips it by name, proven by tests over
  the invocation and the gate output.
- `fm check` green, output unchanged.

### Phase 4b: the check claims its files

The other half, and the one that waits. A check record gains its
claim, named in the category vocabulary rather than as globs, and a
check judges the files its claim reaches. The docs check's claim,
landed narrowly in phase 3b, becomes an ordinary claim here. Ruff is the proof
again: the rules it applies to a package's configuration files
differ from the rules it applies to sources, and the rendered
`per-file-ignores` is generated from the claim rather than
written by hand.

This phase needs the category registry of phase 3b and lands after
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

### Phase 4c: prose fragments serve two readers

The prose a layer ships, the voice, the standards, what a kind's
package looks like, becomes a registry of fragments with the
convention ruled 2026-09-28 (the design section states it): a file
named `<section>.[<kind>.]<topic>[.<audience>].md` in the layer's
content, or `register_fragment(section, topic, render, kind=None)`
with a render that takes the audience, `agent`, `human` or None.
The base defines the sections and their order; `register_section`
adds one at a declared position. A kind in the name gates on a
package of that kind or a derived kind; an empty render is omitted;
a topic collision refuses at sync naming both files. `fm sync`
delivers the agent-or-both set flat under `.workshop/fragments/`,
`<section>.[<kind>.]<topic>.md`, byte for byte the source for a
static file, materialised with the manifest. The first dynamic
fragments are the gate's composition from the check registry, the
verbs by layer, the kinds present and the locked tools, so no
fragment names a checker by hand again. The entry file is an agent
layer's (phase 9); the human rendering is the docs layer's (phase
6). The repository's own fragments live in `fragments/` at the
root with the same names and come last.

**Acceptance**

- A topic collision, a `<topic>.md` beside a `<topic>.agent.md`, an
  unknown section and an unknown kind each refuse at sync naming
  the files, proven by forced tests first.
- A kind-gated fragment is delivered only while a package of that
  kind or a derived kind is present, proven by both arms.
- The gate fragment renders the registered checks for the kinds
  present, differs between a workspace with and without a test
  layer's check, and renders differently for the two audiences,
  proven by tests over the render.
- A static fragment's delivered bytes equal its source, and an
  edited delivered copy is kept and named, proven by a test through
  the materialiser.
- `fm check` green, output unchanged.

### Phase 5: the test role and coverage measurement by kind

The test role joins the registry, and measurement becomes the
kind's answer: a backend returns per-package path-to-percent for
its run. Python answers through coverage.py, unchanged.
`cpp-conan` runs ctest instrumented and reduces, on the leg that
measured, to one answer per source file: the lines the tests
executed and the lines that could have been executed. The
measurer follows the compiler the conan profile names, never the
operating system (ruled 2026-09-29): gcc to gcov (`--coverage`,
`gcov --json-format`), clang and apple-clang to llvm-cov
(`-fprofile-instr-generate -fcoverage-mapping`, `llvm-profdata
merge`, `llvm-cov export -format=lcov`), msvc to Microsoft's Code
Coverage engine, `dotnet-coverage instrument` on the
`/PROFILE`-linked binary and `collect -f cobertura` over the
instrumented run, native instrumentation enabled in its settings.
clang on Windows, `clang-cl` on the MSVC ABI or MinGW clang, is the
llvm family, and MinGW gcc the gcov family; an instrumented
`clang-cl` link goes through `lld-link`, since MSVC's `link.exe`
leaves clang 20's profile names section empty and only clang 22
and later survive it. gcov and the llvm tools are host tools beside
the compiler that built, verified on the host and never downloaded;
Microsoft's engine is a tool of the store, `dotnet_coverage` on the
.NET SDK, which the cpp-conan kind requires on the lock's Windows
hosts alone (`dotnet_coverage@windows`), so a macOS or Linux sync of
a workspace with a native package installs neither. A host with the
compiler and without its measurer refuses on that leg by name. On Windows the gate builds with MSVC unless `CXX` names
another compiler: with `cl` off PATH it enters the newest Visual
Studio's C++ build tools itself, `vswhere` naming the installation
and its vcvars batch file read back through `set`. The union is a set union of lines per file across the
legs that measured, so two compilers never merge raw profiles; a
file both measured has the union of what either could reach as its
denominator. C++ floors are line coverage, Python's stay statements
and branches: a floor is the kind's own measurement, ratcheted
against itself. The `[qa] coverage-floor` is enforced by the same
core floors, grace, and prose, and a package no leg measured refuses
in the union, never passing vacuously. Windows is tier 1, so every
leg measures; the store's unit row carries its measurer, and a row
the reader cannot take falls out and re-measures, no migration. A
cpp-conan member joins the conformance loop, the runner image
gaining gcc and gcov if it lacks them.

**Acceptance**

- The Windows spike showed per-line data for one program from MSVC
  under Microsoft's Code Coverage engine and from `clang-cl` under
  llvm-cov, its log quoted in the decision record and the MSVC tool
  chosen there (2026-09-29, PR #902, never merged).
- A C++ package below its floor fails `fm coverage.enforce` with
  the same prose Python gets, proven by a forced fixture.
- The CI union job unions the three legs' line sets, gcc's, clang's
  and MSVC's, and enforces once, proven by the conformance chain
  with its cpp-conan member.
- A kind without a measurer skips coverage by name, never
  vacuously passes; a host with the compiler and without its
  measurer refuses on that leg by name, proven by forced tests.
- On Windows the gate enters MSVC's environment itself and a run
  built with MSVC is measured by Microsoft's engine, the verdict
  read from ctest's own report: proven by
  `test_a_green_ctest_run_built_with_msvc_is_measured_by_microsoft_s_engine`
  on the windows-latest leg, and by the faked-engine tests on every
  leg.

### Phase 6: the docs layer

Extraction moves to the kind record (Python's griffe wiring is the
first implementation), policy to layer registration, and assembly to
`livery.workshop.layers.docs`, a layer derived from the base that registers
no language (ruled 2026-09-28): the site build and its CI job, the
docs categories (prose, nav, asset, generated, example), the examples
check, the development section rendered from the prose fragments of
phase 4c, one page per section, and the theme slot. The layer asks
each kind record for its extractor and its example runner; a kind
without either says so, and the site names the absence.

The examples become files. A package's examples live under
`docs/examples/` as python files, and a page includes them by
snippet, the extension the rendered site config already enables,
with named sections where a page shows one file in parts. The one
harness ships in the layer and runs each example through the kind
record's runner, pytest for python, replacing footman's
page-as-session harness and the three markers it needed; a block
that must show invalid code stays prose and never runs. Whether the
examples work outside the playground's environment shims is unknown
today, and this check is what finds out.

Layer assets are staged into the build from the wheel: the base's
palette and type css move from seeds to layer content, a theme
layer's files land the same way, and the build wires them into
`extra_css` in layer order with the workspace's own css last, so
the seeds keep only the instance's identity, its og-card and an
empty css of its own. The theme block's values become a slot the
layer declares, `docs.theme`, which a theme layer fills. The
playground is not this phase: it becomes a layer of its own in the
split plan, depending on docs and python, with its assets staged
the same way, its page and its second examples check in the
browser's environment, and the browser runtime declared as its
tool. C++ extraction stays out of scope.

**Acceptance**

- A test layer flips the private-members policy and the rendered
  site reflects it, proven by a docs-build test.
- A kind without an extractor produces a site section naming the
  absence, never an empty page.
- An example file under `docs/examples/` runs through the examples
  check and reports its own file and line on failure; a prose-only
  page edit reaches the site build and no test, proven by tests over
  the affected walk; footman's docs build with its examples as files
  is byte-equal in content to the build before the move, differences
  named and ruled in the phase record.
- A test layer's css appears in the built site after the base's and
  before the workspace's, and a theme contribution to `docs.theme`
  changes the rendered fonts, proven by build plus grep.
- The livery site's content is unchanged by the seam: the site
  is built before and after, the builds are diffed, and every
  difference is named and ruled in the phase record. An empty
  diff is the expected outcome, not the requirement.

### Phase 7: the conformance kit

`livery.workshop.testing` ships the pinning tests a third-party
kind or check plugin must pass: the backend protocol, skip
printing, narrowing behaviour, fix ordering, config-fragment
drift, a fragment resolving per kind down the chain, a withdrawn
check's file removed only when unedited (contract 11), a category
table that orders and refuses a tie, and a contribution module that
mounts only with its target.
The workshop's own kinds and checks run the same kit, so the kit
cannot drift from the enforcement. Once phase 8 exists the kit
carries the base-alone case of contract 10 too.

**Acceptance**

- The builtin python, python-nanobind, cpp-conan kinds and every
  builtin check pass the kit, wired into `fm check`.
- A deliberately broken fake plugin fails the kit with the
  violated clause named, proven per clause.

### Phase 7b: template series per layer, composed on demand

The template channel as ruled 2026-09-28 (contract 14). Today
`fm release.templates` publishes one composed tree, base plus the
last layer's overlay, to the artifact's default branch under the
publisher's version, and a born project's answers record that one
source; an overlay therefore made its home the base's publisher.
Instead:

- `release_templates` publishes every tree-shipping layer in the
  wave, each to a branch of the one artifact named by the layer,
  tagged `<layer>/v<version>` in lockstep with the layer's release
  tag; a layer inside the workshop wheel publishes nothing, its
  templates ride the `workshop` series. The default branch holds a
  README naming the series. `composition.toml` goes.
- The render composes on demand: for each listed layer that ships a
  tree, the series at the version `uv.lock` pins for that layer,
  fetched through the store's cache, composed in layer order into a
  scratch tree copier renders; a home composes its local trees as
  now. Nothing beyond the lock records the stack, and `fm update`
  moves the wheels and the tags follow.
- The drift check compares against that composition, so a lock
  that moved while the files did not is drift, the signal
  `fm update` acts on.

**Acceptance**

- A home whose stack ships two trees publishes two series with
  their own tags, and a second publish of the same content is quiet
  while different content under one tag refuses, proven by tests
  over a local artifact, the refusal first.
- A project listing base and a test layer with an overlay renders
  the composition of the two series at its pinned versions; bumping
  the base's pin alone changes the render; proven by the
  conformance chain.
- `grep -rn composition.toml packages/workshop/src` finds nothing.

### Phase 8: the house layer, and the base's small set

The proof that the seam separates the workshop from livery's
opinions. The house is `livery.housekeeping` (ruled 2026-09-28,
open item 13), a member of this workspace listed after the
languages, depending on the base alone. Its plugin module declares
`WORKSHOP_FOR`, and `livery.housekeeping.python` carries the
registrations for the python layer: mypy on its three platforms, ty
and pyrefly under the `types` role, `D` with the google convention
contributed to the ruff slot, and the three tools contributed to
the `dev` group slot; a `livery.housekeeping.cpp` and a
`livery.housekeeping.nanobind` follow when the house has opinions
there. `typecomplete` stays the python layer's, optional per
package. Its prose fragments carry `voice.interaction-voice.md`,
`standards.documentation-standards.md` and the house half of
today's `CLAUDE.workshop.md`, the docstring convention and every
other line that states livery's preference rather than what the
workshop enforces, in the agent and human renderings phase 4c
gives them; the base's fragment keeps the rest, and the sentence
naming the checkers becomes the dynamic gate fragment. The base
template's `pyproject.toml` loses `[tool.mypy]`, `[tool.ty]` and
`[tool.pyrefly]`, which arrive as the house's fragments through
phase 4, and loses the three tools from the `dev` group, which
arrive through the slot.

The python package kind and its tooling leave the base too, into
`livery.workshop.layers.python` (ruled 2026-09-28, open item 17), a layer
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
its README and LICENSE seeds as an overlay in its own series of the
templates artifact (phase 7b, open item 16 resolved 2026-09-28) and
nothing else in its tree: everything else it says is a fragment, a
slot contribution, a region or content, all in the wheel, and a
need none of those can carry is first a gap in the base template.

Needs phases 3, 4 and 7b: a check moved out of the base takes its
configuration and its tool with it, the contributions need the
mount of phase 3, and the seeds need the series of 7b. Independent
of phases 5 and 6. Livery's own gate does
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
- The house's series carries its two seeds and nothing else, and
  the `workshop` series is unchanged by the house's presence, proven
  by a test over the published trees.
- A project listing the house and the python layer mounts
  `livery.housekeeping.python` and no C++ contribution, `fm layers`
  says so, and its `for` entry lists python alone after `--fix`,
  proven by the conformance suite.

### Phase 9: the agent layer

The Claude pieces leave the base (ruled 2026-09-28): `.claude/`
skills, hooks and settings, the `hooks.pre-bash` verb, and the
assembly of `CLAUDE.md` move into `livery.workshop.claude`, a layer
depending on the base. Its one job beyond delivering its content is
the entry file: one import line per delivered fragment in section
order, the repository's own fragments last, written by `fm sync`
only while the layer is listed. The prose fragments stay with the
layers whose opinions they are; a second agent's layer would
compose the same delivered set into its own entry file. A workspace
that does not list the layer has no `.claude/` and no `CLAUDE.md`.

**Acceptance**

- A workspace born without the layer has no `CLAUDE.md` and no
  `.claude/`, and `fm sync` writes neither, proven by the
  conformance suite's base-alone fixture.
- This workspace's `CLAUDE.md` after the move imports the same
  fragments in the same order as before it, proven by a diff kept
  in the phase record.
- `grep -rn "claude" packages/workshop/src/livery/workshop --include=*.py -il`
  finds only the layer's own modules.

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
| Windows C++ coverage deferral (phase 5) | Microsoft's engine through the store's `dotnet_coverage` in the entered MSVC environment (2026-09-29, #912) |
| `.clang-tidy` and `.clang-format` seeded per package, never rewritten | managed per-kind renders of the clang-tidy and clang-format check records (phase 4) |
| The three basedpyright execution environments naming this repository's packages in the base template | the repository's own tail region in the root `pyproject.toml` (phase 3c); the lines themselves are the tracked content pass's debt |
| The forge layer's `.forge.dev.env` rule in the base `.gitignore` template | the forge layer's fragment for `.gitignore` (phase 4) |
| The hand-written `[tool.ruff]` stubs in four packages | the claim-derived `per-file-ignores` (phase 4b); the docstring carve-outs inside them are the content pass's |
| The four checkers and the voice fragments shipped by the base | `livery.housekeeping` (phase 8) |
| The `dev` group's hand-written tool lines and pytest's `addopts` in the base template | slots the check records fill (phase 4) |
| The three parse walks inside the layering check | the one traversal with registered rules (phase 3) |
| footman's page-as-session harness with its three markers, and the playground assets hand-copied into two packages' docs | examples as files run by the docs layer, assets staged at build (phase 6, then the playground layer) |
| The pre-composed template artifact, `composition.toml`, and the last-layer publisher | template series per layer, composed on demand (phase 7b) |
| The Claude skills, hooks and the `CLAUDE.md` assembly in the base | `livery.workshop.claude` (phase 9) |

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
  `livery.workshop.layers.cpp` (Willem: sounds good), so a python-only
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
  from the base). `livery.workshop.layers.python` beside
  `livery.workshop.layers.cpp`, each a layer depending on the base, and
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
- 2026-09-28, phase 3 built up to the closure lint (issue #876,
  stacked on livery#829): a layer's plugin module may declare
  `WORKSHOP_API_VERSION`, and mount refuses another version than the
  workshop's naming both (contract 8); a layer's checks register
  through `register_check` with the layer named on the record, and
  the gate prints what a layer registered and what builtin it
  withdrew (`unregister_check(name, by=layer)`), so a narrowed gate
  is legible (contract 4); a layer declares the layers it depends on
  in `WORKSHOP_DEPENDS`, the layering check refuses a
  `[workspace] layers` list that lacks one or orders one after its
  dependent, and the fix mode appends the missing line before its
  dependent with a comment naming who requires it (open item 18's
  ruled form: the list stays the whole truth, never pulled in at
  mount); `fm layers` names who requires each layer; `fm doctor`
  lists installed layers the contract does not mount, which do
  nothing until it does. Not built: the layer's own tool
  declaration, which waits on open item 14, and the soft dependency
  of open item 18.
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

- 2026-09-28 and 2026-09-29, the walk (Willem ruled every open item
  the phases waited on, one by one, with the agent supplying context,
  options and examples; each line below names its item):
  - Open 14: a layer declares its tools as `WORKSHOP_TOOLS` on its
    plugin module; a registration at mount is the same channel to
    grow into, and a shipped TOML file was argued down because its
    cost depends on where the layer lives and `fm layers` takes its
    readability advantage. An in-repo layer is a member package.
  - The tool records' future home is ticket livery#882, not a
    musing: musings are for what has no actionable decision yet.
  - Open 10: the check record names its tool's discovery shape and
    the render follows it; open items 6, 7 and 8 as leaned. Open 7's
    answer is the slot: a contested base line is split out until the
    records fill it, a whole-file override and a removing fragment
    both argued down; the python layer's test check contributes
    pytest, pytest-cov, coverage and xdist, ctest contributes
    nothing to the venv.
  - Open 9, all seven: a category rule is a pattern table where
    nothing reads state; most specific first, a tie refuses; the
    per-package exception is a `[categories]` table on that axis
    alone; both ladders migrate at once; `fm explain` prints
    category, channel, supplier and claims; one store keyed by axis
    with typed per-axis functions, since the `object` answer is
    avoidable and the promotion trigger goes; "does the site read
    it" is the docs check's claim. The axis is named **category**,
    since `part` reads as directories split into parts. A `src/` at
    the root refuses in the layering check naming the move.
  - The playground is a layer of its own, since not everyone wants
    it; examples live as files under `docs/examples/` included by
    snippet and are tested twice, standalone by the docs layer and
    in the browser's environment by the playground layer; the
    examples check stays with docs, since a project with docs and no
    playground still wants its examples proven.
  - A check's configuration is overridable per package generically:
    the record declares the package-settable options, a package sets
    them under `[checks.<name>]`, phase 4. Open 2 follows:
    `typecomplete` is such an option on the basedpyright family, on
    by default, never a release refusal and never the base's.
  - Direction: as little unnecessary work as possible. A check's
    inputs, its claimed categories plus the facts it declares,
    digested per check in the gate record, are the next lever after
    4b (open item 19).
  - The docs layer derives from the base, not from python, reading
    each kind record for its extractor and example runner; the
    playground depends on docs and python; the house on the base
    alone. A theme is a layer: files as content staged into the
    build, values as contributions to the docs layer's slot; updates
    by wheel bump. `register_theme` was argued down for the generic
    slot, since the core knows no fonts.
  - Open 11's inner questions: the marker text stands; `types-pyyaml`
    leaves the base template as the workshop member's `dev` extra,
    named `dev` because it declares whatever the member's dev loop
    needs; a region inside a list is allowed wherever a comment is,
    built when a carrier appears; the owning template names a
    region, a repository cannot add one.
  - The layering check owns the one AST traversal and takes
    registered rules: seconds in `fm check` are worth engineering,
    since it ran 42 times on one desk on 2026-09-28; the
    serial-then-parallel ordering stays implemented once.
  - The Claude pieces are a layer of their own, `livery.workshop.claude`;
    the prose fragments stay with the layers whose opinions they are.
  - Prose fragments: a default naming convention and an optional
    audience hint, agent or human, so one set writes the entry file
    and the development documentation in a structured order. Ruled
    to the path alone with no front matter, then flat and
    dot-separated: `<section>.[<kind>.]<topic>[.<audience>].md`, a
    kind in the name because one layer registers several kinds and
    a layer contributes into another layer's section; delivered
    with the audience suffix dropped, byte for byte the source.
  - Open 12: the sets as tabled in the design, the python layer's
    ruff set is today's minus `D`, and the docstring sentence moves
    to the house with the rule.
  - Open 13: the house is `livery.housekeeping`; the collision with
    the sweep's prose sense is mild and prose about cleanup says
    "the sweep".
  - The template channel composes on demand, never pre-composed:
    each tree-shipping layer publishes its own files to a branch
    named by the layer, tagged with its version; the tag per layer
    derives from the version the lock pins, so nothing else is
    recorded; `composition.toml` and the two-hop rule go. Open 16
    then closes: the house's seeds ride an overlay in its own series,
    and registry-contributed files are not needed, since each case
    has a home, an overlay, a check record's render, build-time
    staging, or content delivery.
  - Open 17: the sequencing stands, python and docs first.
  - Open 18 (2026-09-29): contributions by data. A layer's plugin
    module declares `WORKSHOP_FOR`, target layer to contribution
    module; the mount grafts a contribution when both are mounted;
    no soft-dependency attribute and no conditional mount code; the
    fix writes the resolved targets into the entry once and from
    then on `for` is the truth, which gives the per-project opt-out
    for free. Willem: "I like it". The reproducibility argument
    against conditional code was withdrawn as overstated; what
    separates the shapes is legibility.

- 2026-09-29, phase 3's remainder built (issue #884): `WORKSHOP_TOOLS`
  on the plugin module is the profile's fourth site, `layer <import
  path>`, and `fm layers` prints each layer's tools; `WORKSHOP_FOR`
  maps a target layer to a contribution module the mount imports once
  both are mounted, whichever mounts later, and the closure lint's
  `--fix` writes the resolved targets into the entry once, after which
  `for` is the truth, a deleted name staying deleted and an unlisted
  or undeclared name refusing; the layering check parses each source
  once through a memo keyed by path, size and modification time,
  its three walks became the first three registered rules
  (`runner-terminal`, `forge-stdlib-only`, `sibling-references` with
  `write_edges` as its fix), `register_ast_rule` takes a kind's or a
  layer's, a rule's fix runs inside the check's rewrite and its
  judgment inside the judge, and each problem carries the rule's
  name. Two things the phase found. A contribution module mounts by
  import alone: footman's `plugin()` takes entry points only, so a
  contribution carries registrations and never verbs, which stay on
  the owner's plugin module. And `for` is written only when at least
  one target is listed: an empty `for` written early would be the
  truth and silence a target listed later, so the fix waits for the
  first one. Not built: nothing of the phase; its acceptance lines
  are met by the tests named in the commit.

- 2026-09-29, phase 3b built (issue #886): one store keyed by axis in
  `_categories.py`, `register_categories` and `category_of` over
  pattern tables, `register_channels` and `channel_of` over ranked
  callables; specificity is the literal characters a pattern names,
  a tie between two rules of one specificity claiming one path
  refuses naming both, and a derived kind inherits the tables up its
  chain with the nearer kind winning. The builtin tables register
  beside the kinds (the base's docs categories on the abstract base
  kind, python's, cpp-conan's, the workspace unit's) and the
  provenance ladder became nine ranked channel rules; every
  backend's `classify` and the protocol slot are gone. A package's
  `[categories]` table wins over its kind's rules; a `[channels]`
  table refuses. `fm explain` prints the category and the channel
  with their suppliers, and `claimed by: site` on a file the build
  reads; a `src/` at the workspace root refuses in the layering
  check. The docs job's condition (livery#839) is the docs check's
  claim, `site_reads`, and `fm docs.build` skips a pull request's
  build when nothing the site reads changed against the base, on the
  same terms the check legs narrow on. Two things the phase found.
  A tie is detected when a path meets it, at the first gate or
  explain over that path, rather than at mount: two patterns'
  overlap is not decidable from the patterns alone, so the refusal
  names the path as well as both rules. And the workspace unit is
  the root's tests unit where one exists and a root-only record
  otherwise, so `fm explain notes/x.md` answers in a workspace with
  no `tests/`. One acceptance line is open: the conformance chain
  has no notes-only pull request scenario yet, so the skip is proven
  by unit tests over the decision and the claim, and the chain's
  proof waits for that scenario (open item 24).

- 2026-09-29, phase 4's first change built (issue #888): slots in
  `_slots.py`, `register_slot` with a union or nearest rule and
  `contribute`, the dev group's tool lines and pytest's `addopts` as
  the first two, filled by the check records' `contributions` and
  rendered through a `slots` injection the base template reads, so
  this repository's `pyproject.toml` lost the hand-written tool
  lines and their comments and kept the same requirements;
  package-settable options on the record, `[checks.<name>]` in the
  package contract, every check carrying `enabled` and the python
  test check `parallel`, a package that is not worker-safe running
  its suite under `-n 0` in a run of its own, an unknown check, an
  undeclared option or a wrong type refused in the layering check;
  and the check's `tools` on the record, the profile reading them as
  `check <name>` sites, the kinds keeping what operates them (uv,
  cmake, conan, ninja) and clang-format, clang-tidy, ruff, pytest and
  the checkers riding their records, so unregistering a check
  removes its tool. Two things the change found. pytest stays on the
  test record as a store tool beside its venv line, since the typed
  handle the runner calls comes from the store and the venv copy is
  the one that imports the environment. And the shared test run
  runs whatever the members are, since the workspace's own tests
  ride it; the pin over the gate's members counted on that. Not
  built here: the second change, the configuration fragments and
  the discovery shape, the editor settings and extensions, and the
  ruff and clang-tidy proofs.

- 2026-09-29, phase 4's second change built (issue #890): a check
  record carries `fragments`, one per rendered file it has something
  to say in, and the render composes them in check-name order over
  the same data the template reads, so the base `pyproject.toml`
  template lost every `[tool.*]` table to the format, lint, typecheck
  and test records and reads one `fragments` injection instead;
  `.vscode/settings.json` reads a fragment block the same way and the
  ruff record names itself the python formatter there;
  `.vscode/extensions.json` is a new rendered file naming the
  extension ids the records carry (ruff's and basedpyright's) with a
  region for the repository's own, and a record without a verified
  id recommends none (open item 8). The native kinds' `.clang-format`
  and `.clang-tidy` are managed renders of the clang-format and
  clang-tidy records' fragments per kind, resolved down the chain,
  in place of seeds the template never rewrote; a package's
  `.workshop-rendered` receipt records what the render wrote, so a
  withdrawn check's file goes only when nobody edited it, an edited
  one is kept and named, and an unreceipted copy is adopted where it
  equals the render and kept as an override where it does not
  (contract 11). Proven: unregistering the ruff records leaves no
  ruff table, editor line, extension or profile entry; a layer's
  `.clang-tidy` fragment for the nanobind kind differs from the
  cpp-conan kind's and each resolves to its own. Two things the
  change found. The fragments need the roster split the template
  makes (`py` and `native`), so the injection computes it once in
  python and hands it to both; and the drift gate judges a fragment
  file only where a receipt says the render wrote it, since a copy
  nobody receipted is either adopted or an override, never drift.
- 2026-09-29, phase 4b built (issue #892): a check record carries
  `claims`, the categories it judges with the rules it withholds
  there and the suffixes it reads; `judged_files` names the files
  of a package a check's claims reach, the files git holds under
  it, tracked or untracked and not ignored, so a build tree under a
  native member reaches no claim; `fm explain` prints the checks
  whose claims reach a file beside the site's; and the lint
  fragment's category-shaped per-file ignores render from the
  claims over the present kinds' tables and the workspace unit,
  `packages/*/tests/**` and `tests/**` with `D1`, so two checks
  claiming one category under different rules land in one entry
  with both sets, in a stable order. A claim on a category no table
  knows refuses at registration naming the vocabulary. Three things
  the phase found. A category is a role, not a language: a native
  package's `source` is C++ and its `conanfile.py` is
  configuration, so a claim names the suffixes its tool reads, and
  ruff's records apply to `cpp-conan` as well as `python`, which
  puts ruff in the native kind's tool profile and keeps its claim
  on a native package to `conanfile.py`, while the native kind's
  `tests/**/*.cpp` renders no python ignore. The python checks keep
  their directory invocations, since ruff over the tree already
  judges every python file the claims name, and a native member in
  a scoped gate hands ruff the files its claims reach by name,
  which the scoped gate skipped before. And the four packages' own
  `[tool.ruff]` stubs stay: ruff's `extend` replaces the
  per-file-ignores table wholesale rather than merging it, so a
  package that carries carve-outs must repeat `D1` beside them, and
  those carve-outs are the tracked content pass's debt as the phase
  says.
- 2026-09-29, phase 3c's leftover landed (issue #896): `types-pyyaml`
  is the workshop member's `dev` extra, and the answers' dev entry
  for the member is `livery-workshop[dev]`; the base template names
  nothing of this repository, which closes phase 3c's open
  acceptance line.
- 2026-09-29, phase 4c built (issue #894): a layer's prose is a
  registry of fragments, `livery.workshop._prose`. A fragment is a
  file in the layer's `content/fragments/` named
  `<section>.[<kind>.]<topic>[.<audience>].md`, or a registration
  with a render that takes the root and the audience; the base
  defines the sections in order, identity, voice, standards, rules,
  workflow, gate, verbs, kinds, tools, and `register_section` adds
  one after a named anchor. A name outside the convention, an
  unknown section, an unknown kind, a topic spelling an audience,
  and two fragments of one delivered name in one reader's set, a
  `<topic>.md` beside a `<topic>.agent.md` included, each refuse
  naming the files; the reader's set is validated at the same
  delivery. A kind in the name delivers only while a package of that
  kind, or one deriving from it, is present, an abstract kind never
  counting. `fm sync` delivers the agent's set flat under
  `.workshop/fragments/` in section order, the mounted layers in
  mount order inside a section, through the materialiser: a shipped
  fragment byte for byte, a rendered one under a header naming its
  origin, an edited copy kept and named, a withdrawn one removed with
  the manifest rewritten; the entry file imports that set, the
  repository's own `fragments/` after it, then `CLAUDE.project.md`.
  The materialiser gained a bytes delivery for what exists nowhere
  on disk, and its managed ignore lists every copy the directory's
  manifest owns. The base's first rendered fragments are the gate's
  checks for the kinds present, the verbs by layer from one `--json
  --list` of the workspace, left out without a tasks file or the
  runner on PATH, the kinds present and the locked tools, so the
  workshop's rules fragment names no checker by hand; the three
  shipped fragments took their convention names,
  `voice.interaction.md`, `standards.documentation.md` and
  `rules.workshop.md`, and the layer template's became
  `rules.<slug>.md`. Not built here: the human rendering into the
  site, the docs layer's (phase 6), and the entry file's move to the
  agent layer (phase 9). The birth test's fake lock became a lock of
  the schema, since the tools render reads it.
- 2026-09-29, phase 5's measurers (Willem's ruling): the compiler
  family is the package's own, and coverage never prescribes it. Each
  leg reduces its own data to executed lines over instrumentable
  lines per file, measurer chosen by the conan profile's compiler,
  gcc to gcov, clang and apple-clang to llvm-cov, msvc to a
  PDB-driven engine; the union is a set union of lines across the
  legs that measured; C++ floors are line coverage while Python's
  stay statements and branches; the measurer's tools are host tools
  beside the compiler, never downloaded. Windows is tier 1, so it
  measures rather than defers: MSVC has no compile-time line
  instrumentation (`/fsanitize-coverage` counts edges for fuzzing,
  `/GENPROFILE` is optimisation data), and the answer is at the
  binary level over the PDBs, Microsoft's Code Coverage engine
  (`CodeCoverage.exe collect` and `analyze`, shipped in Visual
  Studio and in the `Microsoft.CodeCoverage` package on nuget.org)
  or OpenCppCoverage (GPL v3, breakpoint-based, Cobertura). clang on
  Windows is the llvm family on either ABI. A cpp-conan member joins
  the conformance loop in phase 5. The spike runs first, on
  `chore/coverage-spike-windows`, and its result decides the MSVC
  tool; this closes open item 3 and replaces the phase's earlier
  wording, under which llvm merged profdata in the union.
- 2026-09-29, the Windows coverage spike (PR #902, branch
  `chore/coverage-spike-windows`, never merged; three rounds on
  windows-latest with Visual Studio 18 Enterprise, MSVC 14.51, LLVM
  20.1.8 on the image and clang 22.1.3 bundled with Visual Studio):
  the MSVC measurer is Microsoft's Code Coverage engine through
  `dotnet-coverage` (18.11.2, installed with `dotnet tool install`),
  which instruments the `/PROFILE`-linked binary statically and
  reports Cobertura with one `<line number hits>` per instrumentable
  line, 13 lines and 9 hit for the spike's program, the untaken
  branch and the never-called function at zero. Its dynamic mode
  covers .NET only ("Profiler was not initialized"), and native
  static instrumentation is off by default, enabled by
  `EnableStaticNativeInstrumentation` in its settings file. The
  deprecated `CodeCoverage.exe`, from nuget.org and from the Visual
  Studio copy, prints its usage for every command, and
  `Microsoft.CodeCoverage.Console` is not in the Visual Studio 18
  install. OpenCppCoverage 0.9.9.0 gave the same lines after a silent
  Inno Setup install and is not chosen: its last release is from
  2019, it ships as an installer and not an archive, and the
  maintained engine answers. clang-cl is the llvm family: clang 22
  bundled with Visual Studio gave full lcov with branches linked by
  `link.exe`; the image's clang 20.1.8 gave a profile `llvm-profdata`
  refuses ("symbol name is empty") linked by `link.exe` and a good
  one linked by `lld-link`, so an instrumented clang-cl link uses
  lld-link. The tool shape for phase 5: `dotnet` is a host tool
  beside the compiler, Visual Studio installs it, and
  `dotnet-coverage` comes from nuget.org through it, a delegated kind
  beside npm and pypi whose runtime is the host's .NET. Open item 3
  closes. Two verbs learned on the way: a point contributed on a
  branch alone cannot be dispatched (issue #903), so the spike rode
  the gate's check job through a `[[ci.schedule]]` entry, and a
  spike step without a timeout hung a Windows leg for forty minutes
  behind an installer's window.
- 2026-09-29, phase 5's first change built (issue #905): the
  measurement seam and the gcc and clang measurers.
  `livery.workshop._coverage_lines` is the shape every native
  measurer reduces to, per file the line number to hit count with
  every instrumentable line present, and its parsers read gcov's
  JSON, lcov and Cobertura; a run leaves one part per package at
  the workspace root beside coverage.py's own. The store's unit row
  carries its `measurer`, `arcs` or `lines`, and a row without one
  reads as arcs, so no record migrates. The cpp-conan gate configure
  hands CMake `coverage.cmake` through `CMAKE_PROJECT_INCLUDE`,
  which instruments by the compiler family CMake detected: gcc with
  `--coverage`, clang and apple-clang with
  `-fprofile-instr-generate -fcoverage-mapping`, and clang-cl linked
  by lld through `CMAKE_LINKER_TYPE`; MSVC links with `/PROFILE`.
  The test run clears the last run's counters, names where llvm's
  profiles land, and after a green ctest reads the lines with the
  measurer beside the compiler CMake recorded: gcov, or
  llvm-profdata and llvm-cov over the executables ctest names, xcrun
  answering on macOS. A family without a measurer or a measurer that
  is not there refuses by name; an MSVC run says it is not measured
  and leaves no part until the dotnet kind lands. The leg puts a
  lines unit per native suite beside the python arcs, a suite that
  ran without a part is named and not put, the union merges lines
  units per package across the legs and the records and writes them
  as parts, and the floors read a native package's line coverage
  over its `source` category; a native package with a floor that no
  leg measured refuses by name, never passing vacuously, and below
  its floor it fails with the sentence a python package gets. Proven
  by fixtures for every parser and refusal, by the leg and union
  harness with lines units, and by a real gate build on the host's
  toolchain, apple-clang through xcrun's llvm-cov on the desk and gcc
  through gcov on the Linux leg. Ruled while building: no single
  report format serves every compiler without converters (gcov has
  only its JSON, llvm-cov no Cobertura, dotnet-coverage Cobertura or
  its own XML), so each measurer's plainest line-level format is
  read directly and the standard is the reduced shape. Not built
  here: the MSVC measurer through `dotnet-coverage` and the `dotnet`
  kind (the second change), and the conformance loop's cpp-conan
  member (the third).
- 2026-09-29, the dotnet kind built (issue #907, phase 5's second
  change, its toolroom half): the store's `dotnet` kind installs a
  .NET tool package from nuget.org through the dotnet SDK, its
  runtime, with `dotnet tool install --tool-path` into a directory
  per version under the home's `dotnet/`, the shims the SDK writes
  under `bin` its entry points, no graph since the package carries
  its dependencies and the version pins the whole; `RUNTIMES` gains
  dotnet and a dotnet record may name it, an npm record may not. The
  bench gains two tiers, `dotnet` for the SDK from Microsoft's
  release metadata (the channels in active or maintenance support,
  the SDK version each release shipped, six archives at a versioned
  address) and `nuget` for a tool package from NuGet's flat container
  and its gzipped registration; the SDK is provisioned whole and
  linked into the prefix's bin, the tool installs through it, and a
  walk without a dotnet names the tool as skipped as it names one
  without node. The workshop orders the SDK before the tools that
  run on it and locks it beside them; `DOTNET_ROOT` rides the SDK
  record's environment, so a shim finds the store's runtime and
  never the machine's. Ruled on the way: the kind is cross-platform,
  since `dotnet-coverage` runs on the cross-platform runtime and
  only its native instrumentation is Windows-only, so no host list
  on delegated records; the SDK rather than the bare runtime, since
  `dotnet tool install` is the SDK's, at about 200 MB per host;
  PowerShell 7 becomes a download record from its GitHub releases,
  self-contained per platform, its surface the hand-written shell
  stub as before. The first records are `dotnet` (the SDK) and
  `dotnet_coverage`, read on the desk and recorded for the six
  hosts, every ingest check passing on each, `dnx` and PowerShell's
  `createdump.exe` excluded from their trees as npm is from node's;
  proven on the desk by a scratch workspace that locked both, took
  the SDK from the store with `DOTNET_ROOT` on its receipt, installed
  the tool through it into its own directory, and ran the shim to its
  version under that environment. Not here: the MSVC measurer that
  reads `dotnet-coverage`,
  which lands with the vcvars environment in the phase's third
  change, and the conformance loop's cpp-conan member after it.
- 2026-09-29, phase 5's third change built (issue #912): on Windows
  the cpp-conan gate enters MSVC's environment itself. With no `CXX`
  set and no `cl` on PATH, `vswhere` under the 32-bit program files
  directory, asked for the C++ build tools component of the host's
  architecture, names the newest installation; its `vcvars64.bat`
  (`vcvarsarm64.bat` on ARM64) runs from a batch file whose `set` is
  read back with keys upper-cased, cmd's hidden variables dropped;
  `CC` and `CXX` then name `cl`, so CMake takes MSVC over a MinGW gcc
  that is also on PATH, and a person who sets `CXX` chooses. The
  environment is read once per process and refuses by name when the
  installer is absent, no installation has the component, or the
  batch file leaves no toolset. A run built with MSVC is measured by
  Microsoft's engine through the store's `dotnet_coverage` handle:
  each executable ctest names and each shared library the build made
  is instrumented statically into a copy that takes the original's
  place for the run and gives it back after, so a build output is
  never instrumented twice; ctest runs under `dotnet-coverage collect
  -f cobertura` with child processes collected, the settings naming
  the build's own modules by file name in any letter case and
  telemetry opted out; the verdict is read from the JUnit report
  ctest writes, never from the collector's exit code, which is the
  collector's own, the same rule as never piping a verdict; the
  Cobertura report reduces to lines and lands as the part. ctest is
  now spawned directly for every family, the `test` target no longer
  (the two are the same run). `dotnet` and `dotnet_coverage` join the
  root's `[tools] requires`, so the handle has a stub and every leg
  deploys both, the SDK at 200 MB per host cached by the lock's hash.
  Decided here, for his review (open item 20): the requirement is the
  workspace's and not the kind's, since a kind-level requirement puts
  the SDK on every host of every cpp-conan workspace, the conformance
  loop's Alpine runner included, where the SDK's linux-x64 build does
  not run (musl); a Windows run without the tool refuses naming the
  line to add. Proven by the faked-engine and faked-ctest tests,
  refusals first, the toolchain tests with vswhere and the batch file
  faked, and the Windows leg's real build in the change's own gate
  run. Found on the way and filed: `fm doctor` and `fm env.check`
  name `cc` and `c++` missing on Windows (#913).
- 2026-09-29, the layer template is `package-layer` (Willem's ruling,
  issue #908): every other template names what it makes, and a layer
  is a python plugin by definition, so the python in
  `package-python-layer` said nothing; the directory, the birth's
  layer arm, the template vocabulary and the plan notes take the new
  name, and the kind mapping needs no change, since an unmapped
  template renders over the base and takes the python wiring by
  name. Placed before phase 7b publishes the template series under
  their names; no package born from the old name exists outside the
  conformance loop, so nothing migrates.
- 2026-09-29, host-scoped tool requirements (Willem's ruling on the
  MSVC measurer's tool: a requirement of a cpp package on Windows
  alone, installed on neither macOS nor Linux though the lock is one
  file for every host; issue #917). A requirement may end in
  `@hosts`: `dotnet_coverage@windows`, `tea>=1.1@linux,macos-arm`,
  each token a platform (every locked host of it) or a lock host key,
  anything else refused at parse with its site. The lock resolves a
  tool on the union of its requirements' scopes, the whole host list
  for an unscoped one; an entry locked on fewer hosts than the lock's
  carries `on` naming them and digests for those alone, and a scope
  reaching no locked host locks nothing, which `fm tools.lock` says
  per requirement. A runtime inherits the scope of the tool that runs
  on it, one requirement per distinct scope, so the .NET SDK rides
  with `dotnet-coverage` to Windows and nowhere else unless a site
  requires it outright. `fm sync` supplies the tools locked for its
  host, skips the others, sweeps a receipt they left, and refuses one
  named outright with the hosts it is locked for; the stubs are
  written for every locked tool, since the handle exists on every
  host and a call on the wrong one refuses through the backend. The
  cpp-conan kind requires `dotnet_coverage@windows` itself, so every
  consumer's Windows leg measures with no line in its contract, and
  this repository requires the same for its Windows leg's proof. The
  lock schema stays 1: the field is additive. Open item 20 closes.
- 2026-09-29, phase 5's conformance-loop member (issue #914): the
  loop births `loop-cpp` from `package-cpp-conan` beside
  `loop-native`, its members are the workshop's dependency closure
  (six of them) rather than a fixed list, the tree reset keeps what
  `fm sync` materialises (`.venv`, `typings`, `.workshop`), every git
  the pass runs has signing off through `GIT_CONFIG_*` on the task's
  environment (the birth runs as a child with that environment
  explicit, since a task's `os.environ` writes never reach a child
  spawned through the runner alone), the serving probe follows each
  member's kind (a python registry for wheels, conan's for the
  library), and the pins widen to the three members. Found on the way
  and fixed in the same change: the nanobind template's `_native.cpp`
  was not clang-format clean and `new.package` never locked the tools
  it wired (it syncs them now); the entry script installs a
  platform-wheel member only after the tools are in the environment,
  so the sync runs twice for a native workspace; the dev rig's runner
  image is node's Debian trixie (glibc 2.41, gcc 14.2, the runner
  binaries pinned) since pyrefly's linux-x64 build wants glibc 2.39,
  pinned to `linux/amd64` because the two clang records ship no
  linux-arm build; `fm sync` continues past a tool locked for no build
  on this host and refuses only one named outright; Gitea's pull
  lookup finds a merged pull by its former branch (`head.label`); the
  cpp template seeds `coverage-floor = 100`. Pass 18 landed all three
  members on the runner (`loop-cpp` built with gcc and measured by
  gcov), proved the ratchet, scoped, prose and tests legs with the
  widened pins, opened and merged its release PR, and main's run was
  green after a rerun on a transient index read (#929); its wave then
  hung for 50 minutes in the wheels job and was killed by hand: the
  build container is denied the runner's bind mounts and cibuildwheel
  3.4.1 spins on the dead attach (#931), so the release act's receipt
  probe has no evidence yet. Timing: about 80 minutes of runner work
  to the release PR's merge, 15 runs of 3 to 13 minutes, because the
  runner's store is per job and every job downloads every tool object
  (#930, with the runner's architecture and the clang records' source,
  undecided). Acceptance: the union across gcc, clang and MSVC is
  proven by the reducer's unit tests and the loop proves gcc's leg
  alone, so the third item stays open (item 26).

- 2026-09-30, phase 6's first slice (issue #955): extraction belongs
  to the kind. `KindRecord.extractor` names how a kind's API
  reference is extracted (`livery.workshop._kinds.Extractor`: the
  handler's name, the pages of a package, the handler's search paths
  for it, its configuration lines, the inventories); the Python
  kind's is mkdocstrings' python handler, moved whole into
  `livery.workshop._backends._python` (the module walk, the
  `python-paths`, the handler block, the inventories); a child kind
  takes its nearest ancestor's, so the nanobind kind extracts as
  python does and the cpp-conan kind has none. The site build asks
  the kind and knows no language: one handler block per extractor
  over every package that extracts through it, and a package whose
  kind has no extractor gets one generated page, `api/index.md`,
  naming the absence by kind, linked as its API entry, unless
  `[docs] api = false` declines it; the generated reference is
  rebuilt whole, so a decline leaves no stale page. Proven by tests:
  the absence page and its nav entry, the decline, a second extractor
  registered on a test kind reaching the config beside python's, and
  the ancestor rule. The livery site was built on main (3e015ff2)
  and on the branch and the trees diffed: 519 differences, none from
  the assembly. 93 are coverage pages, whose data differs between the
  two runs that produced them; the tool index's objects, refs and
  stamps differ as build state, the main checkout's tree having
  accumulated 1816 objects against a fresh 1640 and holding a stale
  `_generated/tasks` tree under the toolroom package; the rest are
  the API pages of the three modules this change edited, with
  `objects.inv` and `search.json` following them. Acceptance items 2
  and 5 of phase 6 are met for this slice; the remaining slices are
  the private-members policy as a layer slot, the assets and the
  theme slot, the examples check with examples as files, and the
  docs layer module.

- 2026-09-30, phase 6's second slice (issue #956): policy belongs to
  the layer. Whether private members are documented is a slot,
  `docs.members`, declared by the docs assembly
  (`livery.workshop._docs`) as a scalar with the values `public` and
  `all`, the default `public`, composed by the nearest rule, so one
  layer's contribution wins over the base's default. A slot may now
  declare its values (`register_slot(..., values=...)`), and a
  contribution outside them refuses at once, naming the contributor
  and the values. An extractor's configuration takes the composed
  policy as its third argument beside the search paths and the
  inventories, so every extractor applies it the same way; the Python
  one writes `filters = []` for `all`, which documents every member,
  and writes nothing for `public`, the handler's own default filter
  standing, which hides names with one leading underscore. Proven by
  tests: the refusal in the slots suite and the docs suite, no
  `filters` line without a contribution in the site config and the
  scoped preview, `all` writing the empty filter in both, the nearest
  layer winning and its withdrawal restoring the earlier value. The
  rendered proof is one build of this repository: the config
  assembled with and without the contribution differs by the one
  line `filters = []`; the site built in-process with `all`
  contributed as a layer would, since no layer does today, exited 0
  in 154 s with 708 pages, and the page of `livery.workshop._docs`
  renders `_slug`, `_label`, `_pages`, `_package_section` and
  `_register_builtin` as headings, which the baseline built on main
  (3e015ff2) renders 0 times. Acceptance item 1 asks for a
  docs-build test; the suite proves the policy where the build reads
  it and no test runs zensical today, so the builder-running test is
  open item 27, owed by the docs layer slice.

- 2026-09-30, phase 6's third slice (issue #957): assets belong to
  the layers. A layer ships site assets in its wheel under
  `content/docs/assets/`; the build stages every mounted layer's
  under `docs/_layers/<layer>/assets/`, rebuilt whole and gitignored
  by the template, and lists them in `extra_css` in layer order, then
  the packages' declared sheets, then the workspace's own
  `docs/assets/site.css` last, so the instance's rules win the
  cascade. The base's palette and type sheets are its content now,
  empty of rules, and the project template seeds `site.css` in their
  place; this repository's two seeds are deleted and the new one
  rendered. The installed layer's content directory is one function,
  `livery.workshop._layers.layer_content`, read by the sync, the
  provenance and the build. The theme block's values are a slot,
  `docs.theme`: a table of `language`, `font.text`, `font.code`,
  `features` and `palette`, the base's block the default, each
  contribution's keys merged over it in contribution order; a key
  outside those five, or a contribution that is not a table, refuses
  naming it and the keys; the override directory stays the
  assembly's own. Proven by tests: the two refusals, a layer without
  assets or not installed staging and listing nothing, the staging
  rebuilt whole so a sheet a layer stops shipping leaves no copy, the
  cascade order in the assembled config with the workspace's sheet
  after a package's, a font contribution changing one font and
  keeping the rest in the site config and the scoped preview, and its
  withdrawal restoring the base's. The rendered proof is one build
  of this repository with a test layer listed in the contract for
  that build only, importable by every child the build spawns, and
  `{"font.text": "Lato"}` contributed in-process: exit 0 in 132 s;
  the built home page links, in order,
  `_layers/livery.workshop/assets/palette.css`, its `type.css`,
  `_layers/acme_theme/assets/theme.css`,
  `packages/workshop/assets/workshop.css` and `assets/site.css`, the
  staged sheet is served, and the fonts request names Lato and Fira
  Code. Acceptance item 4 is met; the site's content is unchanged by
  the seam apart from the sheet links, since the moved sheets carry
  no rules.

- 2026-09-30, phase 6's fourth slice (issue #958): examples are
  files. A package's documentation examples live under
  `docs/examples/` as python files a page shows whole or by named
  section through the snippets extension. Footman's 120 running
  blocks on 26 pages became 39 files: one per page session, one per
  fresh session, and one per revision, a revision's file carrying a
  preamble above the shown part that gives it the names the page
  defined earlier; its 54 fragment fences stayed prose; the three HTML
  markers left footman's and toolroom's pages together with the
  harness that read them, so `packages/footman/tests/test_docs_examples.py`
  keeps the playground and gallery tests alone and the `--docs-page`
  option is gone. The kind record names its examples runner
  (`KindRecord.examples`, `livery.workshop._kinds.kind_examples`
  nearest along the chain); the python kind's runs pytest over the
  package's `docs/examples/` from the workspace root, and a `pytest11`
  plugin, `livery.workshop._pytest_examples`, collects each file as
  one item executed whole under its own path, every item carrying the
  `example` marker for a package's own setup: footman's
  `docs/examples/conftest.py` runs each inside a captured registry
  and a recording. The `examples` check, a role in every kind's
  default contract, claims the `example` category beside `lint` and
  the site; `format` claims it no longer, since an example keeps the
  layout its page shows, `lint` withholds every rule there but the
  name checks (`F401` and `F811` withheld as well), and basedpyright's
  rendered exclude skips the directory, since the full gate's checker
  reads every package whole while the desk's scoped gate hands it the
  sources and tests alone. The affected
  walk: a page reaches nothing, an example file its package's
  examples check and no suite (`Scope.examples`,
  `GateContext.examples`), and the package's source both. Proven by
  tests: an example that raises reports its own file and line
  (`docs/examples/bad.py:3`), only example files are collected and a
  conftest never, the runner's absent case and its red exit, the kind
  lookup with its inheritance, the check's registration and its skip
  of a tests-only member, and the walk's three cases. Footman's site
  was built before the move (main, 3e015ff2) and after, and the 73
  articles diffed with the chrome set aside: 55 identical, 13
  differing by the 67 removed marker comments alone, 4 (input,
  plugins, profiling, typechecking) by blank lines beside those
  comments, and typing's three tabbed blocks losing a paragraph
  wrapper the marker inside the tab had caused. Acceptance item 3 is
  met. The check's cost, from the CI store after the merge: 4.1 s on
  macOS, 4.4 s on ubuntu and 8.5 s on windows per leg, with footman's
  suite mark unchanged (277, 209 and 330 s against medians of 276, 210
  and 317 s before the move); the 1m59s a desk run showed was
  contention beside the gate's test step (issue #959, closed with the
  numbers). Landing the slice found three traps the fix commit
  closes: the full gate's basedpyright reads every package whole, so
  the example files are in its rendered exclude; ruff 0.16 formats
  python fences in markdown and would rewrite a snippet directive, so
  the formatter's rendered exclude names `*.md` and the example
  files; and a package's own `[tool.ruff.lint.per-file-ignores]`
  table replaces the workspace's claims under ruff's `extend`, so the
  four packages that carry one spell it `extend-per-file-ignores`
  (issue #965 asks the render or the layering check to enforce that).

- 2026-09-30, the docs layer's rulings, Willem's, before its first
  change (issue #968). An example file is brought as close to the
  normal checks as possible, filed as #967 rather than addressed in
  the examples move. A layer that lives inside the workshop wheel is a
  package under `livery.workshop.layers.<name>`, able to become a
  distribution of its own later but not one now, so the toolchain
  plan's cpp layer is `livery.workshop.layers.cpp`; for that,
  `livery.workshop` keeps its `__init__.py` and extends its path with
  `pkgutil.extend_path`, and so does `livery/workshop/layers/`,
  whose `__init__.py` carries the path extension alone, the layering
  check refusing anything more (a bare directory was the first cut;
  griffe, the API extractor's collector, collects nothing under one,
  so the pkgutil shape holds at both levels); the road after
  that is the pure PEP 420 shape, the public surface leaving the
  `__init__` for a public API module, because layers, third-party
  ones included, call the workshop and what they call is public API
  rather than the private modules everything uses today, and the site
  will document public API only. The site's job comes with the layer:
  mounting the docs layer contributes the job, and the job builds what
  the affected walk tagged. The base knows the minimum of docs
  generation, and everything zensical-related is the layer's.

- 2026-09-30, the docs layer's first change (issue #968): the site's
  assembly, verbs and slots leave the base for
  `livery.workshop.layers.docs`. The assembly module moved whole
  (`_site.py`, with `_llms.py` and `_taskref.py` beside it) into the
  layer package, whose `_tasks.py` is its `footman.tasks` entry point
  (`livery.workshop.layers.docs`) and registers the `docs` group under
  the layer's identity; the contract lists the layer as a table naming
  `livery-workshop` as its distribution, and a new project's seed
  lists it beside the base. The base keeps two seam modules:
  `livery.workshop._navblocks` (the markers and the block writers
  generators use; `rewrite_nav_block` stays public) and
  `livery.workshop._docs_contract` (the `[docs]` table and its
  generators, the docs tree's layout, the wheel-side docs copy, the
  publish seam, the categories the site reads), and gains
  `livery.workshop._site_files`, a registry of files a mounted layer
  renders at the CI emission, which the layer fills with the site's
  override template. `livery.workshop` and `livery.workshop.layers`
  extend their paths the pkgutil way, and the layering check gains
  `base-imports-no-layer`: a base module importing under
  `livery.workshop.layers` refuses, and a layers `__init__.py`
  carrying more than the path extension refuses. Proven by tests: the
  two refusals, a second tree's `livery/workshop/layers/acme`
  importing beside the docs layer in a fresh interpreter, the layer's
  entry point resolving to the `docs` group with its five verbs, the
  base task module free of the site, the seam module carrying what
  the base reads. The livery site was built on main (1d6b1426) and on
  the branch and the articles diffed: 601 of 615 shared pages
  identical; 11 API pages changed where docstrings name the moved
  modules, the workshop's task reference lists the docs verbs under
  the layer's provider, one coverage page carries the machine's
  report state and the workshop index the new paragraph; the three
  moved modules' pages sit under `layers/docs/` now with the layer's
  own pages and the three seam modules' pages new; toolroom's three
  task pages in the baseline were a stale generated tree of the main
  checkout, since toolroom advertises no task entry point. Not this
  change: the layer-contributed CI job (the third ruling), the
  extractor as data with the mkdocstrings lines out of the python
  backend, and the development section from the prose fragments.

- 2026-09-30, the docs layer's second change (issue #970): the site's
  jobs come with the layer. `livery.workshop._points` gains a registry
  a mounted layer fills, `contribute_job(point, job, entries=, gates=,
  layer=)`: a job for a builtin point with the entries it runs and
  whether the point's verdict waits for it; a point that is not
  builtin, a job name the point declares or another layer contributed,
  and an entry naming another job each refuse by name. `points()`
  composes the builtin points with the contributions, a contributed
  job sitting before the point's verdict job or last on a point
  without one, the verdict's needs being its declared needs and the
  gating jobs; `builtin_schedule()` places the contributed entries
  before the verdict's entry, whose `--needs` names the composed list,
  and the whole set is verified as one. The base declares neither the
  gate's `docs` job nor the merge point's `deploy`; the docs layer
  contributes both at mount, their entries naming the layer as their
  source. Proven by tests: the three refusals, a gating job before the
  verdict and in its needs and entry, a job on a point without a
  verdict landing last, the docs layer's two contributions with the
  verdict needing `check,docs`, and the three forges' renders listing
  the jobs. The rendered `ci.yml` differs from before in one way: the
  merge point's deploy job renders after govern and dispatch, since
  the point has no verdict job and a contribution lands last; GitLab's
  `pages` job follows it the same way. The build's answer to the walk
  is what it was, named here as the ruling's reading: the docs job
  skips when nothing the site reads changed against the base and
  regenerates only the mounts whose docs tree's digest moved, and the
  zensical build itself is whole. Not built: a doctor line naming the
  layer that brings the docs job.

- 2026-10-01, the docs layer's third change (issue #971): the
  extractor is data and the development section is pages.
  `livery.workshop._kinds.Extractor` carries the handler's options as
  a table (`options`) where it carried a callable writing TOML; the
  python kind's are `PYTHON_HANDLER_OPTIONS` in the python backend,
  and the layer renders every handler block from the extractor's data
  with the members policy beside it, so the base writes nothing for
  the site. The layer renders the development section from the prose
  fragments of phase 4c: for each section with a fragment for a human
  reader, one page under `development/` with the fragments' markdown
  in `fragments()` order, headings demoted under the section's title,
  an index, and a nav entry derived from the same fragments (never
  from a page a previous build wrote); this repository gets voice,
  standards, rules, gate, verbs, kinds and tools. Three faults found
  on the way are fixed here. The python-coverage generator failed a
  desk build when the local `.coverage` named a moved file; on a desk
  the page now states the absence and an older report goes, and in CI,
  where the data is the record of the tree being built, it stays red.
  The mount compared file paths for a generated page against an
  authored one, so toolroom's authored `api.md` and its generated
  `api/index.md` published at one URL and the build served either;
  it now compares published URLs (`published_url`), and toolroom's
  curated page moved to `reference.md` under the same nav label. Proven
  by tests: the extractor's options reaching a second handler's
  table, the development pages and nav with and without fragments and
  their rebuild, the coverage fallback on a desk and its refusal in
  CI, the URL collision refusing both ways. The site was built in the
  branch's worktree with the change stashed and with it, on the same
  coverage data, and the articles diffed: 574 of 625 identical; the
  eight development pages and toolroom's `reference/` new; five API
  pages changed where the edited modules' docstrings changed, and
  toolroom's `api/` now serves its generated module page every time;
  44 toolroom tool pages' cross-references to `Tool` and `Argv` now
  resolve to `reference/`, the curated page that documents them; one
  coverage page carries its data; the workshop index carries the new
  sentences. Phase 6 is built with two deviations from its text, both
  under the ruling that the base knows the minimum of docs generation:
  the docs categories stay the base's, since the affected walk and
  the provenance lines read them for every workspace, and the
  examples check stays the base's, since it runs a kind's examples
  and generates no site. Open: item 27 (a test running the builder),
  and a doctor line naming the layer that brings the docs job.

- 2026-10-01, phase 7's first change (issue #974): the conformance
  kit. `livery.workshop.testing` follows `livery.forge.testing`: a
  public package whose `Subject` names what a layer registers (its
  kinds, its checks), whose `Clause`s each return the `Violation`s a
  subject commits, every violation naming its clause, and whose
  `CLAUSES` a layer's own suite parametrizes over; `builtin_subject()`
  is the workshop's own kinds and checks, and the workshop's suite
  runs every clause on it, so the kit is in `fm check`. Three clauses
  land here. `backend-protocol` reads the protocol's methods from
  `livery.workshop._kinds.Backend` itself and judges each concrete
  kind's backend module: every method present, the protocol's
  positional parameters in order, its keyword parameters taken, its
  optional ones optional, no extra required parameter.
  `nearest-fragment` judges that a per-package file resolves to the
  nearest kind's fragment and that one kind has one owner per file.
  `category-table` judges each rule of a kind's table on a
  representative path against the documented order: the most
  specific pattern wins, the nearer kind wins a tie between kinds,
  and two rules of one kind never tie. The kit found two faults in
  the base, both fixed here: `package_fragment` and `category_rules`
  walked `kind_chain`, which is parent first, so the farthest
  ancestor won where their docstrings and the ruling of 2026-09-28
  say the nearest kind wins; no builtin table or fragment triggered
  either. Proven by tests: per clause a broken subject fails with the
  clause named (a backend missing a method and taking the wrong
  calls, a concrete kind without a backend, two checks carrying one
  file for one kind, two rules of one kind at one specificity), a
  child kind's fragment and category rule winning over its parent's,
  which fail with the two fixes reverted, and the builtins passing
  every clause. Next: skip printing, narrowing, fix ordering,
  config-fragment drift, contract 11's removal, and a contribution
  mounting only with its target, each with the gap it reveals.

- 2026-10-01, rulings, Willem's, while phase 7's second change was
  being read. The python kind does nothing but register its checks;
  the whole gate is kind independent; and the logic of running the
  checks that can fix serially and the rest in parallel lives in one
  place. The packages a check runs on are filtered by the packages
  the affected walk returns, and by file type first, since there is
  no point starting a process when there are no files to check: the
  engine narrows, not the check's body, which answers the open
  question of what `CheckRecord.narrowing` means. The empty skeleton
  supports python, because it cannot run anything without it; the
  python kind adds building and publishing a wheel. That refines
  phase 8's "the python package kind and its tooling leave the base":
  the checking of python files stays the base's, the wheel is what
  moves. The role verbs (`fm format`, `fm lint`, `fm typecheck`,
  `fm typecomplete`, `fm test`) go, as the record of #312 foresaw;
  the replacement spellings proposed, not yet ruled: `fm check
  <paths>` walks the registry over exactly those files, with `--fix`
  and `--safe-fix`, and the post-edit hook calls it; `fm check
  --point=<point>` selects a point's tests.
- 2026-10-01, the gate's one walk (issue #976). `livery.workshop.
  _quality._walk` is the one place the gate's order lives, and every
  gate calls it: the whole gate, CI's narrowed legs and a machine's
  narrowed step. It prints the narrowings, runs every fixer that
  applies one at a time in registration order under `--fix`, runs a
  hook between the fixers and the judges where a machine's run
  measures the tree the judges read, then runs every judge in one
  parallel block, a check that rewrote not judged again. Before a
  check starts, the walk asks whether its claims reach a file of its
  scope, from one catalogue per walk (`livery.workshop._checks.
  catalogue`: one `git ls-files` at the root, every file with its
  unit and its category); a check reaching none is named ("no file it
  reads in the affected packages; not run") and no process starts, a
  package check the same per package; a check without claims, and a
  run without a listing, run as before. The fault it closes: a
  machine's narrowed `fm check --fix` ran ruff's two fixers through
  the python backend's `scoped_rewrite` and then judged with every
  fixer left out, so the layering check and clang-format were neither
  fixed nor judged there, nor any layer's fixer; a test pinned that.
  `scoped_rewrite` and `scoped_gate`, a composition nothing called,
  left the python backend, and the `rewritten` and `check_style` flags
  went with them. Proven by tests: a layer's fixer and the layering
  fixer rewriting in the narrowed `--fix` before any judge and judged
  by none, the row naming the tree the fixers left, a check with no
  file of its type not started and said, a package check skipping a
  package without its files and running once one appears, a
  workspace without python starting no python check, and the whole
  gate's eight members unchanged on a workspace with python.

## Open

1. Does `Edge.kind` rename too, or does "edge kind" bound by its
   table satisfy contract 9? Current lean: keep it, bound.
   Owner: Willem.
2. Resolved 2026-09-28: `typecomplete` is a package-settable option on the python
   layer's basedpyright check family, on by default; the wave
   follows the gate. Decision record.
3. Resolved 2026-09-29: Windows is tier 1 and measures; MSVC through
   Microsoft's Code Coverage engine (`dotnet-coverage`, static native
   instrumentation), chosen by the spike; clang on Windows as the
   llvm family, linked by lld-link when instrumented. Decision record.
4. Where the graduated design page lives:
   `packages/workshop/docs/` under what name, and whether the
   facts-versus-policy boundary also enters the hse-imported
   guidance fragments. Owner: Willem.
5. Resolved 2026-09-05: `setup.sh` at the root, sourcing optional
   (see the decision record). What stays open is the pwsh spelling,
   deferred to the tool-store port. Owner: Willem.
6. Resolved 2026-09-28: pytest, coverage and their plugins stay in the lock; the
   check record is the one declaration of a tool. Decision record.
7. Resolved 2026-09-28: a contested base line becomes a slot the records fill;
   composition rules in the design section. Decision record.
8. Resolved 2026-09-28: a check record carries a verified extension id or none.
   Decision record.
9. Resolved 2026-09-28: all seven questions, in the decision record; the axis is
   `category`, one store with typed per-axis functions, the root a
   unit, the docs check's claim answers the site build's condition.
10. Resolved 2026-09-28: the record names its tool's discovery shape and the
    render follows it. Decision record.
11. Resolved 2026-09-28: the marker text stands; `types-pyyaml` becomes the
    workshop member's `dev` extra; a region inside a list is allowed
    wherever a comment is and built when a carrier appears; the
    owning template names a region. Decision record.
12. Resolved 2026-09-28: the sets as tabled in the design section; the python
    layer's ruff set is today's minus `D`; the docstring sentence
    moves with the rule. Decision record.
13. Resolved 2026-09-28: `livery.housekeeping`. Decision record.
14. Resolved 2026-09-28: `WORKSHOP_TOOLS` on the plugin module. Decision record.
15. Resolved 2026-09-28: the repository's own override of a
    check's configuration lives in the managed file's region (phase
    3c), so the three basedpyright execution environments move there
    and no instance rung is built. The package rung of the
    2026-09-09 record, a package contributing to another file than
    its own, is designed when such a case exists.
16. Resolved 2026-09-28: an overlay in the house's own series for its seeds; no
    registry-contributed files. Decision record.
17. Resolved 2026-09-28: the sequencing stands; the docs, playground and claude
    layers join the list; each extraction is its own plan after
    phases 2 to 5, python and docs first. Decision record.
18. Resolved 2026-09-28: contributions by data through `WORKSHOP_FOR`, no soft
    dependency; the fix writes `for` once and it is then the truth.
    Decision record, 2026-09-29.
19. A check's inputs, its claimed categories plus the facts it
    declares (a released version the browser installs, a lock
    entry), digested per check in the gate record so a check runs
    only when an input moved. The direction is ruled; the phase is
    not written. Cost named: a row per check keyed by its input
    digest, and the affected walk asking each check instead of
    classifying paths itself. Owner: Willem, after 4b.
20. Resolved 2026-09-29: the cpp-conan kind requires
    `dotnet_coverage@windows`, a host-scoped requirement the lock
    holds on the Windows hosts alone. Decision record.
20. A per-project opt-out of one target's opinions beyond deleting
    a name from `for`, a negative spelling say. Not built until
    wanted. Owner: Willem.
21. The tool records' home and a repository's own records:
    livery#882 carries the decision and its two design points; no
    work is scheduled. Owner: Willem.
22. Whether footman's docs examples work outside the playground's
    environment shims. The docs layer's examples check is what
    finds out (phase 6); what it finds is fixed in the examples or
    ruled as browser-only. Owner: the phase.
23. The browser runtime the playground layer declares as its tool:
    a tool record for pyodide under node, or the CPython sim alone.
    Owner: Willem, with the playground layer's plan.
24. The conformance chain has no notes-only pull request scenario, so
    phase 3b's docs-job skip is proven by tests over the decision and
    the site's claim, and the chain's proof of the skip with the
    `gate` context still reporting waits for that scenario. Owner: the
    phase that adds it, with phase 4b's claims.
25. The conformance loop's runner: its tool store is per job, so every
    job downloads every object (about 80 minutes of runner work to a
    release PR's merge); it runs `linux/amd64` under emulation because
    the two clang records ship no linux-arm build, and their source
    stops at LLVM 20 where LLVM's own releases and the PyPI wheels are
    at 23; and the wave's wheels job cannot bind the runner's paths
    into cibuildwheel's container through the host's socket (#931).
    Mechanisms undecided (#930 lists them). Owner: Willem.
26. Phase 5's third acceptance item, the union of gcc's, clang's and
    MSVC's line sets proven by the conformance chain: the chain has
    one linux runner and proves gcc's leg; the union is proven by unit
    tests over the reducer. A three-runner proof needs a cpp-conan
    member in a workspace with the three runners, and this repository
    has none. Where that proof lives is a ruling. Owner: Willem.
27. Phase 6's first acceptance item names a docs-build test for the
    private-members policy. The suite proves the policy where the
    build reads it, the assembled config, and one build of this
    repository with `all` contributed proves the rendered page
    (decision record, 2026-09-30); no test runs zensical today. A
    test that runs the builder lands with the docs layer slice, whose
    examples check needs the builder under test. Owner: the phase.
