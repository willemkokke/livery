<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# Changelog

All notable changes to livery-extensions-ty are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.1.0] - 2026-10-08

### Added

- A check whose verdict is its tool's exit code is its tool's words, run by the workshop, and six checks become words by @willemkokke

### Changed

- The tool extensions sit inside the workshop's entry of the site's Packages tree, and five contracts state their description by @willemkokke
- The tool store's table, lock and verbs take toolroom's name, and the store ships the table's schema and its own reader by @willemkokke

## [0.0.0] - 2026-10-07

### Added

- Ty runs as its own extension, configured by the root ty.toml, and recommends its editor extension by @willemkokke
- A check's own verb hands the tool it wraps the words after --, and fm check and the role verbs refuse them, naming the verbs that take them by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke
- Every extension declares itself in extension.toml, read at mount without importing it, and the check records leave the public names by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

