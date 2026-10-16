# livery-extensions-coverage

Coverage floors for the livery workshop. Listed as `coverage` in a
workspace's `[workspace] extensions`, it judges each package's coverage
against the floor in its `[coverage]` table, on the union of what every
CI leg measured, keeps the record that carries each suite's parts
across runs and the marks an auto-ratchet floor moves, and draws the
site's coverage pages while `docs` is listed. The test extensions
measure for it: `pytest` the python suites, `cmake` the native builds.
`fm coverage.accept` lowers a mark with a reason, and
`fm coverage.enforce` judges the floors on the data a run left.
