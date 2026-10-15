<!-- Seeded from the package-base seeds at
     birth; this file is the workspace's own. Edit it directly:
     nothing rewrites it.
-->
# livery-extensions-cmake

CMake as an extension of the workshop: the gate build of a CMake
project, configured, compiled and tested through ctest, in whatever
language the project builds. A package turns it on by listing it in
its own contract:

```toml
extensions = ["cmake", "conan"]
```

It requires no language: a package can list cmake alone and build with
the compilers and libraries the host provides. The conan extension's
C++ templates list it beside conan.

## The gate build

For each package whose set holds cmake, the extension adds three steps
to the gate, each on the tree side of its phase:

- `build.configure` configures the package into `build/gate` with the
  Ninja generator and a compilation database;
- `build.compile` builds the configured tree, incrementally;
- `test.ctest` runs ctest over that build: every test, or the ones a
  change to the package's test files selects.

All three run when a package's change is its tests alone, since its
tests run on the build. A release builds on the isolated side, where
none of them runs.

`build.configure` and `build.compile` name cmake, the tool they run, so
each one's verb hands cmake the words after `--`:
`fm build.configure -- -DACME_FAST=ON`. `test.ctest` names no tool: a
tool a test step names is a verdict tool, which no workspace may take
from the host, and a workspace may serve cmake from the host. So
`fm test.ctest` takes no words after `--`.

The lock pins cmake and ninja while a package's set holds cmake, and
`fm sync` installs them from the tool store. A machine where one of
them is not on `PATH` refuses, naming the tool and the sync that
supplies it.

The extension answers the `compile-commands` query with the
compilation database the gate build writes, which clang-tidy reads.

On Windows the gate builds with MSVC unless `CXX` names another
compiler. When `cl` is not already on `PATH`, the build enters the
newest Visual Studio's C++ build tools itself, through vswhere and the
tools' vcvars batch file. A machine with neither refuses, naming the
workload to install.

## Coverage

While the workspace lists coverage, this extension measures every
native suite for it, through its `[for.coverage]` table, and coverage
judges what it measures. A workspace that lists cmake without coverage
builds without instrumentation and runs ctest alone.

A measured gate build is instrumented for the compiler family CMake
detected: gcc with `--coverage`, clang and apple-clang with
`-fprofile-instr-generate -fcoverage-mapping`. ctest runs, and the
measurer beside that compiler reads the run:

- gcov's JSON gives each line's count, each line's branches by index
  with an exception's path tagged, and the functions.
- llvm-profdata and llvm-cov's JSON export give each line's count as
  llvm-cov's own line view counts it, each condition's two branches,
  the functions, named by the demangler beside the compiler, each code
  region as a range with a macro counted where it is called, and the
  code the preprocessor removed as excluded lines.
- A run built with MSVC is measured by Microsoft's Code Coverage
  engine, `dotnet-coverage`, a tool of the store that the
  `[for.coverage]` table requires as `dotnet_coverage@windows`, with
  the .NET SDK it runs on. A workspace that does not list coverage
  locks and installs neither. Each test executable is instrumented
  statically for the run, ctest runs under the collector, its verdict
  is read from the report ctest writes, and the Cobertura report gives
  each line's count, how many of a line's branches ran, and the
  methods.

What the measurer read becomes the package's part in the coverage
extension's line format, one per package under
`.workshop/coverage/parts/`, named after its measurer. A measured run
removes the package's parts of an earlier run first, whichever measurer
left them. A host with the compiler and without its measurer refuses
on that leg by name. The floors judge a native part's lines alone; the
branches, functions and ranges reach the coverage pages.
