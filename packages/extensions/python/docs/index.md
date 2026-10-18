# livery-extensions-python

Python distributions: each package's version stamped, its wheel and
sdist built, published to a python index and verified, its suite run
against the installed wheel for a release, and the layering rules over
the python sources. A package turns it on by listing the extension in
its own `workshop.toml`:

```toml
extensions = ["python"]
name = "acme-core"
```

The package's `pyproject.toml` is its manifest: it declares the
version, the requirements and the scripts. `fm new.project` lists
python at the root, since the workspace's own tests and `tasks.py` are
python. A workspace that does not list it has no python steps, no
`python-library` template, and no way to create a `workshop-extension`
package, whose template extends `python-library`. The nanobind
extension requires python, since its packages are python
distributions, and its distribution requires this one.

## A new package

`fm new.package.python-library <name>` creates a python package while
the workspace lists python: a typed module under `src/`, its tests, and
a `pyproject.toml` uv builds. `--command=<script>` adds the script
`<script>` to `[project.scripts]`, which runs `main()` in the package's
`__main__.py`, and a test of it; without it the package has no script.
A workspace that does not list python has no such verb: list the
extension in `[workspace] extensions` and run `fm sync`, which installs
`livery-extensions-python`, and the verb exists. Until then
`fm new.package.workshop-extension` refuses, naming python and its
distribution.

## A package's files

python gives the paths of each package whose set holds it these
categories:

| Category | Patterns |
| --- | --- |
| `test` | `tests/**/test_*.py`, `tests/**/*_test.py` |
| `test-support` | `tests/**` |
| `source` | `src/**` |

The most specific pattern wins, so a test module under `tests/` is a
test and every other file there, `conftest.py` among them, supports
the tests.

## A release

python adds a step to each phase of a python package's release:

- `stamp.python` writes the version into `pyproject.toml` and into
  each `__init__.py` under `src/` that carries `__version__`, and
  raises each requirement on a member released with the package,
  `"<name>>=<floor>"` wherever the pyproject declares it, to that
  member's version; a member released alone moves no floor. The entry
  module serves the same work as [livery.extensions.python.stamp][],
  which nanobind's stamp step calls.
- `build.python` builds the wheel and the sdist with `uv build` into
  the package's `dist/`, from an empty `dist/` each time. The wheel
  carries the package's `docs/` as the module's `_docs`, copied for
  the build and removed after it. The build refuses a wheel tagged for
  a platform: python's build ships a pure wheel, tagged `none-any`, so
  a package with compiled code lists the extension that builds its
  platform wheels, such as nanobind.
- `test.python` installs the built wheel into a fresh environment and
  runs the package's tests against the installed copy, once per
  resolution the release proves: `floor` installs every direct
  dependency at its declared floor, `latest` the newest, and `head` the
  versions the workspace lock pins. The wheel resolves from the
  co-released set's `dist/` directories and the workspace's indexes
  alone, and installs with its `test` extra when the package declares
  one. The test toolchain installs after the wheel, at the lock's
  pins, and a toolchain install that moves a direct dependency the leg
  resolved refuses, naming it.
- `publish.python` uploads the wheel and the sdist with `uv publish` to
  the registry target the release resolves. An empty upload address
  refuses, since uv's own default is PyPI. An empty
  `UV_PUBLISH_TOKEN` never reaches uv, so trusted publishing still
  runs, and a version the index already holds is walked past.
- `verify.python`, which `fm release.verify` runs, installs the
  released version alone into a plain environment and runs the
  package's tests at the released tree against it.

The release train probes the python index the registry ladder resolves
for the package, through the simple API, before it uploads.

## fm run

python answers `executables` with the scripts a package's
`pyproject.toml` names under `[project.scripts]`. `fm run` starts the
one it names from the workspace's environment, which `fm sync`
installs, with the words after the dashes, and exits with its code. A
script the environment lacks refuses, naming `fm sync`.

## The layering rules

python answers the `references` query for python sources from one
parse per gate, memoised by the file's path, size and modification
time. Its three steps of the layering phase read the same parse, over
the files a narrowed run reaches or every source:

- `private-reaches`: no package's source reaches another
  distribution's private name, apart from the reaches the root
  contract's `[[python.private-reaches]]` allows, each with its reason.
  An entry no source uses any more refuses.
- `runner-terminal`: a module that imports the runner asks
  `livery.footman.attended()` whether a person is there, never the
  terminal (`sys.stdin.isatty()` and its relatives), since the
  terminal knows nothing of `--no-input` or `--dry-run`. The runner's
  own sources, and a module that never imports the runner, are exempt.
  The step judges the root's `tasks.py` with the packages' sources.
- `declared-imports`: at import time, a module under a package's `src`
  imports the standard library, the workspace's own members, and the
  distributions its `pyproject.toml` declares in
  `[project.dependencies]`. Inside a function it may also import a
  distribution an optional extra declares. An import under
  `TYPE_CHECKING`, or in a `try` that handles `ImportError`, is not
  judged. A module a program loads as a plugin, named under the
  `footman.tasks` or `footman.builtin` entry points for the runner or
  `pytest11` for pytest, may also import what that program brings: its
  distribution and the distributions it requires. A refusal names the
  source, the module, the distribution and the `pyproject.toml` to
  declare it in.

An allowed reach is a table of the root contract:

```toml
[[python.private-reaches]]
from = "acme-tools"
reaches = "acme.core._registry.purge"
reason = "the loop deletes published versions until the admin protocol exists"
```

## Requirements

python's requirement writers take a sibling that holds python alone. A
build or runtime edge writes the sibling's requirement into the
package's `[project.dependencies]` at the edge's floor; a test edge
writes it into the `test` extra of `[project.optional-dependencies]`,
which is python's answer to `test-requirements`, and which the
release's isolated test installs the wheel with. A manifest with no
`[project]` table refuses, and so does a test extra in a form the
edit does not take, naming the line to write by hand.

## What a change to a pytest plugin reaches

pytest loads every module a member's `pyproject.toml` names under the
`pytest11` entry points into every session it starts. While pytest is
listed, python answers the base's `reaches` query, so a change to such
a module runs every member pytest runs over, not only its own package
and that package's dependents. A changed path widens when:

- it is a module a member names under `pytest11`, or a file under such
  a package;
- the change removed it, and the tree the change is measured from
  named it there;
- it is a member's `pyproject.toml` whose `pytest11` table differs from
  that tree's.

The members pytest runs over are those whose set holds an extension
pytest's test or examples step names, unless the member turned both
steps off. `fm graph.affected` and the gate print one line for each
path that widens, with why. A module a plugin imports, and does not
name, reaches only its own package's dependents.

## The queries it declares

- `python.test.runner` and `python.examples.runner`: who runs the
  python packages' suites, several in one call, and one package's
  documentation examples. python answers both while pytest is listed,
  and pytest's test and examples steps ask them.
- `python.test.preview`: who prints a machine's coverage preview after
  its suites ran. The coverage extension answers it while python is
  listed.

## Its public names

`livery.extensions.python` serves the names an extension that builds a
python package's wheels with a tool of its own reads, as nanobind
does:

- `module_docs(package)` copies the package's `docs/` into its source
  tree as the wheel's embedded `_docs` for one build, and removes the
  copy after it.
- `run_isolated_test(package, root, ...)` installs a built wheel into a
  fresh environment and runs the package's tests against the installed
  copy.
