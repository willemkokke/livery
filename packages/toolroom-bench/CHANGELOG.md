<!-- Seeded from the template channel (package-python kind) at
     birth; this file is the workspace's own. Edit it directly:
     the template never rewrites it.
-->
# Changelog

All notable changes to livery-toolroom-bench are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.3.0] - 2026-10-08

### Added

- The docs extension's own public API serves the nav block helpers and the generated tree's name, rewrite_nav_block leaves the workshop, and toolroom-bench writes its block through the extension by @willemkokke
- No package reaches another's privates: footman exports host, CommandView, styled and its brand's names, toolroom its colour and stub tables by @willemkokke

### Fixed

- Nobody imports an extension but the workshop's mount: toolroom-bench writes its tools nav block as data, the docs extension's public names go, and a test holds the rule by @willemkokke

### Changed

- The tool extensions sit inside the workshop's entry of the site's Packages tree, and five contracts state their description by @willemkokke
- The tool store's table, lock and verbs take toolroom's name, and the store ships the table's schema and its own reader by @willemkokke

## [0.2.0] - 2026-10-07

### Added

- Examples are files: a package's examples live under docs/examples/ as python files a page includes by snippet, and one harness runs each through the kind's runner, replacing footman's page-as-session harness and its three markers by @willemkokke
- The docs layer: the site's assembly, verbs and slots move to livery.workshop.layers.docs, the base keeps the docs contract, the nav blocks and a registry of layer-rendered files, and the layers namespace spans distributions by @willemkokke
- Every distribution root is a namespace whose public names live in its api module, and the docs layer moves to livery.extensions.docs by @willemkokke
- Extensions replace layers: each declares itself in the workshop.extensions entry point group, and a workspace lists them by name in [workspace] extensions by @willemkokke
- Plugins mount through the project builtin rung: the workshop mounts the listed extensions from its own entry module, and the rendered tasks.py keeps only its comment by @willemkokke
- Clang-format and clang-tidy install from PyPI, the ssciwr wheels of LLVM's own binaries, on every host but windows-arm by @willemkokke
- Git LFS is a workspace setting: extensions ship LFS rules, composed while [workspace] lfs is on and named while it is off, with a git_lfs record, its hooks and LFS checkouts in CI by @willemkokke
- The copier answers move into workshop.toml: identity in [workspace], members from discovery with their own description, dev-extras and template, and the answers files go by @willemkokke
- Tasks.py and each package's cliff.toml are composed by the fragment engine, so copier only births by @willemkokke
- Ruff runs as its own extension, a birth finishes in the newborn's own fm, and the local loop tests an extension before its release by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke

### Fixed

- Tools.artifacts refuses a record that does not load in one line naming the file, and the layout refusal says to list the host first by @willemkokke
- The abort reconcile spawns the runner by its real module, two footman invariant tests scan the real tree again, the push guard tests the branch a push names, and the review's smaller faults go by @willemkokke

### Changed

- A verb that reaches no forge and no tool store loads neither: the forge, store and strongroom roots serve their names on first use, and the workshop and the bench import the store where they use it by @willemkokke
- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

## [0.1.0] - 2026-09-29

### Added

- The release act, the points by hand and the clock on GitLab: the loop is whole on both lanes by @willemkokke
- The surface rides in the record, and the histories are absorbed by @willemkokke
- The index: every record materialised, and the pointer that names it by @willemkokke
- Stubs in the index, the renderer's identity, and the golden render by @willemkokke
- The three declaration sites, the catalogue, and the lock by @willemkokke
- Receipts, materialisation on sync, and the modes by @willemkokke
- The stubs live in typings/, written from the index, and the toolroom wheel ships none by @willemkokke
- Stubs for the locked tools only, with sorted imports and a noqa header, declared by a handles module beside them by @willemkokke
- The steady state costs the stats: a nothing-moved gate, records digested once, a lazy catalogue by @willemkokke
- The store renders stubs from surfaces, and the index holds none by @willemkokke
- Pyrefly has a record, a stub and a place in the python kind's lock by @willemkokke
- The record is one file per tool with one line per option by @willemkokke
- Ingest verification, the nine structural checks over every host by @willemkokke
- The six-host verification point, every downloaded tool installs, runs and reads on its host by @willemkokke
- The refresh records each new version's artifacts per host, and an archive root may carry the version by @willemkokke
- Git-cliff comes from its own release, required by the base kind every package kind derives from by @willemkokke
- Ruff and pyrefly come from their own releases, and ty, uv and prek list from theirs by @willemkokke
- Cmake and ninja come from their own releases, and the asset picker learns universal and arch-less builds by @willemkokke
- Basedpyright comes through bun: the store supplies bun-install, and bun is its locked dependency by @willemkokke
- The site's URL scheme: packages, tasks, releases and tools, with _generated in no published path by @willemkokke
- A package's docs section is the package's, and the site assembles its config at build time by @willemkokke
- A tool's own switches live in its record's env, and gh's update check leaves the code for the record by @willemkokke
- Conan is a tool store record from its own releases, and the cpp backend's refusal names the store by @willemkokke
- The store owns download, unpack and the host table; the bench reads through it and runs a release binary in its own tree by @willemkokke
- An npm kind names its runtime, node has a record from nodejs.org, and basedpyright runs on node by @willemkokke
- The extension compiles against the library at HEAD and a third-party package through the store's cmake-conan provider, and the store's downloaded kinds are one download by @willemkokke
- The pypi and python kinds name their source; uv is the installer, not the kind by @willemkokke
- The native kinds format and lint their sources, with the two clang tools in the store by @willemkokke
- The layering lint reads what the sources use, and each kind answers for its own by @willemkokke
- The tools lock and sync in uv's shape, and a newborn locks its own by @willemkokke
- A managed file carries the regions the repository owns by @willemkokke
- The dotnet kind: .NET as a runtime record on every host, NuGet tools through it, pwsh as a download by @willemkokke

### Fixed

- The stubs and handles live beside the tools package, never inside its directory by @willemkokke
- A stalled version read is tried again alone with a longer budget before it fails the check by @willemkokke
- A bun global install gets its own project under the tool's directory, where bun's walk up for one stops by @willemkokke
- The bench's suite runs in a release leg: the records from its own position, the runner's directories isolated by @willemkokke
- The bench's suite isolates through a pytest plugin and a named helper, never a conftest by @willemkokke

### Changed

- The chain proves one first-party and one third-party symbol in the child's extension by @willemkokke
- The contract key type becomes kind by @willemkokke

## [0.0.0] - 2026-09-13

### Added

- The stub machinery is livery.toolroom.bench, its own distribution by @willemkokke

### Changed

- The checkout tests resolve the repository from their own path by @willemkokke

