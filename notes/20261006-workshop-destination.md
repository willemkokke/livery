# The workshop's destination: an engine, and the API its extensions use

Status: ruled by Willem on 2026-10-07; every design question is closed
but open item 3, and the extensions plan takes phases 10 to 16 from here. Written
2026-10-06 against
`origin/main` at `4f2a5e53`. 10a is built (issue #1218). 10b, 10g
(issue #1301), 10c (issue #1303) and 10d (issue #1306) shipped on
2026-10-08 in the wave of PR #1317, after a dispatch published the
four tool extensions the 2026-10-07 wave could not create;
clang-tidy keeps its check code, and the extension passes of 10b and
10c stop at the coverage leg until 12e (#1225). 10e (issue #1307) and
10f (issue #1313) merged after that wave, as PRs #1312 and #1315, and
shipped on 2026-10-09 in release PR #1340: `livery-workshop` 0.8.0 and
the first releases of `livery-extensions-changelog` and
`livery-extensions-claude`, 0.0.0. The same day every PyPI name the
remaining phases release was claimed with a placeholder, which leaves
debt to clear (see "First publishes").
Phase 11 started on 2026-10-09, and 11a is built: package composition
(issue #1330), the lifecycle phases (issue #1332), the queries (issue
#1333) and `fm run` (issue #1335). 11a's C++ acceptance waits for 11b's
cpp extension. The
extensions plan (`notes/20261002-extensions-plan.md`) stays the one plan; this note
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
| workspace (product) | workspace | checks, CI jobs, slots, guidance, the release notes provider, AST rules, fragments, verbs; a registry of its own that other extensions contribute to | `[checks.<tool>.<role>]`, `[ci.jobs.<point>.<name>]`, `[slots.<name>]`, `[guidance.<section>]`, `release-notes`, `[rules.<name>]`, `[fragments."<target>"]`, `contributions_for` | docs, changelog, claude, housekeeping, coverage |

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
  so are pytest's measurements for coverage and coverage's pages
  for docs.

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
| the site URL in the composed `pyproject.toml` | `_templates` reads `docs_table` for `docs_site_url` | `[docs] site-url` among the docs extension's keys; its `pre_tasks` hook sets footman's task-link template, `inv.docs_url`, and a workspace without the extension sets `[workspace] docs-url` |
| which categories the site reads, for the docs job's skip | `_provenance.site_reads` | `inputs` on the job, the same table a check declares; the shell's skip rule reads it |
| the API extractor on a kind | `_kinds.Extractor`, `KindRecord.extractor`, `kind_extractor` | `livery.extensions.docs.Generator`, declared in `[generators.<name>]` by a generator extension of its own (`mkdocstrings`, `doxygen`), naming the package-level extensions it extracts for, and read by the docs extension through `contributions_for("docs")`; its site configuration a `zensical.toml` fragment |
| coverage pages on a kind | `KindRecord.coverage_pages` | a `[generators.<name>]` entry of the coverage extension's `[for.docs]` table, one renderer for every kind, reading the line format |
| coverage: the floors, the ratchet and its marks, the line format and its union across legs, the coverage CI entries and verbs | `[qa] coverage-floor` and `coverage-epsilon`, `_coverage_store`, `_coverage_lines`, `_coverage_marks`, the python backend's floor policy and leg combine, `_points`' `coverage.leg` and `coverage.union` | the coverage extension; each test tool's extension measures and hands its lines over in a `[for.coverage]` table |
| the nav block format | `_navblocks`, `rewrite_nav_block` in the api | data: a generator writes `docs/_generated/nav.<name>.toml`, the file the docs extension reads; nothing is imported |
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
entry module declares.

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
| `[for.<target>]` | the same keys, read only while both are listed; dormant when no target is, and named so | every | pytest for coverage, coverage for docs (phase 11c) |
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
for generator extensions alone, since nobody imports an extension but
the workshop that mounts it and an extension that requires it. A
package's generator writes data the extension reads. Each name
arrives when an extension that requires docs needs it; none has yet:

| Name | Purpose | Users |
| --- | --- | --- |
| `Page` | one generated page: path, title, nav position; what a generator's `run` returns | mkdocstrings (phase 11), doxygen (phase 12), the task reference and coverage pages |
| `GENERATED` | the generated tree's name under a package's `docs/` | generators |
| `PUBLIC_MEMBERS`, `ALL_MEMBERS` | the members policy's values | mkdocstrings |

### What leaves the public surface, and the break each causes

| Name | Goes to | Who breaks | Phase |
| --- | --- | --- | --- |
| `rewrite_nav_block` | nothing: a generator writes `nav.<name>.toml` as data | any third-party generator | 10 |
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
  `[for.docs]` table such as the coverage extension's pages.
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
| toolroom-bench into workshop's `_navblocks` | data: the bench writes `nav.tools.toml`, the file the docs extension reads | |
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
| `_kinds` | `Extractor`, the members policy, `kind_extractor`, `kind_coverage_pages` | move into the docs extension as `[generators.<name>]` and its policy; the coverage pages generator is the coverage extension's |
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

### First publishes

PyPI holds every name the remaining phases release. On 2026-10-09 each
project was created with a placeholder, version `0.0.0.dev0`: an empty
wheel and an sdist, uploaded by a workflow of the temporary repository
`willemkokke/livery-name-claims` through a pending trusted publisher.
A project created that way is not held to the four new projects a day
that a token's first upload meets: fourteen were created that day. So
a phase's distributions release on its first release day, and the
existing token uploads them like any existing project. The train's
first release of each is 0.0.0, above the placeholder. The limit still
holds for a token's first upload, so it matters only to a release that
brings more than four new projects in one day: that release claims its
names first, the same way.

| Name | First release |
| --- | --- |
| `livery-extensions-changelog`, `livery-extensions-claude` | 10e and 10f: shipped 2026-10-09, release PR #1340 |
| `livery-extensions-python`, `-cmake`, `-conan`, `-cpp`, `-nanobind`, `-unreal` | 11b |
| `livery-extensions-mkdocstrings` | 11c |
| `livery-extensions-docs`, `-housekeeping`, `-doxygen`, `-coverage` | 12a to 12e |
| `livery-lodge` | 15 |

Whether `livery-extensions-housekeeping` publishes at all is decided in
12b; the recommendation is `[release] publish = false` until a second
repository lists the extension, and the name is held either way.

Debt from the claim, an out-of-band one (the workshop's rules):

- [ ] Each placeholder `0.0.0.dev0` is yanked once its package's first
  release lands; a name that never releases keeps its yanked
  placeholder. `livery-extensions-changelog` and
  `livery-extensions-claude` have released, so theirs can go now.
- [ ] Each project's trusted publisher for `livery-name-claims` is
  removed; the repository is deleted already (2026-10-09).

### Phase 10: the API as a contract, and its first consumers

**10a, the entry module: built (issue #1218).** Every root's `api.py`
becomes `__init__.py` in one wave, footman included (ruling 5); what
need not load is imported under `TYPE_CHECKING` and served by
`__getattr__`; `tests/test_namespaces.py` pins the new shape;
`typecomplete` verifies `__init__`; 10b's pins are written against the
new path once. Acceptance: `fm check` exits 0;
`fm workflow.release --local`; the startup measure
(`fm commit --help`, 255 ms median) does not rise.

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
  `_workflow_tasks`, `_provenance` and `_templates` stop reading the
  `[docs]` table; task names link through footman's `inv.docs_url`,
  set by the docs extension's hook or from `[workspace] docs-url`.
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
`test_a_matrix_key_absent_from_the_words_fails_the_kit`; the five
extensions whose checks are all words ship no Python beyond their
packaging (`find packages/extensions/{ruff,mypy,ty,pyrefly,clang-format}/src -name '*.py'`
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
It lands in four parts, each mergeable alone: composition (#1330,
built: the three keys, the validity rule, the canonical list and its
fix, the combinations, contract 28's mount, compatibility through
extras); the phases and their context (#1332, built: `[phases.<phase>]`,
the walk, `PhaseContext`, the refusals and their layering check); the
queries (#1333, built: `[queries]`, `Query`, `answer` and eight of the
eleven queries); `fm run` (#1335, built: the verb, the development
build and its record, the run phase's owner main, and #1299's fix, so
a verb inside a package resolves the workspace at its root). Until 11b
the tests compose fixture extensions; the acceptance's C++ application
runs on 11b's cpp extension, an open line until then.

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

11b also takes the first half of phase 13 (ruled 2026-10-09, option
(a)): as each backend moves, the release train's calls to it become
phase steps and queries (`stamp`, `build`, `prove`, `publish`,
`replay`; `CURRENT_VERSION`, `VERSION_FILES`, `REQUIREMENTS`,
`MODULE_ROOTS`), so no temporary key reaches a backend. 11b lands in
slices, each mergeable alone:

1. **The train's pins and the engine's inputs.** Tests pin the release
   train's observable properties at its seams before any of its calls
   change (contract 8). A phase's steps read the engine's inputs (the
   version, the registry target, the build's epoch) as context keys
   the base provides, which the phase judge counts as provided.
2. **python.** `livery.extensions.python` ships in the workshop wheel
   the way docs does, so the workshop's suite and its release leg keep
   it: the python kind's backend, queries and phases, its categories,
   its tool (`uv`) and seeds, `run_suites`, `kind_examples` and
   `workspace_suite`, and the reach rule. This repository's packages
   list it. Until slice 5 the release train asks a package's
   extensions where they answer, and the kind's backend for a package
   still of a kind: the one bridge of the transition.
3. **cpp, cmake and conan**, from the `cpp-conan` kind: its build and
   test checks, its root files as `[root-files."<path>"]`, and checks
   naming `extensions` instead of `kinds`.
4. **nanobind and unreal**: nanobind's wheels job under
   `[ci.jobs.<point>.<name>]`, so `_ci_generate` stops reading the
   roster; unreal's declaration.
5. **11c, the generators**, before the registry goes, since the docs
   extension reads the extractor and the coverage pages from the kinds
   until then.
6. **The kinds go**: the registry, `KindRecord`, `Backend`, the
   `kind` key, a check's `kinds` and the bridge; `fm new.package` takes
   a combination, completed from `fm extensions --combinations`.
7. **Each extension leaves the workshop wheel** for its own
   distribution, its tests with it, released under the names claimed
   on 2026-10-09.

**11c, the generators.** `[generators.<name>]` and the members policy in
`livery.extensions.docs` (the extension is still in the wheel);
`zensical.toml` becomes a composed file of the fragment engine, the
docs extension's dynamic fragment with contributed tables; `Extractor`
and `KindRecord.coverage_pages` go. `livery-extensions-mkdocstrings`,
its own distribution requiring `docs`: the python generator, the
handler tables as its fragment, `lint.docrefs` and `lint.docstrings`,
`griffelib`, the generator's `paths` option at `[generators.mkdocstrings]`
(`[docs] api` stays docs'); the
workshop wheel drops `griffelib`. The coverage extension,
`livery.extensions.coverage`, starts in the workshop wheel the way
docs did: its `[for.docs]` table declares the coverage pages
generator, one renderer for every kind that reads the line format,
and pytest's `[for.coverage]` table reduces coverage.py's data to
that format. Open item 3 decides whether a line carries its branch
counts. The docs extension reads both generators through
`contributions_for("docs")` and names no language. The
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

**12e, coverage.** `livery-extensions-coverage` leaves the workshop
wheel and takes what the base holds today: the floors (a percentage
or `auto-ratchet`), the epsilon, the ratchet's marks, the line
format and its union across legs, `coverage.leg`, `coverage.union`,
`coverage.enforce` and `coverage.accept`, and their CI entries. Its
`[coverage]` table replaces `[qa]`: `floor` and `epsilon` as
before, and `required = false` for a package whose coverage nothing
judges. Such a package is still measured, and its page still
renders. A package with tests is judged by default. Measuring stays
with each test tool: pytest's `[for.coverage]` table brings
coverage.py's configuration and the reduction to lines, and a C++
test extension brings llvm-cov's or gcov's. A workspace that does
not list `coverage` runs no coverage leg, so a project born with
ruff alone is green (#1225); a listing with no measuring extension
is dormant and named, like any `[for]`-only listing. Acceptance,
refusals first:
`test_a_package_with_coverage_not_required_is_never_judged`,
`test_a_workspace_without_coverage_runs_no_coverage_leg`,
`test_a_coverage_listing_with_no_measurer_is_dormant_and_named`;
`fm ci.e2e --extension=ruff --fresh` is green; `fm check` on this
repository judges every package's floor as before.

### Phase 13: the release train through phases

The plan's 11c and 11e, less what 11b takes (the phases replacing
the backend calls, ruled 2026-10-09): `[publish]` and several
artifacts; ecosystems as registrations with their defaults, which
retires `_registries`' tables; `livery.forge.RegistryKind` an open
name; the forge's `package_admin`; `[release] publish` goes; the graph
from the `REQUIREMENTS` query. Acceptance: the plan's.

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
| `[qa] coverage-floor`, `coverage-epsilon` and the coverage machinery in the base | the coverage extension (phase 12e) |
| `_site_files` | a whole-file fragment (phase 12a) |
| `tests/test_private_reaches.py` at this repository's root | the python extension's rule and `[python] private-reaches` (phase 11b) |
| the layering check's python parse in the base | the `REFERENCES` query (phase 11c) |
| `fm footman.pages`' curated API page | the mkdocstrings extension's generator (phase 12d) |
| the assembled `zensical.toml` the docs build writes | a composed file of the fragment engine, generators contributing their tables (phase 11c) |
| `_taskref`'s spawned `fm --tasks-file` | `livery.footman.docs.site(provider=...)` (phase 12d) |
| the reach allowance's forge row | the admin protocol (phase 15) |
| `KindRecord.before_install`, the nanobind kind's conan profile check before `uv sync` | the conan extension's `sync` phase step (phase 11b, slice 3) |

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
- 2026-10-07, 10a: the six roots with an entry module drop
  `namespace = true` from `[tool.uv.build-backend]`, so `uv build`
  refuses a root that lost its `__init__.py`; the workshop's wheel
  keeps the flag for `livery.extensions.docs`, which has none. The
  workshop no longer reads `api.py` as a root's mark or a version's
  home. The bench's task entry points name `livery.toolroom.bench:tasks`,
  because a lazy root has no group in its namespace for footman's
  loader to adopt. `fm commit --help` measured 255 ms median before
  and after, interleaved over 31 rounds.
- Willem, 2026-10-07: coverage is its own extension, neither the
  core's nor the test extension's, and a package can be left
  unjudged. The site renders coverage one way for every kind, from
  the workshop's own line format, instead of each tool's HTML in a
  frame. Phase 11c starts the extension in the wheel with the pages
  generator, and 12e ships it apart with the policy.
- 2026-10-07, decided here: the opt-out is `[coverage] required =
  false` in the package's contract, and judging is the default for
  a package with tests. A key, not a floor of zero: the page still
  renders, and the reader sees the package was left out on purpose.
- 2026-10-07, 10b lands in slices, each gate-green and mergeable alone:
  the reach scan (#1220); the declaration file, read at mount for the
  eight tool extensions and for the docs extension's identity and
  contract keys (#1222); the docs extension's registrations; the schema;
  the verification; the public names. The first slice's allowance holds
  three reaches #1204 does not list: the bench reads `_NEGATIONS`,
  `_WRAPPERS` and `_console_entrypoint` on `livery.toolroom.tools`, with
  no seam designed yet.
- 2026-10-07, the declaration file (#1222), what the key table above
  does not say: a check's static configuration stays on the check,
  `[checks.<tool>.<role>.fragments]`, a project file's text or a package
  file's `{ kinds, text }`, so it leaves with the check as before; a
  check also takes `kinds` (until phase 11's `extensions`),
  `tests-only`, `arguments`, `flags`, `roles`, `listed-with` and
  `editor-extension`; a key holding a dot is one name, since the judge
  walks a contract's tables. An installed package that ships no
  declaration file, an environment from before the change, is named and
  skipped at mount, and `fm sync` installs the one the workspace builds,
  so no checkout strands. The kit's `contribution-modules` clause
  becomes `declaration-validates`: the file reads and every reference
  resolves; importing them under a recorder joins in the verification
  slice. Reading the nine declarations costs `fm commit --help` about
  4 ms beside 10a, interleaved over 41 rounds, 2.4 ms of it the
  reference check's parse of the modules the references name.
- 2026-10-07, the docs extension's registrations (#1252), first part:
  its three checks are `[checks.<tool>.lint]` tables, and a check
  declares the files it reads as `inputs` (`reads`, `per-file`,
  `widens`, `on-removal`, `ignores`, and `widen`, a reference). Its two
  slots are `[slots."<name>"]` tables, composed by `union`, `nearest` or
  a reference. The mount declares an extension's slots before its checks
  and contributions register, so a `[for]` contribution finds them. A
  test that composes the site without a mount declares the docs slots
  from the declaration file. The jobs and the base's `[docs]` reads are
  the second part.
- 2026-10-07, the docs extension's registrations (#1252), second part,
  the jobs: a job is a `[ci.jobs.<point>.<name>]` table. Besides
  `entries`, `gates`, `installs` and `deploy` from the key table above,
  it takes `needs`, `fetch`, `token` and `note`, the keys the site's
  deploy needs. `token` takes `job` alone, since a grant beyond the
  run's own token is the root contract's to give. `installs` and
  `deploy` are references the CI render calls with the root, and a
  job's entries register under the listed name, as a check does. The
  generators and the publish seam move into the extension with their
  keys, so `_ci_generate` and `_workflow_tasks` read no `[docs]` table.
  The install step is named `System packages`, since any job may name
  packages. Fixed in the same change: an extension a branded App mounts
  as a builtin registers its declaration, and a GitLab deploy is the
  `pages` job only for the `pages` seam. The `project.urls` slot and
  the job's `inputs` are the third part.
- 2026-10-07, the docs extension's registrations (#1252), the job's
  `inputs`: a job declares the files its entries read in the table a
  check declares, and `fm ci.run` skips the job's entries on a pull
  request that changed none of them, nor the sources of the extension
  that contributed it, on the terms the check legs narrow on. The docs
  job's reads are the globs the site's categories named; a
  `packages/**/docs/` glob also matches a `docs/` directory deeper in a
  package, which only builds the site when it could have skipped. The
  site build drops its own skip, and `site_reads` leaves the base:
  `fm explain` names the job, `claimed by: gate/docs`. Fixed in the same
  change: the affected gate finds a declared check's member through the
  package its listed name's entry point names, so a change to an
  extension's sources judges its checks whole again. The
  `project.urls` slot waits on Open item 4.
- 2026-10-07, the schema (#1256), first part: `fm sync` composes the
  three files from the records the judge already holds, the base's and
  the listed extensions', in draft 7, the newest Taplo validates. They
  are written for the checkout alone, as `.workshop/` is, and the
  repair writes them for a checkout that never synced. Decided here,
  against the ruled association in `.vscode/settings.json`: the
  association is a composed `.taplo.toml`. Even Better TOML matches a
  settings association's regex against the document's absolute URI,
  without lookaround, so the root's `workshop.toml` and a package's
  cannot be told apart, and it leaves overlapping patterns undefined. A
  `.taplo.toml` rule's include globs match from the root, and every
  Taplo client reads the file, the CLI and other editors' language
  servers among them. The base recommends Even Better TOML in the
  composed `.vscode/extensions.json`. The judge reading the composed
  file, the base's keys as TOML, `[toolroom]`'s fragment in
  `livery-toolroom-store` and `fm explain <contract>` are the next
  parts.
- 2026-10-07, `fm explain <contract>` (#1262): a contract's lines end
  with `schema: .workshop/schema/<file> (<owners>)`, the base then the
  listed extensions in list order; an `extension.toml` names the base
  alone. Which contract a file is: the root's `workshop.toml`, a
  package's beneath `packages/`, or any `extension.toml`.
- 2026-10-07, the schema (#1258), second part: the contract judge
  validates against the composed schema, with a validator of its own
  over the subset it uses, and keeps every teaching message. It reads
  the composition the files are written from, held per set of
  declarations, never the file: a file written before an extension was
  installed or listed would misjudge until the next sync. Two shapes
  JSON Schema says less exactly than the records did are fixed here. A
  list of strings is the array whose items are a bare string, judged
  whole, its doc set aside; a test checks every declared key's refusal
  words against its record's types, so a list whose entries are
  declared as bare strings, which would read as a list of strings,
  fails that test. An unlisted extension's key is a
  property that takes no value and carries its owner (`x-owner`), so
  the judge and the editor both name the extension. The judge adds the
  composition's import and one composition per contract to a command,
  about 1.5 ms. The kebab-case spelling stays a refusal of the parse.
- 2026-10-07, the schema (#1265), third part: the base declares its
  keys in `livery/workshop/contract.toml`, in an extension's `[contract]`
  grammar extended to the `extension` contract, which only the base's
  file may declare. The composer repeats the `checks`, `ci` and
  `contributions` keys of `extension.toml` under `for.*`, so the file
  states them once. A key named `types`, `values` or `doc` is a key when
  its declaration is a table; a list or a string under those names is
  the declaration's own. Values the code also holds (`MODES`,
  `CADENCES`, the registry kinds, the LFS key) are written in the file
  and held equal by a test. The six composed schemas (each contract,
  every owner and the base alone) are byte-identical before and
  after. `[tools]` stays the base's until phase 10g; a doc on every
  key is the verification slice's.
- 2026-10-07, the verification (#1266), first part: the kit's
  `references-register-nothing` clause imports each module a
  declaration's references name, in a process of its own with the
  workshop's registration functions recording and footman's tree
  captured, and names each module whose import registers anything. The
  docs theme's compose function moves to `livery.extensions.docs._theme`,
  since `_site` defines the `docs` verbs at import. The typed reference
  module per extension waits for the public names, whose role types it
  imports; the ci jobs' entries read against the plugin's tasks by AST
  are the next part.
- 2026-10-07, the verification (#1268), second part: the kit's
  `entries-name-defined-tasks` clause holds every task a CI job's
  entries name to the tasks the extension's sources define, read by
  AST: module-level `group(...)` assignments, nested and imported
  groups, and `@<group>.task` decorators named as footman names them.
  It runs in the kit, not at mount: reading the docs extension's
  sources takes about 17 ms, which every command would pay.
- 2026-10-07, the verification (#1271), third part: the kit's
  `claims-name-categories` clause holds a check's claims to the
  categories some extension registers, each suffix starting with a
  dot, and `plugin-is-an-entry-point` holds an `extension.toml`'s
  plugin to an installed `footman.tasks` entry point. The clauses that
  read a declaration share one reader and one walk over its tables.
- 2026-10-07, the verification (#1278), fourth part: every key the
  base declares carries a doc, which the composed schema gives the
  editor as the key's hover text, and the kit's
  `contract-keys-documented` clause asks the same of an extension's
  `[contract]` tables. One key holds none: an option's own table
  declares a child named `doc`, and TOML gives one value per name, so
  `checks.*.*.options.*` cannot carry a string `doc` beside it. A job's
  `inputs` loses `per-file`, which a job, running its entries or
  skipping them, never reads; `registries.python.prerelease` takes uv's
  five values. The research behind the docs found #1273, #1275, #1276
  and #1277.
- Willem, 2026-10-07: keep the composed `.taplo.toml` that points the
  editor at each contract's schema (#1256).
- Willem, 2026-10-07: a CI job an extension contributes takes the run's
  own token alone. How a job could ever get more is recorded now, with
  security-minded approaches, in #1280, to be ruled before one needs it.
- Willem, 2026-10-07: confirms the coverage decisions recorded on
  2026-10-07 as written: the site renders coverage one way from the
  line format, phase 11c starts the extension and 12e ships it apart,
  and the opt-out is `[coverage] required = false`.
- Willem, 2026-10-07, on Open item 4: to review in detail; as
  proposed, it is too complex for what it brings.
- Willem, 2026-10-07, on Open item 4: the base knows nothing of the
  docs site's address. Only the docs extension specifies it, and the
  extension hands footman the task-link template; a footman parameter
  that overrides the configured `docs-url` is an accepted cost of
  keeping the docs configuration in one place. Added the same evening:
  a workspace without the docs extension may still set a `docs-url` in
  `workshop.toml`, for pages it maintains another way. Open item 4
  closes without new grammar: no `project.urls` slot, and no
  contribution computed from the contract.
- 2026-10-07, the task links (#1252), the last part: footman's
  `Invocation` gains `docs_url`, the run's link template. It starts as
  the configured `docs-url`, a `pre_tasks` hook may set it, and footman
  refuses by name a template it cannot fill. The parameter is a field
  a hook sets, not a call at import: footman imports a provider module
  once per process and resets the template on each invocation, so a
  value set at import would hold for the first invocation alone.
  `[docs] site-url` moves to the docs extension's keys, and its hook
  links each task to its alias page under the site. The base's hook
  reads `[workspace] docs-url`. Decided here, against the ruling's
  word: footman's configured `docs-url` comes before a hook's, then
  `[workspace] docs-url`, then the site's, whichever hook runs first,
  so a template a person wrote is never dropped without a word. In a
  workspace the order changes nothing, since the composed
  `[tool.footman]` writes no `docs-url` and the repository cannot add
  one to a table the render owns. A template footman cannot fill is
  named on stderr and links nothing, as the mount names what it
  skips. `docs_table` moves into the extension; a package's
  `[docs] api` stays the base's until the API extractor moves.
- 2026-10-07, the public names (#1285), first part: `livery.workshop`
  exports `NONE`, `selected_files`, `Changes`, `read_contract`,
  `generated_header`, `ci_run`, `RunContext`, `slot`, `ReleaseNotes`,
  `release_notes`, `forge_repository`, `registry` and `RegistryTarget`,
  and the docs extension reads the workspace through them. Decided
  here: `GateContext` names the running check, `check`, set as a run
  hands it over, so `selected_files(ctx)` takes the context alone and
  `check_for` leaves the extension; the field is the check's name, not
  its record, which stays off the public surface. `read_contract` takes
  the directory a contract sits in, so an extension never spells the
  file's name. `generated_header` takes the lines an extension's own
  file opens with. A compose reference refuses with `ValueError`, which
  the composition names as the slot's, so no error type is public.
  `ci_run`, `read_contract` and `registry` sit beside the names the
  base's own code calls; `remote_repo_name` became `forge_repository`.
  The reach scan reads the workshop's wheel as one distribution, so a
  test holds the docs extension to these names until phase 12 ships it
  apart. The second part brings `ci_changes`, `Prose`, `AGENT`,
  `HUMAN`, `guidance` and `contributions_for`.
- 2026-10-07, the public names (#1285), second part: `livery.workshop`
  exports `ci_changes`, `Prose`, `AGENT`, `HUMAN`, `guidance` and
  `contributions_for`. `guidance(root, audience)` is the one assembly
  of every fragment in play, the mounted extensions' and the
  repository's own, which the docs extension's development pages and
  the base's agent outputs both read; the docs extension no longer
  imports `_prose`, and `Section` stays private, since `guidance`
  answers in section order. `ci_changes(root)` is the diff the check
  legs read, with its merge base, so a `widen` reference can read a
  file as it was; the docs job's skip reads it, and its skip line
  names the run instead of the base. `contributions_for(target)`
  reads the `[for.<target>]` tables as the file holds them, by the rule
  the mount grafts by, and the top-level tables of an extension that
  requires the target; `Declaration` keeps the raw tables for it. The
  slice's breaking part is next: `rewrite_nav_block` leaves for the
  docs extension's own public API, and `mount_extensions` leaves the
  public surface.
- 2026-10-07, the public names (#1290), the breaking part: `_navblocks`
  moves into the docs extension, and `livery.extensions.docs` gets an
  entry module serving `write_nav_block`, `nav_block_markers` and
  `GENERATED`; the workshop's wheel drops `namespace = true`, so `uv
  build` refuses either root that loses its `__init__.py`.
  `rewrite_nav_block` leaves the workshop with no replacement under its
  name: no package's source called it, and `write_nav_block` emits the
  block beside the generated pages, which leaves the committed
  `nav.toml` alone. toolroom-bench writes through the public name, and
  the reach test's `_navblocks` row goes. `mount_extensions` was
  private already, in `livery.workshop._extensions`. `Page` and the
  members policy wait for phase 11's generators. The wave that
  releases the break waits for PyPI's window for the four new
  distributions.
- 2026-10-08, the public names (#1290), found as it merged: the API
  reference documents every root a package ships. The workshop's
  section rendered `livery.workshop` alone, so the strict site build
  refused the workshop's reference to
  `livery.extensions.docs.write_nav_block`. Decided here: the
  shallowest root keeps the section's top and its URLs, and each
  further root's pages sit under a directory named by its dotted
  path, so the docs extension's modules publish under
  `packages/workshop/api/livery.extensions.docs/` until phase 12
  ships the extension apart.
- Willem, 2026-10-08: nobody imports an extension but the workshop,
  which mounts it, and an extension imports another only when it
  cannot work without it. The docs extension's public names go with
  its entry module until an extension needs one.
- 2026-10-08, the rule (#1294): toolroom-bench writes its tools nav
  block as data, `nav.tools.toml` beside its pages, and imports
  nothing of the docs extension. `livery.extensions.docs` loses
  `write_nav_block`, `nav_block_markers`, `GENERATED` and its entry
  module, and the workshop's wheel takes `namespace = true` back for
  it; its modules leave the API site with it, while the reference
  keeps documenting every root a package ships. A test holds the rule
  over every member's sources: an import of an extension's module from
  outside it refuses, naming the file and the line, unless the
  importing extension's `extension.toml` requires the imported one.
- Willem, 2026-10-08: the site's packages move under one Packages
  entry with a generated landing page, as hse's are. A package's place
  comes from its import path, the shared namespace hidden, or its
  folder; `[docs] name` overrides it, and no new construct is added for
  it. The extensions sit inside the workshop's entry, or at least below
  it.
- 2026-10-08, the Packages section (#1296): `_packages_page` places
  each package (`[docs] name`, else the import path less the namespace
  every python package shares, else the folder under `packages/`),
  orders siblings by `order_topologically`, the order a release wave
  publishes in, and writes `packages/index.md` after the mount.
  Decided here: the landing page shows each package's contract
  `description`; replacing the tree by hand, a root `docs/nav.toml`,
  waits for a workspace that needs it. The contracts that place the
  tool extensions and state the missing descriptions land apart, as
  documentation, so no package other than the workshop gets this
  feature in its changelog.
- 2026-10-08, the Packages section's contracts (#1296): the eight tool
  extensions set `[docs] name = "workshop.extensions.<name>"`, so they
  sit inside the workshop's entry after its own pages, and footman,
  strongroom, toolroom, toolroom-store and toolroom-bench state the
  description their `pyproject.toml` already gives, which the landing
  page shows.
- Willem, 2026-10-08: PyPI accepts at most four new projects a day.
  The four the 2026-10-07 wave could not create go first, after
  15:00 UTC, and later phases plan their first publishes against the
  limit.
- 2026-10-08, first publishes: a wave names at most four new
  distributions, and a phase with more spreads them over consecutive
  days in dependency order, python first in 11b. The schedule is under
  "First publishes, four a day". Both rehearsals of the 10b wave
  (`--local`, on `3a3e4c5d` and on `413b9940`) passed every leg.
- 2026-10-08, 10g (#1301): `[tools]` is `[toolroom]` in the root and
  package contracts (`extension.toml` already declared `[toolroom]`),
  `tools.lock` is `toolroom.lock`, and the workshop's and the bench's
  `fm tools.*` verbs are `fm toolroom.*`. The store declares the table
  in its own fragment, `livery.toolroom.store.SCHEMA_FRAGMENT`, and
  reads it without the workshop, `read_table(directory)`, from
  `workshop.toml` and else `toolroom.toml`. Decided here:
  `tools.graphs/` becomes `toolroom.graphs/` with the lock, since a
  lock and its graphs name one owner; the fragment composes under its
  own owner, `livery.toolroom.store`, which every workspace composes
  with the base whatever its root lists (`ALWAYS`), and `fm explain`
  names it; `[tools]` refuses through the judge's nearest match, "did
  you mean 'toolroom'?", with no code of its own; `fm sync` names a
  `tools.lock` or `tools.graphs/` it finds, with `fm toolroom.lock`,
  and leaves it. The checker configs' comments and toolroom's stub
  comments name `fm toolroom.restub`, so basedpyright, pyrefly, ty and
  toolroom release with the store, the bench and the workshop.
- 2026-10-08, 10c (#1303): a check is its tool's words, `judge`, `fix`,
  `safe-fix`, `env` and `matrix`, or a reference to its code, `run`,
  never both. The declaration reader turns words into a command the
  engine runs (`livery.workshop._words`), and the record keeps the
  words for `fm explain <check>` and the kit's `words-are-answerable`
  clause. ruff's two checks, mypy's (a matrix over linux, darwin and
  win32), ty's, pyrefly's, clang-format's and basedpyright's type check
  are words, their check code is gone, and this repository's gate ran
  them through the engine. Decided here: the first word is the tool's
  name as the check's `tools` name it, and the engine calls its
  toolroom handle with the rest; a run that reaches the whole calls the
  tool with no path, so it reads its own configuration, ruff included,
  which was handed `.` before; a run that reaches nothing the check
  reads calls nothing; a selected directory with no file the claims
  reach is never named, since mypy refuses one and an extension's `src`
  can now hold data alone; `{cache}` is answered relative to the
  call's directory; a non-zero exit refuses with the handle's reason
  and the tool's output, and every batch and matrix call still runs;
  `env` keys are names, exempt from kebab-case; `fm explain` takes a
  check's name as well as a file.
- 2026-10-08, 10c and clang-tidy: a check is its tool's words or a
  reference to its code, and code is a first-class form; Willem
  confirmed no rule bars a check from having code. Words fit a check
  whose verdict is its tool's exit code. clang-tidy's is more: it asks
  the host's compiler where its builtin headers are, and skips with a
  reason when none answers. So it keeps its `run`, and the acceptance's
  `find` names the five extensions whose checks are words; on `main`
  at `ecfbb09c` it finds nothing.
- 2026-10-08, open in 10c: `fm ci.e2e --extension=mypy --fresh` stays
  red at its members scenario, at the coverage leg, which #1225 ruled
  lands with 12e. The pass's check leg ran the branch's dev wheel of
  the mypy extension and its gate printed `ok typecheck-mypy`, the
  three calls of the matrix green on the loop's runner, before the
  coverage leg refused a workspace with no test extension. The
  acceptance line stays open until 12e lets the pass end green.
- 2026-10-08, 10d (#1306): the reach test's allowance holds the
  forge's two entries alone. footman exports `host()`, a `Host` whose
  `active`, `in_task`, `real_cwd()`, `target_cwd(cwd, relative)` and
  `argv_override(args)` answer what toolroom's bridge and the bench ask
  of a run; `run(view=...)` with a `CommandView`; `styled(text, style,
  on=...)` and `wants_color(stream)`; and `builtins()`,
  `project_builtins(root)`, `directory_variable(name)` and
  `tasks_file_name()`. toolroom exports `colour_controls()`,
  `negations()`, `wrappers()` and `console_script(name)`. Decided here:
  the painter is `styled`, since footman's public `colored()` already
  answers whether a task's output dresses for colour; the display
  record is `CommandView` on `run(view=...)` rather than a member of
  `host()`, since footman's `Invocation` already names a plugin's
  invocation; `host()` answers while footman's routers are installed or
  a task's context is current, `active` and `in_task` telling the two
  apart, since the bench asked the one and the bridge the other; the
  bench downloads with the store's public `fetch_bytes`, which the
  store's private download wraps, rather than footman's `fetch`, which
  caches into footman's directory and answers a path; the stub tables'
  names, which this note did not design, follow its rule that a
  function another package calls is public; `console_script` answers
  the entry point, not yes or no, since the bench reads its version and
  loads it. The bench's `toolroom.{task}` message, which 10g's rename
  missed, names the verb it has.
- 2026-10-08, 10d's acceptance: `fm workflow.release --local` on `main`
  at `f4614c00`, the wave's twelve members (footman, forge, the
  workshop, toolroom, the store, the bench and six tool extensions),
  passed all 24 legs in 26m12s and restored the tree.
- 2026-10-08, a whole mypy run (#1310): `mypy.ini` named every python
  member's `src`, and the five tool extensions whose `src` holds data
  alone made mypy refuse any run that reaches the whole. The render
  data carries `python_dirs`, the members' directories that hold a
  python file, and `files` lists them; the wave carries the fix.
- 2026-10-08, 10e (#1307): `livery-extensions-changelog` writes the
  release notes. Its `extension.toml` names its provider with a new
  top-level key, `release-notes = "module:name"`, which the mount
  registers and the release train imports when it first asks for an
  entry. Each package's `cliff.toml` is the extension's per-package
  content, rendered into every member whatever its kind, and
  `git_cliff` is its `[toolroom]` requirement; `_cliff` and the
  template left the base, and the base kind requires no tool. Decided
  here: the extension seeds a `CHANGELOG.md` into every member born
  while it is listed (`content/seeds/package-base`), the base's three
  copies of that seed go, and the first record creates the file for a
  member born without it; a tool an extension that declares
  `release-notes` requires takes no host allowance, naming the
  extension, which replaces the base's `git_cliff` by name; the
  workshop's release tests run the train against a stand-in provider,
  and git-cliff's entries, its history and its bump's agreement with
  the workshop's derivation are tested in the extension. Not moved:
  the release driver's rollback, the docs site and the conan release
  body still read `CHANGELOG.md` by name.
- 2026-10-08, 10e's acceptance: `fm workflow.release workshop
  extensions/changelog extensions/claude --local` on `main` at
  `b90e6ae9` passed all six legs in 16m40s, the extensions' at the
  set's workshop 0.8.0, and would release the workshop 0.8.0 with both
  extensions at 0.0.0. Its first runs found two test faults, fixed by
  #1321 (a birth test asserting the claude extension's seed on the
  workshop's wheel alone) and #1322 (a test pinning minijinja's
  wording, which its newest release extended). Before it, every
  package's entry was written offline on `main` at `f4614c00` by the
  base's git-cliff code and by the extension's: the fifteen entries
  are byte-identical.
- 2026-10-08, open in 10e: the `cliff.toml` template's comments still
  say each file is composed from the base's template. Rewording them
  renders every package's `cliff.toml` again, which puts the change
  into every package's next release entry and moves each version, so
  the wording changes in a wave that releases every package anyway.
- 2026-10-08, the wave shipped. `fm workflow.release.dispatch`
  re-ran the wave of `a7686a2f` for its four uncut members: run
  37798177199 published `livery-extensions-mypy` 0.0.0 and met an
  HTTP 500 from PyPI's upload at basedpyright, and one more dispatch,
  run 37798612073, published ty, clang-format and basedpyright 0.0.0.
  The union set's `--local` on `main` at `96dd4826` passed all 24 legs
  in 27m32s; its armed run, PR #1317 and run 37811203813, released
  footman 0.59.0, forge 0.7.0, toolroom 0.11.0, toolroom-store 0.3.0,
  the workshop 0.7.0, toolroom-bench 0.3.0, and basedpyright, pyrefly,
  ty, ruff, mypy and clang-format 0.1.0. The workshop's footman floor
  rose to 0.59.0, which closed #1291. 10b's last acceptance,
  `fm ci.e2e --extension=ruff --fresh` from a branch at `8986e2a4` with
  the released members pinned (pass `20261008T165448Z`), passed birth;
  its members scenario's check leg ran the gate with `ok format-ruff`
  and `ok lint-ruff`, then stopped at `coverage.leg`, #1225's, which
  12e lands. Found on the way and filed: #1316, a `--local` rehearsal
  derives from the checkout's own tags; #1318, a large set's release
  PR is never armed; #1320, the loop run from `main` opened a real
  release PR for strongroom, PR #1319, which `fm workflow.abort
  --force` closed before anything published.
- 2026-10-08, 10f (#1313): `livery-extensions-claude` writes what
  Claude Code reads. An extension declares a file its code writes with
  `[fragments."<target>"]`: `render = "module:name"`, called with the
  workspace root, and `local = true` for a file this checkout keeps
  alone. A target ending in `/` is a directory whose render answers
  each file under it, text to write or a shipped path to link to; a
  path two writers claim refuses naming both
  (`livery.workshop._shipped_files.computed_outputs`). The extension
  declares three: `CLAUDE.md`, committed; `.workshop/fragments/`, the
  agent's guidance over `guidance(root, AGENT)`; and `.claude/`, each
  listed extension's skills and hooks linked and the one
  `settings.json` copied. Its plugin carries `fm hooks.*`, and its
  content the skills, the hook shim and the settings; `_agent_outputs`,
  `_hooks`, `_tree` and the agent-only helpers of `_prose` left the
  base. Decided here: the workshop exports `shipped_content(start)`,
  each stack extension with its `content/`, and `fix_files`, which the
  post-edit hook runs; a computed file's provenance is its own rule
  ("computed", or "materialised" for a local one), replacing the
  base's names for `CLAUDE.md` and `.claude/`; the extension seeds
  `CLAUDE.project.md` at a project's birth, and `fm sync` no longer
  writes it when it is missing; `fm new.project`'s stock list names
  `claude` after `changelog`. Kept in the base: the `.claude/**`
  pattern among the paths no package's checks read, the
  `.claude/worktrees/` ignore line, and the rules fragment's sentences
  naming `fm hooks.pre-bash` and `.claude/worktrees/`, since moving
  the sentences would add an import to every `CLAUDE.md`.
- 2026-10-08, 10f's acceptance: after `fm sync` on this repository,
  `git diff --exit-code CLAUDE.md` exits 0. Under `.claude/` and
  `.workshop/fragments/`, every file hashes as on 10e's tree but five:
  the hook shim and the three skills, whose content header names
  their new owner, a one-line difference each, and `kinds.present.md`,
  which names the new package. Found on the way and fixed here: a
  content header's refresh removed the whole comment block it opened
  (the shim's own documentation) and the blank line after an HTML
  header; `strip_header` now removes the header's own lines alone.
- 2026-10-08, the loop on 10f's branch, 10e under it: `fm ci.e2e
  --fresh` passed birth, verified-skip, members, ratchet and the
  scoped leg in 19m51s (pass `20261008T134358Z`). The newborn lists
  `changelog` and `claude`, and its birth wrote `CLAUDE.md`,
  `CLAUDE.project.md`, `.claude/` and each member's seeded
  `CHANGELOG.md` from the dev wheels. `--scenario=release` (pass
  `20261008T135910Z`) merged the newborn's release pull request, each
  member's `## [0.1.0]` entry written by the changelog extension with
  its authors credited through the local forge; the wave then stopped
  at the cpp member's `conan create` on a profile with no compiler,
  #1113's fault and not this change's.
- 2026-10-09, Willem: `fm new.project`'s default list names no
  `claude`; a project chooses its agent's files, and a birth wizard or
  profiles offer them before the first public release.
- 2026-10-09, 11a's first part (#1330), taken without a ruling as
  cheap to reverse:
  - Composition order is requires, `after` and `before`, ties
    alphabetical. It orders the mount (contract 28), the canonical
    list and a combination's name. A phase's order adds its context
    keys and leaves requires out, as the plan's order states.
  - The validity rule reads `requires` transitively: an extension
    knows what its requirements require.
  - A package's entry takes no options, and an extension listed at the
    package level alone declares no `[options]`: no package-level
    extension has one yet, and an option's scope per package is a
    design question for its first case.
  - A declared order with no start falls back to alphabetical in the
    mount, so the sync that repairs it runs; the layering check names
    the cycle.
  - A package-level extension's contract keys are taken in a package's
    contract while the package lists it, and in the root's while any
    package does; a refusal names the list that would take the key.
  - Completion of a combination's name arrives with the first verb
    that takes one, `fm new.package` in 11b; this part lists them.
- 2026-10-09, 11a's second part (#1332), taken without a ruling as
  cheap to reverse:
  - A context key's type is one of `str`, `int`, `bool`, `path`,
    `strs`, `paths` and `table`; `provide` refuses a value of another
    type, and a step provides and reads only the keys its extension
    declares. One extension's `provides` and `reads` never share a key.
  - A failing `pre` counts as run, so its `post` runs and reads the
    failure. A step's failure is an exception; an interrupt stops the
    phase at once, posts included.
  - A `post` that fails after another step failed is named on stderr,
    and the phase raises the first failure.
  - The layering check judges every phase of every package's set, so
    a declaration that cannot run is named before a verb walks it.
- 2026-10-09, 11a's third part (#1333), taken without a ruling as cheap
  to reverse:
  - Eight queries arrive now: `PUBLIC_MODULES`, `COMPILE_COMMANDS`,
    `MODULE_ROOTS`, `CURRENT_VERSION`, `VERSION_FILES`, `REQUIREMENTS`,
    `DISTRIBUTIONS` and `EXECUTABLES`, typed as the kinds' backends
    answer today. `BUILD_PLAN` arrives with the release train's phases
    (13), `TOOLCHAINS` with the toolchains (14), and `REFERENCES` with
    the python extension's reach rule (11b), each when its first
    reader fixes its type.
  - "Must agree" ignores an empty answer; "the nearest by requires" is
    the answer of the one answering extension no other answering one
    requires, and two such refuse; `REQUIREMENTS` refuses a name two
    answers value differently.
  - `public_modules` and `compile_commands` keep answering from the
    kinds until 11b, whose extensions then answer the two queries.
- 2026-10-09, 11a's fourth part (#1335), taken without a ruling as
  cheap to reverse:
  - The development build's record is `.workshop/.cache/run/built.json`:
    each member's fingerprint, its git subtree in the working tree and
    its dependencies' fingerprints, written after each good build. A
    failed build records nothing. Phase 16's affected engine replaces
    the record.
  - The build role runs over each member whole, its checks' claims
    aside, since a build check without claims reads no named file; a
    member no build check judges is never built.
  - A target resolves a package by its directory under `packages/`, its
    path or its distribution's name, before it names an executable of
    the package the command runs in.
  - The `run` phase's inputs are the context's `executable` and
    `arguments`, and its output `exit_code`: the engine's, not keys an
    extension declares.
  - An extension answering `executables` declares a `main` in
    `[phases.run]`, or its declaration refuses.
  - #1299 is fixed here, since `fm run` inside a package needs it:
    `workspace_root` passes over a package's own contract, and starts
    at the running task's directory rather than the process's.

- 2026-10-09, Willem: the PyPI names the remaining phases release are
  claimed in one session, through a temporary repository with a
  placeholder project. PyPI keeps at most three pending publishers at
  once per account and one per repository, workflow and environment
  (Warehouse's unique constraint); a private repository on the free
  plan has no environments, so the repository carries one workflow
  per name. Fourteen projects were created that day, each by its own
  run (`livery-extensions-changelog` by run 37966784377 first, as the
  proof), against the four a day a token's first upload met on
  2026-10-08. The four-a-day schedule is retired. Willem, the same
  day: the limit is an issue only for more than four new projects at a
  time, since a token's first upload still creates up to four a day;
  such a release claims its names first. "First publishes" lists the
  names and the debt.

- 2026-10-09, the release of 10e and 10f shipped. The local rehearsal
  (`fm workflow.release packages/workshop packages/extensions/changelog
  packages/extensions/claude --local`) passed the floor and latest legs
  of all three members in 15 minutes. The armed act raised
  `changelog`'s and `claude`'s floors on the workshop within the set,
  opened release PR #1340 as "chore(release): released 3 packages"
  (#1318's short title, which arming accepted), and merged it after
  13 minutes of CI; the wave, run 37981710154, published
  `livery-workshop` 0.8.0, `livery-extensions-changelog` 0.0.0 and
  `livery-extensions-claude` 0.0.0. PyPI's project JSON served its
  cached placeholder-only copy after the upload; the version endpoint
  and the simple index listed 0.0.0 at once.

- Willem, 2026-10-09: 11b moves the release train's backend calls onto
  phase steps and queries as each backend moves (option (a)), rather
  than a temporary `backend` key in `extension.toml` until phase 13
  (option (b)). (a) writes no code to delete later and reaches one way
  sooner; it makes 11b larger and touches the release train there, so
  the train's properties are pinned first and every slice rehearses
  with `fm workflow.release --local` and runs the loop's release
  scenario before it merges. 11b's slices, 11c among them before the
  registry goes, are under 11b.
- 2026-10-09, decided while fixing #1113, which failed the loop's
  release scenario for every change: conan's default profile is
  checked before `uv sync` builds a native member and before `conan
  create`. The base reaches a kind only through its registration, and
  `fm sync` runs no lifecycle phase yet, so the check rides a new
  `KindRecord.before_install` until slice 3 moves it to the conan
  extension's `sync` step.

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
3. **Branch counts in the line format.** The format is file, line,
   hits. Python's pages today come from coverage.py's arcs, which
   show a partly taken branch.
   - (a) Lines only. Python's partly taken branches vanish from the
     page, and C++ branch data is dropped as well.
   - (b) A line also carries its branches, taken out of total, as
     lcov's `BRDA` records do. gcov, llvm-cov and Cobertura report
     it, so every measurer can fill it. It does not change what the
     floors judge, only what the page shows.

   Recommendation: (b).
