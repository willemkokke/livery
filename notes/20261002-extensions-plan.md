# Extensions: a base, and what a workspace lists

Status: written 2026-10-02 from Willem's rulings of 2026-10-01 and
2026-10-02. Phases 1 to 4 built (issues #1025, #1028, #1032, #1034,
#1036); the others not started. It is the one plan from now until the end of
the refactor, and it supersedes three plans whose remaining work it
carries: the extensible gate plan
(`notes/20260905-extensible-gate-plan.md`), the empty shell plan
(`notes/20260930-empty-shell-plan.md`) and the local loop plan
(`notes/20260930-local-loop-plan.md`). It takes in the toolchain
plan's phases 2 to 4 (`notes/20260930-toolchain-plan.md`), whose
phase 1 stays built where it is. The releases plan
(`notes/20260927-releases-as-a-target.md`) stays a plan of its own;
phase 11 here builds on its phases 1 and 2.

What the superseded plans built stays built and is not repeated here:
the gate's check registry and its one walk, the conformance kit, the
docs layer's first slices, the vocabulary test, the codec in
strongroom, the derived version, the release member list, the release
notes seam, and the lazy roots of phase 11a.

## The prompt (Willem)

> I want to make sure the core is flexible, fast, empty shell, that
> deal with the core forge operations, codifies the workflow,
> abstracts all common development actions, and can be extended to
> work for every language if we so desire without touching that core.
> Willem, 2026-09-30

> features that you don't use should not cost anything. Willem,
> 2026-09-30

> templates go enterely. copier is one of workshops biggest 3rd party
> dependencies. It fits with livery house style of having as few of
> them as possible, none preferred. Willem, 2026-10-01

> templates have always been a bit of a wart and added complexity. I
> didn't want to reimplement copier's three way merge, but with the
> fragments and editable sections, we've kind of removed that need.
> Willem, 2026-10-01

> The only rule I have I that the templates are as minimal as
> possible so nearly all changes or overrides can be delivered via
> wheel updates. Willem, 2026-10-01

> I think I want each check to be each own layer, in its own wheel,
> and I want fragments to be associated with layers, not with check
> directly. Willem, 2026-10-01

> I'm definitely going to want to support rust and go, and I would
> not want to rule out c / zig / jai if it ever comes out / swift.
> So perhaps we need to generalise toolchain further. Willem,
> 2026-10-01

> Devenv should do for hosted development environments what uv did
> for venvs, make them cheap and disposable. 'devenv create
> url_or_repo_path' should create a rig populated with that repo.
> Workshop based repos will automatically adapt the workshop.toml to
> make it work out of the box. Willem, 2026-10-01

> this is a large and complex project, and strict configuration with
> teaching error messages are the best way to stop it turning into
> spaghetti. Willem, 2026-10-01

## The vocabulary

The words this plan uses, each with one meaning:

- **The base**: `livery.workshop`, the engine. It reads contracts,
  mounts extensions and plugins, owns the registries, the fragment
  engine, the toolchain engine, the gate engine, the state store,
  the issue and submit family, the workflow engine and the release
  train's phases. It depends on no extension.
- **An extension**: a part of a wheel that registers something with
  the workshop: checks, fragments, seeds, lifecycle steps, queries,
  tools, CI jobs, slots, a release notes provider. An extension is
  listed at the workspace level (`[workspace] extensions` in the
  root `workshop.toml`) or at the package level (`extensions` in a
  package's `workshop.toml`), as it declares. Any package may ship
  one, under any name and namespace, through the `workshop.extensions`
  entry point group.
- **A plugin**: a footman plugin, verbs only. It registers nothing
  with the workshop beyond the tools its verbs need. It is active
  because the project depends on it directly.
- **A check**: one job one tool does for one role in the gate, such
  as ruff's `format` check. An extension registers any number.
- **A role**: what a check does: `format`, `lint`, `typecheck`,
  `typecomplete`, `test`, `examples`, `build`. A role exists in a
  workspace only when a listed extension registers a check for it.
- **A fragment**: content an extension delivers to a target path,
  composed with the other extensions' fragments by the target's file
  type.
- **A seed**: a file written once, when a project or a package is
  born, and the project's own from then on.
- **A phase**: a step of a package's lifecycle (`create`, `sync`,
  `stamp`, `build`, `prove`, `publish`, `replay`, `clean`, `run`), with
  `pre`, main and `post`, to which package-level extensions
  contribute.
- **A query**: an answer about a package (its version, its files,
  its requirements, its distributions), combined across its
  extensions by a rule per query.
- **A lodge**: a disposable hosted development environment: a forge
  with its CI runners and seeded accounts, populated with one
  repository.

"Layer", "kind", "trait" and "template" leave the vocabulary. A
package's kind is the combination of its package-level extensions,
named by their minimal set joined with `+`: `python+nanobind+conan`.

## What exists that this builds on

Measured or read on main at `cd8e18e4`, 2026-10-02.

- **The mount.** `livery.workshop._layers.mount_layers()` reads
  `[workspace] layers`, imports each entry's module for its
  `WORKSHOP_*` attributes, and calls footman's `plugin()` for the
  entry point of the same name in `footman.tasks`. A layer is only
  "a listed import path"; nothing declares one, and `available_layers()`
  reports every `footman.tasks` entry point as an available layer.
- **The registries.** Kinds, checks (role and tool, `role.tool`),
  categories, provenance channels, slots, AST rules, prose sections,
  site files, CI job contributions, the release notes provider. All
  private; every check is registered by the base in
  `_checks._register_builtin()`, every kind in `_kinds._register_builtin()`.
- **Fragments.** Check records carry `Fragment(file, text, kind)` in
  jinja, rendered with copier's data. Project files are limited to
  `PROJECT_FILES` (`pyproject.toml`, the two VS Code files) and
  package files to `PACKAGE_FILES` (`.clang-format`, `.clang-tidy`).
  Package fragment files carry receipts in `.workshop-rendered` and
  leave with their check unless edited. Prose fragments are a
  separate channel (`content/fragments/`), and so are skills, hooks,
  `settings.json` and site CSS.
- **Templates.** 65 files under `packages/workshop/src/livery/workshop/templates`:
  `project`, `package-base`, `package-python`, `package-cpp-conan`,
  `package-python-nanobind`, `package-layer`, rendered by copier,
  checked by `fm template.check`, applied by `fm template.apply`,
  published by `fm release.templates` to the `templates-artifact`
  repository, with overlays (`overlay.toml`) composed for member
  layers. `cliff.toml` is the one package file the template chain
  manages.
- **The gate engine.** One walk; checks have a `scope`
  (`workspace`: one call per run over every affected path; `package`:
  one call per package), fixers run serially before judges run in
  parallel. `CiContract.check_verbs` on a kind names its roles and
  `verify_roles` refuses a role nothing implements. Generated role
  verbs (`fm typecheck`, `fm typecheck.mypy`) come from the registry.
- **The contract reader.** `livery.workshop._contract` refuses a key
  spelled with an underscore at any depth. It does not refuse a key
  nobody reads.
- **Tool requirements.** `name`, `name>=floor`, either with `@hosts`,
  hosts a comma-separated list of platforms or host keys, inclusion
  only (`livery.toolroom.store._lock.Requirement`). The sites are
  kind records, check records, `WORKSHOP_TOOLS`, a package's and the
  root's `[tools] requires`. `[tools] hosts` names the locked hosts;
  undeclared, the gated three.
- **Environments.** `fm devenv.*` (named environments, host mode
  from the `gitea` and `gitea_runner` records, docker mode as the
  forge.dev compose rig) and `fm forge.dev.*` (compose for Gitea and
  GitLab, seeding, runner restart), with credentials in
  `.repo.shared.env`. `fm ci.e2e` runs scenarios by name on them.
- **The vocabulary test.** `packages/workshop/tests/test_workshop_vocabulary.py`:
  79 findings of language knowledge in 17 base modules, an allowance
  that only falls.
- **Startup.** `fm commit --help`: median 255 ms, 365 modules,
  after phase 11a of the empty shell plan.
- **minijinja.** On PyPI as `minijinja` 2.24.0, one abi3 wheel per
  platform for macOS, Linux (glibc and musl) and Windows x64 and
  x86; no Windows arm wheel.

## Ground-truth contracts (do not violate)

1. **`fm check` is the frozen seam.** The bare command, typed in any
   machine state, and its exit-code verdict. `uv run` is never
   required, of a person or of CI. The entry contract is the one
   choke point: it provisions uv, syncs the venv against the lock,
   resolves the environment cascade and leaves bare `fm` working.
   Everything behind the command may change.
2. **Two checkouts of the same commit with the same listed extensions
   produce the same verdict.** Nothing ambient (installed wheels,
   environment, machine state) widens or narrows the gate. A value a
   lodge overrides is named wherever it is read (contract 20).
3. **Nothing activates by being installed.** An extension is active
   because a contract lists it; a plugin because the project depends
   on it directly, which the lock pins. Entry points discover, for
   `fm doctor` and completion; they never activate. The lists are
   kept closed under declared requirements by the layering check and
   its fix, never by the mount pulling extensions in.
4. **Configuration is strict, and every refusal teaches.** Every key
   of every contract is declared by its owner, the base or an
   extension, with its type and allowed values. An unknown key, a
   table of an unlisted extension, a wrong type, a reference to a
   check, role, extension, fragment, host or option that does not
   exist: each refuses, naming the file, the place, what is wrong and
   the values that would be right. There are no implicit defaults
   beyond what birth writes explicitly, and no compatibility code:
   this repository is all that exists.
5. **The base names no extension, no package language and no check
   tool.** It depends on no extension and imports none; the layering
   check and the vocabulary test refuse both on every change. Python
   is the base's runtime: the interpreter, uv, the venv, the root
   `pyproject.toml` and `uv.lock`, footman, toolroom and the forge
   are the base's own and stay named.
6. **A narrowed gate prints what was narrowed and by whom.** No check
   disappears silently.
7. **Facts in `workshop.toml`, opinions in extension code.** No
   check or docs policy toggle enters the contract vocabulary beyond
   the declared options of a check, a role or an extension.
8. **Fixers run serially before judges run in parallel**, and the
   current gate's observable properties are pinned by test before any
   implementation is replaced. A property a replaced piece enforces is
   named and pinned before it moves.
9. **Records grow additively** and carry defaults; a mount-time API
   version check turns an incompatible extension into a sentence, not
   an `AttributeError`.
10. **A fragment lives exactly while its extension is listed.** A
    file the engine wrote is removed when its owner is withdrawn only
    if its bytes are the bytes the engine last wrote; anything else is
    kept and named as a local override.
11. **Where a tool composes its own configuration** (upward search,
    `extend`, `InheritParentConfig`, `include`), the engine uses that
    composition and builds no second one.
12. **A managed file may carry content the repository owns**: named
    regions between marker comments, or an appended tail where the
    format has no comments, read from the committed file and written
    back in place on every render.
13. **No templates and no copier.** A project or package is born from
    seeds its extensions ship in their wheels; everything kept current
    afterwards is a fragment from a wheel. Nearly every change or
    override arrives through a wheel update.
14. **Rendering is minijinja over a declared subset**: variables,
    `if`, `for` and the filters minijinja ships. A fragment's template
    calls no python. A dynamic fragment is a python function that
    returns text or template source, and minijinja renders the result.
15. **An extension's declarations are data its module carries, and
    its versions are its wheel's metadata.** A `requires` range is a
    dependency of the wheel, a compatibility claim an extra, a host
    scope an environment marker. Mount code never branches on what is
    listed.
16. **`fm check` parses each source once**, in the layering check's
    one traversal; rules register into it.
17. **Footman imports no workshop and reads no `workshop.toml`.** Its
    changes here are generic.
18. **Re-running a release is its recovery, and a receipt is the tag
    plus the proof.** No phase loosens the walk-past or the probe.
19. **The forge imports only the standard library at module import
    time.** Its admin protocol is HTTP calls in each server's dialect,
    or `Unsupported`.
20. **A lodge's overrides are explicit.** A value that comes from a
    lodge rather than the committed contract is marked as such by
    `fm env.show` and `fm doctor`, and an override naming an unknown
    key refuses like any unknown key.
21. **A sync costs what changed, not how many packages there are.** A
    warm sync with nothing changed costs one tree read and one
    comparison; a change to one package costs about that package's
    work. Budgets are set from the scale fixture's first measurement
    and judged from the CI store.
22. **A workspace-unit check runs once per gate**, over the union of
    the affected paths, never once per package; the engine batches,
    splits and caches, the check only calls its tool. "Affected" is
    always on, one engine for the gate, sync and the development
    build, each consumer supplying only its influence rule.
23. **Toolchains resolve three ways and no fourth**: the host's,
    a floor, or an exact version, which installs or refuses naming
    what would satisfy it. Unwanted families are never probed; a
    probe runs only for an absent or stale receipt. A platform scope
    covers its hosts and a host key overrides it. What was used is
    written down per artifact. One receipt answers every consumer. A
    package overrides key by key, and several toolchains may be
    active at once. The lock schema stays additive.
24. **Verdict tools stay store-first.** A tool a check reads a
    verdict from takes no host allowance; build and toolchain tools
    may.
25. **Every runner is a supported platform.** A supported platform
    needs no runner.

## The design

### Extensions and plugins

An extension is declared by an entry point in the `workshop.extensions`
group, mapping its name to the module that declares it. That module
carries its declarations as data: its API version, the level it may
be listed at (`workspace`, `package` or both), the extensions it
requires, its tools, its options, its checks, fragments, seeds,
lifecycle contributions and queries. A wheel may ship several
extensions; the wheel's version is theirs.

A workspace lists its workspace-level extensions in
`[workspace] extensions`, which is required: a contract without it
refuses and prints the line to add. A package lists its
package-level extensions in `extensions`. Workspace-level extensions
apply wherever their checks and fragments find something to act on,
inside packages included; a package switches a check off through its
options. Only package-level extensions combine into a package's kind.

A plugin is a footman plugin and nothing more. It is mounted because
the project names its distribution as a direct dependency (the
project builtin rung, phase 5), which is how `lodge` and `e2e` reach
this repository. A plugin's entry module may declare the tools its
verbs need, required or optional.

The test for "extension or plugin": an extension registers something
with the workshop; a plugin only adds verbs.

`fm new.project` writes the default workspace list explicitly:
`extensions = ["ruff", "basedpyright", "pytest"]`. The base has no
default of its own.

### Requirements, options and scopes

One grammar for tools, extensions and their options:

```
name[option,option]?>=floor@scope
scope = token(,token)*     token = [!]platform | [!]host-key
```

- `[...]` is a tool's profile (`llvm[slim]`, nested, the largest any
  site asks for wins) or an extension's options (`conan[publish]`,
  `basedpyright[typecomplete]`, independent switches the extension
  declares).
- `?` marks an optional tool: probed, installed where it can be,
  never refused; the receipt says absent. footman's `requires_tool`
  gates the verbs that need it.
- `!` excludes a host from a scope: `tea@!windows-arm`. With only
  exclusions the scope starts from every supported platform.
- Versions of extensions are not written here: they come from wheel
  metadata (contract 15). A package's `extensions` list names
  extensions and options only; the lock pins the versions.

`[workspace] platforms` declares the supported platforms in the same
tokens; undeclared, every host. A requirement without a scope must
resolve on every supported platform. `[tools] hosts` goes.

### Checks and roles

An extension registers checks; each check has its role and its tool.
Options are addressed at three tables, deeper winning key by key:

```toml
[checks.ruff]          # every check the ruff extension registers
[checks.ruff.format]   # ruff's format check alone
[roles.typecheck]      # every check of the role, whatever its tool
```

A check declares how it is invoked, and the engine does the rest:

| Declaration | Values |
|---|---|
| unit | `workspace` (one call per gate) or `package` (one per package) |
| narrowing | `paths` (accepts a path list) or `whole` (its configured scope) |
| transport | how the path list reaches the tool: argv, a file of paths, a generated config |
| threshold | the share of affected units above which it runs whole |

The engine forms the union of affected units, runs whole above the
threshold, sends the union through the transport, splits argv into
the fewest calls under the platform's command-line limit and merges
their results, runs package units in parallel where allowed, and
skips units whose inputs a proved record holds.

`CiContract`, `verify_roles` and kinds' role lists go: the roles that
exist are what the listed extensions register.

### Fragments

One engine for every piece of content an extension delivers. A
fragment is (owner, target path, content, composition), where:

- the target is a path at the root, or a path in every package whose
  extensions include a given one (`package/cmake/CMakePresets.json`);
- the content is a file in the extension's wheel under
  `content/<target>`, or a string, or a dynamic fragment's returned
  text;
- the composition is the target's file type:

| Target | Composition |
|---|---|
| TOML (`pyproject.toml`, `cliff.toml`) | tables in extension order |
| ignore and attribute files | lines, deduplicated, the repository's region last |
| JSON (`.vscode/*.json`, `CMakePresets.json`) | merged by key in extension order |
| whole files (prose, skills, hooks, `settings.json`, CSS, `.clang-format`) | one owner per file |

Extensions apply in the order listed, the base first and the
repository last. Where two contribute to one whole file, or one must
override another's fragment, the upper declares
`replaces = "<extension>:<fragment>"` or `deletes = "<extension>:<fragment>"`
with a reason; an undeclared clash refuses, and so does a reference
to a fragment that does not exist.

The engine renders with minijinja, compiling each template once per
run. Each output is keyed by the digests of its fragment content and
its render data; an unchanged key is never rendered again. Receipts
in `.workshop-rendered` (per package and at the root) carry what the
engine wrote, so withdrawal is contract 10 everywhere. Regions keep
contract 12. `fm sync` writes; the drift check judges.

### Seeds and birth

A seed is a file in an extension's wheel under `seeds/<target>`,
written by the `create` phase when a project or a package is born and
never compared or rewritten afterwards. `fm new.project` and
`fm new.package --extensions=<combination>` (completed from the
generated combinations) write the seeds of the base and of every
listed extension, render with the contract's facts, and write the
contract itself with every list explicit. The answers file goes: its
facts move into `workshop.toml`.

Seeds that a wheel can keep current stop being seeds: the project's
`tests/test_docs_drift.py` and `tests/test_workspace_contracts.py`
become checks, the docs assets become the docs extension's content,
and `CHANGELOG.md` is created by the changelog provider on its first
record.

### Package composition

Each package-level extension declares:

- **requires**: extensions that must also be present, as names; the
  range is the wheel's dependency (contract 15);
- **compatible**: extensions it combines with, as wheel extras; one
  declaration from either side connects two extensions, so a third
  party participates fully;
- **order**: per phase, from declared context keys, then explicit
  `before` and `after` the extension itself declares against the
  extensions it requires or is compatible with, then alphabetical.

A package's list is valid when every pair is connected through
`requires` or compatibility and every requirement is present. The
canonical list is the minimal set (no listed extension implied by
another), in requires order, ties alphabetical; the layering check's
fix rewrites to it. `fm extensions --combinations` lists every valid
combination of the installed extensions by that name, and completion
offers it.

### Phases, context and queries

| Phase | Does |
|---|---|
| `create` | writes seeds at birth |
| `sync` | sets the package up in the development environment |
| `stamp` | writes a version into the extension's own files |
| `build` | builds release artifacts, per host where needed |
| `prove` | proves floors and the latest set against built artifacts |
| `publish` | uploads to each target and waits until served |
| `replay` | tests a published version against its own tests |
| `clean` | removes what the package's builds left |
| `run` | brings the development build up to date and starts one executable |

Each extension may contribute `pre`, main and `post` to a phase.
`pre` and main run in order; `post` runs in reverse and always runs
once its `pre` ran. The context carries `ctx.failed`, `ctx.failure`
and `ctx.failed_extension`. An extension
declares the context keys it provides and reads, typed; a reader runs
after its provider, two providers of one key refuse, a key nobody
provides refuses, and a cycle refuses naming its members.

| Query | Combined by |
|---|---|
| current version | must agree |
| version files | union, declared before `stamp` writes |
| declared requirements, writing one | union; each extension writes its own file |
| distributions (ecosystem, name) | union |
| build plan | per host if any extension needs it |
| module roots, sibling references | union |
| API extractor, examples runner, coverage pages | one each, the nearest by requires |
| toolchains needed | union |
| executables | union; two extensions naming one refuse |

`[publish]` marks an extension whose artifact the package releases;
any number may carry it. Releasing (version, receipt tag, notes)
belongs to the package; a package with no `[publish]` still releases
and uploads nothing. `[release] publish` goes. The release wave
handles each artifact against each of its targets; a dependent picks
the artifact in its own ecosystem.

`run` differs in one way: its main has one owner, the extension that
owns the chosen executable. `fm run <package>[:<executable>] -- <arguments>`
completes packages and their executables from the `executables`
query, may leave out the package inside its directory, and exits
with the executable's exit code. `run.pre` is the development build:
the gate's `build` role over the package's dependency closure,
through the affected engine, building only what changed. It never
runs the release `build` phase.

### The affected engine

One engine answers "what changed since the last proved state, and
what does it influence" for every consumer. It owns the record (a
tree id and each unit's input fingerprint), the changed paths since
it in one git call, the mapping of a path to its unit, and the skip
when nothing changed and every output's receipt is present. Each
consumer supplies only its influence rule:

| Consumer | A change to package A influences |
|---|---|
| the gate | A and everything that depends on A, transitively; a change outside every package, everything |
| sync | the packages whose rendered files the change touches: a package's own contract, that package; an extension's source or locked version, every package listing it; the root contract or the lock as a whole, everything |
| the development build | A and what depends on it within the closure being built; a package's inputs include the output fingerprints of the members it depends on |

An extension declares its build inputs by category; a file no
extension claims and that is not documentation counts as an input,
so the engine errs toward rebuilding, and the tool's own incremental
build stays as the second net. The gate's `build` checks and `run`
share one record, so a green gate leaves nothing for `run` to build.

### Toolchains

The base owns the toolchain engine: declarations under
`[toolchain.<language>]`, the three answers of contract 23, receipts
under `.workshop/receipts/toolchain/`, the release record. A language
extension owns its families, their probes and their store records:
`cpp` (msvc, apple-clang, clang, gcc), later `rust`, `go`, `zig`,
`swift`. A package's toolchains are the union its extensions need.
This changes the toolchain plan's contract 10 and its table name:
`[cpp.toolchain]` becomes `[toolchain.cpp]`, owned by the cpp
extension.

### Lodges

`livery.lodge` (distribution `livery-lodge`) is a plugin:

- `fm lodge.create <url_or_path> [--name]` brings up an environment
  (host mode by default: Gitea and its runners as processes from the
  store; docker mode when docker is present, where GitLab lives),
  seeds it, imports the repository (from a URL through the forge's
  import, from a path by pushing every branch and tag), registers its
  runners, and clones a local checkout whose remote is the lodge.
- `fm lodge.config <name>` prints the `workshop.toml` values the
  repository needs there: `[forge]`, registries, `[ci] runners` and
  `[workspace] platforms` from its runners.
- In the local checkout, `fm` recognises the lodge from the remote
  URL through the lodge's record of what it created, and applies the
  values in memory; in CI, every runner the lodge registers sets
  `WORKSHOP_OVERRIDES` for its jobs. Contract 20 governs both.
- One native host runner by default; more by a runner spec: macOS
  x64 under Rosetta on Apple silicon, Linux arm64 and x64 through
  docker (x64 emulated, opt-in per runner). Windows stays with the
  hosted CI.
- `ls`, `down`, `rm` (no trace beyond the shared caches), runner
  restart, the shared caches in one directory.

The forge gains an admin protocol for the API half: seeding users,
tokens and organisations, runner registration tokens, importing a
repository, deleting a published version, each probed through
`supports()`. `forge.dev`'s compose rig and seeding fold into the
lodge; the forge keeps `forge.conformance` and
`forge.fixtures.record`, which ask the lodge for an environment.

`e2e` is workshop development tooling: a member of this repository
that publishes nothing, mounted as a plugin here only, building on the
lodge.

### Where things live

```
livery/                                  PEP 420, no __init__.py down to a distribution's root
├── workshop/        livery-workshop           the base
├── footman/         livery-footman            plugins: self, janitor, profile, docs, env_files, pages
├── forge/           livery-forge              protocols, backends, the admin protocol; plugin: conformance, fixtures
├── lodge/           livery-lodge              plugin: lodges
├── e2e/             livery-e2e                plugin, never published
├── toolroom/        livery-toolroom
│   ├── store/       livery-toolroom-store
│   └── bench/       livery-toolroom-bench     plugin: tools.*
├── strongroom/      livery-strongroom         with cbor/
└── extensions/
    ├── ruff  basedpyright  mypy  ty  pyrefly  pytest  clang_format  clang_tidy
    ├── docs  changelog  housekeeping  claude
    └── python  cpp  cmake  conan  nanobind  unreal
```

Each extension is `livery-extensions-<name>`, in
`packages/extensions/<name>/`; discovery allows one group directory,
which has no contract of its own. A root's `__init__.py` content moves
to the root's `api` module and nothing else does; no path contains
`api` twice. `api` and `testing` are reserved names under a root.
These are our conventions, checked by the housekeeping extension; a
third-party extension lives under any name.

## Phases

Each phase lands gate-green and mergeable alone; a lettered slice is
its own change. Exceptional paths are tested before happy paths.

### Phase 1: measure first

**1a, the minijinja spike: built (issue #1025).**

- Every template file and every check fragment, rendered through
  copier's jinja2 and through `minijinja` 2.24 over the data copier
  renders them with: 155 renders byte-identical.
- The one construct minijinja refused was list mutation, a
  `py.append(member)` in the project's `pyproject.toml.jinja`; it is
  now `selectattr` and `rejectattr`, with the same rendered bytes.
- Copier's own answers file is the only file left that differs, for
  copier's reasons: its name comes from copier's configuration object
  and its body uses copier's `to_nice_yaml` filter. It goes with
  copier in phase 8.
- minijinja joins the workshop's `dev` extra;
  `test_workshop_minijinja_parity.py` keeps the two engines in
  agreement until the templates go.

The subset of contract 14, as the files use it: variables, `if`,
`for`, `set`, the filters both engines ship (`selectattr`,
`rejectattr`, `list`, `default` among them), and the python-style
methods minijinja implements on strings and maps (`.get`, `.split`,
`.replace`). Changing a value in place (`.append`, `.update`) is
outside it, and so is any filter only copier provides.

Acceptance of 1a, with the evidence of 2026-10-02:

- `fm check --fix` exits 0 (2m00s).
- `test_every_template_renders_the_same_in_minijinja` and
  `test_every_check_fragment_renders_the_same_in_minijinja` pass.
- `fm template.check` exits 0 after the template change.

**1b, the scale fixture: built (issue #1028).**

- `fm ci.scale` copies the committed tree at HEAD into a repository
  of its own with no remote, removes the test modules of the copy's
  own members (they test facts about this repository) and zeroes
  their floors, then renders the generated members: 240 python, 40
  cpp-conan and 20 nanobind by default, the python ones a binary
  tree of runtime edges declared through the layering fix mode's
  writer. It times `fm sync`, `fm template.check` and `fm check`
  cold (no environment, no gate record, the gate whole), warm, and
  after an edit to one leaf member, and prints every timing.
- Inside CI the timings land on the `metrics` series as the run's
  `scale` job, a task per verb and state, so `fm ci.timings` reads
  them with no change to its reader.
- No schedule runs it: the baseline is the local run in the decision
  record, and a later phase re-runs `fm ci.scale` to compare. A job
  only a `[[ci.schedule]]` entry names was listed by `jobs_of` and
  never emitted; `emitted_points` now adds it to the shell, on one
  runner, with the point's own checkout and credential.
- `fm sync` in a clone with no `origin` (a `--local` birth, the
  fixture's copy) stopped on a traceback; it now skips bringing the
  checkout current and says so.
- The generator and its verb sit beside `_e2e.py` and move with it.

Acceptance of 1b:

- `fm check` exits 0.
- The baseline is quoted in the decision record, from a local run;
  no CI row is recorded (Willem's ruling of 2026-10-02).

### Phase 2: strict contracts

**Built (issue #1032).**

Deliverables:

- A declaration registry for contract keys: the base declares its
  tables and keys with types and allowed values; an extension
  declares the tables it owns.
- Every reader of `workshop.toml`, root and package, reads through it:
  unknown keys, unlisted extensions' tables, wrong types and wrong
  values refuse, naming the file, the table, the key, what is known
  and the nearest match.
- The keys no code reads today refuse as unknown (the `[verbs]` table
  in two package contracts among them).

Acceptance, refusals first:

- `test_an_unknown_key_refuses_naming_the_known_ones`
- `test_a_table_of_an_unlisted_extension_refuses_naming_the_extension`
- `test_a_wrong_type_refuses_naming_the_allowed_values`
- `fm check` exits 0 with this repository's contracts unchanged but
  for the removed dead keys.

### Phase 3: namespaces, `api`, and `livery.extensions`

**Built (issue #1034).**

Carried from the empty shell plan's phase 2, extended by the
namespace ruling.

Deliverables:

- The pinning tests first: each distribution's root `__init__` exports
  are its `api` exports after.
- Every distribution root becomes a namespace; its `__init__.py`
  content moves to `api.py`; footman's lazy `__getattr__` moves with
  its exports.
- `livery.extensions` exists as a namespace; the docs layer moves to
  `livery.extensions.docs`; `livery.workshop.layers` and its path
  extension go.
- The reserved names (`api`, `testing`) refuse under a root.
- Every import, docs page and example uses the new paths; each
  changelog states the break.

Acceptance:

- `fm check` exits 0.
- `test_api_exports_what_the_package_exported`,
  `test_every_distribution_root_is_a_namespace_with_one_api`.
- `fm workflow.release --local` releases every changed distribution.

### Phase 4: extensions

**Built (issue #1036).**

Deliverables:

- The `workshop.extensions` entry point group; the mount reads it,
  never `footman.tasks`, to find an extension's declaring module.
- `[workspace] extensions` (required) and a package's `extensions`;
  the level each extension declares; `[workspace] layers` refuses as
  unknown.
- Discovery for `fm doctor` lists real extensions only.
- `fm extensions` replaces `fm layers`; "layer" leaves the code and
  the docs.
- The closure fix writes requirements into the list at the level
  each requirement declares.

Acceptance, refusals first:

- `test_a_contract_without_extensions_refuses_printing_the_line`
- `test_an_extension_listed_at_the_wrong_level_refuses`
- `test_a_footman_plugin_is_not_offered_as_an_extension`
- `fm extensions` on this repository lists what it lists today, as
  extensions.

### Phase 5: plugins, tools and platforms

**5a built (issue #1038): the project builtin rung.** Inside a
project, the `footman.builtin` names of the distributions its
`pyproject.toml` names directly (`[project] dependencies` and every
`[dependency-groups]` list) mount after the machine's built-ins and
before the user's file; `[tool.footman] builtin-exclude` keeps a name
out and `fm --plugins` labels both.

**5b built (issue #1042).** The workshop's entry module
(`livery.workshop._mount`) registers the base's verbs and mounts the
listed extensions, so the rendered `tasks.py` keeps its comment and the
repository's region alone. forge and the bench declare `footman.builtin`
entries and left `[workspace] extensions`; footman declares
`footman.profile` a built-in of every project depending on it, and its
own docs plugin stays out of the rung (every born project names
footman, and the verb builds footman's pages), mounted in this
repository's region. A plugin mounted inside another's import keeps its
own name in footman's provenance. `fm --list` offers the same 161
verbs before and after.

**5c built (issue #1045): one requirement grammar.**
`livery.toolroom.store.api.Spec` parses `name[options]?>=floor@scope`
and `Scope` resolves a scope's inclusions and exclusions against a
supported set, both public; `Requirement` parses through them and, until
its sites take them, refuses options, `?` and `!` as before. Willem's
ruling of 2026-10-03: parse the grammar once and let each site add its
own rule, the parser being public API like the lock it serves.

**5d built (issue #1047).** A tool requirement takes `?` and `!`. The
lock resolves required sites as before; an optional site's hosts join
where the chosen version has an artifact, a tool every site marks
optional takes the newest version that resolves somewhere, its entry
says `optional`, and what is left out is the lock's `notes`, printed by
`fm tools.lock`; `fm tools.sync` names an optional tool its host lacks.
A plugin the project rung mounts declares `TOOLS = (...)` in its entry
module, read with `ast` and never imported; forge's dev plugin declares
`docker?` and the root contract dropped `docker`. The lock holds the
same 20 tools, docker now `optional`. The receipt was not given an
"absent" word: the lock's `optional` and the sync's line carry it, so a
receipt stays the record of an install.

5e (`[workspace] platforms`) is not started.

Carried from the empty shell plan's phase 12, extended.

Deliverables:

- In footman: the project builtin rung (the `footman.builtin` entry
  points of the project's direct dependencies, never a transitive
  install), `[tool.footman] builtin-exclude`, and a nearer cascade
  file shadowing a plugin's task with `inherited()` reaching it and
  `fm --where` showing it.
- Plugins and extensions declare tools; `?` marks an optional one;
  scopes accept `!`.
- `[workspace] platforms`, every host when undeclared; an unscoped
  requirement must resolve on every supported platform; a runner on
  an unsupported platform refuses.
- The rendered `tasks.py` keeps only its comment: the workshop is a
  direct dependency, so it mounts through the rung.

Acceptance, refusals first:

- `test_a_transitive_builtin_never_mounts`,
  `test_builtin_exclude_names_the_exclusion`,
  `test_a_root_task_shadows_a_plugin_task_and_inherits_it` in
  footman's gate.
- `test_an_optional_tool_absent_is_named_and_never_refused`,
  `test_an_exclusion_scope_removes_its_hosts`,
  `test_a_runner_on_an_unsupported_platform_refuses`.
- `fm tools.lock` on this repository with `platforms` undeclared
  locks every host; the tools without a build somewhere carry a
  scope, quoted in the decision record.

### Phase 6: one fragment engine

**6a, the engine.** Deliverables:

- Fragments owned by extensions, file-based or string or dynamic,
  composed per target type; targets at the root and per package by
  extension.
- minijinja rendering over contract 14's subset; the render cache
  keyed by content and data digests.
- Receipts at the root and per package; withdrawal under contract 10;
  regions under contract 12.
- `replaces` and `deletes` with reasons; refusals for undeclared
  clashes and missing references.

**6b, the channels move onto it.** Deliverables:

- Check fragments (the `[tool.*]` tables, `.vscode` files,
  `.clang-format`, `.clang-tidy`) become their extensions' fragments.
- Prose fragments, skills, hooks, `settings.json` and site CSS
  become fragments.
- The root `.gitignore` and `.gitattributes` become composed files,
  each line owned: the base, each tool, docs, footman's profile, the
  agent, the notes convention. The dead `.forge.dev.env` line goes.
- `PROJECT_FILES`, `PACKAGE_FILES` and `Fragment(file, text, kind)`
  on check records go.

Acceptance, refusals first:

- `test_an_undeclared_clash_refuses_naming_both_owners`,
  `test_a_replace_of_a_missing_fragment_refuses`,
  `test_a_withdrawn_extensions_edited_file_is_kept_and_named`.
- This repository's rendered files are byte-identical before and
  after 6b, proven by `git diff --exit-code` after `fm sync`.
- `fm check` exits 0.

### Phase 7: seeds and birth without copier

Deliverables:

- Seeds in extension wheels under `seeds/`; the `create` phase writes
  them.
- `fm new.project` writes the contract with every list explicit
  (`extensions = ["ruff", "basedpyright", "pytest"]`), the bootstrap
  root `pyproject.toml` and `setup.sh`, and the base's seeds;
  `fm new.package --extensions=<combination>` writes the package's.
- The answers file's facts move into `workshop.toml`; the answers
  file goes.
- The two project test seeds become checks; the docs assets become
  docs content.

Acceptance:

- `fm check` exits 0.
- A project born by `fm new.project` in a scratch directory passes
  its own `fm check`: `test_a_born_project_is_green`.
- `fm new.package --extensions=python` and `--extensions=cmake+conan`
  birth members whose gate is green, in the conformance kit.

### Phase 8: templates go

Deliverables:

- copier leaves the workshop's dependencies.
- `fm template.apply` and `fm template.check` go; `fm sync` writes
  and the drift check judges every managed file.
- `fm release.templates`, the `templates-artifact` repository's use,
  the release point's templates job, overlays and `overlay.toml` go.
- This repository is converted in place.
- Removing a package is deleting its directory and running
  `fm sync` (closes #1005).

Acceptance:

- `fm check` exits 0.
- `grep -rn "copier" packages/*/src` finds nothing;
  `uv pip show copier` in the workspace venv finds nothing.
- `test_removing_a_member_needs_only_sync`.

### Phase 9: checks as tools

**9a, the engine and the options.** Deliverables:

- A check's invocation declarations (unit, narrowing, transport,
  threshold); the engine's batching, splitting and merging.
- `[checks.<tool>]`, `[checks.<tool>.<role>]`, `[roles.<role>]`.
- Roles from listed checks only; `CiContract` and `verify_roles` go;
  a role verb exists only where a check provides it.

**9b, each tool its own extension.** Deliverables:

- `ruff`, `basedpyright` (with the `typecomplete` option),
  `mypy`, `ty`, `pyrefly`, `pytest`, `clang_format`, `clang_tidy`
  under `livery.extensions`, each its own distribution in
  `packages/extensions/<name>/`, each registering its checks,
  fragments and tools.
- Discovery allows the group directory; receipt tags take the longer
  path; the affected graph, CODEOWNERS and provenance follow.
- This repository lists the eight.

Acceptance, refusals first:

- `test_a_workspace_check_runs_once_for_two_hundred_packages`,
  `test_an_overflowing_path_list_splits_into_the_fewest_calls`,
  `test_above_the_threshold_the_check_runs_whole` (a counting seam).
- `test_a_role_with_no_listed_check_has_no_verb`.
- `fm check` exits 0 with the same gate members as before, proven by
  the gate's pinning tests.

### Phase 10: the workspace extensions ship apart

Deliverables:

- `docs` as `livery-extensions-docs` (carried: the empty shell plan's
  phase 4; the doctor line naming the extension that brings the docs
  job; the docs-build test of the private-members policy).
- `changelog` as `livery-extensions-changelog`: the git-cliff provider,
  `cliff.toml` as its fragment for every package, `git_cliff` as its
  tool; `CHANGELOG.md` created on the first record.
- `housekeeping` as `livery-extensions-housekeeping`: requires `mypy`,
  `ty`, `pyrefly`; the voice and documentation prose; our naming and
  layout rules (a root `__init__.py`, `api` twice, an `api`
  re-exporting its own module, a distribution not named after its
  import path).
- `claude` as `livery-extensions-claude`: skills, hooks, settings, the
  `hooks.pre-bash` verb and the `CLAUDE.md` assembly.

Acceptance:

- `fm check` exits 0.
- `test_a_workspace_without_the_docs_extension_installs_none`,
  `test_a_release_without_the_changelog_extension_writes_no_notes`.
- A project born without `claude` has no `.claude/` and no
  `CLAUDE.md`; without `housekeeping`, no mypy, ty or pyrefly; both in
  the conformance kit.
- `fm docs.build` renders every package's changelog page.

### Phase 11: package composition and the release train

**11a, the model.** Deliverables: package-level extensions with
`requires`, compatibility through extras, order, options; the
validity rule; the canonical minimal set; `fm extensions
--combinations` and completion; the phases with `pre` and reversed
`post`; the declared context; the queries, `executables` among them;
the `run` phase and `fm run <package>[:<executable>] -- <arguments>`,
its `pre` the `build` role over the dependency closure.

**11b, the package extensions.** Deliverables: `python`, `cpp`,
`cmake`, `conan`, `nanobind`, `unreal` (its declaration only, as in
phase 12) under `livery.extensions`, each its own distribution; the
backends move into them; kinds, `KindRecord` and the kind registry go;
this repository's packages list their extensions.

**11c, the release train through phases.** Deliverables, carried from
the empty shell plan's phases 5 and 6: `stamp`, `build`, `prove`,
`publish` and `replay` replace the backend calls; `[publish]` and
several artifacts per package; ecosystems as registrations with their
defaults; `livery.forge.RegistryKind` an open name; the forge's
`package_admin`; `[release] publish` goes. Builds on the releases
plan's phases 1 and 2.

**11d, the vocabulary at zero.** Deliverables, carried from the empty
shell plan's phase 9: every remaining reach-in leaves the base; the
vocabulary allowance holds only the runtime.

**11e, the graph from the native manifests.** Deliverables: the
workspace graph's edges come from the `declared requirements` query,
every section included (runtime, build, test extras), so `[[depends]]`
restates nothing a manifest already says; its `kind` key goes. The
forge's `test` extra on footman and toolroom then becomes edges, and
a change to either reruns the forge's tests, which the graph misses
today. Acceptance: `test_a_test_extra_on_a_sibling_is_an_edge`, and
`fm check` reruns the forge's suite after a change to footman.

Acceptance, refusals first:

- `test_an_unconnected_pair_refuses_naming_both`,
  `test_two_providers_of_one_key_refuse`,
  `test_a_post_runs_after_a_failed_main_and_sees_the_failure`,
  `test_two_extensions_naming_one_executable_refuse`.
- `fm run` of a C++ application whose library member changed rebuilds
  the library, then the application, then starts it; run again with
  nothing changed, it starts at once and calls no build tool (a
  counting seam).
- The release train's pinned properties pass before and after 11c.
- `fm workflow.release --local` releases this repository.
- `fm ci.e2e --scenario=release` is green on the lodge's host setup.
- The vocabulary test's allowance holds runtime lines only.

### Phase 12: toolchains

Carried from the toolchain plan's phases 2 to 4.

Deliverables:

- The toolchain engine in the base; `[toolchain.<language>]`.
- The `cpp` extension's families and probes behind seams; the `llvm`
  and `gcc` records with profiles; the conan profile rendered from the
  receipt; the lock holding several toolchain versions side by side.
- Receipts in the run record and, per artifact, in the release series
  (`fm store.show release --key=<tag>`).
- The `unreal` extension deriving its declaration from the engine's
  SDK json files.

Acceptance: the toolchain plan's phase 2 to 4 acceptance, with
`[toolchain.cpp]` for `[cpp.toolchain]`.

### Phase 13: lodges

**13a, the forge's admin protocol.** Deliverables: seeding, runner
tokens, repository import, version deletion, per backend or
`Unsupported`, in the conformance suite.

**13b, the lodge.** Deliverables: `livery-lodge` with `create`,
`config`, `ls`, `down`, `rm`, runner restart; host and docker modes;
runner specs and emulation opt-in; the local checkout; overrides by
remote lookup and `WORKSHOP_OVERRIDES`; `forge.dev` folded in; the
local loop plan's setups and container setups.

Acceptance, refusals first:

- `test_an_override_naming_an_unknown_key_refuses`,
  `test_an_overridden_value_is_named_by_env_show`.
- `fm lodge.create .` from nothing, on a machine whose store holds the
  records, is up, seeded and populated within the time measured in
  the local loop plan's phase 2 (25 s), and `fm lodge.rm` leaves no
  file and no process.
- In the lodge's checkout, `fm submit --armed` lands a change through
  the lodge's CI.

### Phase 14: e2e on the lodge

Deliverables:

- `e2e` leaves the base (`_e2e.py`) for `packages/devtools/e2e`,
  published nowhere, mounted here as a plugin, building on the lodge.
- The local loop plan's phase 3 (the bench), phase 3b (the extension
  under test: `fm ci.e2e --extension=<name>` births one member per
  combination the extension takes part in) and phase 4 (images from
  the lock).

Acceptance: the local loop plan's phase 3, 3b and 4 acceptance, with
`--extension` for `--layer`.

### Phase 15: one affected engine, and sync at scale

Deliverables:

- The gate's affected engine extracted into the shared form: the
  record, the changed paths, the unit mapping, the skip, and an
  influence rule per consumer; its behaviour pinned by its existing
  tests before it moves.
- Sync as its second consumer: the synced record (the locks, every
  contract, every in-repository extension's source, each package's
  input fingerprint); a sync skips whole when nothing changed and
  every output's receipt is present, and otherwise re-renders only
  the packages its rule names.
- The development build as its third consumer, shared with the
  gate's `build` checks.
- One filesystem walk per sync, no process per package.
- The budgets of contract 21, set from phase 1's baseline.

Acceptance:

- The scale fixture's warm, nothing-changed sync and its one-package
  sync meet their budgets in the CI store's metrics.
- `test_a_warm_sync_with_nothing_changed_renders_nothing`,
  `test_a_missing_output_receipt_defeats_the_skip`,
  `test_an_extension_change_resyncs_only_the_packages_listing_it`.
- The gate's affected tests pass unchanged before and after the
  extraction.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| check-owned fragments, prose, skills, hooks, settings and CSS as separate channels | one fragment engine (phase 6) |
| copier, the templates, the answers file, the template channel | seeds and fragments from wheels (phases 7, 8) |
| `CiContract` and kinds' roles | roles from listed checks (phase 9) |
| every check registered by the base | one extension per tool (phase 9) |
| kinds and the backend modules | package-level extensions (phase 11) |
| `[release] publish`, `KindRecord.artifact`, `wheel_identity` | `[publish]` and the phases (phase 11) |
| the vocabulary allowance | runtime lines only (phase 11) |
| `[cpp.toolchain]` and `host_tools=("cc", "c++")` | `[toolchain.cpp]` (phase 12) |
| `fm devenv.*`, `fm forge.dev.*` | `fm lodge.*` (phase 13) |
| `_e2e.py` in the base | the e2e plugin (phase 14) |
| `_scale.py` in the base | the e2e plugin (phase 14) |
| `livery-cbor` 0.0.0 on the index | nothing: kept as the name's claim |

## Decision record

- 2026-10-03, phase 4, after the merge (issue #1039): the mount names
  a missing list, an undeclared extension, a wrong level and a plugin
  that will not mount on stderr and goes on; the gate's layering check
  and `fm extensions` refuse them. Pulling the change left a checkout's
  environment without the new entry points, and the refusing mount
  stopped `fm sync`, the command that installs them. Only a wrong API
  version still refuses at mount.
- 2026-10-03, phase 4: an extension's declaring module carries
  `API_VERSION`, `LEVELS`, `PLUGIN`, `REQUIRES`, `TOOLS`, `FOR` and
  `CONTRACT_KEYS`; the `workshop.contract` group folded into
  `workshop.extensions`. The base is never listed: `stack_entries()`
  puts it first for the readers that walk the whole stack (content,
  templates, the site's assets). A list entry is a name or
  `{ name, for }`: `import` and `dist` went, since the entry point
  names both, and `for` stays as the project's opt-out from an
  extension's contributions. The docs extension is listed as `docs`;
  forge, the bench and footman add verbs alone, so they are plugins
  listed under their module names until phase 5. A missing
  `[workspace] extensions` refuses at mount and in `fm extensions`,
  printing the line; a reader of the list finds it empty, so a
  contract written by a test or a tool before its list is not refused
  on every read. "Layer" left the code and the docs; "layering", the
  package dependency check, is another word and stays. The editor's
  marketplace ids, which the check records also called `extension`,
  are `editor_extension` now.
- 2026-10-02, phase 3: seven roots became namespaces with an `api`
  module: footman, forge, strongroom, workshop, and toolroom's store,
  bench and `tools`, whose house spelling is now
  `import livery.toolroom.tools.api as tools`, like
  `import livery.footman.api as footman`, so every call site reads as
  before. A module inside a distribution imports a name from the
  module that defines it, never from its own `api`, so `api` imports
  nothing that imports it back. `module_roots` takes the topmost
  `api.py` or `__init__.py` as a root, and the version stamp writes
  either, so a project born from the templates keeps a regular
  package: the `api` layout is this repository's convention, pinned
  by `tests/test_namespaces.py`, not a workshop rule. The docs
  extension moved to `livery.extensions.docs` inside the workshop
  wheel (`module-name` lists both); it is a namespace with no public
  names, so the typecomplete check verifies each root's `api` and
  skips it. A footman plugin entry naming a root's `api` stands for
  the root in the layering check's plugin exemption.
- 2026-10-02, phase 2: the declarations are data, `Declared(contract,
  path, types, values)`, a dotted path with `*` for a user-named key
  and `[]` for a list's entries. The base's live in
  `livery.workshop._contract_keys` and beside the readers that own a
  vocabulary (`_registries`, `_docs_contract`, `_tools`, `_points`),
  so an allowed set is written once; a layer names a data module under
  the `workshop.contract` entry point group, loaded without its tasks,
  since `plugin()` must stay a layer's first importer. Phase 4 folds
  the group into `workshop.extensions`. Every check a reader made of
  a type or an allowed value the judge now makes went, the `type` and
  `channels` refusals and the contributed point's grant refusal among
  them (an unknown key now). One behaviour reverses: a `[ci]
  profile-*` key of the wrong type stopped nothing and kept its
  default; it now refuses like every key, and only a value of the
  right type out of range keeps the default. `_layers` still reads
  `[workspace] layers` raw at mount, so a broken contract never stops
  the verb that fixes it. The weekly `scale` entry from phase 1b
  leaves the nightly in the same change, per Willem's ruling.
- 2026-10-02, phase 1b: the fixture is a copy of this tree, not a
  newborn project, because a newborn installs the published workshop
  and the copy runs the one under test with no index. Its own
  members keep their source and lose their test modules. The local
  baseline on this machine, 300 members (240 python, 40 cpp-conan,
  20 nanobind), every verb exit 0: generation 464.1s; `fm sync`
  62.5s cold, 20.3s warm, 10.8s one changed; `fm template.check`
  564.5s, 528.7s, 412.0s; `fm check` 595.2s cold and whole, 0.7s
  warm, 454.1s one changed. The template render costs about 9
  minutes at this size in every state, and the one-changed gate
  pays most of it again; that is the cost phases 6b and 15 remove.
  These are a local wall clock, kept for comparison as the phases
  land. Willem ruled the same day that no weekly CI job runs it:
  most of what it measures changes before a first Monday run, so
  the local numbers are the record, and `fm ci.scale` is re-run by
  hand to compare.
- 2026-10-02, phase 1a: phase 1 splits into the spike (1a) and the
  scale fixture (1b), which needs a CI point to record its timings
  in the store and so lands apart. The spike's one finding changed a
  template line, with no change to any rendered byte.
- 2026-10-02: this plan written. It supersedes the extensible gate
  plan, the empty shell plan and the local loop plan, and takes in
  the toolchain plan's phases 2 to 4. The rulings below were taken
  between 2026-10-01 and 2026-10-02.
- Willem: templates and copier go entirely; seeds come from
  extensions' wheels and everything kept current is a fragment.
- Willem: one new plan supersedes the plans in progress, until the
  end of the refactor; the other session's plan is parked.
- Willem: a check is per tool; each tool is its own extension in its
  own wheel, so a workspace mixes and matches without paying for what
  it does not list. "Check" keeps its name; each has a role.
- Willem: roles exist only when a listed extension registers a check
  for them.
- Willem: fragments belong to extensions, not to checks; file-based
  preferred, strings for dynamic content; one mechanism for every
  kind of delivered content.
- Willem: rendering is minijinja, in python too; a dynamic fragment
  produces text or template source and minijinja never calls python.
- Willem: an extension may replace or delete another's fragment,
  declared; a reference to a fragment that does not exist refuses.
- Willem: python stays the language of the workshop and footman;
  speed comes from design and caching, with `fm sync` over hundreds
  of packages the target.
- Willem: extensions are the one concept, listed at the workspace or
  the package level; "layer" and "trait" go. Any package may
  contribute an extension, and a wheel may hold several. An extension
  exists only where a plugin is not enough.
- Willem: plugins may declare tools, optional ones with `?`; scopes
  take `!`.
- Willem: `[workspace] platforms` declares the supported platforms;
  undeclared, every host; an unscoped requirement must resolve on
  every supported platform; every runner must be a supported
  platform, and a supported platform needs no runner.
- Willem: configuration is strict and teaching everywhere; removed
  keys refuse as unknown, with no code naming them.
- Willem: `[workspace] extensions` is required; `fm new.project`
  writes `ruff`, `basedpyright` and `pytest` explicitly; the base
  depends on no extension.
- Willem: `cpp` and `cmake` are separate extensions; `conan` is
  additive; a package's kind is a composition of package-level
  extensions with requires, compatibility declared from either side,
  and order declared by the extension that knows.
- Willem: options use `[...]`, as profiles do; `[publish]` may appear
  any number of times; options are the extension's own.
- Willem: extensions contribute to lifecycle phases with `pre`, main
  and `post`; `post` runs in reverse and always once its `pre` ran,
  and the context makes the failure easy to check.
- Willem: extension version ranges are the wheel's own metadata.
- Willem: combinations are generated, named in evaluation order with
  alphabetical ties, and listed and completed.
- Willem: `[checks.<tool>]`, `[checks.<tool>.<role>]` and
  `[roles.<role>]`.
- Willem: `basedpyright` takes typecomplete as an option, off by
  default; this repository lists `basedpyright[typecomplete]`.
- Willem: our extensions live in `livery.extensions`, grouped in
  `packages/extensions/`.
- Willem: the toolchain generalises beyond C and C++; rust and go are
  wanted, and zig, swift and others are not ruled out.
- Willem: devenv becomes the lodge, `livery.lodge`: cheap disposable
  hosted development environments populated from a URL or a path; a
  lodge prints the configuration it needs, the local checkout detects
  it, and CI jobs receive the overrides by variable; one native host
  runner by default.
- Willem: `e2e` builds on the lodge and is workshop development
  tooling, never published.
- Willem, 2026-10-02: `[[depends]]` edges carry no `kind`. The field
  mixed two questions. When a dependency is needed (build, runtime,
  test) is stated by the native manifest's sections, `pyproject.toml`
  or `conanfile.py`, and checked there through the `declared
  requirements` query; whether a sibling is used as a tool is
  additive to all three, so it cannot be one of the values. In the
  code `build` and `runtime` were checked identically, this
  repository declared no `test` or `tool` edge, and every other
  consumer of an edge (the cycle check, the gate's closure, the
  release order, the floor bump) ignores the field. An edge on a
  sibling used as a tool, with no native home, is a declaration of
  its path alone, counted like every edge. Phase 2's strictness
  makes a `kind` key refuse as unknown. This closes the extensible
  gate plan's open item 1.
- Willem: a `run` phase starts a package's executables, after a
  development build of the dependency closure that builds only what
  changed; one affected engine serves the gate, sync and that build.
- 2026-10-02: the release notes provider, the derived version and the
  member list (the empty shell plan's phases 7 and 8a) are the
  foundations phase 11 composes on.

## Open

1. **Manifest dispatch** (the empty shell plan's phases 10, 11 and
   13). Option A is built; B and C wait to be revisited, with the five
   facts that note records. Owner: Willem.
2. **The proof of the C++ coverage union across gcc, clang and MSVC**
   (the extensible gate plan's open item 26): it needs a C++ member
   in a workspace with three runners. Owner: Willem.
3. **A check's input digests** (the extensible gate plan's open item
   19), now the finer grain of contract 22's caching. Owner: phase 15.
4. **The tool records' home** (livery#882). Owner: Willem.
5. **The playground**: whether footman's docs examples work outside
   its shims, and its browser runtime (the extensible gate plan's
   open items 22 and 23). Owner: Willem, with the playground's plan.
6. **The conformance loop's runner** (#930, #931; the extensible gate
   plan's open item 25), now the lodge's. Owner: phase 13.
7. **Verb arguments passed through to a tool**
   (`fm test.pytest <paths> -- <arguments>`; the extensible gate
   plan's open item 29). Owner: Willem.
8. **The lodge's runner store**: the machine's own or its own
   directory (the local loop plan's open item 3); and Gitea's version
   in host mode against docker mode (its open item 6). Owner:
   phase 13.
9. **The CycloneDX rendering of the release rows** (the toolchain
   plan's open item 5). Owner: Willem, after phase 12.
10. **The pwsh spelling of the entry** (the extensible gate plan's
    open item 5). Owner: Willem.
