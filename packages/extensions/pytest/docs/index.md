<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-pytest

pytest as a check of the workshop. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["pytest"]
```

`fm test` then runs the suites, `fm examples` the documentation examples,
and `fm check` runs both with the rest of the gate:

- `test.pytest` runs the suites of the python packages a run reaches in one
  pytest call, across cores, with the workspace's own `tests` directory
  beside them. Coverage is measured on every run: on a machine the run
  prints each package's number beside its floor, and in CI the legs'
  measurements combine into the union the floors judge.
- `examples.pytest` runs each python package's documentation examples, the
  files under `docs/examples/`, one test per file.

A package whose suite is not safe to run across cores turns that off in its
own `workshop.toml`, and its suite then runs in a pytest call of its own
under one worker:

```toml
[checks.pytest]
parallel = false
```

## The configuration

`fm sync` writes two files at the workspace root, each under the name its
tool looks for, so a bare `pytest` or `coverage` call reads the same
settings as the gate:

- `pytest.toml`: where the tests are, the import mode that names a test
  module by its path, the options every run takes, and the cache, kept
  under `.workshop/.cache/pytest/`.
- `.coveragerc`: what coverage measures (the project's namespace, with
  branches), and how the data of Windows and POSIX runs combine.

Each file ends in a region that is the repository's own: settings written
there stay when the render runs again, and an edit anywhere else is drift.
