<!-- Seeded from the package-python seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# Changelog

All notable changes to livery-extensions-clang-tidy are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.0.0] - 2026-10-07

### Added

- Clang-tidy runs as its own extension, over the compilation database its package's kind says the build writes by @willemkokke
- A check's own verb hands the tool it wraps the words after --, and fm check and the role verbs refuse them, naming the verbs that take them by @willemkokke
- Every distribution root declares its public names in its __init__.py and serves what need not load on first use, and the api modules go by @willemkokke

### Changed

- Footman's internal modules are private, every public module under a root is its api or declared there, and other packages' sources reach footman through its api by @willemkokke

