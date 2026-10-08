# Changelog

## [0.7.0] - 2026-10-08

### Added

- Forge lookups ask each backend's narrowest query and stop reading once they have what they need: pull requests by branch and commit, runs by workflow and limit, tags by prefix by @willemkokke
- The docs extension declares its checks and slots in extension.toml by @willemkokke
- The docs extension declares its CI jobs in extension.toml, and the CI render asks each job what it installs and where it deploys instead of reading the [docs] table by @willemkokke
- A CI job declares the files its entries read, and fm ci.run skips them on a pull request that changed none, so the base no longer decides what the site reads by @willemkokke
- Fm sync composes each contract's JSON Schema under .workshop/schema from the base's and the listed extensions' keys, and a composed .taplo.toml points the editor at them by @willemkokke
- Fm explain names the schema a contract is judged by, with the owners whose keys it holds by @willemkokke
- The conformance kit imports every reference a declaration names in a process of its own, and a module that registers at import fails it by @willemkokke
- The conformance kit holds every task a CI job's entries name to the tasks the extension defines, read from its sources by AST by @willemkokke
- The conformance kit checks that a check's claims name known categories and an extension's plugin is an installed entry point by @willemkokke
- Task names link to their pages through footman's Invocation.docs_url, set by a pre_tasks hook: the docs extension's from [docs] site-url, now its own key, and the base's from [workspace] docs-url by @willemkokke
- The workshop exports the names an extension reads the workspace by, and the docs extension reads it through them by @willemkokke
- The workshop exports guidance, its readers and Prose, ci_changes and contributions_for, and the docs extension reads its prose through guidance by @willemkokke
- The docs extension's own public API serves the nav block helpers and the generated tree's name, rewrite_nav_block leaves the workshop, and toolroom-bench writes its block through the extension by @willemkokke
- The site's packages sit under one Packages entry, placed by import path, folder or [docs] name and ordered by their dependencies, with a generated landing page by @willemkokke
- A check whose verdict is its tool's exit code is its tool's words, run by the workshop, and six checks become words by @willemkokke
- No package reaches another's privates: footman exports host, CommandView, styled and its brand's names, toolroom its colour and stub tables by @willemkokke

### Fixed

- A bare wave dispatch reads the release record on the base, so a checkout behind it never sends the wave to a squash a later release took over by @willemkokke
- The release wave uploads new projects one at a time, and a registry's new-project limit stops it with the projects still to create and the re-dispatch by @willemkokke
- The release train retries an unreadable poll while it waits for the wave, and a spent budget names the forge and the command that follows the wave by @willemkokke
- Fm submit names a push the forge rejected for its own reason in git's words, and keeps the add-a-commit advice for a branch behind origin by @willemkokke
- A git revision reaches git whole on Windows: the workshop spells none with a caret, which cmd.exe drops on the way through the store's .cmd launcher by @willemkokke
- A check under [for.<target>] registers under the options its owner's listing turns on, so one with listed-with registers at all by @willemkokke
- Fm start names the tree that holds a started branch, drops a branch no tree holds whose pull request merged and took its tip and starts fresh, and re-enters any other such branch on its own tip by @willemkokke
- Fm sync removes what an older delivery left in .workshop/fragments, the materialiser's digest list and its managed ignore file, and names each by @willemkokke
- Nobody imports an extension but the workshop's mount: toolroom-bench writes its tools nav block as data, the docs extension's public names go, and a test holds the rule by @willemkokke
- A whole mypy run checks what a narrowed one does: mypy.ini names only the member directories that hold a python file by @willemkokke

### Changed

- The contract judge validates against the composed JSON Schema, in its own words, and an unlisted extension's key stays in the schema naming its owner by @willemkokke
- The base declares its own contract keys in contract.toml, in the grammar an extension declares its own by @willemkokke
- Every contract key the base or an extension declares carries a doc, which the composed schema shows on hover, and the conformance kit keeps it so by @willemkokke
- The tool store's table, lock and verbs take toolroom's name, and the store ships the table's schema and its own reader by @willemkokke

## [0.6.0] - 2026-10-07

### Added

- A package may live in a group directory under packages/, its member and receipt taking the longer path, and a seed's import path follows its distribution name by @willemkokke
- Adding or removing a package affects that package alone, not every package through the root files by @willemkokke
- Fm sync --locked changes nothing a commit holds, CI is set up by it, and fm submit checks the locks before it pushes by @willemkokke
- Each workspace check runs when the files it reads change, on those files, and the root files no package reads affect no package by @willemkokke
- A rate-limited forge response names when the budget returns, and a watch waits for it and slows while it runs low by @willemkokke
- The local gate resolves the cross-references in docstrings as the API site does by @willemkokke
- A checkout that never synced is set up by its first command, fm sync --frozen changes nothing a commit holds, fm env.check judges this host's tools, and lint.docrefs reads any source layout by @willemkokke
- CI's check legs measure a change from the nearest tree CI's record proves, an earlier push's merge rebuilt among them, as the local gate does by @willemkokke
- Ruff runs as its own extension, a birth finishes in the newborn's own fm, and the local loop tests an extension before its release by @willemkokke
- Fm ci.e2e --extension=<name> tests one extension in isolation: a birth that lists it, the members of its checks' kinds, the gate on the runner by @willemkokke
- Basedpyright runs as its own extension, with type completeness as its option, and an extension's entry turns its options on by @willemkokke
- Mypy runs as its own extension, configured by the root mypy.ini, with a cache per platform under the workshop's state by @willemkokke
- Ty runs as its own extension, configured by the root ty.toml, and recommends its editor extension by @willemkokke
- Pyrefly runs as its own extension, configured by the root pyrefly.toml, and the base checks no types by @willemkokke
- Clang-format runs as its own extension, with each native package's .clang-format, and a package's files are its checks' kinded fragments by @willemkokke
- Clang-tidy runs as its own extension, over the compilation database its package's kind says the build writes by @willemkokke
- Pytest runs as its own extension, configured by the root pytest.toml and .coveragerc, and the python kind's test runner collects only the packages it is handed by @willemkokke
- A check's own verb hands the tool it wraps the words after --, and fm check and the role verbs refuse them, naming the verbs that take them by @willemkokke
- Conan members resolve from their sources through a rendered conanws.yml instead of editables registered in the conan home, so checkouts never collide and nothing outlives a workspace by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke
- Every extension declares itself in extension.toml, read at mount without importing it, and the check records leave the public names by @willemkokke

### Fixed

- The local loop passes on a fresh environment again, and a build leaves no docs copy in the source tree by @willemkokke
- Fm start is a verb again: its task decorator sits on start, not on the helper inserted above it by @willemkokke
- Work started inside a worktree lands beside the rest, and an abandon says where the checkout is by @willemkokke
- A rebase or merge whose only conflicts are the render's receipts merges them key by key and goes on by @willemkokke
- A dev build's changelog excerpt asks the forge for no author, and a refused lookup tells a spent API budget from a refused token by @willemkokke
- A sync that installs an extension the workspace lists continues with it mounted, so one sync renders its files by @willemkokke
- The workshop wheel's isolated release legs pass: it declares griffelib, and its tests need no extension it lacks and leave a fresh clone unsynced by @willemkokke
- A tool's graph lock writes what the resolution decides and no path of the run, so re-locking an unchanged version changes nothing by @willemkokke
- Fm sync mounts every listed extension before it renders or locks, and the re-run guard counts each cause once, so a sync onto commits listing new extensions converges and removes nothing by @willemkokke
- The root .gitignore ignores a native member's coverage lines file again, beside the other coverage data, and still tracks .coveragerc by @willemkokke
- Fm sync installs a newly listed extension's distribution when uv sync cannot, then mounts it in a fresh process, instead of refusing it by @willemkokke
- A flag on by default is named in help by its own spelling beside its own text, and says it is on by default and how to turn it off by @willemkokke
- The abort reconcile spawns the runner by its real module, two footman invariant tests scan the real tree again, the push guard tests the branch a push names, and the review's smaller faults go by @willemkokke
- A refused arm follows a running CI and merges when green, and a release of many members is named by its size and a digest by @willemkokke
- The push check reads the installed plugins again, and the scan records each entry point's distribution, so a sync that moves a member under a long process keeps its tools by @willemkokke
- A release tag names its package at any depth under packages/, so a grouped package verifies, raises floors, and shows in the release history by @willemkokke
- A Linux wheel build mounts a conan workspace file beside the package it builds, so a sibling library resolves from its source inside the container by @willemkokke
- A sync that rewrites a distribution's entry points at the same version runs the command again, so it never goes on with extensions it mounted before by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke
- The reconcile spawn's test brings its own workspace, so the workshop wheel's isolated release leg runs it outside any checkout by @willemkokke

## [0.5.0] - 2026-10-04

### Added

- The template verbs go: the drift check is drift.check, fm sync writes the composed and generated files, and one workflow.update moves the lock and what it composes by @willemkokke
- Removing a member is deleting it and running fm sync: the runner's handoff enters a project that syncs itself without syncing it first by @willemkokke
- A check's options are addressed by tool, by the tool's check, and by role, deeper winning key by key by @willemkokke
- A check declares its transport and threshold, and the engine splits its paths into the fewest calls, merges their failures, and runs whole at the threshold by @willemkokke
- A check's own kinds decide which packages it judges, and a kind's role list and verify_roles go by @willemkokke
- A release pull request behind main under its set re-derives instead of merging, and a failed prepare's rollback restores what it staged by @willemkokke

### Fixed

- Every release squash touches the manifest, which records its mining point, so the wave publishes it and a takeover at the same versions has a commit by @willemkokke

## [0.4.0] - 2026-10-04

### Added

- The three declaration sites, the catalogue, and the lock by @willemkokke
- Receipts, materialisation on sync, and the modes by @willemkokke
- The stubs live in typings/, written from the index, and the toolroom wheel ships none by @willemkokke
- Stubs for the locked tools only, with sorted imports and a noqa header, declared by a handles module beside them by @willemkokke
- The steady state costs the stats: a nothing-moved gate, records digested once, a lazy catalogue by @willemkokke
- The store renders stubs from surfaces, and the index holds none by @willemkokke
- Pyrefly has a record, a stub and a place in the python kind's lock by @willemkokke
- Every forge refuses a missing credential in one shape, GitLab falls back to glab, and the lane names its own variables by @willemkokke
- An armed release follows its pull request to the squash and the wave to its verdict by @willemkokke
- The record is one file per tool with one line per option by @willemkokke
- The six-host verification point, every downloaded tool installs, runs and reads on its host by @willemkokke
- A test whose child exits on a console control event reports who shared the console by @willemkokke
- Git-cliff comes from its own release, required by the base kind every package kind derives from by @willemkokke
- Ruff and pyrefly come from their own releases, and ty, uv and prek list from theirs by @willemkokke
- Basedpyright comes through bun: the store supplies bun-install, and bun is its locked dependency by @willemkokke
- The site's URL scheme: packages, tasks, releases and tools, with _generated in no published path by @willemkokke
- The sidebar's machine sections are marker blocks the author may place, and land in a fixed order otherwise by @willemkokke
- A docs page edit runs the page's own examples in the affected gate by @willemkokke
- A package's docs section is the package's, and the site assembles its config at build time by @willemkokke
- The tool store and uv's cache are restored on every GitHub job and saved when their lock moved by @willemkokke
- Fm start takes --from=<branch>, and fm submit targets the recorded parent until it merges by @willemkokke
- Kebab-case is the spelling of every key we define, the migration is gone, and footman warns on an unknown key with the closest one named by @willemkokke
- Conan is a tool store record from its own releases, and the cpp backend's refusal names the store by @willemkokke
- An npm kind names its runtime, node has a record from nodejs.org, and basedpyright runs on node by @willemkokke
- The watch prints every job's move as it happens, stamped with the elapsed time, and names a red job with its failure lines at once by @willemkokke
- Every watch line opens with the time since the watch began by @willemkokke
- The extension compiles against the library at HEAD and a third-party package through the store's cmake-conan provider, and the store's downloaded kinds are one download by @willemkokke
- The pypi and python kinds name their source; uv is the installer, not the kind by @willemkokke
- A conan member's caches ride its own release, and every declared floor is built against by @willemkokke
- The lane keeps conan's home on the working drive and caches it around the native legs by @willemkokke
- The metrics row keeps what each task's own steps took, so a checker that moved is named by @willemkokke
- Both native kinds render CMake presets, so an editor opens them with no verb by @willemkokke
- The native kinds format and lint their sources, with the two clang tools in the store by @willemkokke
- The lock names a delegated tool's resolved graph, and fm tools.lock writes it by @willemkokke
- A delegated tool installs the graph the lock pins, resolving nothing and checking every artifact by @willemkokke
- A profiled run carries what it launches by @willemkokke
- The trace leaves the leg by @willemkokke
- One run assembles into one timeline by @willemkokke
- The local command carries the run it caused by @willemkokke
- A skipped gate is an event on the timeline by @willemkokke
- Every CI entry keeps a trace, and one switch governs it by @willemkokke
- The commit a merge made, and a gate that skips a deleted test by @willemkokke
- A job's setup and teardown are drawn, so its parts add up by @willemkokke
- One walk follows a commit through everything CI did by @willemkokke
- The layering lint reads what the sources use, and each kind answers for its own by @willemkokke
- An issue's thread has a verb to write to it by @willemkokke
- The tools lock and sync in uv's shape, and a newborn locks its own by @willemkokke
- The check registry: every gate member is a registered check by @willemkokke
- The layering check's fix mode writes the edge the graph already reaches by @willemkokke
- A managed file carries the regions the repository owns by @willemkokke
- A layer declares its API version and its dependencies, and the gate names what a layer registered or withdrew by @willemkokke
- A package may opt out of publishing: [release] publish = false by @willemkokke
- A layer declares its tools and its contributions by target, and the layering check parses once by @willemkokke
- The category and channel registries: what a file is and who wrote it, extensible by a layer by @willemkokke
- Slots the records fill, options a package sets, and the check's tools on its record by @willemkokke
- A check record owns its configuration: fragments, the editor files, and withdrawal by @willemkokke
- A check claims its files, and the render reads the claim by @willemkokke
- Prose fragments serve two readers: sections, a registry, and the entry file from it by @willemkokke
- Coverage measured by kind: the seam and the gcc and clang measurers by @willemkokke
- The dotnet kind: .NET as a runtime record on every host, NuGet tools through it, pwsh as a download by @willemkokke
- The MSVC measurer and the Windows toolchain environment for the cpp-conan kind by @willemkokke
- Host-scoped tool requirements: a tool required on some hosts alone by @willemkokke
- The conformance loop's cpp-conan member, its runner on Debian, and the fixes the pass found by @willemkokke
- The host allowance for store tools: a tool on PATH serves when allowed and satisfying, the store installs otherwise by @willemkokke
- The loop runs its scenarios by name and prints a timing table per pass by @willemkokke
- Local development environments by name in host mode, and the loop runs on one by @willemkokke
- Extraction belongs to the kind: the kind record names its API extractor, and a kind without one names the absence on the site by @willemkokke
- Policy belongs to the layer: whether private members are documented is a slot a layer fills, read by the kind's extractor by @willemkokke
- Assets belong to the layers: a layer's css is staged into the build from its wheel, in layer order with the workspace's own last, and the theme block is a slot a theme layer fills by @willemkokke
- Examples are files: a package's examples live under docs/examples/ as python files a page includes by snippet, and one harness runs each through the kind's runner, replacing footman's page-as-session harness and its three markers by @willemkokke
- The docs layer: the site's assembly, verbs and slots move to livery.workshop.layers.docs, the base keeps the docs contract, the nav blocks and a registry of layer-rendered files, and the layers namespace spans distributions by @willemkokke
- The site's jobs come with the docs layer: a mounted layer contributes jobs and entries to a builtin point, the verdict waits for the ones that gate, and the base declares neither the docs job nor the deploy by @willemkokke
- The extractor is data and the development section is pages: the site's handler blocks render from the kind's data, the prose fragments render one page per section, and the mount refuses two pages at one URL by @willemkokke
- The conformance kit: livery.workshop.testing judges what a layer registers, starting with the backend protocol, the nearest kind's fragment, and the category tables, and the nearest kind now wins where the kind chain was read parent first by @willemkokke
- The role verbs go: fm check takes paths and walks the registry over exactly those files, --safe-fix is the fixers' in-flight mode, --point selects a point's tests, and the post-edit hook runs the fixers through the walk by @willemkokke
- A check is its role and its tool, and the role verbs are generated from the registry: fm test runs the test role's checks and fm test.pytest one, each verb offering exactly the flags its checks declare by @willemkokke
- The conformance kit judges a check's after list and a layer's contribution modules, and a for entry naming an undeclared contribution no longer crashes every fm command by @willemkokke
- The conformance kit renders a layer's fragments: a composed file that stops parsing and a file that drifts from its render are named, and a withdrawn check's file follows contract 11 by @willemkokke
- The conformance kit walks the gate over a layer's checks: every fixer rewrites before any judge, a check with no file to read is named and never started, and every check names its layer by @willemkokke
- The base names no package kind: a vocabulary test with an allowance that only falls, and a kind's coverage pages come from its kind record, generated by docs.coverage-pages by @willemkokke
- The deterministic CBOR codec is livery.strongroom.cbor, reached by its own path, and the livery-cbor distribution retires by @willemkokke
- Fm sync and fm integrate remove the ignored files a removed package leaves under packages/, and discovery's refusal of such a directory names the sync that removes it by @willemkokke
- A coverage record keeps each row a write replaced or removed for a day, so a run's union finds what its legs skipped after main's run moved the record on by @willemkokke
- Fm ci.scale times sync, template.check and check over a generated 300-member workspace, weekly in the nightly's scale job onto the metrics series by @willemkokke
- Every workshop.toml key is declared by the layer that reads it, and a contract holds nothing else: an unknown key, an unlisted layer's key, a wrong type or value refuses on read by @willemkokke
- Every distribution root is a namespace whose public names live in its api module, and the docs layer moves to livery.extensions.docs by @willemkokke
- Extensions replace layers: each declares itself in the workshop.extensions entry point group, and a workspace lists them by name in [workspace] extensions by @willemkokke
- Plugins mount through the project builtin rung: the workshop mounts the listed extensions from its own entry module, and the rendered tasks.py keeps only its comment by @willemkokke
- A tool requirement takes ? and !: an optional tool is locked where it can be served and never refused, and a plugin declares the tools its verbs need by @willemkokke
- Footman scans the installed entry points once per process and shares the scan through installed_entry_points, and a plugin declares its tools in a data module by @willemkokke
- [workspace] hosts declares the supported hosts, the lock covers them all, and a sync elsewhere refuses in CI by @willemkokke
- One fragment engine: targets composed by type in extension order, minijinja renders cached by digest, receipts that keep an edited file by @willemkokke
- Every checkout is LF: the rendered .gitattributes pins eol=lf whatever core.autocrlf says, with .bat and .cmd as CRLF by @willemkokke
- .gitignore and .gitattributes are composed by the fragment engine on sync, each line shipped by the extension that needs it by @willemkokke
- Git LFS is a workspace setting: extensions ship LFS rules, composed while [workspace] lfs is on and named while it is off, with a git_lfs record, its hooks and LFS checkouts in CI by @willemkokke
- The vscode files are composed: extensions and checks contribute settings and recommendations as data to one template, and check --fix moves what VS Code added into the region by @willemkokke
- Pyproject.toml is composed: the base's template rendered from the answers, each check's tables a fragment of its extension, the repository's region last by @willemkokke
- A package's clang-format and clang-tidy are composed by the fragment engine, and the separate settle path goes by @willemkokke
- Skills and hooks are engine link outputs, and settings.json, the prose fragments and the CLAUDE.md stub engine files, withdrawn by the receipt rule by @willemkokke
- The copier answers move into workshop.toml: identity in [workspace], members from discovery with their own description, dev-extras and template, and the answers files go by @willemkokke
- Tasks.py and each package's cliff.toml are composed by the fragment engine, so copier only births by @willemkokke
- A birth writes the extensions' seeds through the fragment engine's resolution, and copier, the overlays and the template artifact go by @willemkokke
- A newborn's test and asset seeds become docs extension checks and content, and a born project's gate is proved green by @willemkokke
- The floor leg is the [release] prove-floors setting, workspace default overridable per package, and a failed release's rollback restores the version in api.py by @willemkokke
- A died release wave can be abandoned: --abandon prepares a new release over it, and a later release squash takes over a package's uncut receipt by @willemkokke

### Fixed

- The merge point's dispatch job carries actions: write on GitHub, and a refused dispatch is red by @willemkokke
- The stubs and handles live beside the tools package, never inside its directory by @willemkokke
- A machine's gate reads the state store from the last fetched snapshot and never reaches origin by @willemkokke
- A system tool is held to the highest of the record's floor and the sites' floors, and the site is named by @willemkokke
- Git-cliff stays off Windows ARM, where it has no wheel and its sdist does not build by @willemkokke
- The project ignore file covers .DS_Store, so Finder never dirties a tree a workflow verb refuses by @willemkokke
- Fm enters the environment for its own process, and the editor's terminal enters it on open by @willemkokke
- The entry contract leads PATH in its own order, the venv ahead of the tools by @willemkokke
- A gone branch's coverage record lingers a day, so main's run for its squash can still carry from it by @willemkokke
- Fm submit refuses a branch with no commits beyond its base before pushing anything by @willemkokke
- Ci.rerun names a run still in progress, ci.logs names the failure above the tail, and commit refuses plainly when git cannot write the object by @willemkokke
- The tool store lives on the runner's working drive on a GitHub job, beside uv's cache by @willemkokke
- The workflow caches the tool store alone, swept of the artifacts it extracted, and nothing of uv's by @willemkokke
- A sync whose first act moved the checkout hands the rest to a fresh process on the new code by @willemkokke
- The shipped .claude/settings.json is JSON a strict reader accepts by @willemkokke
- The fragment sweep removes only what a delivery wrote by @willemkokke
- The re-exec hands its guard to the replacement instead of writing it here by @willemkokke
- A merged parent's child submits against main by @willemkokke
- The job runner names the leg it pushes for by @willemkokke
- The native kinds find conan where the leg puts it by @willemkokke
- The ignore list follows where a profiled run writes by @willemkokke
- The trace sweep takes every file in its directory, whichever writer made it, and keeps fifty instead of ten by @willemkokke
- A check's prerequisites run through their tasks, once per gate by @willemkokke
- The lock check compares entries without their graphs by @willemkokke
- An allowance ahead of its requirement is kept and named by the lock, and the loop's pass series is declared with the others by @willemkokke
- The entry script keeps a cache placement the job already sets, so a persistent runner's jobs find their store warm by @willemkokke
- The gate walks the registry in one place: every fixer that applies runs one at a time before the judges, a check with no file to read starts no process, and the python backend composes nothing by @willemkokke
- A package's tests run whole whenever any of its test or source files changed: the gate stops narrowing a suite to the changed test files by @willemkokke
- Every python in a workspace venv stops importing coverage at startup: coverage's own hook meters the tests' subprocesses, and the floor moves to 7.13 by @willemkokke
- A stacked branch moves only its own commits when its parent moves or merges: fm start --from records where the branch starts, and fm sync and fm submit rebase from there by @willemkokke
- A submit whose pull request merged finishes clean when the janitor already swept its worktree by @willemkokke
- Fm issue.show prints the whole issue when git cannot reach origin: it finds the branch among the local branches and the remote-tracking refs, and a failed lookup costs only the pull request line by @willemkokke
- Fm sync names git's own words when a rebase stops on anything but a conflict, a commit the signer refused say, instead of reporting conflicts by @willemkokke
- A submit's self-heal hands the merged checkout to a fresh fm submit, so the gate judges the merged tree with the code it carries, never the code the follow loaded by @willemkokke
- The extension mount names what it cannot mount and goes on, so fm sync can install a missing declaration; the gate refuses the same problems by @willemkokke
- An optional tool the host cannot supply is named and never refused, strict or not by @willemkokke
- An armed submit of a stacked branch leaves it unarmed while its parent is open, and names the sync and submit that arm it after by @willemkokke
- Ci.rerun off main names a red run on a commit main already has and leaves it to main, so a branch no longer re-runs main's nightly by @willemkokke
- The armed drives pass again: a sync ends by writing the composed and generated files, a birth lists the site's extension before a brand's, and the base ignores the docs its build writes by @willemkokke
- A nanobind container build mounts the stores its provider and conan paths name by @willemkokke
- Release verification reads __version__ from the files the stamper writes, a namespace root's api.py among them by @willemkokke
- Speed.judge says it is a CI verb before it looks for a workspace by @willemkokke

### Changed

- The project ignore file covers a root docs/_generated/, and the modular docs plan is complete by @willemkokke
- A page on efficient development patterns: submit and move on, parallel worktrees, stacking, what the forge does with several green pull requests by @willemkokke
- The kind hierarchy plan sequences compile-time consumption as phases 7 and 8, and the merge path may depend on a token-reached service by @willemkokke
- A test workspace locks for the host running the test, measured on a Windows ARM round that leaves the matrix by @willemkokke
- The chain proves one first-party and one third-party symbol in the child's extension by @willemkokke
- Conan and ctest run through their store handles, and receipts say where the store put them by @willemkokke
- Git and ssh run through their handles, and a test refuses a literal program name by @willemkokke
- Layer fragments get their own folder under .workshop by @willemkokke
- A run as one timeline has a page by @willemkokke
- The edge kind describes what it decides and what it does not by @willemkokke
- An assembled trace lands with the checkout's other records, and the newest stay by @willemkokke
- The trace window holds a working week by @willemkokke
- The profiles page names the directory a trace lands in by @willemkokke
- The stdlib-only rule reads the plugin the metadata names by @willemkokke
- Pin what the gate is, before the check registry replaces it by @willemkokke
- The contract key type becomes kind by @willemkokke
- The workshop member carries types-pyyaml as its dev extra by @willemkokke
- The layer template is package-layer by @willemkokke
- Birth tests never reach the network: the tool-sync engine is faked for every caller, and a fetch the fakes miss refuses naming its host by @willemkokke
- The base derives a package's next version from its commits; git-cliff only writes the changelog entry by @willemkokke
- The release member list is the only record of what a release contains: discovery, the title check and the recovery read it, and the changelogs are no longer read by @willemkokke
- A verb that reaches no forge and no tool store loads neither: the forge, store and strongroom roots serve their names on first use, and the workshop and the bench import the store where they use it by @willemkokke
- Release notes are a registration the release train calls: prepare, verify, the dev release and the docs layer ask the provider, and with none the train stamps and writes no notes by @willemkokke
- Minijinja renders every template and check fragment byte-identical to copier's jinja2, pinned by a parity test until the templates go by @willemkokke

## [0.3.0] - 2026-09-16

### Added

- A Windows leg's temporary directory lives on the runner's working drive by default on GitHub by @willemkokke
- The state store reads and writes a series in a fixed handful of git processes by @willemkokke
- The speed marks are off unless the contract declares them by @willemkokke
- A given body updates a reused pull request by @willemkokke
- A verb reads the state store through one snapshot of the remote namespace by @willemkokke
- The gate job drops the stale speed marks while the marks are off by @willemkokke
- The store's remaining round trips: no readback, one listing for heads, one write per leg, one snapshot per job by @willemkokke
- The wave publishes with a PyPI token when the repository has one by @willemkokke
- Livery.toolroom is a namespace, the handles live in livery.toolroom.tools by @willemkokke
- The merged head decides what a merge left behind, and the release act cleans up after itself by @willemkokke
- A schedule entry runs every week or every two weeks, and the refresh submits what moved by @willemkokke
- The loop's verbs at the top level: start, commit, abandon a branch, submit --no-close, and issue.reopen by @willemkokke
- The stub machinery is livery.toolroom.bench, its own distribution by @willemkokke
- Tests are namespaced by their path, and a helper carries its package's name by @willemkokke
- The gate record is a chain, and fm check runs what changed since the nearest proved tree by @willemkokke
- The kind seam classifies a change, and a test-only delta runs its files by @willemkokke
- The environment follows the branch by @willemkokke
- The gate runs on command, and a GitLab pipeline names its workflow by @willemkokke
- A point is a declaration, and the emitters' properties are pinned by @willemkokke
- One renderer per forge for the gate, the merge and the nightly, and the clock on GitLab by @willemkokke
- The release point is data: its two YAML decisions move into verbs and its jobs become ci.run jobs by @willemkokke
- A package contributes a point: [[ci.point]] in its contract, rendered for every forge, removed with the package by @willemkokke
- Every GitLab pipeline names its workflow, a merge request pipeline is a pull request run, and the recorder's pytest is a child by @willemkokke
- The GitLab lane is born: fm ci.e2e --forge=gitlab merges the setup PR on the real runner and proves the verified skip by @willemkokke
- The GitLab runner carries the toolchain, the docker socket is opt-in on both lanes, and the members and the three legs prove themselves on merge request pipelines by @willemkokke
- The release act, the points by hand and the clock on GitLab: the loop is whole on both lanes by @willemkokke
- Issue.show prints one issue whole, and the protocol reads an issue's thread by @willemkokke

### Fixed

- The publish job pushes receipts with a credential that has the workflows scope by @willemkokke
- The wave walks past a version the index already serves before uploading by @willemkokke
- The release legs' toolchain install leaves what the leg resolved by @willemkokke
- A re-run of only the verdict job runs the whole run instead by @willemkokke
- The release legs refresh the co-released members' cached wheels by @willemkokke
- A submit re-run on the merged head reports the merge by @willemkokke
- The changelog credits authors with the token the forge lane connects with by @willemkokke
- The train's recovery finds the requested set's own uncut squash by @willemkokke
- The site build names its sources and refuses a checkout without them by @willemkokke
- Dropping a view and evicting an object clear the read-only mark Windows refuses on by @willemkokke
- The verbs that remove a worktree are serial, so the teardown may move the process by @willemkokke
- An empty publish token variable never reaches uv publish by @willemkokke
- One attended check, and the janitor sweeps wherever it runs by @willemkokke
- A dev release tells a decline from an unanswered question by @willemkokke
- The terminal is asked in one place, and the lint keeps it there by @willemkokke
- The workshop's toolroom floor is 0.7.0, the namespace's first release by @willemkokke

### Changed

- Remove the footman and toolroom compat shim packages by @willemkokke
- The isolated legs resolve from local wheels, never an index by @willemkokke
- The suites build their repositories once by @willemkokke
- Closure ids are read in one git process by @willemkokke
- The layer walk's test mounts a scratch contract, not this repository's by @willemkokke

## [0.2.0] - 2026-09-11

### Added

- The armed suite rehearses the release path on the true graph
- The docs site's rendered config and build verb
- The API reference, every docstring published
- Changelog pages and the paginated release view
- The docs gate, drifted links red before CI says so
- The publish seams, declared in the contract per forge kind
- The docs seeds and the gradient add-back
- The registry ladder and the kind registry, opened to layers
- The C/C++ library kind on conan 2
- The binary-extension kind through the chain
- The cross-kind dependency
- Publish and the identity guard across kinds
- The chain add-back for both kinds
- Issue bodies through fm
- The entry contract and the bare fm spelling
- Package-owned nav for the docs site
- The docs generator seam
- The docs config surface and the theme
- Llms.txt and llms-full.txt for the site
- The coverage seam
- The per-package task reference
- Toolroom joins the workspace
- Footman joins the workspace
- The version rules speak as the ecosystem's own
- The train carries a migrated line's first release
- The release branch carries its own set manifest
- Fm ci.e2e drives the whole loop against the local forge
- The server blocks red merges on every forge
- Receipt tags are protected on every forge
- The post-edit hook never removes an import mid-edit
- Every gate leg runs profiled and uploads its trace
- The CI state store on refs/workshop/*, with compare-and-swap writes and a janitor
- Timing rows on the state store, and fm ci.timings
- CI as points on the gitea emitter, fm ci.run per job
- The merge point dispatches the release wave, fm workflow.release.dispatch
- The nightly point, fm release.replay, and the schedule seam's first entry
- [ci] python-versions, matrices as contract config; a one-leg loop
- Contract keys are kebab-case; one loader refuses underscores, the render verbs migrate
- The wheels matrix reads wheel-platforms; the loop builds a nanobind wheel through the docker socket
- [ci] affected-legs, the check legs run the scoped gate against the pull request's base
- Workshop/verified, the gate stamps the tree it proved green and a run of the same tree skips
- A cancelled run names the run that superseded it, and the watchers follow
- The gitea lane meters its check legs and unions them in the gate job
- A skipped suite's floor is judged from the workshop/coverage store
- The leg splits one pooled test run by test context instead of one process per suite
- The GitHub emitter runs the check legs and the gate job through the points shell
- The auto-ratchet floor mode, fm coverage.accept, and a coverage row on every gated run
- A diff confined to notes and markdown affects no package, so its legs skip the gate
- A test declares the CI points it runs at, and the nanobind wheel build runs at the nightly point only
- The submit's gate is the gate the CI legs run
- The site's own root files affect no package
- A narrowed green run on a verified base stamps its tree in full
- The GitHub shell carries the merge point's jobs, and the legs start at once
- The timings record every job of the run and the run's own wall
- The submit's gate skips a tree this machine's check already proved
- The runner's directories are swept through a plugin surface
- Every test runs apart from the live runner state, and the registry stays clean
- The check legs run the newest Python; the nightly runs the matrix
- Every series reads and writes its rows through the store
- The coverage store and the per-run halves are keyed series
- The gate record and the diagnostics are local series of the store
- One janitor sweeps the runner's directories and the store
- The state store is read by hand, and the pages read it after a skip
- The coverage record is keyed by main, one row per unit per leg
- Each branch keeps its own coverage record, copied to main's at the merge
- The gate judges statements and branches, and the record holds arcs
- The workspace tests directory is a unit of the affected engine
- Dispatch and read the nightly, ride out dropped connections, merge notes by union, judge the task nav
- The test speed ratchet, slice 6 of the state store plan
- A re-dispatched wave may name a released driver, and recovery reaches older squashes

### Fixed

- Prepare refreshes uv.lock with the stamp
- Ci.rerun re-runs the branch's verdict, read back
- The changelogs are the manifest and recovery handles its leftovers
- Tie the submit verdict to the pushed head
- The newborn's first release is v0.0.0 everywhere
- One release number per distribution line
- The isolated leg answers for its own environment
- The leg runs serial, and the history ships in the wheel
- Re-preparing an unpublished release is recovery, not a fault
- The emitted title check speaks footman's grammar, and a red title cancels the matrix
- The docs deploy fetches the receipt tags
- The scoped gate hands its steps to the block
- The context-rename heal skips a forge that names no contexts
- Lint and format take paths and --safe-fix; the hook calls the verbs
- The metrics collect looks a pull request's run up under the event's head
- The completion test heals no real project; the dev build keeps timestamps
- Classify takes the emitters' path set once instead of rendering per path
- The units a leg ran come from its marker, the legs trace with ctrace, and the workspace's tests are a stored unit
- The gate's own driver is not measured, only the tests
- Six frictions of the daily loop, and workshop's floor at 83
- The ecosystem rung carries PyPI's upload endpoint
- The dev plugin imports livery.toolroom, and the registration gate's test states both cases
- The train recovers an uncut wave only for the set that names it
- The registration gate's test reads the wheel case off the module's own file

### Changed

- The post-mortem's process rules enter the conventions
- Integrate and the wip park through their paces
- The docs seeds move to a shared package-base template
- The task reference documents the advertised tree
- The footman floor rises to 0.52.1
- The kind backend owns its gate composition
- The connection surfaces its credential
- The remaining quick wins on the leg's profile
- The test suite seeds git once and renders once
- The floor on forge names the co-released 0.3.0
- The floor on copier names 9.18.2, the locked version

## [0.1.0] - 2026-09-04

### Added

- The template source is the contract's call
- The other forges' CI variants, and the release legs
- Phase 7 closes green, and the stalled grace loosens
- Affected, coverage floors, and the 0.1.0 stamp
- Every fm call measured, floors judged on the union
- Fm ci.logs, the first verb the fm-only rule surfaced
- Releases derive their version and changelog from the commits
- Fm check --fix heals mechanical findings before the gate judges
- Abandon, submit.merge, submit --fix, and sync matches the lock
- Git-cliff writes the changelogs, per package, from the template
- The engine plan, and the pre-engine tidy
- The workflow engine: state, decision, abort, diagnostics
- The release train's driver, base gate, and two-leg validation
- The wave publishes, the receipts say when each member is done
- The update family rides the engine and finishes itself
- Dev releases, decided by the branch
- The isolated leg installs the gate's toolchain, aimed starvation
- The entered environment, clean, and the hook family
- The entered shell and the issue family
- Submit --force, a leased push with full disclosure
- No livery home, one shared env file, issues as the way of work
- Sync brings the checkout current, integrate is the merge spelling
- Governance in the workshop - owners, the applied contract, the heals
- Implement gitlab's licence-gated governance
- Phase 9, the branded runner
- Brand-ready runtime on footman 0.50
- Identity-free core on workshop.toml
- The template channel reads the artifact repository
- The environment store and the forge tokens
- Fm new.project, birth end to end
- The layer axis built
- The composed artifact release
- The dummy descendant proof chain

### Fixed

- A half-point coverage grace below the floor
- A running job's missing log is a line, not a crash
- Check --fix runs the fixing gates themselves, hse's shape
- Submit refuses an ambiguous title before pushing
- The changelog works on a private forge
- Close the phase 5 audit gaps
- Close the phase 6 audit gaps
- Close the phase 7a audit gaps
- Close the phase 7b audit gaps; assignment is documentation
- Close the phase 8b audit gaps
- Close the phase 9 audit gaps
- FORGE_ADMIN_TOKEN and zero-approvals default
- No livery-named environment variables, no fallbacks
- Livery is only the workspace name
- Ban every misuse note and speak toolroom
- Released-ness is the tag's, and stranded entries regenerate
- Derive_plans shares prepare's released-by-tag judgement
- The set builds every wheel before any isolated leg
- An explicit version regenerates a stranded entry too
- The isolated leg's toolchain pins skip the members
- Version tests assert the installed metadata, never a literal
- Pyyaml floors at the oldest current-Python release
- Floors on toolchain-shared deps align with the lock
- The dogfood sync test skips outside its checkout
- The deploy-key printf keeps its backslash-n
- The member-keys test clears the rung override
- The merged-PR guard allows a fresh cycle of a reused branch

### Changed

- The workshop's cheap coverage wins, floor ratcheted to 80
- Plan labels and jargon out of the published text
- The pre-0.1.0 roadmap, and the release story recorded
- The task shells lit through the resolution seam
- The workshop floor ratchets to the union's 84.5
- Fallbacks before happy paths, and the copy manifest learns content
- The workshop floor ratchets to the union's 87.4
- Migrate to footman 0.49
- The copier floor and pin move to 9.18.1

## 0.0.2 — 2026-08-31

- The whole dev loop: the quality family dispatched by contract, the
  layering lint, and the layer walk (`fm layers`).
- The content channel: `fm sync` materialises fragments, skills, and
  hooks from every mounted layer, and manages the CLAUDE.md stub.
- The template channel: `templates/` with the project and
  package-python kinds, `fm template.check` in the gate,
  `fm template.apply`, and `fm new.package`.
- The forge lane: `fm submit` (verify onto the remote; `--armed`
  lands it), `fm status`, `fm ci.rerun/watch/cancel`, `fm doctor`,
  `fm workflow.abort`, `fm workflow.merge-now`.
- The release train: `fm release.prepare` and `fm release.verify`,
  and the template snapshot publication (`fm release.templates`).
- The update wave: `fm update` bumps floors, refreshes content and
  render, and submits the result.

## 0.0.1 — 2026-08-31

- The plugin host: the `footman.tasks` entry point and the layer walk
  that mounts every layer named by the workspace contract, in order.
- Package skeleton: the `livery.workshop` PEP 420 namespace module
  and the package contract (`livery.toml`). Reserves the distribution
  name and proves the release train's second path.
