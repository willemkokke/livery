# The workshop's destination: an engine, and the API its extensions use

Status: ruled by Willem on 2026-10-07; every design question is closed
and the extensions plan takes phases 10 to 16 from here. Written
2026-10-06 against
`origin/main` at `4f2a5e53`. Nothing here is built. The extensions plan
(`notes/20261002-extensions-plan.md`) stays the one plan; this note
rewrites its phases 10 to 15 against a designed destination.

## The rulings this design satisfies

Willem, 2026-10-06, quoted by intent:

1. The core does not know about documentation generation. As a
   principle: the core knows no language, no ecosystem and no product
   concern. Those live in extensions. The core is a composition engine
   and an extension point API.
2. The workshop has a public API that extensions use. That API is what
   the refactor establishes.
3. No package's source reaches into another package's privates without
   a compelling case. Tests have one and may.
4. Delivery stays incremental, gate-green and mergeable one phase at a
   time, against this destination.
5. Every root in livery drops the `.api` experiment, footman included: a
   root's entry module is its `__init__.py`; what need not load at runtime is imported under
   `TYPE_CHECKING`, so type checking, type completeness and the API
   reference still see it.
6. Java is not planned and nothing may rule it out. Rust must be
   supported.

The principles that rank every option below, in order: less code,
simpler code, one way to do each thing, one concern per module.

## What this note read

Measured on `4f2a5e53`, 2026-10-06, each claim of the brief checked:

- `livery.workshop.api` exports 29 names. The eight tool extensions use
  20 of them and import no private workshop module.
- The docs extension (`livery.extensions.docs`, inside the workshop
  wheel) imports 20 private workshop modules: `_checks`, `_contract`,
  `_contract_keys`, `_docs_contract`, `_extensions`, `_forge_lane`,
  `_git_ops`, `_influence`, `_kinds`, `_navblocks`, `_packages`,
  `_points`, `_prose`, `_provenance`, `_quality`, `_registries`,
  `_release_notes`, `_site_files`, `_slots`, `_state`.
- `_navblocks` has three readers: the docs extension, `api.py` and
  toolroom-bench's `_tasks.py`. The brief's "within workshop, only the
  docs extension and `api.py`" holds; the bench is the third, outside.
- The base reads the `[docs]` table in `_ci_generate`,
  `_workflow_tasks`, `_templates`, `_provenance`, `_backends/_python.py`
  and `_backends/_python_nanobind.py`, as the brief lists.
- `KindRecord.root_files` exists; `_shipped_files._composed` appends the
  agent outputs and the kinds' root outputs to the composed files.
- The source-side reaches across distributions, found by one AST walk
  over `packages/*/src` and `packages/extensions/*/src`:

| From | Into | Names |
| --- | --- | --- |
| toolroom (`tools/api.py`, `tools/_host.py`) | footman | `_globals` (`real_getcwd`, `active`, `argv_override`), `_context._target_cwd`, `_context._current`, `_context.Invocation`, `run(_show=)` |
| toolroom-bench (`_tasks.py`) | footman | `_describe.bold`, `cyan`, `wants_color`; `_globals.active` |
| toolroom-bench (`_tasks.py`) | toolroom | `tools._colordata` |
| toolroom-bench (`_artifacts.py`) | toolroom-store | `store._engine.download` |
| toolroom-bench (`_tasks.py`) | workshop | `_navblocks.write_nav_block` |
| workshop (seven modules) | footman | `_paths.env_var`, `_paths.builtin`, `_paths.tasks_file_name`, `_config.project_builtin` |
| workshop (`_e2e.py`) | forge | `_registry.purge_packages`, `purge_gitlab_packages` |

  This is #1204's list, with footman's `current` and `RunTimeout` gone
  from it since #1205 made them public.

- The plan's decision record holds the rulings of 2026-10-06 on
  privates, on footman's privatisation (#1152) and on Java.
- footman's docs: `livery.footman.markdown` (`render_site`,
  `render_page`) and `livery.footman.docs` (`fm docs page`, `site`,
  `shots`) are public. `fm footman.pages` (`_docsgen`) writes footman's
  API page from a hand-kept section table validated against `__all__`,
  its errors-and-notes page, three tables, an example render and the
  latest-release admonition. The workshop's task reference
  (`livery.extensions.docs._taskref`) already renders every package's
  `footman.tasks` providers through `render_site`, spawning
  `fm --tasks-file=<probe> --json --list` per provider for isolation.
  footman's contract declares `generators = ["footman.pages",
  "docs.task-reference"]` and `[docs] api = false`.

## Ground-truth contracts (do not violate)

Numbered after the extensions plan's 25; they bind its phases from 10
on.

26. **Every declaration is data in the extension's `extension.toml`,
    read at mount and judged against the schema.** Behaviour is
    referenced by name, `"module:function"`, and imported when it runs.
    Extension code calls no `register_*` function; the base exports
    none. A registry an extension owns (the docs extension's
    generators) is read the same way, through
    `livery.workshop.contributions_for`.
27. **A registry is written at mount and read at use.** No code reads a
    registry while the mount runs, so no extension's correctness
    depends on its position in the list beyond what `requires` and
    `[for]` state.
28. **Mount order is the base, then the package-level extensions, then
    the workspace list.** Package-level extensions mount in requires
    order over the union of every package's list, then the workspace
    list in listed order. Composition order is the same order. A
    workspace extension that names a capability at mount (a check's
    `kinds`, a category it extends) finds it registered or refuses by
    name.
29. **Every registration names its tools, and a tool is required only
    while the registration is in use.** A check's tools while a package
    it judges exists; a package-level extension's while a package lists
    it; a generator's while the docs extension is listed and a package
    its claims reach exists; a workspace extension's `tools` while it is
    listed. A mounted extension whose registrations are unused costs its
    declaration file's parse and nothing else.
30. **A public name lives in the root's entry module or a public module
    it declares, is documented, type-complete, pinned by a test and
    judged by the conformance kit.** A package's source never imports
    another distribution's underscore module or name, except through
    the allowance the reach check reads, each entry with its reason.
31. **One `api-version` at a time, no compatibility code.** The
    number is 1 and stays 1 until the workshop keeps backwards
    compatibility. Until then every API change lands with every
    extension of ours in one wave, this repository being the only user,
    and no migration is written. The mount turns a mismatch into a
    sentence naming both versions, which is what a wheel from another
    era meets.
32. **One schema per contract, shipped as data.** The base declares
    the schema of `extension.toml` and of its own tables; an extension
    declares the keys it owns in `[contract.<contract>.<table>]`; `fm sync`
    composes the workspace's effective schema into JSON Schema under
    `.workshop/schema/`, and the judge, the editor and the contract
    reference pages read that one file. The judge keeps the rules a
    schema cannot say. A table's owner may be a dependency of the
    base, not only an extension: `[toolroom]` is toolroom's, and its
    schema fragment ships in `livery-toolroom-store`.
33. **A check whose verdict is its tool's exit code is words, not
    code.** `judge`, `fix`, `safe-fix`, `env`, `matrix` and the
    placeholders the engine answers; `run` only where the words cannot
    say it, and never both.
34. **The docs extension owns every word of documentation generation.**
    Pages, generators, handlers, inventories, nav blocks, the members
    policy, the site's config, the publish seam and the `[docs]` table.
    Another extension contributes to it by requiring it or through
    `[for]`, never the other way.

## The architecture

### The engine

`livery.workshop` is a composition engine and an extension point API.
It owns:

- the contracts: `workshop.toml` at the root and per package, strict,
  every key declared by its owner;
- package discovery and the workspace graph;
- the mount: the `workshop.extensions` entry point group, the levels,
  `requires`, `[for]`, options, the API version check;
- the registries: checks, fragments and seeds, categories, slots,
  contract keys, CI points and jobs, guidance sections and fragments,
  the release notes provider, AST rules over a language's reader,
  lifecycle phases, queries, root files, toolchains (phase 14), the
  affected engine (phase 16);
- the gate walk, sync and the fragment engine, the workflow family
  (`start`, `commit`, `integrate`, `submit`, `issue.*`), the release
  train's phases, the state store, the CI shell (plumbing only, #286),
  `doctor`, `explain`, `extensions`;
- its own runtime, named: Python, uv, the venv, the root
  `pyproject.toml` and `uv.lock`, footman, toolroom, the forge.

Guidance stays in the engine. The sections (identity, voice, standards,
rules, workflow, gate, verbs, kinds, tools), the fragment naming
convention under `content/fragments/` and the sections rendered from
the registries are how the workshop explains itself, to an agent and to
a reader alike, the way `--help` is footman's. They name no language,
ecosystem or product. What leaves is each reader's file format: the
agent's entry file and `.claude/` (the claude extension), the site's
development pages (the docs extension).

### The sorts of extension

The engine knows two levels, `workspace` and `package`, and nothing
else about an extension's sort: an extension is what it registers.
Four sorts exist from a reader's view, and the conformance kit's
clauses apply by what is registered, not by a declared sort.

| Sort | Level | May register | Seams it uses | Examples |
| --- | --- | --- | --- | --- |
| check (tool) | workspace | checks, page generators, tool configuration fragments, editor contributions, tools, options | `[checks.<tool>.<role>]`, `[generators.<name>]`, `[fragments."<target>"]`, `content/`, `tools`, `[options]` | ruff, basedpyright, mypy, ty, pyrefly, pytest, clang-format, clang-tidy; doxygen (phase 12) |
| package | package | what a package is composed from: phases, queries, categories, seeds, root files, tools, host tools, toolchains, the layering reader, CI jobs, checks of its own | `[phases]`, `[queries]`, `[categories]`, `seeds/`, `[root-files."<path>"]`, `tools`, `[ci.jobs.<point>.<name>]`, `[checks.<tool>.<role>]`, `[for]` | a language: python, cpp, later rust, go, java; a build system: cmake, nanobind; an ecosystem: conan, later crates, maven, npm; a platform or SDK: unreal |
| workspace (product) | workspace | checks, CI jobs, slots, guidance, the release notes provider, AST rules, fragments, verbs; a registry of its own that other extensions contribute to | `[checks.<tool>.<role>]`, `[ci.jobs.<point>.<name>]`, `[slots.<name>]`, `[guidance.<section>]`, `release-notes`, `[rules.<name>]`, `[fragments."<target>"]`, `contributions_for` | docs, changelog, claude, housekeeping |

The package sort has four flavours and no sub-sort: a language, a
build system, an ecosystem and a platform differ in which phases and
queries they answer, nothing more. `conan` composes with `cpp` and
`cmake` the way the plan already states, `nanobind` with `python` and
`cmake`, and `python` carries PyPI inside it because uv and
`pyproject.toml` are the engine's runtime. A package-level extension
contributes CI jobs like a workspace one, and its job exists while a
package lists it: the wheels job is nanobind's, with the runner labels
its packages declare, and an engine build on its own runners is
unreal's.

No `sort` key is declared. The brief's question, whether to
define extension types with a load order between them, is answered by
contract 28 without a new declaration: the level is the order. Every
package-level extension mounts before any workspace extension, so a
language's capabilities are registered before a product reads them,
and a product that names a language's capability at mount gets a
refusal naming the capability instead of an `AttributeError` at verb
time.

### Requiring and extending

Two declarations say how an extension relates to another, and no
third is needed:

- `requires` says the extension cannot work without the target. It
  declares its registrations at the top level of its `extension.toml`,
  since a required
  target is always present, and a listing without the target refuses
  through the layering check and its fix, naming the entry and the
  missing target. The mkdocstrings and doxygen extensions require
  `docs`.
- `[for.<target>]` says the extension adds to the target when the
  target is there. The table is read only when both are listed, and a
  reference in it imported only then, so a workspace without the
  target is silent by construction.
  A house extension with opinions on python and cpp is this case, and
  so is pytest's coverage pages for docs.

An extension whose every registration lives in `[for.<target>]` tables, listed
in a workspace where no target is listed, is dormant. That is not
refused: a listing kept until the first python package arrives is a
statement of intent. It is visible: `fm extensions` and `fm doctor`
print the entry as dormant with the targets that would wake it,
derived from the declarations alone. "At least one of several
targets" has no spelling and no known case; if one appears it is a
`requires` entry spelled as alternatives, a grammar change to rule on
then.

`compatible` is a third declaration on another axis: composition, not
contribution. It says two package-level extensions may sit on one
package, and the validity rule reads it: a package's list is valid when
every pair is connected through `requires` or compatibility, else the
package refuses naming the pair. For a package-level pair, a `requires`
or `for` target counts as compatible: contributing to an extension
means knowing it, so `[for.python]` without `compatible = ["python"]`
would be a contradiction to refuse, and deriving it removes the case.
`compatible` is needed only where neither side requires or contributes
to the other, `conan` beside `nanobind` for one. The wheel's side is
one rule as well: a compatibility claim is an extra (contract 15), and
a reference under `[for.<target>]` that imports its target's public
names is the same extra, `livery-extensions-nanobind[python]`. So three
outcomes, each at its own place: a missing `requires` target refuses at
the listing; an absent `for` target is silence, the entry dormant and
named; an unconnected pair refuses at the package, naming both.
`before` and `after` are declared against required or compatible
extensions, which the derived compatibility covers.

### Dependencies follow use

The brief's third thought: mounting an extension must not install its
dependencies. Contract 29 states the rule per registry, and the parts
already hold for checks ("a check's tools reach the tool profile for
every kind the check judges"). What changes:

- a package-level extension's distribution enters the dev group only
  while some package lists it (the composed `pyproject.toml` lists the
  distributions of the listed extensions, workspace list plus the
  union of the packages' lists);
- a generator's tools (doxygen for the C++ reference) are required
  only while the docs extension is listed and a package the generator
  claims exists;
- `extension.toml` imports nothing, and a reference in it is imported
  when it runs; every verb and every generator imports its library
  (griffe, a Doxygen XML reader) inside the function that runs.

### What the core must not know, and the seam that replaces each

| Knowledge in the core today | Where | Seam in the destination |
| --- | --- | --- |
| the `[docs]` table and its keys | `_docs_contract.DECLARED`, `docs_table` | the docs extension's `[contract.<contract>.<table>]`, `api` among them; a generator's own settings are its `options`, set at `[generators.<name>]` in a package's contract as a check's are at `[checks.<tool>.<role>]` |
| the docs jobs' system requirements and the pages seam in the CI render | `_ci_generate` reads `docs_requirements`, `publish_seam` | `installs` (system packages the job installs before entering) and `deploy` (the seam's value), set by the contributing extension on its `[ci.jobs.<point>.<name>]` entry |
| pages hosting asserted at `fm workflow.configure` | `_workflow_tasks` reads `publish_seam` | `[setup.<name>]`: steps an extension contributes to the repository's configuration, run by `workflow.configure` |
| the site URL in the composed `pyproject.toml` | `_templates` reads `docs_table` for `docs_site_url` | the `project.urls` slot, merged by key; the docs extension contributes `Documentation` |
| which categories the site reads, for the docs job's skip | `_provenance.site_reads` | `inputs` on the job, the same table a check declares; the shell's skip rule reads it |
| the API extractor on a kind | `_kinds.Extractor`, `KindRecord.extractor`, `kind_extractor` | `livery.extensions.docs.Generator`, declared in `[generators.<name>]` by a generator extension of its own (`mkdocstrings`, `doxygen`), naming the package-level extensions it extracts for, and read by the docs extension through `contributions_for("docs")`; its site configuration a `zensical.toml` fragment |
| coverage pages on a kind | `KindRecord.coverage_pages` | a `[generators.<name>]` entry of pytest's `[for.docs]` table |
| the nav block format | `_navblocks`, `rewrite_nav_block` in the api | `livery.extensions.docs.write_nav_block`, `nav_block_markers` |
| the site's override template as a rendered file | `_site_files`, read by `_ci_generate` | a whole-file fragment the docs extension ships under `content/root/overrides/main.html`; `_site_files` goes |
| the wheels job: which members build platform wheels and on which runner labels | `_ci_generate` reads `member_roster`, `wheel_runners`; `Job.only = "wheels"` in the base | nanobind's `[ci.jobs.<point>.<name>]` entry, existing while a package lists it, its runners from the labels its packages declare under a key nanobind owns |
| the docs tree embedded into a wheel | `_docs_contract.module_docs`, read by the python backends | the python extension's `build` phase; it reads the `prose` category's directory, which is the engine's layout, not generation |
| whether a package declines its reference, and its module root | `_docs_contract.declines_api`, `module_root` | `[docs] api` stays the docs extension's key; the handler's search paths are the mkdocstrings generator's `paths` option, set at `[generators.mkdocstrings]`; the module root is the `MODULE_ROOTS` query |
| the python-only checks `lint.docrefs`, `lint.docstrings` in the docs extension, filtering by kind chain | `docs/_checks.py` | `[checks.<tool>.<role>]` of the mkdocstrings extension: both exist because the reference publishes every docstring, and `lint.docrefs` resolves names the way that reference does, through griffe |
| git-cliff, `cliff.toml`, `CHANGELOG.md` | `_cliff`, the `cliff.toml` fragment | the changelog extension; `release-notes` on its module; the base keeps the provider protocol and asks |
| the agent's entry file, `.workshop/fragments/`, `.claude/`, the hooks verb | `_shipped_files._agent_outputs`, `_hooks` | the claude extension's dynamic fragments over `guidance(root, AGENT)`; `hooks.pre-bash` its verb |
| the python kinds and backends | `_kinds._register_builtin`, `_backends/` | the python, cpp, cmake, nanobind, conan and unreal extensions (phase 11) |
| the layering check's python parse | `_ast_rules.parsed_source`, `imports_of` | the `references` query: a language extension answers what a source references; rules register against it |
| the registry kinds and their defaults | `_registries._ECOSYSTEM` | ecosystems as registrations (phase 13) |

Everything else the docs extension reaches is generic and becomes
public under the names in the next section.

## The public API, as a contract

Ruling 5 puts every root's public names in its `__init__.py`. So a
name below lives in `livery.workshop`, `livery.extensions.docs`,
`livery.footman` or `livery.toolroom.tools`, or in a public module the
entry module declares. `livery.workshop.api` is the path today; phase
10a retires it.

### The declaration file

An extension declares itself in one `extension.toml` in its wheel,
found through the `workshop.extensions` entry point, which names the
package that ships it. The file is a contract: the engine reads it with
the reader that reads `workshop.toml`, judged against the schema the
base ships, kebab-case keys, every refusal naming the file, the key,
what the table takes and the nearest spelling. Nothing in it is
imported at mount. Behaviour is referenced by name, `"module:function"`,
and imported when it runs; a reference that does not import refuses at
mount naming the file and the key. Every key is optional.

The two files share their conventions, so a reader of one reads the
other: kebab-case keys; an identity table named after the thing,
`[workspace]` there and `[extension]` here; tools under `[toolroom]
requires` in the one requirement grammar, the table named after its
owner like every other; an address as a table path,
so a check is declared at `[checks.ruff.format]`, the address the
contract configures it by, and no array of tables exists; `[ci]` for
what reaches the CI shell; `[categories]` the same shape in both; and
a schema fragment with the shape of the file it describes.

| Key | Value | Sort | Users today, or the phase that brings one |
| --- | --- | --- | --- |
| `[extension] api-version` | int | every | the eight tool extensions, docs |
| `[extension] levels` | `["workspace"]`, `["package"]` or both | every | the eight, docs |
| `[extension] plugin` | the footman plugin carrying the verbs | every | docs |
| `[extension] requires` | extensions it cannot work without, listed before it; it declares for them at the top level | every | mkdocstrings (phase 11), housekeeping, doxygen (phase 12) |
| `[extension] compatible` | extensions it combines with, from either side; a `requires` or `for` target counts as one | package | phase 11 |
| `[extension] before`, `after` | order within a phase against named extensions | package | phase 11 |
| `[toolroom] requires` | the tools its verbs need, in the root contract's own table and grammar | every | forge's dev plugin (as a plugin) |
| `[options]` | option name to what it turns on | every | basedpyright |
| `[contract.<contract>.<table>]` | the keys it owns, at the table path they have in that contract: `publish = { types = ["str"], values = [...], doc = "..." }` | every | docs |
| `[checks.<tool>.<role>]` | a check, at the address the contract configures it by: `scope`, `narrowing`, `transport`, `threshold`, `extensions`, `claims`, `tools`, `options`, `inputs`, `after`, and either its words (`judge`, `fix`, `safe-fix`, `env`, `matrix`) or references (`run`, `fix`) | every | the eight, docs |
| `[generators.<name>]` | a page generator: `extensions`, `claims`, `tools`, `options`, `run` | check | mkdocstrings (phase 11), doxygen (phase 12) |
| `[fragments."<target>"]` | a dynamic fragment: `render`; files ship under `content/` | every | claude's `CLAUDE.md` (phase 10), docs' override template (phase 12) |
| `[ci.jobs.<point>.<name>]` | a CI job for a builtin point, under the contract's `[ci]` table: `entries`, `gates`, `installs`, `deploy`, `inputs`; a package-level extension's job exists while a package lists it | workspace, package | docs (phase 10), nanobind's wheels job (phase 11) |
| `[slots.<name>]`, `[contributions]` | a slot it declares (`compose`, `default`, `values`), and the values it puts into others' | workspace, check | docs (`docs.members`, `docs.theme`), pytest (dev group lines) |
| `[guidance.<section>]` | a section (`after`), or `[guidance.<section>.<topic>]` a rendered fragment (`render`); files ship under `content/fragments/` | every | the base's own sections; housekeeping's prose (phase 12) |
| `release-notes` | a reference to the provider | workspace | changelog (phase 10) |
| `[rules.<name>]` | an AST rule: `language`, `judge`, `fix` | workspace, package | python's reach rule (phase 11) |
| `[setup.<name>]` | a repository configuration step for `workflow.configure` | workspace | docs' pages hosting (phase 12) |
| `[categories]` | a category to its patterns, `source = ["src/**"]`, the shape a package's `[categories]` has | package | python, cpp (phase 11) |
| `[phases.<phase>]` | `pre`, `main`, `post` references; `provides` and `reads` context keys | package | phase 11 |
| `[queries]` | query name to reference | package | phase 11 |
| `[root-files."<path>"]` | a file written at the root while a package of it exists: `render` | package | cmake and conan (phase 11), from `KindRecord.root_files` |
| `[for.<target>]` | the same keys, read only while both are listed; dormant when no target is, and named so | every | pytest for docs, the coverage pages (phase 11) |
| `[replaces]`, `[deletes]` | `"<owner>:<name>"` to reason | every | the descendant chain's brand |

A `[for.<target>]` table carries the same keys as the top level. The
base registers what it owns from it (`checks`, `contract-keys`,
`rules`) under the contributor's name; the target reads the rest
through `contributions_for`, which returns the tables.

### A check in words alone

A check whose whole body is "run this tool with these words over these
paths, a non-zero exit is the verdict" needs no code. The engine already
selects the paths a run reaches, applies the threshold, splits the list
into the fewest calls under the command-line limit, runs the tool
through its toolroom handle, merges several calls' failures into one
refusal, prints the tool's output verbatim on red and chooses the fixer
under `--fix`. What is left is the tool's name and its words:

```toml
[checks.ruff.format]
scope = "workspace"
narrowing = "paths"
tools = ["ruff>=0.16"]
claims = [{ category = "source", suffixes = [".py"] }]
judge = ["ruff", "format", "--check", "--force-exclude"]
fix = ["ruff", "format", "--force-exclude"]
```

The engine appends the selected paths through the transport, or
nothing when the run is whole. Under `--fix` it runs `fix` first and
does not judge again; `safe-fix` is a third list where the tool
distinguishes the two. The vocabulary that keeps it words is small, and
closed:

- placeholders the engine answers: `{cache}`, the check's directory
  under `.workshop/.cache/<tool>/`; `{package}`, a package-scoped
  check's directory; and a query's name, `{compile-commands}`, for
  clang-tidy's `-p`;
- `env`, a table of variables the call gets;
- `matrix`, a placeholder to its values, one call per value in
  parallel, each its own verdict: mypy's three platforms as
  `matrix = { platform = ["linux", "darwin", "win32"] }` with
  `--platform={platform}` and `{cache}/{platform}` in the words.

Anything beyond that is code, declared as `run` instead of `judge`;
the two are exclusive and the judge refuses both. Of the ten checks
the eight tool extensions register, eight become words: `format.ruff`,
`lint.ruff`, `typecheck.basedpyright`, `typecheck.mypy`, `typecheck.ty`,
`typecheck.pyrefly`, `format.clang-format` and `lint.clang-tidy`.
`typecomplete.basedpyright` runs one call per public module and reads a
report, and pytest's two checks collect packages, arm coverage and
choose `-n`: those stay code. `fm explain <check>` prints the command a
check will run, and the conformance kit judges the words: an
unanswered placeholder, a `matrix` key absent from the words, a `fix`
on a role that cannot fix. What the words do not cover: a tool whose
verdict is not its exit code, or whose paths need reshaping before the
call; and a wrong word is found when the tool runs, not before.

### The schema

`Declared(contract, path, types, values)` is a schema already, in
Python, with a judge that refuses an unknown key, a wrong type or a
value outside its set and names the nearest spelling. The destination
makes the schema data (contract 33). An extension declares the keys it
owns in `[contract.<contract>.<table>]`; the base declares the schema of
`extension.toml` and of its own tables the same way. `fm sync`
composes the workspace's effective schema, the base plus the listed
extensions, into JSON Schema under `.workshop/schema/` (one file for
the root contract, one for a package's, one for `extension.toml`) and
associates them in the composed `.vscode/settings.json`, so the editor
completes and validates every contract as it is typed. The judge reads
the same composed file, with a validator of its own over the subset it
uses (types, enums, required, no unknown keys, a pattern for kebab-case
and for user-named keys), and keeps its teaching messages. The docs
extension renders the contract reference pages from it. JSON Schema is
the published form because Taplo and the editors read it and every
language a port could use validates it; the authoring form stays TOML.
The schema covers shape. The judge keeps the rules a schema cannot say:
`judge` and `run` are exclusive, a `matrix` key appears in the words, a
`for` target is an extension, a `requires` entry is listed before.

### What stays verifiable

A declaration in TOML loses the type checker's eye on its references,
and nearly all of it is won back, one layer at a time:

1. The judge at mount: a reference's module exists in the extension's
   package and its name is defined at the module's top level, read
   from the AST without importing.
2. The type checkers: `fm sync` writes a generated module per
   extension under `typings/`, one typed assignment per reference,
   `check: Callable[[GateContext], None] = format`, so the four
   checkers verify every signature against the role the reference
   fills, a query's answerer against its `Query[T]` included, with no
   code of ours comparing signatures.
3. The conformance kit: importing every reference under a recorder
   registers nothing.

Everything else the TOML names is judged without a run:

| What a table names | Verified that |
| --- | --- |
| `tools`, and the first word of `judge` | the tool is in the catalogue and the lock, its floor parses through `Spec`, and the first word names a tool the check declares |
| `claims` | each category is one some extension registers; each suffix starts with a dot |
| `extensions` on a check or generator | each names a package-level extension that is installed or follows the naming convention |
| `requires`, `compatible`, `for` | each target exists and may be listed at its level; a `requires` target is in the wheel's `Requires-Dist` and a `for` target is one of its extras (contract 15) |
| `after` | names a registered check, and following `after` never returns (an existing clause) |
| `contributions`, `options` | the slot exists and the value is in its set; the option is one the extension declares |
| `[contract.<contract>.<table>]` | kebab-case paths, known types, values of those types, a `doc` on every key, no two extensions declaring one key |
| `[ci.jobs.<point>.<name>]` | the point is builtin, the name unused, each entry's task one the plugin defines, read from its `@group.task` decorators by AST |
| `[fragments."<target>"]`, `[replaces]`, `[deletes]` | a file fragment exists under `content/`, a TOML fragment parses, a template renders with the probe data (an existing clause), and a replaced owner and name exist among the installed extensions |
| the words | every placeholder is answered, every `matrix` key is used, `fix` only on a role that fixes, `judge` and `run` exclusive |
| `[guidance.<section>]` | the section exists, `after` exists, the names parse, no duplicates |
| `[phases.<phase>]` | the phase is one of the nine; across the mounted set one provider per key, a provider for every reader, no cycle |
| `[queries]` | the name is one the base defines |
| `[categories]` | the patterns are globs, and specificity never ties within one extension (an existing clause) |
| `api-version`, `levels`, `plugin` | the version is the engine's, the levels are valid, the plugin is an entry point in the wheel's metadata |

Where each runs: the judge at mount for shape and existence; the gate,
through the layering check's one parse, for an extension that lives in
the workspace; the conformance kit, in the extension's own suite, for
the whole table; the type checkers for signatures. What no layer sees
before a run: a tool's words beyond their shape, a system package name
under `installs`, a job's runner label.

### The names

Grouped by the sort that needs them. "Users" names today's importer or
the phase that brings one. The rule that decides what stays a Python
name: a record an extension builds is a table in `extension.toml`; a
type the engine hands to an extension's function, and a function an
extension calls, stay public.

**Every extension.**

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `__version__` | the workshop's version | tests | `livery.workshop` |
| `Package`, `Edge` | a discovered package and a graph edge | the eight, docs | `livery.workshop` |
| `discover_packages`, `verify_workspace`, `workspace_root` | the workspace and its graph | docs, tests | `livery.workshop` |
| `read_contract` | one contract's declared keys, judged | docs (`load_contract` today) | `livery.workshop` |
| `extension_names` | the listed extensions, in order | tests | `livery.workshop` |
| `contributions_for` | the `[for.<target>]` tables grafted for a target, and the top-level tables of the extensions that require it | docs (phase 11) | `livery.workshop` |
| `generated_header` | the provenance header for a file an extension writes outside the engine | docs (private today) | `livery.workshop` |
| `ci_run`, `RunContext` | the CI run's context, or None at a desk | docs (`run_context` today) | `livery.workshop` |
| `check_option` | a package's value of an option the check declares | pytest | `livery.workshop` |
| `slot` | a slot's composed value | docs (`_slots` today), the base's `pyproject.toml` template | `livery.workshop` |
| `testing` | the conformance kit: `Subject`, `Clause`, `CLAUSES`, `Violation`, `judge`, `builtin_subject` | every extension's suite | `livery.workshop.testing`, declared in `livery.workshop` |

**Check extensions** (the model that works; eight of its ten checks
need none of these once they are words).

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `GateContext` | what a run hands a check's `run`: the paths, the packages, the arguments, the check's own record | the three checks that stay code | `livery.workshop` |
| `PACKAGE`, `PACKAGES`, `PATHS`, `WHOLE`, `NONE` | the scope and narrowing vocabulary | the same; docs needs `NONE` | `livery.workshop` |
| `scoped_paths`, `scoped_files`, `scoped_packages` | what a run reaches | the same | `livery.workshop` |
| `selected_files` | the changed files a workspace check with `inputs` judges this run | docs (`_checks.selected_files` today) | `livery.workshop` |
| `Changes` | what changed, handed to a check's `widen` reference | docs (`_influence` today) | `livery.workshop` |
| `run_batched` | the fewest tool calls under the command-line limit | a check in code that calls a tool | `livery.workshop` |

**Package extensions** (phase 11 brings every user).

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `PhaseContext` | what a phase's steps share: the package, the root, the failure | python, cpp, conan | `livery.workshop` |
| `Query`, `answer` | a typed question about a package, and its answer across the package's extensions | basedpyright (`public_modules`), clang-tidy (`compile_commands`), docs | `livery.workshop` |
| `PUBLIC_MODULES`, `COMPILE_COMMANDS`, `MODULE_ROOTS`, `CURRENT_VERSION`, `VERSION_FILES`, `REQUIREMENTS`, `DISTRIBUTIONS`, `BUILD_PLAN`, `EXECUTABLES`, `TOOLCHAINS`, `REFERENCES` | the queries the base defines, each a `Query[T]`; an extension answers them under `[queries]` | the same | `livery.workshop` |
| `Stamper` | what the `stamp` phase writes through | python, cpp, conan | `livery.workshop` |
| `ParsedModule`, `RuleContext` | what a rule's `judge` reference receives | housekeeping | `livery.workshop` |
| the toolchain types | phase 14 names them | cpp | `livery.workshop`, phase 14 |

**Workspace extensions.**

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `Prose`, `AGENT`, `HUMAN`, `guidance` | a guidance fragment, the audiences, and the composed set for one audience | docs (human pages), claude (agent file) | `livery.workshop` |
| `ReleaseNotes`, `release_notes` | the provider protocol and the mounted provider | changelog, docs (the release view) | `livery.workshop` |
| `ci_changes` | the paths changed since the base a CI run measures from, as `Changes` | docs (the docs job's skip; `GitOps` and `ci_affected_base` today) | `livery.workshop` |
| `forge_repository` | this workspace's repository name on its forge | docs (publish by container or ssh) | `livery.workshop` |
| `registry`, `RegistryTarget` | the resolved registry target of a kind | docs (the container registry) | `livery.workshop` |

**The docs extension's own public API**, `livery.extensions.docs`,
for generator extensions and for generators in any package:

| Name | Purpose | Users |
| --- | --- | --- |
| `Page` | one generated page: path, title, nav position; what a generator's `run` returns | mkdocstrings (phase 11), doxygen (phase 12), the task reference and coverage pages |
| `write_nav_block`, `nav_block_markers` | emit a nav block beside generated pages, and place it | toolroom-bench, the task reference |
| `GENERATED` | the generated tree's name under a package's `docs/` | generators |
| `PUBLIC_MEMBERS`, `ALL_MEMBERS` | the members policy's values | mkdocstrings |

### What leaves the public surface, and the break each causes

| Name | Goes to | Who breaks | Phase |
| --- | --- | --- | --- |
| `rewrite_nav_block` | `livery.extensions.docs.write_nav_block` | toolroom-bench (ours); any third-party generator | 10 |
| `public_modules`, `compile_commands` | `answer(package, PUBLIC_MODULES)`, `answer(package, COMPILE_COMMANDS)` | basedpyright, clang-tidy (ours) | 11 |
| `run_suites`, `kind_examples`, `workspace_suite` | `livery.extensions.python` | pytest (ours), which then requires `python` | 11 |
| `CheckRecord.kinds` | `CheckRecord.extensions` | the eight (ours) | 11 |
| `mount_extensions` | private; the mount is the plugin's | this repository's tests | 10 |
| `CheckRecord`, `Claim`, `Fragment`, `Option` as constructed records | `[checks.<tool>.<role>]`, `claims`, `[fragments."<target>"]` and `options` tables in `extension.toml` | the eight (ours) | 10 |
| `API_VERSION = 1` on a module | `api-version = 1` in `extension.toml`; the number does not move | nothing: every extension of ours lands in the wave | 10 |

Each is a break before 1.0: a minor bump of the workshop, every
extension of ours re-released in the same wave (the ruling of
2026-10-04 on releasing as required), each changelog stating it.

### Extraction, registered like a check

The brief's first thought: expose API documentation extraction the way
checks are exposed, each extractor naming what it operates on, and
factor out what the two share. The answer, in `[generators.<name>]`:

- A generator is a `[generators.<name>]` table with `extensions` (the
  package-level extensions a package lists for the generator to apply;
  a check's `kinds` becomes the same key in phase 11), `claims`
  (categories and suffixes, the same `Claim` a check carries), `tools`,
  `options` and a `run`. The
  docs build hands it a package and the output directory under the
  package's generated tree, and it returns the pages it wrote. The
  site's configuration is the fragment engine's: `zensical.toml` is a
  composed file the docs extension owns, rendered by a dynamic fragment
  of its own, and a generator's extension contributes its tables (the
  mkdocstrings handler block, its options and inventories) as a TOML
  fragment in extension order. No generator key carries configuration.
- A generator lives with the extension that owns its tool, as a check
  does: `lint.ruff` lives in ruff and names python, `build.configure`
  lives in the cpp extension. Doxygen reads C, C++, Java and more, so
  its generator is `livery-extensions-doxygen`, a workspace extension
  requiring `docs`, declaring `extensions=("cpp",)` today and `java`
  when a java extension exists. The Python reference is
  `livery-extensions-mkdocstrings`, a workspace extension requiring
  `docs`, declaring `extensions=("python",)`, its generator writing the
  handler stubs, its fragment the handler tables of `zensical.toml`,
  its checks `lint.docrefs` and `lint.docstrings`, its dependency
  `griffelib`, its generator's `paths` option set at
  `[generators.mkdocstrings]`. A
  workspace that lists `docs` without it renders a site with no API
  reference and no handler block. Every generator is an extension of
  its own, and no language extension contributes to docs.
  `contributions_for("docs")` returns every table declared for docs:
  the top level of an extension that requires `docs`, and a
  `[for.docs]` table such as pytest's coverage pages.
- Matching is by `extensions` against the package's listed extensions,
  then by the claims within the package: `python+nanobind+cmake` meets
  mkdocstrings' generator over its `.py` sources and doxygen's over its `.h`
  and `.cpp` sources, each listed in its own section. That is the
  brief's "file type it can operate on", through the claims model
  checks already use. Two mounted generators naming one extension refuse
  at mount, naming both, unless the package's contract picks one.
- What checks and generators share is already public: `Claim`, the
  scoping of files to a package, `Option`, the tools-in-use rule,
  `run_batched`. There is no shared base class: a check returns a
  verdict and a generator returns pages, and a class holding only
  `claims`, `tools` and `options` would be a third thing to name.
- One concept replaces three: the kind's `extractor`, the kind's
  `coverage_pages`, and the contract's `[docs] generators` verbs. A
  package's declared generator verb is a generator whose `run` calls
  the verb; the task reference is the docs extension's own generator
  over every package that advertises `footman.tasks`.
- Zensical runs only its native plugins, and its mkdocstrings support
  covers the Python handler alone. So mkdocstrings' generator is the
  one that writes handler stubs; every other writes Markdown
  (Doxygen XML turned into pages for C++, `rustdoc` JSON or a Markdown
  renderer for Rust, `gomarkdoc` for Go) or places a rendered tree and
  links it without cross-references (Javadoc, whose `element-list` is
  no `objects.inv`). The table carries both shapes; the plan's open
  item 11 closes on it.

### One docs mechanism across footman and workshop

The brief's fourth thought. What exists twice today:

| Job | footman | workshop docs extension | Destination |
| --- | --- | --- | --- |
| a task tree as pages | `livery.footman.markdown.render_site`, public; `fm docs site` renders the invoking project's tree | `_taskref` renders one provider in isolation by spawning `fm --tasks-file=<probe> --json --list`, then `render_site` | one renderer: `livery.footman.docs.site` takes `provider=` and renders that plugin in isolation in-process; the task reference generator calls it and assembles the nav. A change to footman, allowed by the brief |
| footman's API reference | `fm footman.pages` writes a curated page from `_API_SECTIONS`, validated against `__all__`; footman's contract sets `[docs] api = false` | the mkdocstrings extension's generator writes one page per module | the curation becomes authored pages footman owns, each name a mkdocstrings directive in the order and with the prose of today's table; the mkdocstrings generator's `curated` option writes no per-module pages and checks every export is placed and no directive names a vanished name; `_API_SECTIONS`, `_API_OMITTED`, `_api_markdown` and the page writer go (Willem's ruling of 2026-10-07) |
| errors-and-notes page, the config, notes and globals tables, the example render, the latest-release admonition | `fm footman.pages` | | stays footman's generator verb, declared as today; a generator whose `run` calls it |

Two mechanisms for the API reference are the one real duplication;
deleting footman's is less code and one way.

## The reaches

Every private reach, mapped. "Public name" means a name the owning
root's entry module exports; "moves" means the code moves into the
reaching package; "exception" means an allowance entry with its reason.

### #1204's list

| Reach | Treatment | Name |
| --- | --- | --- |
| toolroom's hosted lane into footman's `_globals` and `_context`, and `run(_show=)` | public seam, one object: what the lane asks of its host | `livery.footman.host()`: `None` outside a run; else `active`, `real_cwd()`, `target_cwd(cwd, relative)`, `argv_override`, `invocation`, and `run`'s display switch as a keyword the api documents |
| toolroom-bench into footman's `_describe` | public: `colored` exists; `bold` and `cyan` are styles of it | `colored(text, style=...)`; `wants_color()` public |
| toolroom-bench into footman's `_globals.active` | the same `host()` seam | |
| toolroom-bench into toolroom's `_colordata` | public read of the probed colour table | `livery.toolroom.tools.colour_controls()` |
| toolroom-bench into toolroom-store's `_engine.download` | footman's public `fetch` does the job; the store's own download stays private | `livery.footman.fetch` |
| toolroom-bench into workshop's `_navblocks` | the docs extension's public name | `livery.extensions.docs.write_nav_block` |
| workshop into footman's `_paths` and `_config` | public, named for the question each asks | `livery.footman.builtins()` (the App's mounted builtin set), `project_builtins(root)`, `directory_variable("DATA_DIR")`, `tasks_file_name()` |
| workshop's `_e2e` into forge's `_registry` | the forge's admin protocol, phase 15, makes deleting a published version public; `_e2e` leaves the base in the same phase | the admin protocol in `livery.forge` |

No exception. The hosted lane, #1204's likeliest candidate, is one
object with five members, cheaper than an allowance entry that every
later reader must re-justify.

### The docs extension's twenty modules

| Module | Names used | Treatment |
| --- | --- | --- |
| `_checks` | `GateContext`, `PACKAGES` | public already |
| | `CheckRecord` | a `[checks.<tool>.<role>]` table |
| | `NONE`, `selected_files` | public |
| | `check_for`, `register_check` | go: `[checks.<tool>.<role>]` is data; a check in code reads its own table from `GateContext` |
| `_contract` | `load_contract` | public `read_contract` |
| `_contract_keys` | `Declared` | a `[contract.<contract>.<table>]` table |
| `_docs_contract` | everything | moves into the docs extension; `site_reads` becomes `inputs` on the job; `module_docs`, `declines_api`, `module_root` go to the python extension |
| `_extensions` | `workspace_root` | public already |
| `_forge_lane` | `remote_repo_name` | public `forge_repository` |
| `_git_ops` | `GitOps`, `GitError` | replaced by `ci_changes` |
| `_influence` | `Changes` | public |
| | `Inputs` | the `inputs` table of a check |
| `_kinds` | `Extractor`, the members policy, `kind_extractor`, `kind_coverage_pages` | move into the docs extension as `[generators.<name>]` and its policy |
| | `kind_chain`, `kind_names` | go with the python checks moving to python's contribution |
| `_navblocks` | all | moves into the docs extension |
| `_packages` | `Package`, `discover_packages` | public already |
| | `member_depth`, `package_directories` | `Package.member` and `Package.directory` answer both |
| `_points` | `Job`, `Entry`, `contribute_job` | `Job`, `Entry`, `JobContribution` public; `[ci.jobs.<point>.<name>]` data |
| `_prose` | `Prose`, `HUMAN`, `sections`, `fragments`, `shipped`, `repository_fragments` | `Section`, `Prose`, `HUMAN`, `guidance` public |
| `_provenance` | `format_header`, `generated_header` | `generated_header` public |
| `_quality` | `ci_affected_base` | folded into `ci_changes` |
| `_registries` | `resolve_registry` | public `registry` |
| `_release_notes` | `release_notes` | public |
| `_site_files` | `register_site_file` | goes: a whole-file fragment |
| `_slots` | `register_slot`, `NEAREST`, `composed` | `[slots.<name>]` data with `compose = "nearest"`; `slot` public |
| `_state` | `run_context` | public `ci_run` |

## Stability and proof

**What a public name promises.** Its docstring is published; the
typecomplete check verifies it; `tests/test_namespaces.py` pins the
root's `__all__` and that every public module under a root is declared
there; the conformance kit judges every table an extension declares
against the schema. Before 1.0 a name may change in a minor release; the changelog
states the break and the name that replaces it, and every extension of
ours moves in the same wave. After 1.0 a change is a major bump.

**How an extension declares its target.** `api-version` in its
`extension.toml`. The base supports one version
(contract 31). An extension's wheel also depends on
`livery-workshop>=<floor>` as ordinary metadata (contract 15); the
version check is the one that produces a sentence, since a wheel
installed against the wrong workshop is a lock problem the mount meets
first.

**How the conformance kit proves an extension built on the public
surface alone.** Two clauses join `CLAUSES`:

- `public-surface`: the subject's sources import no underscore module
  or name of another distribution. The clause runs the reach scan over
  the extension's own `src`, so a third-party extension proves it in
  its own suite, where the housekeeping extension is not listed.
- `declaration-validates`: the subject's `extension.toml` validates
  against the schema, every `module:function` it references imports,
  and importing them registers nothing (the kit imports under a
  recorder, as the walk clause does for checks).
- `words-are-answerable`: every placeholder in a check's words is one
  the engine answers, every `matrix` key appears in the words, and no
  check declares both `judge` and `run`.

And one drive: `fm ci.e2e --extension=<name>` births a project listing
the base and the extension from this checkout's wheels and runs its
gate. An extension that reaches a private name the wheel does not ship
fails there.

**Where #1204's check runs, and the ratchet.** The reach check is a
`[rules.<name>]` entry of the python extension, on by default wherever
python is listed (Willem's ruling of 2026-10-07), read by
`layering.imports` over python's `REFERENCES` reader in the one parse
(contract 16). It judges every package's sources, never its tests, per
changed file and whole when the graph changed, and refuses each reach
by file and line. The rule is an option of that check,
`private-reaches`, default true, declared by the python extension and
set at `[checks.layering.imports]`, so a workspace that shares privates
across its own distributions on purpose turns it off in one line
instead of growing an allowance. Its allowance is a
contract key the python extension declares at the root,
`[python] private-reaches`, a list of `{ from, into, name, reason }`
tables: facts of the workspace in the contract, the rule in code. The
ratchet: the check also refuses an allowance entry that no source uses,
so the list can only shrink, the way the vocabulary allowance does.
Until phase 11 lands the extension, this repository carries the same
scan as a root test over the allowance written as data in the test,
seeded from the table above in phase 10.

## Phases

Each phase lands gate-green and mergeable alone; a lettered slice is
its own change. Exceptional paths are tested before happy paths. The
extensions plan's phases 10 to 15 are replaced by phases 10 to 16
here; the mapping is in the decision record.

### Phase 10: the API as a contract, and its first consumers

**10a, the entry module.** Every root's `api.py` becomes `__init__.py`
in one wave, footman included (ruling 5);
what need not load is imported under `TYPE_CHECKING` and served by
`__getattr__`; `tests/test_namespaces.py` pins the new shape;
`typecomplete` verifies `__init__`; 10b's pins are written against the
new path once. Acceptance: `fm check` exits 0;
`fm workflow.release --local`; the startup measure
(`fm commit --help`, 255 ms median today) does not rise.

**10b, the declaration file and the schema.** Deliverables:

- `extension.toml` with every key of "The declaration file"; the mount
  reads it through the contract reader and the attribute readers go;
  every `register_*` the base exports goes; a reference that does not
  import refuses at mount; the docs extension, still in the wheel,
  declares instead of registering.
- The schema: the base's `[contract.<contract>.<table>]` for `extension.toml` and
  its own tables; `fm sync` composes `.workshop/schema/workshop.json`,
  `package.json` and `extension.json` and associates them in the
  composed `.vscode/settings.json`; the judge reads the composed file;
  `fm explain <contract>` names the schema it was judged by.
- The verification of "What stays verifiable": the judge's AST read of
  every reference; the generated `typings/` module per extension that
  the four type checkers verify; the kit's clauses over every table.
- The public names of "The names" that need no package extension:
  `NONE`, `selected_files`, `Changes`, `read_contract`,
  `generated_header`, `ci_run`, `RunContext`, `ci_changes`, `slot`,
  `Prose`, `AGENT`, `HUMAN`, `guidance`, `ReleaseNotes`,
  `release_notes`, `forge_repository`, `registry`, `RegistryTarget`,
  `contributions_for`; the records that become tables leave.
- `installs`, `deploy` and `inputs` on a job; `_ci_generate`,
  `_workflow_tasks` and `_provenance` stop reading the `[docs]` table;
  the `project.urls` slot.
- `rewrite_nav_block` leaves for `livery.extensions.docs`;
  `mount_extensions` leaves the public surface; `api-version` stays 1; the
  eight tool extensions and this repository move; the wave releases
  them.
- The pin tests first: `test_api_exports_what_the_package_exported`
  lists the new set; the reach scan as
  `tests/test_private_reaches.py` with today's table as its allowance.

Acceptance, refusals first:

- `test_an_unknown_key_in_extension_toml_refuses_naming_the_file_and_the_nearest`,
  `test_a_reference_that_does_not_import_refuses_at_mount`,
  `test_a_reference_with_the_wrong_signature_fails_typecheck` (a
  generated module with a wrong assignment, judged by
  `fm typecheck`),
  `test_an_extension_declared_for_another_api_version_refuses_naming_both`,
  `test_a_declaration_that_registers_at_import_fails_the_kit`,
  `test_a_new_private_reach_refuses_naming_the_file_and_line`.
- `fm check` exits 0; `fm sync` writes the three schema files and the
  editor association, proven by `git diff --exit-code` on
  `.vscode/settings.json` after a second sync.
- `fm ci.e2e --extension=ruff --fresh` is green on the re-released
  wheels.

**10c, checks in words.** Deliverables: `judge`, `fix`, `safe-fix`,
`env`, `matrix` and the placeholders of "A check in words alone"; `run`
exclusive with `judge`; the eight checks named there become words and
their Python bodies go; `fm explain <check>` prints the command; the
kit's `words-are-answerable` clause. Acceptance, refusals first:
`test_a_check_with_both_judge_and_run_refuses_naming_the_check`,
`test_an_unanswered_placeholder_fails_the_kit`,
`test_a_matrix_key_absent_from_the_words_fails_the_kit`; the six
extensions whose checks are all words ship no Python beyond their
packaging (`find packages/extensions/{ruff,mypy,ty,pyrefly,clang-format,clang-tidy}/src -name '*.py'`
finds nothing); `fm check` exits 0 with the same gate members, proven
by the gate's pinning tests; `fm ci.e2e --extension=mypy --fresh` is
green, the matrix's proof.

**10g, toolroom's table, lock and verbs.** `[tools]` becomes
`[toolroom]` in the root and package contracts and in
`extension.toml`, since every key in it is toolroom's and a table is
named after its owner; `tools.lock` becomes `toolroom.lock`; the
workshop's `tools.add`, `tools.lock`, `tools.restub` and `tools.sync`
and the bench's `tools.*` become `fm toolroom.*`. The store ships the
`[toolroom]` schema fragment, and a standalone toolroom reads the same
table from `workshop.toml` where there is one and from its own
`toolroom.toml` otherwise. No migration code: `[tools]` refuses as an
unknown table naming `[toolroom]`, and a lock at the old name is
named by `fm sync` with the verb that writes the new one. This slice
depends on nothing else in phase 10 and may land first. Acceptance,
refusals first: `test_the_old_tools_table_refuses_naming_toolroom`,
`test_an_old_lock_name_is_named_with_the_verb_that_writes_the_new_one`;
`fm check` exits 0; `fm ci.e2e --fresh` is green with its pinned
lines renamed; `fm workflow.release --local` releases the store, the
bench and the workshop.

**10d, footman's and toolroom's seams.** Deliverables: `host()`,
`colored` styles, `wants_color`, `builtins`, `project_builtins`,
`directory_variable`, `tasks_file_name` in footman's entry module;
`colour_controls` in toolroom's; the bench on footman's `fetch`; the
workshop's seven modules on the new names. Acceptance: the reach test's
allowance holds the forge row alone; `fm check` exits 0;
`fm workflow.release --local` releases footman, toolroom and the
workshop.

**10e, changelog ships apart.** `livery-extensions-changelog`: the
git-cliff provider as `release-notes`, `cliff.toml` its fragment for
every package, `git_cliff` its tool, `CHANGELOG.md` created on the
first record; `_cliff` leaves the base. Acceptance:
`test_a_release_without_the_changelog_extension_writes_no_notes`;
`fm workflow.release --local` on this repository writes the same
entries as before.

**10f, claude ships apart.** `livery-extensions-claude`: `CLAUDE.md`
and `.workshop/fragments/` as dynamic fragments over
`guidance(root, AGENT)`, skills, hooks and `settings.json` as its
content, `hooks.pre-bash` its verb; `_shipped_files._agent_outputs`
and `_hooks` leave the base. Acceptance: a project born without
`claude` has no `.claude/` and no `CLAUDE.md`
(`test_a_project_without_claude_writes_no_agent_file`); this
repository's `.claude/` and `CLAUDE.md` are byte-identical before and
after, by `git diff --exit-code` after `fm sync`.

### Phase 11: package composition and the package extensions

**11a, the model** (the plan's 11a, unchanged): `compatible`,
`before`, `after`, the validity rule, the canonical set,
`fm extensions --combinations`, `[phases]` with `pre` and reversed
`post`, the declared context, `[queries]` and `answer`, `fm run`.

**11b, the package extensions** (the plan's 11b, with two changes):
`python`, `cpp`, `cmake`, `conan`, `nanobind`, `unreal`; the backends
move; `KindRecord`, `register_kind` and the kind registry go; a check's
`kinds` becomes `extensions`, the package-level extensions a package
lists for the check to apply, and the eight tool extensions move with
it; nanobind declares the wheels job under `[ci.jobs.<point>.<name>]` and `_ci_generate` stops
reading the member roster;
`[categories]` and `[root-files."<path>"]` as data; `run_suites`, `kind_examples`
and `workspace_suite` become `livery.extensions.python`, and the
pytest extension requires `python`; `public_modules` and
`compile_commands` become queries. The second change: the plan's sentence
that the conan extension owns the conan cache is replaced. Since #1112
a rendered `conanws.yml` resolves siblings from their sources and the
machine's package cache is shared; what 11b still owes is that a
container build never reuses a binary built against the host's glibc.
The python extension carries the reach rule as a `[rules.<name>]`
entry, on by default, with `[python] private-reaches` as its allowance;
this repository's root test from 10b retires, and
`test_an_unused_allowance_entry_refuses_naming_it` and
`test_a_new_private_reach_refuses_naming_the_file_and_line` move into
the extension's suite.

**11c, the generators.** `[generators.<name>]` and the members policy in
`livery.extensions.docs` (the extension is still in the wheel);
`zensical.toml` becomes a composed file of the fragment engine, the
docs extension's dynamic fragment with contributed tables; `Extractor`
and `KindRecord.coverage_pages` go. `livery-extensions-mkdocstrings`,
its own distribution requiring `docs`: the python generator, the
handler tables as its fragment, `lint.docrefs` and `lint.docstrings`,
`griffelib`, the generator's `paths` option at `[generators.mkdocstrings]`
(`[docs] api` stays docs'); the
workshop wheel drops `griffelib`. pytest's `[for.docs]` table
declares the coverage pages generator. The docs extension reads
both through `contributions_for("docs")` and names no language. The
`REFERENCES` query replaces the base's python parse in the layering
check, python's extension answering it. This repository and
`fm new.project`'s stock list add `mkdocstrings` after `docs`.

Acceptance, refusals first:

- the plan's 11 refusals: `test_an_unconnected_pair_refuses_naming_both`,
  `test_two_providers_of_one_key_refuse`,
  `test_a_post_runs_after_a_failed_main_and_sees_the_failure`,
  `test_two_extensions_naming_one_executable_refuse`;
- `test_a_generator_for_an_unlisted_extension_is_never_asked`,
  `test_a_workspace_without_mkdocstrings_renders_no_reference_and_no_handler_block`;
- `fm run` of a C++ application rebuilds only what changed (a counting
  seam);
- `fm docs.build` renders the same site before and after 11c: the two
  `site/` trees compared by `diff -r`, provenance headers excluded;
- the vocabulary test's allowance names only the runtime (the plan's
  11d, which 11b and 11c complete together).

### Phase 12: docs and housekeeping ship apart

**12a, docs.** `livery-extensions-docs`: everything under
`livery.extensions.docs` leaves the workshop wheel;
the override template becomes a whole-file fragment and `_site_files`
goes; the docs build's state and zensical's cache move under
`.workshop/.cache/docs/` and `.workshop/.cache/zensical/` (#1187's
docs rows); the doctor line naming the extension that brings the docs
job. Acceptance:
`test_a_workspace_without_the_docs_extension_installs_none`; a fresh
clone after `fm sync`, `fm check` and `fm docs.build` has no
`.docs-build/` and no `.cache/` at its root and `git status` is clean;
`fm ci.e2e --extension=docs --fresh` is green.

**12b, housekeeping.** `livery-extensions-housekeeping`: requires
`mypy`, `ty` and `pyrefly`; the voice and documentation prose as
`[guidance.<section>]`; the layout rules: a namespace
`__init__.py`, a root with public names and no `__init__.py`, a
distribution not named after its import path. `api` stays an ordinary
module name. Acceptance:
`test_a_root_without_its_entry_module_refuses_naming_it` in the
extension's suite; a project born without `housekeeping` lists no
mypy, ty or pyrefly, in the conformance kit; `fm check` on this
repository runs the rule and exits 0.

**12c, the C++ reference.** The pending spike first: Doxygen XML to
Markdown before the Zensical build, measured on a native member of this
repository. Then `livery-extensions-doxygen`: a `[generators.<name>]` entry with
`extensions=("cpp",)`, claiming the native sources, `doxygen` its tool,
pages under the package's generated tree. Acceptance: the spike's note quotes the pages rendered and the
build time; `fm docs.build` on this repository renders the cpp member's
reference. If the spike finds no workable route, 12c becomes a
follow-up issue and the plan's open item 11 records why.

**12d, footman's docs onto one mechanism.** `livery.footman.docs.site`
takes `provider=`; the task reference generator calls it. footman's
Python reference becomes authored pages with mkdocstrings directives
in today's order and prose, under the mkdocstrings generator's
`curated` option, which validates them; `_API_SECTIONS`,
`_api_markdown` and the page writer go, and the rest of
`fm footman.pages` stays a declared generator. Acceptance, refusals
first: `test_a_curated_package_with_an_unplaced_export_refuses_naming_it`,
`test_a_directive_naming_a_vanished_name_refuses`; `fm docs.build`
renders footman's reference with every exported name present;
`grep -rn "_API_SECTIONS" packages/footman/src` finds nothing.

### Phase 13: the release train through phases

The plan's 11c and 11e, unchanged: `stamp`, `build`, `prove`,
`publish`, `replay`; `[publish]` and several artifacts; ecosystems as
registrations with their defaults, which retires `_registries`'
tables; the forge's `package_admin`; the graph from the `REQUIREMENTS`
query. Acceptance: the plan's.

### Phase 14: toolchains

The plan's phase 12, unchanged. The toolchain records join the api
here.

### Phase 15: lodges and e2e

The plan's phases 13 and 14, unchanged, with one addition: 13a's admin
protocol carries deleting a published version, so `_e2e` reaches no
private forge name, and 14 moves `_e2e` out of the base; the reach
allowance is then empty.

### Phase 16: one affected engine, and sync at scale

The plan's phase 15, unchanged. Follow-ups beside it, not inside it:
#1201 (concurrent tool supply in `fm sync`, toolroom-store's work,
measured against the same scale fixture) and #1200 (the locking
review; its recommendation decides whether a foundation library joins
the stack, which this design neither needs nor rules out).

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `register_check`, `contribute_job`, `register_slot`, `register_site_file`, `register_release_notes`, `register_fragment`, `register_section`, `register_ast_rule`, `register_categories`, `register_kind`, and the `[checks.<tool>.<role>]`, `[options]`, `[for]`, `tools` attributes | `extension.toml`, read at mount (phase 10, 11) |
| `Declared` records in Python | `[contract.<contract>.<table>]` and the composed schema under `.workshop/schema/` (phase 10b) |
| the Python bodies of eight tool checks | their words in `extension.toml` (phase 10c) |
| `_docs_contract` in the base | the docs extension's keys and `Job.installs`, `Job.deploy`, `Job.inputs` (phase 10, 12) |
| `KindRecord.extractor`, `coverage_pages`, `[docs] generators` as three mechanisms | `Generator` (phase 11c) |
| `_site_files` | a whole-file fragment (phase 12a) |
| `tests/test_private_reaches.py` at this repository's root | the python extension's rule and `[python] private-reaches` (phase 11b) |
| the layering check's python parse in the base | the `REFERENCES` query (phase 11c) |
| `fm footman.pages`' curated API page | the mkdocstrings extension's generator (phase 12d) |
| the assembled `zensical.toml` the docs build writes | a composed file of the fragment engine, generators contributing their tables (phase 11c) |
| `_taskref`'s spawned `fm --tasks-file` | `livery.footman.docs.site(provider=...)` (phase 12d) |
| the reach allowance's forge row | the admin protocol (phase 15) |

## Decision record

- 2026-10-06: this note written, against `origin/main` at `4f2a5e53`.
  Every claim in "What this note read" was checked in the code that
  day. The phase mapping from the extensions plan: its 10 becomes 10d,
  10e, 12a and 12b here; its 11a and 11b become 11a and 11b, its 11c
  and 11e become 13, its 11d dissolves into 11b and 11c; its 12
  becomes 14; its 13 and 14 become 15; its 15 becomes 16. Phases 10a
  to 10d, 10g, 11c, 12c and 12d are new.
- Willem, 2026-10-06, the rulings this note satisfies, quoted above.
- Willem, 2026-10-06, after the first draft: the `.api` drop is for
  every package in livery, not footman alone. The draft's open ruling
  on it is closed and phase 10's entry-module move is its first slice,
  so the public names are pinned once, at their final path.
- Willem, 2026-10-06: `api` is not forbidden as a module name; the
  housekeeping extension carries no rule against it.
- Willem, 2026-10-06: a generator declares the languages it extracts
  for. So `Generator` carries `languages`, `GENERATORS` joins the
  declaration vocabulary, and the doxygen generator is an extension of
  its own rather than the cpp extension's contribution.
- Willem, 2026-10-06: the mkdocstrings configuration is an extension
  of its own, so a workspace can have no API reference at all; it
  contributes the handler tables to `zensical.toml`. So every generator
  is an extension of its own, `zensical.toml` is a composed file of the
  fragment engine, and no language extension contributes to docs.
- Willem, 2026-10-06: a package-level extension may contribute CI
  jobs; nanobind and unreal are not languages. So the sort is
  `package`, with a language, a build system, an ecosystem and a
  platform as its flavours; `JOBS` is read at both levels and a
  package-level job exists while a package lists its extension; the
  wheels job leaves the base for nanobind; and the field that matches
  a record to a package is `extensions` on checks and generators
  alike, replacing `kinds` and the draft's `languages`.
- 2026-10-06, on Willem's question whether an extension with `FOR` and
  no listed target is silent or an error: `REQUIRES` is the error and
  `FOR` the silence, with a dormant entry named by `fm extensions` and
  `fm doctor`; no third declaration.
- Willem, 2026-10-07: the extension model fits a TOML configuration
  model, so a port of the engine stays possible. So an extension's
  declarations are one `extension.toml`, read by the contract reader
  and judged like `workshop.toml`, with behaviour referenced by name;
  a record an extension built becomes a table and leaves the public
  names.
- Willem, 2026-10-07: a check whose verdict is its tool's exit code is
  words in `extension.toml`, a step against technical debt; eight of
  the ten tool checks become words, with a closed placeholder
  vocabulary and a `matrix` for mypy's platforms.
- Willem, 2026-10-07: TOML schemas and their verification make sense.
  An extension declares its keys in `[[contract-keys]]`, `fm sync`
  composes the workspace's schema into JSON Schema for the judge, the
  editor and the reference pages, and the judge keeps the rules a
  schema cannot say.
- Willem, 2026-10-07: a reference's target is verified by AST, and
  whatever else can be verified despite TOML is. So the judge reads
  every reference's existence from the AST, a generated typed module
  per extension lets the four type checkers verify every signature,
  and the kit judges every table against what it names; the list is
  in "What stays verifiable".
- Willem, 2026-10-07: `workshop.toml` and `extension.toml` keep their
  names and conventions as consistent as possible. So `[extension]` is
  the identity table, `[toolroom] requires` the tools, an address is a
  table path in both files (`[checks.ruff.format]` declares what
  `[checks.ruff.format]` configures), `[ci.jobs...]` sits under `[ci]`,
  and a schema fragment has the shape of the file it describes.
- Willem, 2026-10-07: `[tools]` becomes `[toolroom]`, so the table is
  named after its owner and a standalone toolroom can share it; every
  `fm tools.*` verb is toolroom's, so the lock and the verbs rename
  with it: `toolroom.lock`, `fm toolroom.*`. Phase 10g.
- Willem, 2026-10-07, on the open items: the docs extension owns the
  generator registry; one package sort with four flavours and no key;
  package-level extensions mount first, then the workspace list
  (contract 28 stands); the hosted lane is a `host()` seam in footman;
  Doxygen XML to Markdown is the C++ route; docs ships apart after the
  package extensions. All closed as recommended.
- 2026-10-07: Willem asked whether the reach check is a generally
  useful check that should come with python support and default to
  on, and asked for the agent's opinion. The opinion, written into the
  design as its position and open until ruled (open item 1): yes, the
  python extension's rule, on wherever python is listed, an option of
  `layering.imports` to turn off, `[python] private-reaches` as its
  allowance (phase 11b); the housekeeping extension keeps the layout
  rules alone.
- Willem, 2026-10-07: the reach check lives in the python package
  extension, with an option to turn it off. Ruled as recommended;
  open item closed.
- Willem, 2026-10-07: footman's Python reference stays curated, option
  (c): authored pages with mkdocstrings directives in today's order and
  prose, validated by the mkdocstrings generator's `curated` option;
  footman's table and page writer go (phase 12d). Open item closed.
- Willem, 2026-10-07: a generator's own settings split by meaning,
  option (c): `[docs] api` stays the docs extension's key, and a
  generator's settings are its options set at `[generators.<name>]`,
  as a check's are at `[checks.<tool>.<role>]`. No "keys under
  another's table" mechanism.
- Willem, 2026-10-07: every API change lands together and
  `api-version` stays 1 until the workshop keeps backwards
  compatibility; livery is the only repository that uses it, so no
  migration is written. Contract 31 restated.
- 2026-10-06, on how that meets `COMPATIBLE`: composition is its own
  axis, and a `REQUIRES` or `FOR` target counts as compatible for a
  package-level pair, so `COMPATIBLE` is declared only where neither
  side requires or contributes to the other.
- Willem, 2026-10-06, the brief's thoughts, taken as rulings where
  they state one: less code, simpler code, one way, one concern per
  module rank the options; an extension's dependencies follow its use,
  not its mount; footman's docs plugin may change to serve one
  mechanism.
- 2026-10-06: the plan's phase 11b sentence on the conan cache is
  stale since #1112 (see 11b above). The plan's vocabulary line "a
  root's `__init__.py` content moves to the root's `api` module" and
  its 2026-10-02 phase 3 decision are superseded by ruling 5.
- 2026-10-06: the brief's "No language extension exists yet", "the
  docs extension imports 20 private workshop modules" and "the eight
  tool extensions use 20 names" all hold on `4f2a5e53`.

## Open

Each with its options, what each option does not cover, and a
recommendation. Owner: Willem, unless named.

1. **#1187's remaining rows** (`records/` and `tools.graphs/`): the
   bench's and the store's, outside this design. Owner: the issue.
2. **#1200's foundation library**: whether the advisory lock, the pid
   probe and the atomic write join one distribution below footman.
   This design adds no lock; phase 16's shared temp roots and two
   gates in one checkout are the cases it would serve. Owner: Willem,
   after the review.
