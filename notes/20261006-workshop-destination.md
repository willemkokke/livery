# The workshop's destination: an engine, and the API its extensions use

Status: proposed, awaiting Willem's ruling. Written 2026-10-06 against
`origin/main` at `4f2a5e53`. Nothing here is built. The extensions plan
(`notes/20261002-extensions-plan.md`) stays the one plan; this note
rewrites its phases 10 to 15 against a designed destination, and the
plan takes the rewrite in once ruled.

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

26. **Every registration is data on a declaring module, read at
    mount.** The base reads `CHECKS`, `JOBS`, `SLOTS`, `GUIDANCE`,
    `RELEASE_NOTES`, `RULES`, `CATEGORIES`, `PHASES`, `QUERIES`,
    `ROOT_FILES` and `FRAGMENTS` the way it reads `CHECKS` today.
    Extension code calls no `register_*` function; the base exports
    none. A registry an extension owns (the docs extension's
    generators) is read the same way, through
    `livery.workshop.contributions_for`.
27. **A registry is written at mount and read at use.** No code reads a
    registry while the mount runs, so no extension's correctness
    depends on its position in the list beyond what `REQUIRES` and
    `FOR` state.
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
    its claims reach exists; a workspace extension's `TOOLS` while it is
    listed. A mounted extension whose registrations are unused costs its
    declaring module's import and nothing else.
30. **A public name lives in the root's entry module or a public module
    it declares, is documented, type-complete, pinned by a test and
    judged by the conformance kit.** A package's source never imports
    another distribution's underscore module or name, except through
    the allowance the reach check reads, each entry with its reason.
31. **One `API_VERSION` at a time, no compatibility code.** A record
    grows additively within a version. A removal or a rename bumps the
    version and the workshop's minor; the mount turns a mismatch into a
    sentence naming both versions.
32. **The docs extension owns every word of documentation generation.**
    Pages, generators, handlers, inventories, nav blocks, the members
    policy, the site's config, the publish seam and the `[docs]` table.
    A language contributes to it through `FOR`, never the other way.

## The architecture

### The engine

`livery.workshop` is a composition engine and an extension point API.
It owns:

- the contracts: `workshop.toml` at the root and per package, strict,
  every key declared by its owner;
- package discovery and the workspace graph;
- the mount: the `workshop.extensions` entry point group, the levels,
  `REQUIRES`, `FOR`, options, the API version check;
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
| check (tool) | workspace | checks, page generators, tool configuration fragments, editor contributions, tools, options | `CHECKS`, `GENERATORS`, `FRAGMENTS`, `content/`, `TOOLS`, `OPTIONS` | ruff, basedpyright, mypy, ty, pyrefly, pytest, clang-format, clang-tidy; doxygen (phase 12) |
| language | package | a package's capabilities: phases, queries, categories, seeds, root files, tools, host tools, toolchains, the layering reader, checks of its own; contributions to the docs extension | `PHASES`, `QUERIES`, `CATEGORIES`, `seeds/`, `ROOT_FILES`, `TOOLS`, `CHECKS`, `FOR` | python, cpp, cmake, nanobind, unreal; later rust, go, java |
| ecosystem | package | a `[publish]` artifact and its registry kind, a manifest's requirements, an installer's cache | the same seams as a language, through the `publish` and `requirements` phases and queries | conan; later crates, maven, npm |
| workspace (product) | workspace | checks, CI jobs, slots, guidance, the release notes provider, AST rules, fragments, verbs; a registry of its own that languages contribute to | `CHECKS`, `JOBS`, `SLOTS`, `GUIDANCE`, `RELEASE_NOTES`, `RULES`, `FRAGMENTS`, `contributions_for` | docs, changelog, claude, housekeeping |

An ecosystem is not a fourth level and gets no seam of its own: `conan`
composes with `cpp` and `cmake` the way the plan already states, and
`python` carries PyPI inside it because uv and `pyproject.toml` are the
engine's runtime. A language and an ecosystem differ in which phases
and queries they answer, nothing more.

No `SORT` attribute is declared. The brief's question, whether to
define extension types with a load order between them, is answered by
contract 28 without a new declaration: the level is the order. Every
package-level extension mounts before any workspace extension, so a
language's capabilities are registered before a product reads them,
and a product that names a language's capability at mount gets a
refusal naming the capability instead of an `AttributeError` at verb
time.

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
- a declaring module imports only what its declarations name
  (already the rule for `CHECKS`); every verb and every generator
  imports its library (griffe, a Doxygen XML reader) inside the
  function that runs.

### What the core must not know, and the seam that replaces each

| Knowledge in the core today | Where | Seam in the destination |
| --- | --- | --- |
| the `[docs]` table and its keys | `_docs_contract.DECLARED`, `docs_table` | the docs extension's `CONTRACT_KEYS`; a language's docs contribution declares the keys it reads under `[docs]` (`api`, `python-paths`) |
| the docs jobs' system requirements and the pages seam in the CI render | `_ci_generate` reads `docs_requirements`, `publish_seam` | `Job.installs` (system packages the job installs before entering) and `Job.deploy` (the seam's value), set by the contributing extension on its `JOBS` entry |
| pages hosting asserted at `fm workflow.configure` | `_workflow_tasks` reads `publish_seam` | `SETUP`: steps an extension contributes to the repository's configuration, run by `workflow.configure` |
| the site URL in the composed `pyproject.toml` | `_templates` reads `docs_table` for `docs_site_url` | the `project.urls` slot, merged by key; the docs extension contributes `Documentation` |
| which categories the site reads, for the docs job's skip | `_provenance.site_reads` | `Job.inputs`, the same `Inputs` record a check declares; the shell's skip rule reads it |
| the API extractor on a kind | `_kinds.Extractor`, `KindRecord.extractor`, `kind_extractor` | `livery.extensions.docs.Generator`, declared in `GENERATORS` by a generator extension of its own (`mkdocstrings`, `doxygen`), naming the languages it extracts for, and read by the docs extension through `contributions_for("docs")`; its site configuration a `zensical.toml` fragment |
| coverage pages on a kind | `KindRecord.coverage_pages` | a `Generator` of the same contribution |
| the nav block format | `_navblocks`, `rewrite_nav_block` in the api | `livery.extensions.docs.write_nav_block`, `nav_block_markers` |
| the site's override template as a rendered file | `_site_files`, read by `_ci_generate` | a whole-file fragment the docs extension ships under `content/root/overrides/main.html`; `_site_files` goes |
| the docs tree embedded into a wheel | `_docs_contract.module_docs`, read by the python backends | the python extension's `build` phase; it reads the `prose` category's directory, which is the engine's layout, not generation |
| whether a package declines its reference, and its module root | `_docs_contract.declines_api`, `module_root` | the mkdocstrings extension's keys (`[docs] api`, `[docs] python-paths`), and the `MODULE_ROOTS` query |
| the python-only checks `lint.docrefs`, `lint.docstrings` in the docs extension, filtering by kind chain | `docs/_checks.py` | `CHECKS` of the mkdocstrings extension: both exist because the reference publishes every docstring, and `lint.docrefs` resolves names the way that reference does, through griffe |
| git-cliff, `cliff.toml`, `CHANGELOG.md` | `_cliff`, the `cliff.toml` fragment | the changelog extension; `RELEASE_NOTES` on its module; the base keeps the provider protocol and asks |
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

### The declaration vocabulary

Attributes of an extension's declaring module, every one optional,
each read at mount. Nothing here is imported; the types the values are
built from are in the next table.

| Attribute | Value | Sort | Users today, or the phase that brings one |
| --- | --- | --- | --- |
| `API_VERSION` | int | every | the eight tool extensions, docs |
| `LEVELS` | `("workspace",)`, `("package",)` or both | every | the eight, docs |
| `PLUGIN` | the footman plugin carrying the verbs | every | docs |
| `REQUIRES` | extensions that must be listed before it | every | housekeeping (phase 12) |
| `COMPATIBLE` | extensions it combines with, from either side | package | phase 11 |
| `BEFORE`, `AFTER` | order within a phase against named extensions | package | phase 11 |
| `TOOLS` | the tools its verbs need | every | forge's dev plugin (as a plugin) |
| `OPTIONS` | option name to what it turns on | every | basedpyright |
| `CONTRACT_KEYS` | `Declared` records | every | docs |
| `CHECKS` | `CheckRecord` tuple | every | the eight, docs |
| `GENERATORS` | `Generator` tuple, each naming the languages it extracts for | check, language | mkdocstrings (phase 11), doxygen (phase 12) |
| `FRAGMENTS` | dynamic `Fragment` records; files ship under `content/` | every | claude's `CLAUDE.md` (phase 10), docs' override template (phase 12) |
| `JOBS` | `JobContribution` tuple | workspace | docs (phase 10 moves it onto data) |
| `SLOTS` | `Slot` records it declares; `CONTRIBUTIONS` fills others' | workspace, check | docs (`docs.members`, `docs.theme`), pytest (dev group lines) |
| `GUIDANCE` | `Section` and rendered `Prose` records; files ship under `content/fragments/` | every | the base's own sections; housekeeping's prose (phase 12) |
| `RELEASE_NOTES` | a `ReleaseNotes` provider | workspace | changelog (phase 10) |
| `RULES` | `AstRule` tuple, each naming the language whose reader it reads | workspace, language | housekeeping's reach rule (phase 12) |
| `SETUP` | repository configuration steps for `workflow.configure` | workspace | docs' pages hosting (phase 12) |
| `CATEGORIES` | category tables per language | language | python, cpp (phase 11) |
| `PHASES` | contributions to the lifecycle phases, `pre`, main, `post` | package | phase 11 |
| `QUERIES` | `Query` to answering callable | package | phase 11 |
| `ROOT_FILES` | the files written at the root while a package of it exists | package | cmake and conan (phase 11), from `KindRecord.root_files` |
| `FOR` | target extension to contribution module | every | pytest for docs, the coverage pages (phase 11) |
| `REPLACES`, `DELETES` | `"<owner>:<name>"` to reason | every | the descendant chain's brand |

A contribution module (the value of a `FOR` entry) carries the same
attributes. The base registers what it owns from it (`CHECKS`,
`CONTRACT_KEYS`, `RULES`) under the contributor's name; the target
reads the rest through `contributions_for`.

### The names

Grouped by the sort that needs them. "Users" names today's importer or
the phase that brings one.

**Every extension.**

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `__version__` | the workshop's version | tests | `livery.workshop` |
| `Declared` | one contract key: contract, path, types, values | docs (private today) | `livery.workshop` |
| `Fragment` | a dynamic fragment: owner, target, render | the eight (check fragments), claude (phase 10) | `livery.workshop` |
| `Package`, `Edge` | a discovered package and a graph edge | the eight, docs | `livery.workshop` |
| `discover_packages`, `verify_workspace`, `workspace_root` | the workspace and its graph | docs, tests | `livery.workshop` |
| `read_contract` | one contract's declared keys, judged | docs (`load_contract` today) | `livery.workshop` |
| `extension_names` | the listed extensions, in order | tests | `livery.workshop` |
| `contributions_for` | the contribution modules grafted for a target extension | docs (phase 11) | `livery.workshop` |
| `generated_header` | the provenance header for a file an extension writes outside the engine | docs (private today) | `livery.workshop` |
| `ci_run` | the CI run's context, or None at a desk | docs (`run_context` today) | `livery.workshop` |
| `RunContext` | its type | docs | `livery.workshop` |
| `Option`, `check_option` | an option a package may set, and its value | pytest | `livery.workshop` |
| `Slot`, `slot`, `NEAREST`, `UNION`, `MERGE` | a declared slot, its composed value, the composition rules | docs (`_slots` today), the base's `pyproject.toml` template | `livery.workshop` |
| `testing` | the conformance kit: `Subject`, `Clause`, `CLAUSES`, `Violation`, `judge`, `builtin_subject` | every extension's suite | `livery.workshop.testing`, declared in `livery.workshop` |

**Check extensions** (the model that works; unchanged but for two
names).

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `CheckRecord`, `Claim`, `GateContext` | a check, what it claims, what a run hands it | the eight | `livery.workshop` |
| `PACKAGE`, `PACKAGES`, `PATHS`, `WHOLE`, `NONE` | the scope and narrowing vocabulary | the eight; docs needs `NONE` | `livery.workshop` |
| `scoped_paths`, `scoped_files`, `scoped_packages` | what a run reaches | the eight | `livery.workshop` |
| `selected_files` | the changed files a workspace check with `inputs` judges this run | docs (`_checks.selected_files` today) | `livery.workshop` |
| `Inputs`, `Changes` | what a workspace check reads, and what changed | docs (`_influence` today) | `livery.workshop` |
| `run_batched` | the fewest tool calls under the command-line limit | ruff, the type checkers | `livery.workshop` |

**Language and ecosystem extensions** (phase 11 brings every user).

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `Phase`, `PhaseContext` | a lifecycle phase and what its steps share | python, cpp, conan | `livery.workshop` |
| `Query`, `answer` | a typed question about a package, and its answer across the package's extensions | basedpyright (`public_modules`), clang-tidy (`compile_commands`), docs | `livery.workshop` |
| `PUBLIC_MODULES`, `COMPILE_COMMANDS`, `MODULE_ROOTS`, `CURRENT_VERSION`, `VERSION_FILES`, `REQUIREMENTS`, `DISTRIBUTIONS`, `BUILD_PLAN`, `EXECUTABLES`, `TOOLCHAINS`, `REFERENCES` | the queries the base defines, each a `Query[T]` | the same | `livery.workshop` |
| `Category` | one row of a category table | python, cpp | `livery.workshop` |
| `Seed` | nothing: seeds are files under `seeds/` | | |
| `Stamper` | what the `stamp` phase writes through | python, cpp, conan | `livery.workshop` |
| `AstRule`, `ParsedModule`, `RuleContext` | a rule over a language's reader | housekeeping | `livery.workshop` |
| `Toolchain` records | phase 14 names them | cpp | `livery.workshop`, phase 14 |

**Workspace extensions.**

| Name | Purpose | Users | Lives in |
| --- | --- | --- | --- |
| `Job`, `Entry`, `JobContribution` | a CI job, its entries, its contribution to a builtin point | docs | `livery.workshop` |
| `Section`, `Prose`, `AGENT`, `HUMAN`, `guidance` | the guidance sections, a fragment, the audiences, and the composed set for one audience | docs (human pages), claude (agent file) | `livery.workshop` |
| `ReleaseNotes`, `release_notes` | the provider protocol and the mounted provider | changelog, docs (the release view) | `livery.workshop` |
| `ci_changes` | the paths changed since the base a CI run measures from, as `Changes` | docs (the docs job's skip; `GitOps` and `ci_affected_base` today) | `livery.workshop` |
| `forge_repository` | this workspace's repository name on its forge | docs (publish by container or ssh) | `livery.workshop` |
| `registry` | the resolved registry target of a kind | docs (the container registry) | `livery.workshop` |
| `RegistryTarget` | its type | docs | `livery.workshop` |
| `Setup` | one repository configuration step | docs | `livery.workshop` |

**The docs extension's own public API**, `livery.extensions.docs`,
for languages contributing to it and for generators in any package:

| Name | Purpose | Users |
| --- | --- | --- |
| `Generator` | a page generator: name, languages, claims, tools, options, `run(package, out) -> pages`; its site configuration is a `zensical.toml` fragment of its extension | mkdocstrings (phase 11), doxygen (phase 12), the task reference and coverage pages |
| `Page` | one generated page: path, title, nav position | the same |
| `write_nav_block`, `nav_block_markers` | emit a nav block beside generated pages, and place it | toolroom-bench, the task reference |
| `GENERATED` | the generated tree's name under a package's `docs/` | generators |
| `PUBLIC_MEMBERS`, `ALL_MEMBERS` | the members policy's values | mkdocstrings |

### What leaves the public surface, and the break each causes

| Name | Goes to | Who breaks | Phase |
| --- | --- | --- | --- |
| `rewrite_nav_block` | `livery.extensions.docs.write_nav_block` | toolroom-bench (ours); any third-party generator | 10 |
| `public_modules`, `compile_commands` | `answer(package, PUBLIC_MODULES)`, `answer(package, COMPILE_COMMANDS)` | basedpyright, clang-tidy (ours) | 11 |
| `run_suites`, `kind_examples`, `workspace_suite` | `livery.extensions.python` | pytest (ours), which then requires `python` | 11 |
| `mount_extensions` | private; the mount is the plugin's | this repository's tests | 10 |
| `API_VERSION` 1 | 2 | every extension built against 1 refuses at mount with a sentence naming both versions | 10 |

Each is a break before 1.0: a minor bump of the workshop, every
extension of ours re-released in the same wave (the ruling of
2026-10-04 on releasing as required), each changelog stating it.

### Extraction, registered like a check

The brief's first thought: expose API documentation extraction the way
checks are exposed, each extractor naming what it operates on, and
factor out what the two share. The answer, in `Generator`:

- A generator is a record with `languages` (the package-level
  extensions it extracts for, the way a check names `kinds`), `claims`
  (categories and suffixes, the same `Claim` a check carries), `tools`,
  `options` and a `run`. The
  docs build hands it a package and the output directory under the
  package's generated tree, and it returns the pages it wrote. The
  site's configuration is the fragment engine's: `zensical.toml` is a
  composed file the docs extension owns, rendered by a dynamic fragment
  of its own, and a generator's extension contributes its tables (the
  mkdocstrings handler block, its options and inventories) as a TOML
  fragment in extension order. No record field carries configuration.
- A generator lives with the extension that owns its tool, as a check
  does: `lint.ruff` lives in ruff and names python, `build.configure`
  lives in the cpp extension. Doxygen reads C, C++, Java and more, so
  its generator is `livery-extensions-doxygen`, a workspace extension
  requiring `docs`, declaring `languages=("cpp",)` today and `java`
  when a java extension exists. The Python reference is
  `livery-extensions-mkdocstrings`, a workspace extension requiring
  `docs`, declaring `languages=("python",)`, its generator writing the
  handler stubs, its fragment the handler tables of `zensical.toml`,
  its checks `lint.docrefs` and `lint.docstrings`, its dependency
  `griffelib`, its keys `[docs] api` and `[docs] python-paths`. A
  workspace that lists `docs` without it renders a site with no API
  reference and no handler block. Every generator is an extension of
  its own, and no language extension contributes to docs.
  `contributions_for("docs")` returns every mounted module that
  declares for docs: the declaring module of an extension that requires
  `docs`, and a `FOR` module such as pytest's coverage pages.
- Matching is by `languages` against the package's listed extensions,
  then by the claims within the package: `python+nanobind+cmake` meets
  mkdocstrings' generator over its `.py` sources and doxygen's over its `.h`
  and `.cpp` sources, each listed in its own section. That is the
  brief's "file type it can operate on", through the claims model
  checks already use. Two mounted generators naming one language refuse
  at mount, naming both, unless the package's contract picks one.
- What checks and generators share is already public: `Claim`, the
  scoping of files to a package, `Option`, the tools-in-use rule,
  `run_batched`. There is no shared base class: a check returns a
  verdict and a generator returns pages, and a class holding only
  `claims`, `tools` and `options` would be a third thing to name.
- One concept replaces three: the kind's `extractor`, the kind's
  `coverage_pages`, and the contract's `[docs] generators` verbs. A
  package's declared generator verb is a `Generator` whose `run` calls
  the verb; the task reference is the docs extension's own generator
  over every package that advertises `footman.tasks`.
- Zensical runs only its native plugins, and its mkdocstrings support
  covers the Python handler alone. So mkdocstrings' generator is the
  one that writes handler stubs; every other writes Markdown
  (Doxygen XML turned into pages for C++, `rustdoc` JSON or a Markdown
  renderer for Rust, `gomarkdoc` for Go) or places a rendered tree and
  links it without cross-references (Javadoc, whose `element-list` is
  no `objects.inv`). The record carries both shapes; the plan's open
  item 11 closes on it.

### One docs mechanism across footman and workshop

The brief's fourth thought. What exists twice today:

| Job | footman | workshop docs extension | Destination |
| --- | --- | --- | --- |
| a task tree as pages | `livery.footman.markdown.render_site`, public; `fm docs site` renders the invoking project's tree | `_taskref` renders one provider in isolation by spawning `fm --tasks-file=<probe> --json --list`, then `render_site` | one renderer: `livery.footman.docs.site` takes `provider=` and renders that plugin in isolation in-process; the task reference generator calls it and assembles the nav. A change to footman, allowed by the brief |
| footman's API reference | `fm footman.pages` writes a curated page from `_API_SECTIONS`, validated against `__all__`; footman's contract sets `[docs] api = false` | the mkdocstrings extension's generator writes one page per module | the curated page, `_API_SECTIONS`, `_API_OMITTED`, `_api_markdown` and `api = false` go; footman's reference is the generator's like every package's; the curated grouping becomes an authored page linking into it |
| errors-and-notes page, the config, notes and globals tables, the example render, the latest-release admonition | `fm footman.pages` | | stays footman's generator verb, declared as today; a `Generator` whose `run` calls it |

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
| `_checks` | `CheckRecord`, `GateContext`, `PACKAGES` | public already |
| | `NONE`, `selected_files` | public |
| | `check_for`, `register_check` | go: `CHECKS` is data; a check reads its own record from `GateContext` |
| `_contract` | `load_contract` | public `read_contract` |
| `_contract_keys` | `Declared` | public |
| `_docs_contract` | everything | moves into the docs extension; `site_reads` becomes `Job.inputs`; `module_docs`, `declines_api`, `module_root` go to the python extension |
| `_extensions` | `workspace_root` | public already |
| `_forge_lane` | `remote_repo_name` | public `forge_repository` |
| `_git_ops` | `GitOps`, `GitError` | replaced by `ci_changes` |
| `_influence` | `Changes`, `Inputs` | public |
| `_kinds` | `Extractor`, the members policy, `kind_extractor`, `kind_coverage_pages` | move into the docs extension as `Generator` and its policy |
| | `kind_chain`, `kind_names` | go with the python checks moving to python's contribution |
| `_navblocks` | all | moves into the docs extension |
| `_packages` | `Package`, `discover_packages` | public already |
| | `member_depth`, `package_directories` | `Package.member` and `Package.directory` answer both |
| `_points` | `Job`, `Entry`, `contribute_job` | `Job`, `Entry`, `JobContribution` public; `JOBS` data |
| `_prose` | `Prose`, `HUMAN`, `sections`, `fragments`, `shipped`, `repository_fragments` | `Section`, `Prose`, `HUMAN`, `guidance` public |
| `_provenance` | `format_header`, `generated_header` | `generated_header` public |
| `_quality` | `ci_affected_base` | folded into `ci_changes` |
| `_registries` | `resolve_registry` | public `registry` |
| `_release_notes` | `release_notes` | public |
| `_site_files` | `register_site_file` | goes: a whole-file fragment |
| `_slots` | `register_slot`, `NEAREST`, `composed` | `SLOTS` data; `slot`, `NEAREST` public |
| `_state` | `run_context` | public `ci_run` |

## Stability and proof

**What a public name promises.** Its docstring is published; the
typecomplete check verifies it; `tests/test_namespaces.py` pins the
root's `__all__` and that every public module under a root is declared
there; the conformance kit judges every record type an extension
builds. Before 1.0 a name may change in a minor release; the changelog
states the break and the name that replaces it, and every extension of
ours moves in the same wave. After 1.0 a change is a major bump.

**How an extension declares its target.** `API_VERSION` on its
declaring module, as today. The base supports one version
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
- `declarations-are-data`: every attribute of the declaration
  vocabulary the subject declares is a tuple or map of the record types
  the api exports, and the module registers nothing at import (the kit
  imports it under a recorder, as the walk clause does for checks).

And one drive: `fm ci.e2e --extension=<name>` births a project listing
the base and the extension from this checkout's wheels and runs its
gate. An extension that reaches a private name the wheel does not ship
fails there.

**Where #1204's check runs, and the ratchet.** The reach check is an
`AstRule` on the housekeeping extension's python contribution
(`RULES`), read by `layering.imports` over python's `REFERENCES` reader
in the one parse (contract 16). It judges every package's sources, never
its tests, per changed file and whole when the graph changed, and
refuses each reach by file and line. Its allowance is a contract key
the housekeeping extension declares, `[housekeeping] reaches`, a list of
`{ from, into, name, reason }` tables: facts of the workspace in the
contract, the rule in code. The ratchet: the check also refuses an
allowance entry that no source uses, so the list can only shrink, the
way the vocabulary allowance does. Until phase 12 lands the extension,
this repository carries the same scan as a root test over the allowance
written as data in the test, seeded from the table above in phase 10.

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

**10b, the names.** Deliverables:

- Every name in "The names" above that needs no language extension:
  `Declared`, `NONE`, `selected_files`, `Inputs`, `Changes`,
  `read_contract`, `generated_header`, `ci_run`, `RunContext`,
  `ci_changes`, `Slot`, `slot`, `NEAREST`, `UNION`, `MERGE`, `Job`,
  `Entry`, `JobContribution`, `Section`, `Prose`, `AGENT`, `HUMAN`,
  `guidance`, `ReleaseNotes`, `release_notes`, `forge_repository`,
  `registry`, `RegistryTarget`, `Setup`, `contributions_for`.
- The declaration attributes `JOBS`, `SLOTS`, `CONTRIBUTIONS`,
  `GUIDANCE`, `RELEASE_NOTES`, `RULES`, `SETUP`, `FRAGMENTS`; every
  `register_*` the base exports goes; the docs extension, still in the
  wheel, declares instead of registering.
- `Job.installs`, `Job.deploy`, `Job.inputs`; `_ci_generate`,
  `_workflow_tasks` and `_provenance` stop reading the `[docs]` table;
  the `project.urls` slot.
- `rewrite_nav_block` leaves for `livery.extensions.docs`;
  `mount_extensions` leaves the public surface; `API_VERSION = 2`; the eight tool
  extensions and this repository move; the wave releases them.
- The pin tests first: `test_api_exports_what_the_package_exported`
  lists the new set; the reach scan as
  `tests/test_private_reaches.py` with today's table as its allowance.

Acceptance, refusals first:

- `test_an_extension_declared_for_api_version_1_refuses_naming_both`,
  `test_a_declaration_that_registers_at_import_fails_the_kit`,
  `test_a_new_private_reach_refuses_naming_the_file_and_line`.
- `fm check` exits 0.
- `fm ci.e2e --extension=ruff --fresh` is green on the re-released
  wheels.

**10c, footman's and toolroom's seams.** Deliverables: `host()`,
`colored` styles, `wants_color`, `builtins`, `project_builtins`,
`directory_variable`, `tasks_file_name` in footman's entry module;
`colour_controls` in toolroom's; the bench on footman's `fetch`; the
workshop's seven modules on the new names. Acceptance: the reach test's
allowance holds the forge row alone; `fm check` exits 0;
`fm workflow.release --local` releases footman, toolroom and the
workshop.

**10d, changelog ships apart.** `livery-extensions-changelog`: the
git-cliff provider as `RELEASE_NOTES`, `cliff.toml` its fragment for
every package, `git_cliff` its tool, `CHANGELOG.md` created on the
first record; `_cliff` leaves the base. Acceptance:
`test_a_release_without_the_changelog_extension_writes_no_notes`;
`fm workflow.release --local` on this repository writes the same
entries as before.

**10e, claude ships apart.** `livery-extensions-claude`: `CLAUDE.md`
and `.workshop/fragments/` as dynamic fragments over
`guidance(root, AGENT)`, skills, hooks and `settings.json` as its
content, `hooks.pre-bash` its verb; `_shipped_files._agent_outputs`
and `_hooks` leave the base. Acceptance: a project born without
`claude` has no `.claude/` and no `CLAUDE.md`
(`test_a_project_without_claude_writes_no_agent_file`); this
repository's `.claude/` and `CLAUDE.md` are byte-identical before and
after, by `git diff --exit-code` after `fm sync`.

### Phase 11: package composition and the language extensions

**11a, the model** (the plan's 11a, unchanged): `COMPATIBLE`,
`BEFORE`, `AFTER`, the validity rule, the canonical set,
`fm extensions --combinations`, `PHASES` with `pre` and reversed
`post`, the declared context, `QUERIES` and `answer`, `fm run`.

**11b, the language extensions** (the plan's 11b, with one change):
`python`, `cpp`, `cmake`, `conan`, `nanobind`, `unreal`; the backends
move; `KindRecord`, `register_kind` and the kind registry go;
`CATEGORIES` and `ROOT_FILES` as data; `run_suites`, `kind_examples`
and `workspace_suite` become `livery.extensions.python`, and the
pytest extension requires `python`; `public_modules` and
`compile_commands` become queries. The change: the plan's sentence
that the conan extension owns the conan cache is replaced. Since #1112
a rendered `conanws.yml` resolves siblings from their sources and the
machine's package cache is shared; what 11b still owes is that a
container build never reuses a binary built against the host's glibc.

**11c, the generators.** `Generator` and the members policy in
`livery.extensions.docs` (the extension is still in the wheel);
`zensical.toml` becomes a composed file of the fragment engine, the
docs extension's dynamic fragment with contributed tables; `Extractor`
and `KindRecord.coverage_pages` go. `livery-extensions-mkdocstrings`,
its own distribution requiring `docs`: the python generator, the
handler tables as its fragment, `lint.docrefs` and `lint.docstrings`,
`griffelib`, the keys `[docs] api` and `[docs] python-paths`; the
workshop wheel drops `griffelib`. pytest's `FOR = {"docs": ...}`
module declares the coverage pages generator. The docs extension reads
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
`GUIDANCE`; the reach rule as `RULES` for python with
`[housekeeping] reaches` as its allowance (this repository's root test
from 10b retires); the layout rules: a namespace
`__init__.py`, a root with public names and no `__init__.py`, a
distribution not named after its import path. `api` stays an ordinary
module name. Acceptance:
`test_an_unused_allowance_entry_refuses_naming_it`,
`test_a_new_private_reach_refuses_naming_the_file_and_line` in the
extension's suite; a project born without `housekeeping` lists no
mypy, ty or pyrefly, in the conformance kit; `fm check` on this
repository runs the rule and exits 0.

**12c, the C++ reference.** The pending spike first: Doxygen XML to
Markdown before the Zensical build, measured on a native member of this
repository. Then `livery-extensions-doxygen`: a `Generator` with
`languages=("cpp",)`, claiming the native sources, `doxygen` its tool,
pages under the package's generated tree. Acceptance: the spike's note quotes the pages rendered and the
build time; `fm docs.build` on this repository renders the cpp member's
reference. If the spike finds no workable route, 12c becomes a
follow-up issue and the plan's open item 11 records why.

**12d, footman's docs onto one mechanism.** `livery.footman.docs.site`
takes `provider=`; the task reference generator calls it; footman's
curated API page and `[docs] api = false` go, its reference rendered by
the mkdocstrings extension's generator; the rest of `fm footman.pages` stays a declared
generator. Acceptance: `fm docs.build` renders footman's reference
with every exported name present (`test_the_reference_lists_every_export`);
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
| `register_check`, `contribute_job`, `register_slot`, `register_site_file`, `register_release_notes`, `register_fragment`, `register_section`, `register_ast_rule`, `register_categories`, `register_kind` | declaration attributes read at mount (phase 10, 11) |
| `_docs_contract` in the base | the docs extension's keys and `Job.installs`, `Job.deploy`, `Job.inputs` (phase 10, 12) |
| `KindRecord.extractor`, `coverage_pages`, `[docs] generators` as three mechanisms | `Generator` (phase 11c) |
| `_site_files` | a whole-file fragment (phase 12a) |
| `tests/test_private_reaches.py` at this repository's root | the housekeeping extension's rule and `[housekeeping] reaches` (phase 12b) |
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
  to 10c, 11c, 12c and 12d are new.
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

1. **Mount order.** (a) Contract 28: package-level extensions before
   the workspace list; it does not cover a workspace extension that
   wants to precede a language in composition, which none does today.
   (b) Listed order only, as today, with every registry read at use:
   less rule; it does not cover a refusal at mount for a wrong
   capability name, which then surfaces at verb time.
   Recommendation: (a).
2. **Who owns the generator registry.** (a) The docs extension, through
   `contributions_for`; it does not cover a second product that wants
   page generators (none is planned). (b) A generic "generators"
   registry in the base; it does not cover ruling 1, since "page",
   "handler" and "inventory" are documentation words. Recommendation:
   (a).
3. **Where the reach check lives.** (a) The housekeeping extension's
   python rule with `[housekeeping] reaches`; it does not cover a
   workspace that lists no housekeeping, which then has no reach
   check. (b) The base's `layering.imports`, as a layering rule
   ("dependencies point downward, through public names"); it does not
   cover ruling 1, since "underscore name" is Python. Recommendation:
   (a); the conformance kit's `public-surface` clause covers an
   extension's own suite either way.
4. **The hosted lane.** (a) The `host()` seam in footman's api; it does
   not cover a future footman that changes how a run is hosted, which
   then changes a public name. (b) A named exception in the allowance;
   it does not cover ruling 3's "conservative with exceptions", and
   every later reader re-justifies it. Recommendation: (a).
5. **The API version policy before 1.0.** (a) One supported version,
   bumped on any removal or rename, every extension of ours re-released
   in the wave; it does not cover a third-party extension between two
   workshop releases, which refuses with a sentence until it moves.
   (b) The base accepts a range; it does not cover contract 4's "no
   compatibility code". Recommendation: (a).
6. **The C++ reference route.** (a) Doxygen XML to Markdown as cpp's
   generator (phase 12c, after the spike); it does not cover
   cross-references into C++ names from Python pages, which need an
   inventory the route does not produce. (b) A placed Doxygen HTML
   tree linked from the nav; it does not cover the site's theme or
   search. Recommendation: (a), the spike deciding; the `Generator`
   record carries both shapes.
7. **footman's curated API page.** (a) Delete it; the generator renders
   footman's reference (phase 12d); it does not cover the curated
   grouping, which becomes an authored page. (b) Keep both; it does not
   cover "one way". Recommendation: (a).
8. **Docs ships apart after the language extensions** (phase 12a after
   11). (a) As written; it does not cover a workspace wanting the docs
   extension as a wheel of its own before phase 12. (b) Docs ships
   apart in phase 10 with its python parts inside it, which move to
   the mkdocstrings extension in 11c: two moves of the same code.
   Recommendation: (a).
9. **Keys a contribution declares under its target's table.**
    (a) The mkdocstrings extension declares `docs.api` and
    `docs.python-paths` under `[docs]`, live while both are listed; it
    does not cover a reader of `[docs]` that does not know which keys
    are a language's, which `fm explain` must say. (b) Each language
    keeps its own table, `[python] docs-paths`; it does not cover the
    reader's expectation that documentation settings sit under `[docs]`.
    Recommendation: (a).
10. **An ecosystem as a sort.** (a) No sort and no seam of its own, as
    written; it does not cover a reader who wants `fm extensions` to
    label one. (b) A `SORT` attribute for the listing; it does not cover
    contract 26's rule that the base reads only what it acts on.
    Recommendation: (a); the listing can say what an extension
    registers.
11. **#1187's remaining rows** (`records/` and `tools.graphs/`): the
    bench's and the store's, outside this design. Owner: the issue.
12. **#1200's foundation library**: whether the advisory lock, the pid
    probe and the atomic write join one distribution below footman.
    This design adds no lock; phase 16's shared temp roots and two
    gates in one checkout are the cases it would serve. Owner: Willem,
    after the review.
