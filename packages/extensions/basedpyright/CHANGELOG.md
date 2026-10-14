<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# Changelog

All notable changes to livery-extensions-basedpyright are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.2.0] - 2026-10-14

### Added

- A check names the package-level extensions whose packages it judges instead of package kinds, and a kind stands for what a package that lists none holds by @willemkokke
- [coverage] replaces [qa]: the coverage extension declares a package's floor, epsilon and required, a package with tests and no floor is judged at 100, and new projects list coverage by @willemkokke
- The coverage machinery leaves the base for the coverage extension, so a workspace that does not list coverage runs no coverage leg by @willemkokke
- Every package lists its extensions, a contract's kind refuses naming the list to write, and the kind fallbacks, the backend protocol and the kind guards go by @willemkokke
- Package.kind goes, public_modules and compile_commands leave the workshop's public names, and a step's kinds refuses naming extensions by @willemkokke

### Changed

- Every extension declares its steps as [phases.<phase>.<step>] with main, fix and safe-fix placing their paths and arguments, the lifecycle phases name their extension, listed-with is enabled at the step's address, and the package option tables follow by @willemkokke
- Slots, contributions, editor-extension and release-notes are declared queries with a combine rule, declared by the base in its contract file and by extensions, answered by the listed extensions in list order; the slots module and the release-notes registration go by @willemkokke
- The lifecycle phases and the gate share one walk: every step is a footman step inside parallel(), a package step once per package, ordered by after and by provides and reads; pre and post go, every step reads into the one record and DeclaredPhase goes, and GateContext folds into PhaseContext by @willemkokke
- The names: StepRecord, _steps.py, register_step and the public step_option say step and phase, PhaseContext.step names the running step, and role is gone by @willemkokke
- This repository's packages list their extensions by @willemkokke

## [0.1.0] - 2026-10-08

### Added

- A check whose verdict is its tool's exit code is its tool's words, run by the workshop, and six checks become words by @willemkokke

### Changed

- The tool extensions sit inside the workshop's entry of the site's Packages tree, and five contracts state their description by @willemkokke
- The tool store's table, lock and verbs take toolroom's name, and the store ships the table's schema and its own reader by @willemkokke

## [0.0.0] - 2026-10-07

### Added

- Basedpyright runs as its own extension, with type completeness as its option, and an extension's entry turns its options on by @willemkokke
- The editor answers with the configured type checker alone by @willemkokke
- A check's own verb hands the tool it wraps the words after --, and fm check and the role verbs refuse them, naming the verbs that take them by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke
- Every extension declares itself in extension.toml, read at mount without importing it, and the check records leave the public names by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

