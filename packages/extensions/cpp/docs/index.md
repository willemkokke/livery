# livery-extensions-cpp

C and C++ as languages, as an extension of the workshop: which of a
package's files are sources, tests and test support, the answers to
the module queries for a C or C++ package, and the compilers the host
must provide. A package holds it by listing it in its own contract, or
through an extension that requires it:

```toml
extensions = ["cmake", "conan"]
```

cpp is optional for a build: a package can build C or C++ with cmake
alone. Held, it tells a change's tests from its sources, so the gate
runs what a change needs, and it names the files the formatter and
the linter of a C or C++ package judge. The conan extension and the
nanobind extension require it, so a package that lists either holds
cpp, and their distributions require this one.

## A package's files

cpp gives the paths of each package whose set holds it these
categories:

| Category | Patterns |
| --- | --- |
| `test` | `tests/**/*.cpp`, `tests/**/*.cc`, `tests/**/*.cxx`, `tests/**/*.c` |
| `test-support` | `tests/**` |
| `source` | `src/**`, `include/**` |

The most specific pattern wins, so a C or C++ file under `tests/` is a
test and every other file there supports the tests. The workshop's own
table calls what is left configuration, such as `CMakeLists.txt` and
`conanfile.py`. A step that names cpp in its `extensions` judges each
package whose set holds it: the clang-format and clang-tidy extensions
read a C or C++ package's sources this way.

## The module queries

A C or C++ package names no module: other packages reach its code
through headers and libraries, never an import path. cpp answers
`module-roots` and `public-modules` with nothing: the package has no
name another package imports it by, and no importable API. A consumer
names the package by its recipe or its CMake target, which the
package's own build files declare.

cpp answers no `references` query, so the layering checks read no C or
C++ source: a header of another member, included with no requirement
on that member, is not seen.

## The host's compilers

cpp answers `host-tools` with `cc` and `c++`, the C and C++ compilers a
build runs, which the host provides and no tool store installs. While a
package's set holds cpp, `fm doctor` and `fm env.check` look for both
on `PATH` and name each one that does not resolve, with the platform
toolchain to install. A workspace whose packages hold no cpp needs no
compiler, and neither verb looks for one.
