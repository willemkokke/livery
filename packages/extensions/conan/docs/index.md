# livery-extensions-conan

A C or C++ package's [conan](https://docs.conan.io/2/) recipe: its
version stamped, its package built into the conan cache, and its
release published to a conan remote, a folder, or the forge's releases.
A package turns it on by listing the extension in its own
`workshop.toml`:

```toml
extensions = ["cmake", "conan"]
name = "acme-geometry"
```

conan requires cpp, which ships in `livery-extensions-cpp`, a
requirement of this distribution, so a package that lists conan holds
both. The recipe, `conanfile.py`, is the package's manifest: it
declares the version and the requirements. A recipe that names a
reference, a `name` and a `version`, releases the conan package. A
consumer's recipe, requirements alone, releases nothing: it answers no
version, no distribution and no registry, so the set's other answers
stand.

## A new package

`fm new.package.cpp-package <name>` creates a C++ package built with
CMake and packaged by conan while the workspace lists conan and cmake.
`--package-type` takes conan's package types, `library` when absent,
and writes the answer into the recipe's `package_type`; the CMake
target follows from it. A workspace that does not list conan has no
such verb: list the extension in `[workspace] extensions` and run
`fm sync`, which installs `livery-extensions-conan`, and the verb
exists.

## Conan's home

conan keeps its cache, its remotes and its logins in its home, and its
cache takes one writer at a time. The extension declares the home in
its `extension.toml`, and the workshop places it in a directory of its
own wherever it names one:

```toml
[env.CONAN_HOME]
value = "{place}/conan"
always = true
doc = "conan's cache takes one writer at a time, so each CI job, loop pass and scale fixture keeps a home of its own"
```

On a CI job the home is `conan` under the job's own directory:
`RUNNER_TEMP` on a GitHub or Gitea job, `.workshop` under
`CI_PROJECT_DIR` on a GitLab one. `always` makes it beat a
`CONAN_HOME` the runner sets for every job, since a runner may run
several jobs at once: the job's entry places it, and every later `fm`
run of the job keeps that home, as a run nested in another keeps the
one its parent set. A pass of the loop keeps it in its development
environment's directory, so a remote a release writes there goes with
the environment's forge, and the scale fixture keeps it in the
fixture's directory. On a desk nothing names a directory, and conan's
own default stands.

## The sync

Before `uv sync` installs a member whose build goes through the
cmake-conan provider, conan's default profile must name a compiler:
the provider writes a profile itself when there is none, with no
compiler in it. conan's sync step detects the profile first, and
detects it again when it names no compiler. The workspace has one
conan home and so one default profile, so the step runs once over the
workspace while a package's set holds conan, however many do. A
profile file that already names a compiler costs no process.

While the workspace has a member whose set holds conan, listing it or
an extension that requires it, `fm sync` also writes `conanws.yml` at
the root, the conan extension's fragment, listing each such member by
its path. A conan command run inside the checkout resolves a member
from its source tree through that file, and nothing is registered in
the conan home. So each checkout of a repository, a worktree included,
resolves to its own sources, and all of them share one package cache.
The file goes with the last such member, and the drift check judges it
like any composed file.

## A sibling's requirement

conan answers `requirement-writers` with the recipe's writer, which
takes a conan package alone: a sibling that holds conan and whose
recipe names a reference. It writes the sibling's floor into the
recipe's `requires` tuple, and opens the tuple when the recipe
declares no requirement. A recipe whose `requires` it cannot place a
tuple in refuses, and nothing is written.

## The release

The `stamp` step writes the release's version into the recipe's
`version` attribute, and raises each requirement on a member released
with the package, `"<name>/[>=<floor>]"`, to that member's version. A
member released alone moves no floor. A python package that lists
nanobind consumes a recipe that names no version: nanobind's own
stamp step raises that recipe's floors through
[livery.extensions.conan.raise_floors][], which the entry module
serves. The `build` step runs `conan create`, which builds
the package into the conan cache, on the build phase's isolated side;
the gate builds on the tree side, where conan creates nothing. A
release build saves the package out of the cache as
`dist/<name>-<version>-<host>.tgz`, one file per host, the host named
as the tool store names it.

A conan package resolves the registry ladder for its own kind: the
`[registries.conan]` declaration first, then a conan registry the
forge hosts, and on a forge that hosts none, the forge's releases.
On that route each wheel-platform leg creates the package and saves
its cache, and the wave attaches every collected file to the member's
own release. The tag is pushed before the upload, because a release is
addressed by its tag, so a tag alone is not a receipt there: the probe
reads the attached caches, and a re-run after a half-finished upload
finishes it. A consumer lists the release, checks the bytes against
the digest the forge reports, and restores the cache into its conan
home; a forge that reports no digest says so rather than passing
silently.

The two other routes, a conan remote and a folder, upload from the
publish job's conan cache, and every CI job has a conan home of its
own, so that cache starts without the package. When the wave
publishes what the run collected, the `publish` step first restores
every host's file for the version from `dist/`, in name order
(`conan cache restore`), and then uploads. The upload sends every
host's binary the run collected, the same hosts the releases route
attaches. A file for another version is not restored. A `dist/`
without a saved cache for the version refuses, naming the member, the
version and the files `dist/` holds. A member the publish job built
itself is in its cache already, and nothing is restored.

On the remote route the upload goes to the remote named `workshop`,
always pointed at the resolved target first, so a stale remote from an
earlier workspace never takes an upload. The registry's token logs in
to that remote alone, and conan's own login variables win over it. A
folder target saves the package's cache into the folder, and the probe
reads the saved files' versions from their names.

## The names another extension reads

`livery.extensions.conan` serves the names an extension whose builds
link a conan member reads: the conan workspace file and its text, the
block that sets the file aside so a build resolves each member from
the conan cache, the saved cache of a release's build, and the restore
of a released version from the member's release on the forge.
