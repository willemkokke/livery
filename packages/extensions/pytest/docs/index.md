<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-pytest

pytest as steps of the workshop. A workspace turns it on by listing the
extension:

```toml
[workspace]
extensions = ["pytest"]
```

`fm test` then runs the suites, `fm examples` the documentation examples,
and `fm check` runs both with the rest of the gate:

- `test.pytest` runs the suites of the python packages a run reaches in one
  pytest call, across cores, with the workspace's own `tests` directory
  beside them. While the coverage extension is listed, every run is
  measured: on a machine the run prints each package's number beside its
  floor, and in CI the legs' measurements combine into the union the
  floors judge. Without it, the suites run with no meter.
- `examples.pytest` runs each python package's documentation examples, the
  files under `docs/examples/`, one test per file.

While the coverage extension is listed, this extension measures for it,
through its `[for.coverage]` table, and coverage judges what it measures.
A workspace that lists coverage with nothing that measures has coverage
dormant, and `fm extensions` names pytest among what would wake it.

This extension also reduces coverage.py's measurements to parts in the
coverage extension's line format, per file coverage.py's own analysis of
it. On each check leg the coverage extension asks it once for every suite
the leg ran: the suite's part holds the files of its closure, as the
suite's own tests and the code outside every test reached them, and that
part is what the leg stores. On a machine the data the last run left at
the root becomes one part, which the coverage pages and the preview read.
Each file's numbers are checked against coverage.py's before a part is
handed over, and a file whose numbers differ stops the reduction, named.

`fm test.pytest -- -k name` hands pytest the words after `--`, and
`fm examples.pytest` takes them the same way.

A package whose suite is not safe to run across cores turns that off in its
own `workshop.toml`, and its suite then runs in a pytest call of its own
under one worker:

```toml
[phases.test.pytest]
parallel = false
```

That call puts `-n 0` after the words from the verb, so
`fm test.pytest -- -n 4` cannot spread that suite across cores.


## The configuration

`fm sync` writes `pytest.toml` at the workspace root, and `.coveragerc`
beside it while coverage is listed, each under the name its tool looks
for, so a bare `pytest` or `coverage` call reads the same settings as the
gate:

- `pytest.toml`: where the tests are, the import mode that names a test
  module by its path, the options every run takes, the cache, kept under
  `.workshop/.cache/pytest/`, and the `tmp_path` retention: a passing
  test's directory goes at its teardown, a failing test's stays for
  inspection, so a session ends when its last test ends instead of
  deleting an older session's whole tree.
- `.coveragerc`: what coverage measures (the project's namespace, with
  branches), each file named from the workspace root, the names the union
  reads, and how the data of Windows and POSIX runs combine.

While coverage is listed, the dev group also takes `coverage`, whose
startup hook meters every python the tests start once the runner arms
it, and `pytest-cov` for a machine's measured run, and the root
`.gitignore` takes the lines for the data the meter leaves.

Each file ends in a region that is the repository's own: settings written
there stay when the render runs again, and an edit anywhere else is drift.
