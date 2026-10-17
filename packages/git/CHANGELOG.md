<!-- Seeded from the changelog extension's seeds at birth; this
     file is the workspace's own. Each release records its entry at
     the top, and an edit stays.
-->
# Changelog

All notable changes to livery-git are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.3.0] - 2026-10-17

### Added

- The coverage union carries a reading at a file's earlier content across the change, keeping it at the lines the change left in place and dropping the rest by name, and livery.git reads a blob by its id by @willemkokke
- Fm sync mirrors the store's row series alone, and a read outside the mirror names the scope by @willemkokke

### Changed

- No extension list's order means anything: composition order decides, the root list is written in its canonical form, and fm new.project takes --extensions in place of --stack by @willemkokke
- Remove what the redesigns left behind: the register-and-withdraw functions, dead definitions, leftover sweeps, empty stylesheets and the text naming kinds and seeds by @willemkokke

## [0.2.0] - 2026-10-14

### Added

- [coverage] replaces [qa]: the coverage extension declares a package's floor, epsilon and required, a package with tests and no floor is judged at 100, and new projects list coverage by @willemkokke
- The coverage record stores parts in the line format with each file's fixed facts once, the union merges them without coverage.py's commands, and llvm-cov's counted ranges reach the pages by @willemkokke
- Every package lists its extensions, a contract's kind refuses naming the list to write, and the kind fallbacks, the backend protocol and the kind guards go by @willemkokke

### Fixed

- Credentials in command lines reach a profile trace as ***, and a test's mounts and environment writes are undone before the next test by @willemkokke

## [0.1.0] - 2026-10-12

### Added

- The package is born and every git call the workshop makes is a function of the root through livery.git by @willemkokke
- The plugin gives every git spawn under footman one ssh connection per host, the tools' identity, an editor that never opens, and a task's own marks by @willemkokke
- The state store moves to livery.git as a StateStore built with the workshop's policy, and _state goes by @willemkokke
- Every direct git call in the workshop converges on livery.git, and a one-door test refuses a call of the handle by @willemkokke
- Reads and the store's local writes go in-process through dulwich by @willemkokke

### Changed

- The isolated release leg skips the checkout-only tests and scans the workshop's own trees by @willemkokke

## [0.0.0]

### Added

- Package born from the workshop template.
