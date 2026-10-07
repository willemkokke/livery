<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# Changelog

All notable changes to livery-extensions-mypy are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.0.0] - 2026-10-07

### Added

- Mypy runs as its own extension, configured by the root mypy.ini, with a cache per platform under the workshop's state by @willemkokke
- A check's own verb hands the tool it wraps the words after --, and fm check and the role verbs refuse them, naming the verbs that take them by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke
- Every extension declares itself in extension.toml, read at mount without importing it, and the check records leave the public names by @willemkokke

### Fixed

- The descendant chain passes again: it drives the dev containers with the credentials their seed recorded and takes this checkout's code from the checkout index by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

