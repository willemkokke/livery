# Changelog

## [0.11.0] - 2026-10-17

### Added

- Cmake declares its configure, compile and ctest steps, the build steps' gate side registers in the gate, and the base imports no extension by @willemkokke
- An extension declares the templates fm new.package creates a package from, the seed trees become the first templates, and python's scripts are what fm run starts by @willemkokke
- Fm sync refreshes the completion manifest in the background when it changed what is installed by @willemkokke
- Fm ci.dispatch follows the run it started: the run the forge names, or the one new dispatched run of its ref and commit, and two such runs refuse naming both by @willemkokke
- The agent's guidance gives each installed extension a block read from its declaration, and the kit holds every declaration to its docs by @willemkokke
- Each installed template is a verb, fm new.package.<template> <name>, whose declared creation options are its options; python-library's --command replaces python-cli, workshop-extension's App comes with --app, and cpp-package with --package-type replaces cpp-library and cpp-executable by @willemkokke
- Fm submit --every-runner labels a pull request ci:every-runner so the gate runs every runner for it, the forge adds labels to a pull request on every backend and refuses an unknown one by name, the gate workflow runs on labeled and its legs job picks the full matrix while the label is there, the repository configuration creates the label, and the coverage union expects every leg from a labelled pull request by @willemkokke
- Fm ci.balance reads each platform's full green timing rows, predicts its wall at one to several shards, and proposes the counts by @willemkokke
- Shard spelling, test-file partition and the durations family by @willemkokke
- Fm ci.run --shard names the shard in the leg's label and display by @willemkokke
- Fm check --shard runs one shard's share of the sharded steps by @willemkokke
- The check matrix renders the shards the contract declares by @willemkokke
- The gate job folds a platform's shards by @willemkokke
- Fm ci.timings shows a platform's shards beside its folded row by @willemkokke
- Fm ci.footprint sums the runner-minutes the metrics window holds, and prices them where GitHub gives them away by @willemkokke

### Fixed

- Fm sync hands itself to a fresh process when its uv sync moves or removes the distribution of the workshop or of an extension it mounted by @willemkokke
- Coverage writes each set of test indexes as digits and commas, in version 3 of the line format, so no store push holds a token shape GitHub's push protection refuses by @willemkokke
- Fm sync installs a newly listed extension through the project file's dev group and uv sync, so its path or git source applies by @willemkokke
- Conan's sync step makes the one default profile once per sync, and a registered phase's workspace step runs only while a package holds an extension it names by @willemkokke
- Every CI job keeps conan's home in a directory of its own, so two jobs on one runner never write one conan cache at once by @willemkokke
- A release's publish job restores every host's saved conan cache the run collected, so the upload sends every host's binary by @willemkokke
- Read the verified record per platform leg: a check leg skips only on a full row naming its own leg, and the stamp names the legs' labels by @willemkokke
- The loop's contributed point asks for the environment's runner, never ubuntu-latest by @willemkokke
- The release rehearsal passes and leaves its tree as it found it by @willemkokke
- Pin every action by commit and hand the secrets to the entry step by @willemkokke
- Drop the shard key from a one-shard runner's entry in a sharded matrix by @willemkokke

### Changed

- The in-wheel extensions call the workshop's public names where one exists by @willemkokke
- Undeployed is a public name of the workshop, and cmake and conan call it there by @willemkokke
- Every extension is one kind: an extension declares no levels, either list takes any extension, and the installed set is the root's set and every package's set by @willemkokke
- No extension list's order means anything: composition order decides, the root list is written in its canonical form, and fm new.project takes --extensions in place of --stack by @willemkokke
- The workspace's own tests hold the root's set, so a root whose set holds no python runs none of them and says why, and fm new.project's stock list names python by @willemkokke
- Fm new.package offers the installed set's templates, a template needing an extension the workspace does not have refuses naming it, and a creation installs nothing by @willemkokke
- A root entry's without names the targets its extension contributes nothing to, in place of for, so cooperation stays greedy and fm extensions prints what each entry leaves out by @willemkokke
- A package listing an extension that composes nothing in a package refuses and the gate's fix moves the entry to the root list, a template listing one refuses, and the conformance kit gains template-composes by @willemkokke
- Remove what the redesigns left behind: the register-and-withdraw functions, dead definitions, leftover sweeps, empty stylesheets and the text naming kinds and seeds by @willemkokke
- The cmake extension leaves the workshop's wheel for livery-extensions-cmake, and a test edge renders into the package's test extra by @willemkokke
- Git LFS becomes the lfs extension in its own distribution, the CI checkout takes inputs extensions answer, and the sync phase runs over the workspace by @willemkokke
- Mkdocstrings reads public names alone: extension_set, step_extensions and step_enabled join the workshop's API, step_option answers from the installed declaration, and the docs extension gets an entry module by @willemkokke
- The mkdocstrings extension leaves the workshop's wheel for livery-extensions-mkdocstrings by @willemkokke
- Docs reads public names alone, its override template is a fragment, and a docs step declares the outputs a build clears once it stops running by @willemkokke
- A removed key, option, template or name has no code of its own: the ordinary refusal of an unknown one covers it by @willemkokke
- The docs extension leaves the workshop's wheel for livery-extensions-docs by @willemkokke
- Coverage reads public names alone: the CI run's state store, legs, run-row sections, leg scope, verified branch and tested units join the workshop's API by @willemkokke
- The coverage extension leaves the workshop wheel for its own distribution, livery-extensions-coverage, taking Pygments with it by @willemkokke
- Nanobind reads public names alone: the release build, the release's members, the CI matrix, the job-built members query, the tool receipts and the store home join the workshop's API, and python and conan get entry modules by @willemkokke
- The nanobind extension leaves the workshop wheel for its own distribution, livery-extensions-nanobind by @willemkokke
- Conan reads public names alone: a package's workspace root, the requirement writer protocol and the registry target read from the publish phase's table join the workshop's API, and python reads them too by @willemkokke
- The conan extension leaves the workshop wheel for its own distribution, livery-extensions-conan by @willemkokke
- The cpp extension leaves the workshop wheel for its own distribution, livery-extensions-cpp by @willemkokke
- Python reads public names alone: the source records, the reference map, the graph widening, a venv's paths and the example and speed names join the workshop's API, python's upload moves into python, and the dev act publishes through each member's publish phase by @willemkokke
- One follow() for every wait on the forge in the workflow lane by @willemkokke
- Python leaves the workshop wheel, which now ships the base alone by @willemkokke
- The loop proves each scenario from the rows the legs write, not from their log lines by @willemkokke

## [0.10.0] - 2026-10-14

### Added

- Fm doctor reports a uv cache over its limit and prunes it under --fix; the limit is [workspace] uv-cache-limit, 10G by default, and the fix names raising it by @willemkokke
- A dispatched nightly runs only the scheduled tasks --only names, so a rehearsal runs the loop alone, and the tool refresh observes nothing off the default branch by @willemkokke
- Nanobind declares the release's wheels job with its runner labels at [nanobind] wheel-platforms, and the mount takes what a package holds through its kind by @willemkokke
- [coverage] replaces [qa]: the coverage extension declares a package's floor, epsilon and required, a package with tests and no floor is judged at 100, and new projects list coverage by @willemkokke
- The coverage extension draws every language's pages from parts in the line format with a renderer of its own, and pytest reduces coverage.py's arcs to those parts by @willemkokke
- The coverage machinery leaves the base for the coverage extension, so a workspace that does not list coverage runs no coverage leg by @willemkokke
- Pytest and cmake measure for coverage through their [for.coverage] tables, so a workspace without coverage runs its tests unmetered, and coverage with nothing that measures is dormant and named by @willemkokke
- The coverage record stores parts in the line format with each file's fixed facts once, the union merges them without coverage.py's commands, and llvm-cov's counted ranges reach the pages by @willemkokke
- Every coverage row names the tests that reached each line, numbered once so a test keeps its index across runs and legs by @willemkokke
- The docs extension declares a docs phase and a deploy phase, and the task reference, the package generators and coverage's pages become their steps by @willemkokke
- The python extension judges each source's imports against its package's declared dependencies, and holds the terminal rule by @willemkokke
- An mkdocstrings extension draws the python API reference and writes its handler's tables through docs.config, the docstring checks move to it, and the kind's extractor goes by @willemkokke
- The run row's reader reads coverage from sections alone by @willemkokke
- A package's [docs] table is the docs extension's, every docs build removes what earlier builds generated, and the base's docs contract goes by @willemkokke
- The docs dependency group follows the listed extensions, and griffelib moves from the workshop wheel's runtime to its test extra by @willemkokke
- Nanobind answers which members its wheels job builds, and the wheel identity check moves into the build steps by @willemkokke
- Each extension writes its own manifest's requirement on a sibling, conan opens a recipe's requires tuple, and a sibling no writer takes refuses naming its set by @willemkokke
- The cpp extension names the C compilers a package needs, answering a host-tools query that fm doctor and fm env.check union by @willemkokke
- One base constant names the runtime's language, and what a member holds decides whether it carries a pyproject.toml and joins the uv workspace by @willemkokke
- The release train asks a member's extensions for its registry by @willemkokke
- A package holds nanobind by listing it, nanobind's build step and its deletions replace the kind's backend, and the python-nanobind kind refuses naming the list to write by @willemkokke
- Every package lists its extensions, a contract's kind refuses naming the list to write, and the kind fallbacks, the backend protocol and the kind guards go by @willemkokke
- Package.kind goes, public_modules and compile_commands leave the workshop's public names, and a step's kinds refuses naming extensions by @willemkokke

### Fixed

- The sync's deliver step survives an install that moved a member's metadata by @willemkokke
- The hook enters the receipts its sync wrote into the task's own environment, so a fresh checkout's first command runs by @willemkokke
- A command the hook's sync re-runs starts without the sync marker, so its own hook syncs by @willemkokke
- The hook's sync runs on the root it found and fails no command, and new.package wires its member in a fresh runner whose mount holds it by @willemkokke
- Credentials in command lines reach a profile trace as ***, and a test's mounts and environment writes are undone before the next test by @willemkokke
- A loop pass and each runner's jobs run conan with a home inside the environment, so a pass after another never builds against the earlier forge's remote by @willemkokke
- The MSVC measurer rides cmake's [for.coverage] table, so a workspace without coverage locks and installs no dotnet_coverage on Windows by @willemkokke
- Lint.docrefs judges the references in each docstring the site renders against the anchors the site renders, so one the strict build cannot resolve fails the gate by @willemkokke
- A pull request found by a branch's head commit is the branch's own only when it proposed that branch, never a stacked branch's parent's by @willemkokke

### Changed

- Root files are fragments and the layering rules are steps: a package-level extension writes [fragments."<path>"] while a package holds it, each layering rule is a step over the one parse, python's private-reaches is [phases.layering.private-reaches], and [root-files] and [rules] refuse naming the new spelling by @willemkokke
- The train takes isolated and configuration: a step serves the side of its phase it declares, the test phase's isolated side runs once per resolution [release] prove lists, replay is verify, and a bare fm build and fm run build debug while the gate instruments by @willemkokke
- The names: StepRecord, _steps.py, register_step and the public step_option say step and phase, PhaseContext.step names the running step, and role is gone by @willemkokke
- Pins for nanobind's wheels job in today's spelling, the loop's release act requires the publish job's prebuilt line, and the nanobind extension ships declaring nothing by @willemkokke
- A CI job's runner labels come from a function and its driver pin is derived, so the base's wheels job is a generic job by @willemkokke
- Pins for what coverage's move out of the base must keep, and 12e restated with the extension owning the pages before 11c by @willemkokke
- Raise the workshop's coverage floor to 90, just under the 90.1% the CI union measures, so a drop below 89.5% fails the gate by @willemkokke
- The coverage extension starts in the workshop wheel and draws the coverage pages as its answer to the docs extension's query, and a [for] table's queries are asked while its target is listed by @willemkokke
- Pins for the docs phase's parts, a control pair of site builds, and 11c restated as the ruled parts by @willemkokke
- The references query, which the python extension answers from its own parse, replaces the base's python parse in the layering rules by @willemkokke
- Pins for what each package seed births, composes and runs, in today's spelling by @willemkokke
- Each tool is required by the extension that runs it, and uv by the base by @willemkokke
- Pytest runs the suites and examples through runners the python extension answers, and run_suites and kind_examples leave the public names by @willemkokke
- A package's categories, the per-file ignores and the guidance read the extensions packages hold, the kinds guidance becomes compositions.present.md, and the dead kind render facts go by @willemkokke
- The kind registry goes: the seed chain and the base's category tables are data, and the conformance kit probes sets of extensions by @willemkokke
- The tests that mount the ruff or pytest extension skip in a run of the workshop's wheel alone by @willemkokke
- The seed pins skip while an extension their workspace lists is not installed by @willemkokke

## [0.9.0] - 2026-10-12

### Added

- The python extension answers a package's version, version files, requirements, module roots and public modules; the seam asks a package's extensions first, and the workshop package and the python templates list it by @willemkokke
- The release train's stamp, build and publish run as a package's lifecycle phases, with the engine's inputs as context keys, where its extensions add steps; the python extension adds them by @willemkokke
- The release's isolated legs and the replay of a published version run as the prove and replay phases through the lifecycle seam; the replay's python half moves into the python extension by @willemkokke
- A package-level extension declares its packages' path categories as data, and the python kind's table becomes the python extension's declaration by @willemkokke
- The private-reach rule becomes the python extension's [rules.private-reaches], checked by fm check, with its allowance in the root contract; an extension declares a layering rule as data by @willemkokke
- The cpp extension declares the C and C++ path categories, which the cpp-conan kind reads, and a new cpp-conan package lists cmake and conan by @willemkokke
- The conan and cpp extensions answer a package's version, version files, requirements and module names, and run its stamp, build and publish; extension.toml takes [root-files], and conanws.yml becomes conan's by @willemkokke
- A check names the package-level extensions whose packages it judges instead of package kinds, and a kind stands for what a package that lists none holds by @willemkokke
- The nightly point runs the loop's release scenario from a scratch branch of main through a once-per-matrix schedule entry, and fm ci.e2e --from-main leaves a branch whose build is the release train for the pass by @willemkokke
- A pull request's check legs run on the runners [ci] pull-request-runners names, every runner at merge and on a dispatch; the render chooses between the two literal lists by the event, and a label outside [ci] runners refuses naming both by @willemkokke
- The loop lands its members through one pull request and a plain pass births without the docs extension, so a release pass waits on two fewer gate runs and no docs job; the note records the ruling by @willemkokke
- The site is built once per run and published from the gate job with this run's coverage trees, the forge's pages deploy is a job with no checkout and no entry, the release dispatch is the gate job's entry, and a [ci.jobs] table naming a job the point already has joins it by @willemkokke
- Footman.scratch_dir makes a process's scratch under the cache, named for its owner, and the collector removes what a gone process left; the replay, the shell, the two isolation plugins and the playground cache use it by @willemkokke
- A push to main whose tree no run proved measures from the record's nearest proved tree and pays the whole gate when it proves none in reach, and the loop's unproven-push scenario lands a tree nobody proved and reads main's run narrowing and composing by @willemkokke
- Fm sync runs the sync phase, with conan's profile step replacing the kind's pre-install hook; a package that lists no extension runs it over what its kind stands for, the nanobind kind stands for conan beside cpp, and conan's workspace lists the recipes that name a reference by @willemkokke
- The check record carries a step's keys and the declaration reads [phases.<phase>.<step>] with one body under main beside the check tables, a template placing its paths and arguments where {paths} and {arguments} stand by @willemkokke
- The git helper remembers the facts it read live, the position, a branch's heads, its stack and a config key, forgets them on any call that moves the repository, and answers a reader called with cached=True from that memory by @willemkokke
- Phases are declared, the base's in a [phases] section of its contract file and an extension's in its extension.toml, each with engine keys, level, placement and whether a verb is generated; an undeclared phase refuses naming the declared ones and two steps at one address refuse unless one replaces or deletes the other by @willemkokke
- Fm doctor is footman's: every package registers its checks under footman.doctors, --fix runs the repairs and checks again, the workshop's forge and host checks move behind it, and the nightly runs it first by @willemkokke
- The package is born and every git call the workshop makes is a function of the root through livery.git by @willemkokke
- The plugin gives every git spawn under footman one ssh connection per host, the tools' identity, an editor that never opens, and a task's own marks by @willemkokke
- The state store moves to livery.git as a StateStore built with the workshop's policy, and _state goes by @willemkokke
- The tool store's view walk leaves the sync: Store.probe keeps the ref and record checks, audit and audit_views name a damaged view, mend_view views its tree again, and the store's doctor reports and mends under fm doctor --fix by @willemkokke
- Every direct git call in the workshop converges on livery.git, and a one-door test refuses a call of the handle by @willemkokke
- Reads and the store's local writes go in-process through dulwich by @willemkokke

### Fixed

- Conan's default profile is checked before uv builds a native member and before conan create, and one that names no compiler is detected again by @willemkokke
- A template refusal is worded from minijinja's error kind and line, not from its message text, which its releases reword by @willemkokke
- A cpp-conan member's upload to a forge's conan registry logs in with the token the registry ladder resolved, unless conan's own login variables are set by @willemkokke
- The loop probes for new.project where the birth runs, outside any project, since inside the checkout the project mounts the verb a bare footman lacks by @willemkokke
- A catalogue read roots each tool's index tree under tools/index/<name>, so the store's sweep keeps the records, versions and surfaces a job's entry re-fetched; the sweep keeps the store whole on Gitea's runner, which saves no archive by @willemkokke
- The Gitea adapter reads an epoch timestamp as a time the server has not set, and a job still running when the gate job collects is measured to the collection and named incomplete by @willemkokke
- A hosted job's entry persists the placements it exported for footman's data directory and uv's cache, so every later step reads the same store and cache, and the nightly job carries the write grant its profile trace push needs by @willemkokke
- A read of the state store brings the ref's key group in one fetch, a snapshot names a prefix of any namespace and a nested one brings what it names, and the sweep reads a family's commit times in one call, so the janitor's 382 about keys cost one fetch instead of one each by @willemkokke
- The rendered setup.sh runs uv sync once, before fm sync, and enters and persists the environment in one env.emit call by @willemkokke
- The loop's release pass runs green on a hosted Linux runner: the seven differences from a desk fixed, and cibuildwheel's container given the locked conan by @willemkokke
- A catalogue, a contract and the task tree are read once per process, a locked sync judges the tool lock once, and coverage omits pytest's temp trees by @willemkokke
- The coverage preview skips a measured module the disk no longer has, so a passing test's deleted tmp_path module cannot turn a local check red by @willemkokke
- The nanobind seed keys a native member's build on the git commit, so a fresh checkout at the same commit reuses it by @willemkokke
- The isolation plugin puts the slot registry back after every test, an inner pytester session keeps its own base temp, and the coverage page's absence test builds as a desk does whatever the run's event, so no test passes or fails by the worker's schedule or by the event it runs on by @willemkokke
- A daemon the devenv starts never inherits the checkout the verb ran from: VIRTUAL_ENV, uv's run marker and that venv's bin on PATH are dropped by @willemkokke
- The coverage preview reads the report's file names in posix spelling, so a package's files match on Windows, and the receipt test writes its lock as bytes, so the receipt it compares carries no platform line ends by @willemkokke
- Conan's prepare reads the default profile's file and starts no conan process when it names a compiler by @willemkokke
- The entry script runs uv at the lock's pin, installing it once under the runner's data directory when PATH holds another version, so a job's syncs share one uv and one cache by @willemkokke
- A flow reads the checkout's position in one process and its stack's two keys in one, the submit flow passes the branch it read to the steps that need it, and the test fixtures set the committer in the clone call by @willemkokke
- An extension pass births with the test extension beside the extension under test and lands the loop's first member, so every gate it drives meters a suite until coverage is its own extension by @willemkokke
- The submit and sync flows read a branch's stack once and hand it to the restack, reading it again only after a restack that moved the branch by @willemkokke
- A sync fetches origin once, the branches and the state store in one round trip, and its phases are live steps with a receipt, a --json row and a profile slice each by @willemkokke
- The submit flow reads HEAD's subject and body in one process and the subjects ahead of the base once, carried on the plan for the checks that read them, and the submit tests' other clones set their identity in the clone call by @willemkokke
- The state store reads a ref's tree with one git archive into a file, read back as bytes, instead of ls-tree, cat-file and a show per mismatched blob by @willemkokke
- Footman.task_tree serves the run's own tree to a task, and the verbs fragment reads it in process for this workspace instead of running fm --json --list again by @willemkokke
- A bytes stdin takes footman's run and the toolroom's handle down a binary lane, no newline translation and the capture decoded with surrogateescape, and the state store writes every blob, the tree and the commit with one fast-import through it by @willemkokke
- The state store's archive read switches the checkout's end-of-line conversion off for the call, so a row reads back as git holds it on Windows by @willemkokke
- A quiet sync says the store and the tools are up to date, and a store fetch that moved refs says how many, not a count that reads as a download by @willemkokke
- An offline sync fetches nothing: the bring-current reads the remote-tracking refs the last fetch left by @willemkokke

### Changed

- The update tests render with no contributed jobs, and docs_jobs puts back the jobs it found, so a mount earlier on the worker no longer makes an update submit by @willemkokke
- The release train, the layering check, the docs and the loop reach a package's build, versions, publish and requirements through one seam, livery.workshop._lifecycle by @willemkokke
- The python kind's code moves into livery.extensions.python, shipped in the workshop wheel; the base imports it under an allowance until the kinds go by @willemkokke
- The cpp-conan kind's code splits into the cmake, cpp and conan extensions, shipped in the workshop wheel; cmake names no language, and conan requires cpp by @willemkokke
- The per-task hook brings a checkout current by calling sync itself, frozen and offline, once before the command's first task; the repair and the reconcile go by @willemkokke
- Footman.alive is the one process-liveness probe, public and injectable for its Windows path, and the workshop's devenv drops its tasklist probe for it by @willemkokke
- Pin the gate and train behaviours the one-mechanism slice replaces: a step's after dependency runs once, a tests-only step runs for a tests-only change, a code and a words step share one option table, the isolated legs follow the listed order with the set's dist directories, and a package-level extension's root file is written once while any package lists it by @willemkokke
- Composition is requires alone: compatible, before and after on [extension] go with the every-pair check, the combinations listing and the extras half of the metadata clause by @willemkokke
- Every extension declares its steps as [phases.<phase>.<step>] with main, fix and safe-fix placing their paths and arguments, the lifecycle phases name their extension, listed-with is enabled at the step's address, and the package option tables follow by @willemkokke
- The check tables refuse naming the step spelling: the transition reader goes with placed, narrowing, transport, threshold, roles, listed-with, options, judge, run and arguments as keys, the transports and the further roles, and [for.<target>] carries every table by @willemkokke
- Slots, contributions, editor-extension and release-notes are declared queries with a combine rule, declared by the base in its contract file and by extensions, answered by the listed extensions in list order; the slots module and the release-notes registration go by @willemkokke
- Sync reads its receipts first: a frozen sync of a current checkout runs no local step and prints nothing, the tools step supplies only what its receipts name as off, and start fetches origin's heads and the store in one round trip by @willemkokke
- The lifecycle phases and the gate share one walk: every step is a footman step inside parallel(), a package step once per package, ordered by after and by provides and reads; pre and post go, every step reads into the one record and DeclaredPhase goes, and GateContext folds into PhaseContext by @willemkokke
- The isolated release leg skips the checkout-only tests and scans the workshop's own trees by @willemkokke

## [0.8.0] - 2026-10-09

### Added

- The changelog ships apart: livery-extensions-changelog writes each package's CHANGELOG.md with git-cliff, and the base keeps the release notes' protocol by @willemkokke
- The agent's files ship apart: livery-extensions-claude writes CLAUDE.md, the agent's guidance and .claude/ from [fragments] renders, and carries the hooks verbs by @willemkokke
- Package-level extensions mount first and compose on a package: compatible, before and after, the validity rule, canonical lists and their combinations by @willemkokke
- Lifecycle phases: a package-level extension adds pre, main and post steps to a package's phases, ordered by the context keys they provide and read by @willemkokke
- Queries: a package-level extension answers typed questions about a package under [queries], and answer combines them by each query's rule by @willemkokke
- Fm run builds a package's dependency closure, skipping what is unchanged, and starts the executable its extension answers; a verb inside a package resolves the workspace's root by @willemkokke

### Fixed

- The workshop's birth test asserts no seed another extension ships, so it passes on the workshop's wheel alone by @willemkokke
- The fragment engine's refusal test pins the engine's words and leaves minijinja's detail, which its newest release spells out further by @willemkokke
- Fm workflow.release --local fetches origin's tags before it derives, so a rehearsal after a wave names the versions the armed act will release by @willemkokke
- Fm new.project's default list names no claude extension: a project chooses its agent's files by @willemkokke
- Fm ci.e2e refuses to start on a branch whose release verb is the train, before it builds anything that would open a real release by @willemkokke
- A release title counts its members past 80 characters, and a package touched only by its composed files leaves a commit's scope by @willemkokke

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
