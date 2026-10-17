<!-- Seeded from the changelog extension's seeds at birth; this
     file is the workspace's own. Each release records its entry at
     the top, and an edit stays.
-->
# Changelog

All notable changes to livery-extensions-claude are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.1.0] - 2026-10-17

### Added

- [coverage] replaces [qa]: the coverage extension declares a package's floor, epsilon and required, a package with tests and no floor is judged at 100, and new projects list coverage by @willemkokke
- Every package lists its extensions, a contract's kind refuses naming the list to write, and the kind fallbacks, the backend protocol and the kind guards go by @willemkokke
- An extension declares the templates fm new.package creates a package from, the seed trees become the first templates, and python's scripts are what fm run starts by @willemkokke
- The agent's guidance gives each installed extension a block read from its declaration, and the kit holds every declaration to its docs by @willemkokke

### Fixed

- The claude extension composes the fragments once per delivery: the guidance files render first and the entry file takes their composition by @willemkokke

### Changed

- The names: StepRecord, _steps.py, register_step and the public step_option say step and phase, PhaseContext.step names the running step, and role is gone by @willemkokke
- This repository's packages list their extensions by @willemkokke
- Every extension is one kind: an extension declares no levels, either list takes any extension, and the installed set is the root's set and every package's set by @willemkokke
- Remove what the redesigns left behind: the register-and-withdraw functions, dead definitions, leftover sweeps, empty stylesheets and the text naming kinds and seeds by @willemkokke
- The cpp extension leaves the workshop wheel for its own distribution, livery-extensions-cpp by @willemkokke
- Python leaves the workshop wheel, which now ships the base alone by @willemkokke

## [0.0.0] - 2026-10-09

### Added

- The agent's files ship apart: livery-extensions-claude writes CLAUDE.md, the agent's guidance and .claude/ from [fragments] renders, and carries the hooks verbs by @willemkokke

### Fixed

- Fm new.project's default list names no claude extension: a project chooses its agent's files by @willemkokke

