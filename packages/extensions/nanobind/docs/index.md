# livery-extensions-nanobind

A python package whose extension module is C++, bound to python through
[nanobind](https://nanobind.readthedocs.io/), built into one wheel per
platform. A package turns it on by listing the extension in its own
`workshop.toml`:

```toml
extensions = ["nanobind"]
name = "acme-native"

[nanobind]
wheel-platforms = ["ubuntu-latest", "macos-latest", "windows-latest"]
```

nanobind requires python for the distribution, cpp for the extension
module's sources, and conan for the provider that resolves their C++
dependencies, so a package that lists nanobind holds all four. The
package consumes conan's packages without being a conan package: its
recipe names the requirements its extension module builds against, and
no version.

## A new package

`fm new.package.nanobind-module <name>` creates such a package while the
workspace lists nanobind: the python library's seeds, then the extension
module's sources, its CMake build, its `pyproject.toml` with
scikit-build-core as the build backend, and the conan recipe that names
fmt as its one requirement. The template takes no `--command`. A
workspace that does not list nanobind has no such verb: list the
extension in `[workspace] extensions` and run `fm sync`, which installs
`livery-extensions-nanobind`, and the verb exists.

## The build

The release train builds a package that holds nanobind on the build
phase's isolated side. cibuildwheel builds and repairs each platform
wheel, so even one host's wheel is manylinux-compliant on Linux, and
`uv build` writes the sdist. Every build resolves the extension
module's C++ dependencies through the cmake-conan provider and conan
from the store. On Linux cibuildwheel builds in a container, which
mounts the workspace, the store, the provider and the conan home at
their host paths, and installs conan at the store's version for
itself, since the store's binary is linked against the host's C
library. A build refuses a wheel tagged `none-any`: such a wheel was
built without its extension module.

The steps of python and conan that would build or release something
else are deleted in every package whose set holds nanobind, each with
its reason:

```toml
[phases.build.nanobind]
main = "livery.extensions.nanobind._build:build"
isolated = true
reads = ["epoch"]
provides = { dist = "path" }

[phases.build.nanobind.deletes]
"python:build.python" = "uv build writes a pure wheel, and the package's wheels are platform wheels, which this step builds with the sdist"
"conan:stamp.conan" = "the package's recipe names the requirements its extension module builds against, and no version to stamp"
"conan:build.conan" = "the package's recipe creates no conan package: the provider installs its requirements while the wheels build"
"conan:publish.conan" = "the package releases its wheels, never a conan package"
```

A package whose set does not hold nanobind runs those steps as before.
The build runs cmake, ninja, conan and the cmake-conan provider, which
the lock pins while a package's set holds nanobind. nanobind itself and
scikit-build-core arrive through the package's own build-system
requires. The venv carries `nanobind` too, so an editor's configure of
the extension module finds nanobind's cmake directory.

## The wheels job

A package that holds nanobind names the runner labels that build its
platform wheels under `[nanobind] wheel-platforms`. The key is required
there, must be a non-empty list of labels, and refuses in a package that
holds no nanobind. The extension adds a wheels job to the release
workflow: before the wave, one leg per label in the union over those
packages builds that platform's wheels for the workspace's python
matrix, and the publish job waits for the legs and ships the collected
set. A workspace whose packages hold no nanobind has no wheels job.

Each leg runs `fm nanobind.wheels` at the release squash the wave
publishes. A leg builds every member of the release that the workspace's
`job-built-members` answer names. nanobind answers it with every package
that holds nanobind and every package that releases a conan package. A
release with no such member builds nothing and says so, so the leg
stays green on a pure release.

A leg builds the packages the release ships, so it sets the workspace's
`conanws.yml` aside while it builds: a member's `conan create` and a
nanobind package's wheel resolve a sibling to the package the leg
created, never to its source tree. The file comes back when the builds
end, a failure included. The leg creates each conan package first and
saves its cache as `dist/<name>-<version>-<host>.tgz`, then builds the
wheels, which resolve the library from the leg's own cache.

Every floor a member declares on a conan member is proved in the leg
that builds it: the floor's cache is restored from its own release, one
wheel is built for that machine with a conan profile replacing the
requirement with the floor, and the package's tests run against it. A
floor equal to the version the wave is releasing is the package the
main build already linked, and the leg says so instead of building
twice.

In CI the wave publishes what the run collected into those members'
`dist/`; a job-built member whose `dist/` holds nothing refuses the
wave, naming the job.
