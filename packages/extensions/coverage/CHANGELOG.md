# Changelog

## [0.0.0] - 2026-10-17

### Added

- Fm submit --every-runner labels a pull request ci:every-runner so the gate runs every runner for it, the forge adds labels to a pull request on every backend and refuses an unknown one by name, the gate workflow runs on labeled and its legs job picks the full matrix while the label is there, the repository configuration creates the label, and the coverage union expects every leg from a labelled pull request by @willemkokke
- The coverage union carries a reading at a file's earlier content across the change, keeping it at the lines the change left in place and dropping the rest by name, and livery.git reads a blob by its id by @willemkokke
- A shard runs its share of the tests by @willemkokke
- The gate job folds a platform's shards by @willemkokke

### Fixed

- Read the verified record per platform leg: a check leg skips only on a full row naming its own leg, and the stamp names the legs' labels by @willemkokke

### Changed

- The coverage extension leaves the workshop wheel for its own distribution, livery-extensions-coverage, taking Pygments with it by @willemkokke
- The conan extension leaves the workshop wheel for its own distribution, livery-extensions-conan by @willemkokke
- Python leaves the workshop wheel, which now ships the base alone by @willemkokke
