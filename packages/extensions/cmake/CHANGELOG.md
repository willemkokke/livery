<!-- Seeded from the changelog extension's seeds at birth; this
     file is the workspace's own. Each release records its entry at
     the top, and an edit stays.
-->
# Changelog

All notable changes to livery-extensions-cmake are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), the
project adheres to [Semantic Versioning](https://semver.org/), and
while it is pre-1.0 a minor version may include breaking changes.

## [0.0.0] - 2026-10-17

### Added

- Each installed template is a verb, fm new.package.<template> <name>, whose declared creation options are its options; python-library's --command replaces python-cli, workshop-extension's App comes with --app, and cpp-package with --package-type replaces cpp-library and cpp-executable by @willemkokke

### Changed

- The cmake extension leaves the workshop's wheel for livery-extensions-cmake, and a test edge renders into the package's test extra by @willemkokke
- The coverage extension leaves the workshop wheel for its own distribution, livery-extensions-coverage, taking Pygments with it by @willemkokke
- The nanobind extension leaves the workshop wheel for its own distribution, livery-extensions-nanobind by @willemkokke
- The conan extension leaves the workshop wheel for its own distribution, livery-extensions-conan by @willemkokke

