<!-- Seeded from the template channel (package-python kind) at
     birth; this file is the workspace's own. Edit it directly:
     the template never rewrites it.
-->
# Changelog

All notable changes to livery-strongroom are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.4.0] - 2026-10-07

### Added

- Ruff runs as its own extension, a birth finishes in the newborn's own fm, and the local loop tests an extension before its release by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke

### Fixed

- One process at a time supplies a tool version: a second supply waits for the first and finds it present, and a view record retired mid-read is left out instead of crashing the probe by @willemkokke
- The lock_stale docstring says what the code does: a lock older than the bound is broken even when its holder looks alive by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

## [0.3.0] - 2026-10-04

### Added

- Strongroom's conformance kit is livery.strongroom.testing, and importing the store no longer loads it by @willemkokke
- The deterministic CBOR codec is livery.strongroom.cbor, reached by its own path, and the livery-cbor distribution retires by @willemkokke
- Every distribution root is a namespace whose public names live in its api module, and the docs layer moves to livery.extensions.docs by @willemkokke
- The copier answers move into workshop.toml: identity in [workspace], members from discovery with their own description, dev-extras and template, and the answers files go by @willemkokke
- Tasks.py and each package's cliff.toml are composed by the fragment engine, so copier only births by @willemkokke

### Changed

- A verb that reaches no forge and no tool store loads neither: the forge, store and strongroom roots serve their names on first use, and the workshop and the bench import the store where they use it by @willemkokke

## [0.2.0] - 2026-09-29

### Added

- The release act, the points by hand and the clock on GitLab: the loop is whole on both lanes by @willemkokke
- Cmake and ninja come from their own releases, and the asset picker learns universal and arch-less builds by @willemkokke
- The sidebar's machine sections are marker blocks the author may place, and land in a fixed order otherwise by @willemkokke
- The store owns download, unpack and the host table; the bench reads through it and runs a release binary in its own tree by @willemkokke
- A managed file carries the regions the repository owns by @willemkokke

### Fixed

- The test HTTP servers stop on a short poll by @willemkokke
- Fetch_url follows a bounded redirect chain, so a release asset lands by @willemkokke
- A landing refused by its twin on Windows settles on the twin's file, for the object and for its mark by @willemkokke

### Changed

- The contract key type becomes kind by @willemkokke

## [0.1.0] - 2026-09-12

### Added

- A group of ref moves lands as a whole or not at all by @willemkokke

### Fixed

- Dropping a view and evicting an object clear the read-only mark Windows refuses on by @willemkokke

### Changed

- Strongroom's first release, the debrief by @willemkokke
- Windows is in the gate, and the four places that said otherwise say so by @willemkokke

## [0.0.0] - 2026-09-11

### Added

- The package is born, and the formats have vectors
- The local store, objects and refs
- Sources and tiers, fill and offline
- The lifecycle, the pending publish, the sweep, tombstones
- The materialiser, the whole ladder, prefetch and shed
- The conformance harness in the package, the docs pages

### Fixed

- Give the lifecycle tests a basename no other package uses

### Changed

- The first release is v0.0.0, per the newborn ruling

