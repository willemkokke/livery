# Adding Rust support

Status: written 2026-10-13. Nothing is built. Ruled the same day: the
coverage line format carries llvm-cov's counted ranges; the conan
consumer helper is one copy shared with nanobind; both native kinds
build wheels in three ABI modes, `mixed` by default; maturin builds
Python extensions in Rust. Every other design question ends in an open item, and a phase
whose open item is unruled does not start.

Timing: phase 1 follows 12e's P3 (issue #1554, PR #1569), which
builds the line format this phase extends. Phase 9, the wheel modes
for nanobind, needs no Rust and can land at any time. The other
phases from 2 on need the toolchain engine, the destination plan's
phase 14 (`notes/20261006-workshop-destination.md`), which is not
built. Phase 11 needs the release train through phases, its phase 13.
Phases 2 onward start only when Willem schedules them.

## The prompt (Willem)

> I'm definitely going to want to support rust and go, and I would
> not want to rule out c / zig / jai if it ever comes out / swift.
> Willem, 2026-10-01

> Rust must be supported. Willem, 2026-10-06

> b, add that to a new plan note "Adding rust support". other things
> we'd need to look into for that is C linkage compatibily and
> interpackage dependencies (do we export rust crates with c
> compatibility to conan?), python extensions in rust, cargo locking
> integration. Willem, 2026-10-13

The intent: a Rust package is a member like a Python or a C++ one.
The gate formats, lints, builds, tests and measures it. The release
train versions and publishes it. It links with the other kinds in
both directions: Rust calls C and C++ from conan, C and C++ call Rust
through a C interface, and Python imports Rust through an extension
module.

The first consumers named so far are all Rust under Python (the
musings of 2026-09-28): a native strongroom, `livery-cbor`'s native
implementation, and a test-selection library extracted from pants2.

## What exists that this builds on

Measured on `origin/main` at `96702420c`, 2026-10-13.

- **The line format.** On main, a native measurer's reading is lines
  alone: `livery.workshop._coverage_lines.Lines`, file to line to
  hits. The cmake extension measures clang with `llvm-profdata merge
  -sparse` and `llvm-cov export -format=lcov`, and `from_lcov` reads
  only the `SF:` and `DA:` records
  (`livery/extensions/cmake/_build.py`, `_coverage_lines.py`).
  `llvm-cov` and `llvm-profdata` are found beside the compiler, on
  `PATH`, or through `xcrun`; they are not store tools. PR #1569
  replaces the shape with format version 1 of the coverage
  extension's parts (`livery/extensions/coverage/_format.py`): a
  `Measurer` that says what its reading holds, and per file an
  `Entry` with statements, counts, excluded lines, multi-line spans,
  branches by target or by index, functions and classes as `Region`,
  and the contexts that reached each line. Native lines enter it as a
  lines-only measurer. It has no column anywhere. Since 12e's P4 the
  format is version 2 with counted ranges, and the cmake extension
  reads llvm-cov's JSON export and gcov's JSON format: phase 1 below
  is done (decision record).
- **The toolchain engine is planned, not built.** The extensions plan
  gives the base `[toolchain.<language>]`, the receipts under
  `.workshop/receipts/toolchain/` and the release record, and gives a
  language extension its families, their probes and their store
  records, "later `rust`, `go`, `zig`, `swift`"
  (`notes/20261002-extensions-plan.md`, "Toolchains", and contract
  23's three answers: the host's, a floor, or an exact version). The
  same section rules that a language extension owns its ecosystem's
  caches and switches off every installer its ecosystem brings, and
  names rustup's toolchain file. Only the toolchain plan's phase 1,
  the host allowance, exists in code.
- **The tool store** installs `download`, `pypi`, `python`, `npm`,
  `dotnet` and `system-check` records for six hosts, pinned in one
  `toolroom.lock` per workspace. An artifact is recorded by
  downloading and hashing it, never from a vendor's checksum file
  (`notes/20260925-direct-downloads.md`, contract 2). The dotnet tier
  is the precedent for a vendor index: it reads Microsoft's
  `releases-index.json` for versions and hashes each artifact itself.
  No record ships llvm; the toolchain plan's `llvm` profile (lld,
  llvm-cov, llvm-profdata, the profile runtimes) is planned.
- **Locks.** One `uv.lock` and one `toolroom.lock` per workspace.
  `fm lock` writes both; `fm lock --check`, `fm sync --locked` (the CI
  entry) and `fm submit` refuse a stale one (`stale_locks` in
  `livery.workshop._sync`). `fm workflow.update.dependencies` runs
  `uv lock --upgrade` and bumps sibling floors. The affected engine
  attributes a `uv.lock` change to the members whose resolved closure
  moved (`livery.workshop._root_attribution.lock_affected`); any other
  root lock change runs everything.
- **conan.** The `conan` extension (`requires = ["cpp"]`) declares
  the `requirements` and version queries and the `sync.conan`,
  `stamp`, `build` and `publish` phases. Siblings resolve from source
  through a rendered `conanws.yml`; release legs set it aside. A C++
  member publishes to a declared remote, the forge's registry or the
  forge's release assets (`_registries.resolve_registry`). The
  layering lint expects a C++ edge's home in `conanfile.py` as
  `requires "<name>/[>=<floor>]"`.
- **nanobind.** `python-nanobind` builds wheels with scikit-build-core
  under cibuildwheel, one wheel per interpreter (`cp314-*`,
  `cp314t-*`), no stable ABI. It consumes conan through cmake-conan's
  dependency provider with `CMakeDeps`. Its consumer code lives in
  `livery/workshop/_backends/_python_nanobind.py`: the shared consumer
  helper the kind hierarchy plan named does not exist. On Linux,
  cibuildwheel's container gets conan through `CIBW_BEFORE_ALL_LINUX`.
  Its C++ is not measured, and it generates no `.pyi` stubs.
- **Adding a language.** An extension declares itself in
  `extension.toml`: `requires`, `[categories]`, `[queries]`,
  `[phases.<phase>.<step>]`, fragments, `[for.<target>]` and CI jobs
  (`livery.workshop._declaration`). conan's declaration is the one to
  copy; cmake's build and test steps are still registered in the base
  (`_steps.py`) and are not the model. The loop seeds one member per
  native kind (`loop-native`, `loop-cpp` in `_e2e.py`).

## Ground-truth contracts (do not violate)

1. **The core knows no language.** Everything Rust lives in
   extensions; the base gains no Rust name (the destination note's
   ruling 1).
2. **The store is the one installer.** rustc, cargo, the standard
   library, llvm-tools, rustfmt, clippy and every cargo tool come
   from store records, hashed by download. rustup, a
   `rust-toolchain.toml`, `cargo install` and anything on the
   developer's `PATH` never decide what the gate runs.
3. **One lock per ecosystem per workspace, in its native format.**
   One root `Cargo.lock`. Every cargo command the workshop runs passes
   `--locked`, and a stale lock refuses where `uv.lock`'s does.
4. **The line format carries the most detail a measurer gives**
   (2026-10-12), counted ranges included (2026-10-13). The floors
   judge what they judge today; ranges change what the pages show.
5. **Rust consumes conan packages through `PkgConfigDeps`, through
   one consumer helper shared with nanobind** (2026-09-26).
6. **A dependency between members has one native home**, which the
   layering lint reads, and edges point downward. For a Rust member
   the home is `Cargo.toml`.

## The design

### Coverage: counted ranges in the line format (ruled)

rustc measures with LLVM's source-based coverage, the same measurer
as clang, so Rust and clang share one reducer. llvm-cov's unit is a
region: a start line and column, an end line and column, and a count.
The ruled extension adds them to the format.

- **The field is `ranges`.** PR #1569 already names functions and
  classes `regions`, so llvm's regions enter as `Range(start_line,
  start_column, end_line, end_column, count)` in `Entry.ranges`, and
  `Measurer` says whether its reading has them. The format's version
  moves to 2; a version-1 part refuses, named, as any other version
  does.
- **The reducer reads `llvm-cov export -format=text`** (JSON), not
  lcov: lcov has no columns. From the JSON it fills line counts,
  branches by index (true and false counts per condition), functions
  with demangled names, and ranges. Code regions become ranges.
  Skipped regions, the code the C preprocessor removed, mark their
  lines excluded. Expansion regions count at the macro's call site.
  The line counts must equal what llvm-cov's own lcov export gives
  for the same profile; a parity test holds that.
- **coverage.py and gcov leave `ranges` empty.** Neither gives
  columns.
- **A page marks the part of a line that never ran**, so `if ok { a()
  } else { b() }` on one line shows its `else` as not run.

What Rust fills, and what it does not:

- **Lines, functions, ranges**: on stable Rust.
- **Branches**: only with `-Zcoverage-options=branch`, a nightly flag
  the compiler team does not plan to stabilise, which also crashes
  `llvm-cov export` on some generic crates (rust-lang/rust#157358).
  With stable only (open 1), Rust's branches stay empty and ranges
  are its only detail finer than a line.
- **Functions**: names are demangled. A generic function arrives once
  per instantiation (`parse::<u8>`, `parse::<u16>`); the reducer
  merges them by source position, or a page lists each one. rustc
  emits unused generic functions at zero, so they count as not run.
- **Classes**: Rust has none. The impl type comes from the demangled
  path (`<crate::Parser as Iterator>::next`); llvm-cov reports
  nothing about it itself.
- **Excluded lines**: Rust has no per-line pragma. `#[coverage(off)]`
  removes a whole function from instrumentation, so its lines are
  absent, not excluded. The floor reads the same either way.
- **The tests that reached each line**: LLVM records no contexts, but
  processes separate them. Each file in a crate's `tests/` is its own
  binary, so `%p` in `LLVM_PROFILE_FILE` gives one profile per test
  file at no cost: the unit per-test-file selection uses
  (`notes/20261012-what-a-change-affects.md`). Unit tests in `src/`
  share one binary per crate; cargo-nextest runs each test in its own
  process, which gives one profile per test at the cost of one
  `llvm-profdata merge` and one `llvm-cov export` per test.
- **The measurer's version**: llvm-cov must match rustc's own LLVM,
  since the profile format changes between LLVM releases. Rust's
  measurer is the `llvm-tools` component of the pinned toolchain, not
  the clang the cpp extension finds.

### The toolchain from the store

The Rust project publishes each release's components as standalone
archives per host, listed in a channel manifest
(`static.rust-lang.org/dist/channel-rust-<version>.toml`): `rustc`,
`cargo`, `rust-std-<target>`, `llvm-tools`, `rustfmt`, `clippy`. All
six hosts have host tools. The bench reads the manifest for versions
and URLs and hashes each archive itself, as the dotnet tier does with
Microsoft's index; the manifest's own hashes are not copied
(direct downloads, contract 2).

- **One sysroot.** rustc finds its standard library relative to its
  own binary (`<sysroot>/lib/rustlib/<target>/lib`), so `rustc` and
  `rust-std` install into one tree. Whether a store record can
  assemble several archives into one root is the first question
  phase 2 answers; if it cannot, that is the store's change.
- **The `rust` toolchain family**: a probe over `rustc -vV`, contract
  23's three answers, a receipt with the host triple, the version, the
  driver's digest and the sysroot, rendered into cargo's environment
  (`RUSTC`, `CARGO_HOME`, the path).
- **rustup is off.** The store ships no rustup. A `rust-toolchain.toml`
  in the workspace is refused and named, since only rustup reads it
  and its presence says something that is not true.
- **The linker comes from the cpp toolchain.** rustc links with the
  system C toolchain: `cc` on Linux and macOS, MSVC's `link.exe` on
  Windows. The `rust` family requires the cpp family's receipt, and
  on Windows enters MSVC the way the cmake extension does today.
- **Cargo tools** (cargo-nextest, cargo-c, cbindgen, maturin) are
  store records too. Where a tool ships no prebuilt binary for a host,
  the store needs a record kind that builds it with the store's own
  cargo (`cargo install --locked`). Which tools need it is read in
  phase 2.

### Rust members, the cargo workspace, and the lock

The cargo locking integration.

- **One cargo workspace at the root.** Sync renders a root
  `Cargo.toml` whose `[workspace] members` lists the Rust members, as
  the root `pyproject.toml` lists the Python members, with one
  `Cargo.lock` and one `target/` beside it. Cargo resolves the whole
  workspace as one graph, which matches `uv.lock`'s one resolution.
- **`--locked` everywhere.** Every build, test, lint and fetch the
  workshop runs passes `--locked`, so a lock that disagrees with a
  manifest refuses instead of being rewritten.
- **The verbs.** `fm lock` adds `cargo generate-lockfile` beside `uv
  lock`; `fm lock --check` and `stale_locks` add `cargo metadata
  --locked`; `fm sync --locked` runs `cargo fetch --locked`; `fm
  workflow.update.dependencies` runs `cargo update`, or `cargo update
  --package <name>` for a named one, and bumps sibling floors as it
  does for Python.
- **The affected engine.** A `Cargo.lock` change is attributed per
  member like `uv.lock`'s: each member whose resolved closure moved,
  read from `cargo metadata`'s resolve graph. Without that, every
  lock change runs everything.
- **Hashes.** Cargo's lock records a checksum for every registry
  package; `--locked` with a fetch verifies them, as uv's hashes are.
- **Caches.** `CARGO_HOME`, the registry and git caches, is shared
  machine-wide as conan's cache is, under `$RUNNER_TEMP` in CI with a
  cache keyed by `Cargo.lock`. `target/` is a build output, cleared
  by the clean step.
- **Edges.** A member depends on a sibling with a path and a version
  in `Cargo.toml` (`fmt = { path = "../fmt", version = "0.3.1" }`):
  the path resolves at HEAD in the workspace, and the version is what
  a published crate requires. When the dependency is needed comes
  from the section: `[dependencies]`, `[dev-dependencies]`,
  `[build-dependencies]`. The layering lint reads them as it reads
  `conanfile.py`. How the floor is written is open 4.

### Rust calling C and C++

Ruled on 2026-09-26: Rust consumes conan packages through
`PkgConfigDeps`, and the consumer helper is shared. The consumer
helper is the code that runs `conan install` for a member and hands
the result to that member's build. Shared means one copy of that
code, called by every kind that builds against conan: nanobind's
CMake build and a Rust member's `build.rs` alike. Today the only copy
is nanobind's own, inside `_backends/_python_nanobind.py`, so the
phase that brings Rust to conan moves it out into one helper and
moves nanobind onto it in the same work (Willem, 2026-10-13: no
duplication).

- **The helper** runs `conan install` with `PkgConfigDeps` for the
  member's `conanfile.py`, with the profile the build uses, and puts
  the generated `.pc` folder on `PKG_CONFIG_PATH`. The member's
  `build.rs` reads it with the `pkg-config` crate.
- **pkg-config on Windows.** The crate runs a `pkg-config` binary,
  which Windows lacks. pkgconf ships for all six hosts and becomes a
  store record.
- **The profile must match the target.** conan's `os`, `arch` and, on
  Windows, `compiler.runtime` must agree with rustc's target: the
  `-msvc` targets link the dynamic CRT unless `crt-static` is set.
  The helper derives the profile from the rust receipt's triple; a
  mismatch refuses, named.
- **C++ needs its standard library linked.** A C++ static library
  pulls in libstdc++ or libc++, which a `.pc` file may not list. The
  helper adds it from the cpp receipt's family.
- **Bindings.** For C: `bindgen`, which needs libclang, from the store
  once the toolchain plan's llvm record exists, or `extern "C"`
  blocks by hand. For C++: the `cxx` crate's checked bridge. Which one
  a member uses is the member's choice; the helper serves all three.

### C and C++ calling Rust, and Rust in conan

Willem's question: do we export Rust crates with a C interface to
conan? The recommendation is yes, for a crate that declares a C
interface, and only for it (open 6).

- **A C interface crate** sets `crate-type` to `staticlib` or `cdylib`
  and exports `extern "C"` functions; cbindgen writes its header.
- **cargo-c builds it**: the library, the header, a `.pc` file, an
  import library on Windows, and an install layout. The conan
  package's `build()` runs cargo-c with the store's toolchain, and
  `package_info()` declares the library and the system libraries
  Rust's standard library needs, read from `rustc --print
  native-static-libs` for the target, never a list by hand.
- **It publishes through conan's existing routes**: a declared
  remote, the forge's registry, the forge's release assets. In the
  workspace, siblings resolve from source through `conanws.yml` like
  any C++ member.
- **Two Rust static libraries in one binary collide.** Each carries
  its own copy of the standard library, and `rust_eh_personality`
  and the `__rdl_*` allocator symbols are unmangled, so the link
  fails with duplicate symbols. An upstream change mangles the
  personality function (rust-lang/rust#148413 with
  llvm/llvm-project#166095); whether it has shipped, and whether it
  covers every colliding symbol, is read in phase 8. Until then a
  conan graph that links two Rust static libraries into one binary
  refuses, naming both, and shared libraries have no such limit.
- **A panic does not cross the interface.** Since Rust 1.81 a panic
  that reaches an `extern "C"` function aborts the process. The seed
  shows the interface returning an error code.

### Python extensions in Rust

PyO3 writes the bindings; maturin builds them. maturin is a PEP 517
build backend, as scikit-build-core is for nanobind: given a
`pyproject.toml` with `build-backend = "maturin"`, it runs cargo to
build the extension module as a shared library, names it for the
interpreter and the ABI, and packs it with the package's Python files
into a wheel or an sdist. Its settings are `[tool.maturin]` in
`pyproject.toml`. The `maturin` extension requires `python` and
`rust`, as `nanobind` requires `python`, `cpp` and `conan`.

**Why maturin.** The alternatives are setuptools-rust, a setuptools
plugin, and scikit-build-core driving cargo through CMake (corrosion).
maturin is PyO3's own tool: it knows PyO3's ABI features and names
the wheel to match, packs workspace path dependencies into the sdist,
and writes PyO3's stubs. The other two add a build system the Rust
code does not otherwise need.

**The shape of a member.** A Python package that gains a native
module, which is what the first consumers are (a native strongroom,
`livery-cbor`):

- **Two crates.** The logic is a plain Rust member: its own tests,
  usable from Rust and, through phase 8, from C. A thin binding crate
  holds the PyO3 code and depends on it by path. The binding crate
  belongs to the Python package and is never published as a crate
  (`publish = false`). The split also decides testing: a PyO3 crate
  built as an extension module does not link libpython, so `cargo
  test` cannot run it; the logic's tests run under cargo, the
  bindings' under pytest.
- **The mixed layout.** maturin's `python-source` points at the
  package's Python sources, and `module-name` puts the native module
  inside the package (`livery.strongroom._native`). The namespace is
  PEP 420: maturin must place the module without writing
  `livery/__init__.py`, which the layering lint refuses. Phase 10
  proves this first.
- **One version.** maturin merges the metadata of `Cargo.toml` and
  `pyproject.toml`, and `pyproject.toml` wins. The package's version
  lives in `pyproject.toml`, where the python extension's stamp
  writes it; the binding crate's own version is never read.

**In the workspace.** `uv sync` builds the member through maturin, as
it builds a nanobind member through scikit-build-core. uv rebuilds a
package only when one of its cache keys changes, so the maturin
extension renders `tool.uv.cache-keys` from the binding crate's whole
cargo closure (`cargo metadata`): every `.rs` file and `Cargo.toml` of
the crates it reaches by path, and `Cargo.lock`. A key list written by
hand misses the core crate, and the gate then tests a stale module.
The binding crate is a member of the root cargo workspace, so the one
`Cargo.lock` covers it. PyO3 finds its interpreter at build time
through `PYO3_PYTHON`, which the extension sets to the workspace
venv's interpreter for every cargo command, clippy's included.

**The gate.** The Python half gets the python extension's steps:
ruff, the four type checkers, pytest. The Rust half gets the rust
steps: rustfmt, clippy, and cargo's tests for the logic crate. A
change to a `.rs` file of the closure affects the Python package's
tests, which the maturin extension's categories declare.

**Free-threaded Python.** A module that does not declare
`#[pymodule(gil_used = false)]` turns the GIL back on when 3.14t
imports it, with a warning, and the free-threaded leg then tests the
GIL build without failing. The seed declares it, and a test asserts
the GIL is still off after the import. nanobind's `FREE_THREADED`
flag is the same case, under the same test.

**Wheels** build under cibuildwheel, with the same legs and publish
job as nanobind, so a workspace has one way to build wheels.
cibuildwheel drives any PEP 517 backend. On Linux, the manylinux
container receives the store's Rust toolchain through a mount, never
an installer run inside it (contract 2). maturin's own manylinux
handling (`--zig`, maturin-action) stays unused.

**The wheels' ABI** (ruled 2026-10-13, for both native kinds): a
package chooses one of three modes, and nanobind and maturin both
build all three.

| Mode | Builds | Allowed when |
| --- | --- | --- |
| `per-interpreter` | one wheel per interpreter in the matrix | always |
| `stable` | stable-ABI wheels only | the interpreter floor reaches the binding's stable-ABI minimum |
| `mixed` | one wheel per interpreter below that minimum, a stable-ABI wheel from it | always |

- **The floor** is the package's `requires-python` lower bound. Each
  build flavour is judged against its own minimum. GIL builds use
  `abi3`: nanobind from 3.12 (below it nanobind ignores the flag),
  PyO3 from 3.8, its oldest supported Python. Free-threaded builds
  use `abi3t`, from 3.15: PyO3 0.29 and maturin 1.14 build it;
  whether nanobind does is read in phase 9. 3.14t has no stable ABI,
  so a matrix holding 3.14t always gets a 3.14t wheel.
- **`stable` with a floor below a minimum refuses**, naming the
  flavour, its minimum and the `mixed` mode.
- **A stable-ABI wheel claims every interpreter from its tag on**, so
  the legs install it into each interpreter of the matrix it claims
  and run the tests there, not only on the one that built it. abi3audit,
  a store tool, checks that the wheel calls only stable-ABI symbols.
- The key is `[wheels] abi`, read by both kinds. Its default is
  `mixed`, which builds the fewest wheels the floor permits.

**The sdist and its path dependencies.** maturin packs the crates the
binding reaches by path into the sdist, so the sdist builds alone.
The python kind's split, ruled 2026-09-26 for nanobind, builds against
HEAD in the workspace and against the released floor in the isolated
legs. Which one a Rust core crate follows is open 9.

**Stubs.** The four type checkers need a `.pyi` for the native
module. PyO3's introspection writes one (`experimental-inspect`,
`maturin build --generate-stubs`); it is experimental (open 10).

**Coverage of the Rust under pytest.** The coverage leg builds the
extension with `-C instrument-coverage`, and the maturin extension's
`[for.coverage]` table reduces its profile through phase 1's reducer,
so one package page shows both languages. Per-test contexts would
need the profile written and reset between tests through the LLVM
runtime's own functions (`__llvm_profile_set_filename`,
`__llvm_profile_write_file`, `__llvm_profile_reset_counters`); that
is unproven.

### The gate's steps for a Rust member

Declared in the extensions' `extension.toml`, never in the base:

| Step | Command | Under `--fix` |
| --- | --- | --- |
| `format.rustfmt` | `cargo fmt --check` | `cargo fmt` |
| `lint.clippy` | `cargo clippy --locked --all-targets -- -D warnings` | `cargo clippy --fix` |
| `build.cargo` | `cargo build --locked` | |
| `test.nextest` | `cargo nextest run --locked`, then `cargo test --doc --locked` | |
| `lint.rustdoc` | `cargo doc --no-deps --locked` with `-D warnings` and `missing_docs` | |

Type checking is the build: rustc checks types, so there is no
separate typecheck step. nextest does not run doc tests, so the test
step runs them after it (open 3).

**Docs.** rustdoc's JSON output is nightly-only, so the API pages
place rustdoc's HTML tree in the package's site, the second of the
two ways the destination note gives a docs generator. The
documentation standard applies to `///` comments as to docstrings,
with rustdoc's sections (`# Errors`, `# Panics`, `# Safety`,
`# Examples`) in place of Google style's, and intra-doc links in
place of `[path][]` references.

### What it does not cover

- Targets beyond the six hosts: cross-compilation, WebAssembly,
  `no_std` and embedded targets. One `rust-std` per host is all the
  store records.
- A nightly toolchain on the merge path (open 1).
- Dependency auditing (cargo-deny, cargo-audit) and `build.rs`
  sandboxing. A `build.rs` runs arbitrary code at build time, as a
  `setup.py` does.
- Shared build caches across machines (sccache).

## Phases

Each phase lands gate-green and mergeable alone, tested on the six
hosts' legs a pull request runs. Refusals and fallbacks are tested
before the happy paths. Every extension a phase adds is proved with
`fm ci.e2e --extension=<name> --fresh` before it releases.

### Phase 1: counted ranges in the line format

After PR #1569 merges. Format version 2: `Range`, `Entry.ranges`, and
`Measurer` saying whether its reading has them. The llvm reducer
reads `llvm-cov export -format=text` in place of lcov and fills line
counts, branches by index, functions with demangled names, and
ranges; skipped regions mark their lines excluded. The renderer marks
the part of a line that never ran. Done inside 12e's P4 (decision
record), which also reads gcov as its JSON format.

Acceptance:

- `test_a_part_of_format_version_1_refuses_and_names_its_version`,
  `test_a_measurer_without_columns_leaves_ranges_empty` and
  `test_a_range_outside_its_file_refuses`, before the happy paths.
- `test_llvm_line_counts_from_json_equal_its_lcov_export`, on a
  fixture profile with a macro, a skipped `#if 0` block and a
  one-line `if`/`else`.
- `test_a_line_with_an_unrun_range_is_drawn_partial`.
- `fm check` green; a `fm ci.e2e --fresh` pass shows ranges on
  `loop-cpp`'s pages from the macOS leg.

### Phase 2: the Rust toolchain from the store

After the destination plan's phase 14. Store records for `rustc`,
`cargo`, `rust-std`, `llvm-tools`, `rustfmt` and `clippy` on six
hosts, read from the channel manifest and hashed by download, plus
pkgconf, cargo-nextest, cargo-c and cbindgen. The `rust` toolchain
family: probe, receipt, rendering, the cpp family as its linker. The
answer to whether a record assembles several archives into one
sysroot, and to which cargo tools need a build-from-source record
kind.

Acceptance:

- `test_a_rust_toolchain_file_refuses_and_names_the_receipt`,
  `test_an_exact_rust_version_the_store_lacks_refuses_naming_what_would_satisfy`,
  `test_a_rustup_proxy_on_the_path_is_never_run`, before the happy
  paths.
- `fm sync` on a fresh clone of a workspace declaring
  `[toolchain.rust]` installs it on every leg, and `rustc -vV` from
  the receipt prints the pinned version.

### Phase 3: the cargo workspace and its lock

The `rust` and `cargo` extensions' first part: the rendered root
`Cargo.toml`, the one `Cargo.lock`, `fm lock`, `fm lock --check`,
`fm sync --locked`, `fm workflow.update.dependencies`, the per-member
attribution of a `Cargo.lock` change, the layering lint reading
`Cargo.toml`, `fm new.package` for a Rust member, and the `loop-rust`
seed.

Acceptance:

- `test_a_stale_cargo_lock_refuses_at_sync_locked_and_submit`,
  `test_a_path_dependency_without_an_edge_refuses`,
  `test_an_upward_cargo_edge_refuses`, before the happy paths.
- `test_a_cargo_lock_change_affects_only_members_whose_closure_moved`.
- `fm ci.e2e --extension=cargo --fresh` green.

### Phase 4: the gate's steps for a Rust member

`format.rustfmt`, `lint.clippy`, `build.cargo`, `test.nextest` and
`lint.rustdoc`, each declared in its extension, `--fix` where the
table gives one.

Acceptance:

- `test_an_unformatted_rust_file_fails_format_and_fix_rewrites_it`,
  `test_a_clippy_warning_fails_lint`,
  `test_a_failing_doc_test_fails_the_test_step`, before the happy
  paths.
- `fm check` on `loop-rust` runs the five steps; `fm ci.e2e
  --extension=rust --fresh` green.

### Phase 5: Rust coverage

The test step builds with `-C instrument-coverage`, writes one profile
per process, and the rust extension's `[for.coverage]` table reduces
them with the toolchain's `llvm-tools` through phase 1's reducer:
instantiations merged, impl types read from the path, contexts per
test binary and, under nextest, per test.

Acceptance:

- `test_a_profile_from_another_llvm_version_refuses_naming_both`,
  `test_a_coverage_off_function_is_absent_not_counted`, before the
  happy paths.
- `test_two_instantiations_of_one_generic_function_draw_as_one`.
- `test_each_integration_test_file_is_its_own_context`.
- `fm check` judges `loop-rust`'s floor; its page draws ranges.

### Phase 6: the API pages

rustdoc's HTML tree placed in the package's site, linked from its
navigation.

Acceptance: `fm docs.build` on a workspace with `loop-rust` renders
its API under the package's pages; a broken intra-doc link fails
`lint.rustdoc`.

### Phase 7: Rust calls C and C++ through conan

The shared consumer helper, `PkgConfigDeps`, the profile derived from
the rust receipt, the C++ standard library added. nanobind moves onto
the helper in the same phase.

Acceptance:

- `test_a_conan_profile_that_disagrees_with_the_rust_target_refuses`,
  `test_a_missing_pc_file_refuses_naming_the_requirement`, before the
  happy paths.
- `loop-rust` calls a function of `loop-cpp` through conan, and its
  test passes on every leg, Windows included.
- `loop-native` builds through the shared helper unchanged.

### Phase 8: C and C++ call Rust, and Rust in conan

After open 6 and open 7. The C interface crate's conan package built
with cargo-c, its system libraries from `rustc --print
native-static-libs`, published through conan's routes; the refusal of
two Rust static libraries in one binary, unless the upstream fix has
shipped by then.

Acceptance:

- `test_a_graph_linking_two_rust_static_libraries_refuses_naming_both`
  first.
- `loop-cpp` calls a `loop-rust` C function through conan on every
  leg, statically and as a shared library.

### Phase 9: the wheels' ABI modes, for nanobind

Needs no Rust and no toolchain engine, so it can land at any time.
`[wheels] abi` with `per-interpreter`, `stable` and `mixed` for
`python-nanobind`: the floor judged per flavour against nanobind's
minimums, the wheel set each mode builds, every stable-ABI wheel
tested on each interpreter it claims, abi3audit as a store tool, and
the free-threaded import test. Whether nanobind builds `abi3t` is read
here.

Acceptance:

- `test_stable_with_a_floor_below_the_minimum_refuses_naming_mixed`,
  `test_a_stable_abi_wheel_calling_a_private_symbol_fails_abi3audit`,
  `test_a_free_threaded_import_that_enables_the_gil_fails`, before
  the happy paths.
- `test_mixed_builds_per_interpreter_wheels_below_the_minimum_and_one_stable_wheel_from_it`.
- `test_a_member_that_names_no_mode_builds_mixed`.
- `fm workflow.release --local` builds `loop-native` in each of the
  three modes, and the stable wheel's tests pass on every interpreter
  of the matrix it claims.

### Phase 10: Python extensions in Rust

After phases 3, 4 and 9, open 9 and open 10. The `maturin` extension:
the two-crate seed with its mixed layout under the namespace, the one
version in `pyproject.toml`, the build in the workspace with
`tool.uv.cache-keys` rendered from the cargo closure, `PYO3_PYTHON`,
the categories, wheels under cibuildwheel with the mounted toolchain
in all three ABI modes, stubs, the Rust code measured under pytest;
a `loop-pyo3` seed.

Acceptance:

- `test_the_wheel_writes_no_namespace_init`,
  `test_a_change_to_the_core_crate_rebuilds_the_extension_on_sync`,
  `test_a_module_without_stubs_fails_typecheck`,
  `test_a_free_threaded_import_that_enables_the_gil_fails`, before
  the happy paths.
- `fm workflow.release --local` builds `loop-pyo3`'s wheels in each
  ABI mode; `fm ci.e2e --extension=maturin --fresh` green.

### Phase 11: releases to a crate registry

After the destination plan's phase 13 and open 11. The `crates`
extension: the version stamp in `Cargo.toml`, the changelog per
crate, a cargo row in the registry ladder, `cargo publish` with
trusted publishing where the registry offers it.

Acceptance: `fm ci.e2e --scenario=release --extension=crates --fresh`
publishes `loop-rust` to the local Gitea's cargo registry, and a
second workspace resolves it from there.

## Temporary, replaced by

| Temporary | Replaced by | When |
| --- | --- | --- |
| nanobind's consumer code in `_backends/_python_nanobind.py` | the shared consumer helper | Phase 7 |
| nanobind's one wheel per interpreter, the only mode | `[wheels] abi` | Phase 9 |
| clang's llvm-cov found beside the compiler | the toolchain plan's llvm record; Rust uses its own `llvm-tools` from phase 5 | the toolchain plan |

## Decision record

- 2026-09-26, Willem (`notes/20260904-kind-hierarchy-plan.md`): a
  Rust kind consumes conan packages through `PkgConfigDeps`, so the
  consumer helper is shared. In the workspace an extension builds
  against its library at HEAD, and in the isolated legs against the
  released floor.
- 2026-10-01, Willem: Rust and Go are wanted; the toolchain
  generalises beyond C and C++.
- 2026-10-02, the extensions plan: a language extension owns its
  toolchain families, probes and store records, owns its ecosystem's
  caches, and switches off every installer its ecosystem brings,
  rustup's toolchain file included.
- 2026-10-06, Willem: Rust must be supported.
- 2026-10-12, Willem: the line format carries the most detail a
  measurer gives.
- 2026-10-13, Willem: the line format carries llvm-cov's regions, each
  a start line and column, an end line and column, and a count.
  llvm-cov fills them for clang and rustc; coverage.py and gcov leave
  them empty. An extension of the 2026-10-12 ruling. The floors do
  not change.
- 2026-10-13, the agent: the field is `ranges`, since PR #1569's
  format names functions and classes `regions`.
- 2026-10-13, the agent: the topics Willem named (C linkage and the
  dependencies between packages, Python extensions in Rust, the cargo
  lock) are the plan's core. The toolchain, the gate's steps, the API
  pages and the releases are added because a Rust member cannot
  exist without them.
- 2026-10-13, Willem: the consumer helper stays one copy shared by
  nanobind and Rust; no duplication.
- 2026-10-13, Willem: both native kinds build wheels in any of three
  modes: one per interpreter; the stable ABI only, when the
  interpreter floor allows it; or mixed, per interpreter below the
  floor the stable ABI needs and stable from it.
- 2026-10-13, Willem: the default wheel mode is `mixed`, because it
  saves build time. A nanobind member that never chose a mode moves
  from one wheel per interpreter to `mixed` with phase 9.
- 2026-10-13, Willem: maturin is the build for Python extensions in
  Rust, and the plan gives it its own design.
- 2026-10-13, the agent, in that design: a member is two crates, the
  logic and a PyO3 binding that is never published; the version lives
  in `pyproject.toml`; the cache keys come from the cargo closure,
  never a hand list.
- 2026-10-13, Willem: "Fold it in": phase 1, counted ranges in the line
  format, is built inside 12e's P4 (#1583), which already moved the
  llvm reading to the JSON export and the record to a new schema, so
  the format changes and every unit is measured afresh once, not twice.
  Ranges are LLVM's, so clang's C and C++ fill them as rustc will.
  Proved by a desk pass on macOS (loop series pass 20261013T133429Z):
  loop-cpp's stored parts carry llvm-cov's columns and its ranges with
  their counts.

## Open

Owner: Willem, unless named.

1. **Stable only.** (a) The merge path uses a pinned stable toolchain
   and no `-Z` flag. (b) A nightly toolchain beside it for the
   features that need one. (a) does not cover branch coverage,
   rustdoc JSON or cargo's minimal-versions resolution; (b) pins a
   toolchain that changes daily and whose flags are not promised to
   last. Recommendation: (a).
2. **The extensions.** (a) `rust` (the language: categories, the
   toolchain family, coverage), `cargo` (the build system: the
   workspace, the lock, the build and test steps), `crates` (the
   ecosystem: registries and publishing), with `rustfmt`, `clippy`,
   `nextest` and `maturin` as their own, as ruff and clang-tidy are.
   (b) One `rust` extension holding all of it. (b) does not cover a
   workspace that uses Rust without publishing crates, or a tool
   swapped for another. Recommendation: (a), the destination note's
   own split of language, build system and ecosystem.
3. **The test runner.** (a) cargo-nextest, with `cargo test --doc`
   for doc tests. (b) `cargo test` alone. (b) does not cover per-test
   contexts for unit tests, or a test that crashes its process without
   taking the others with it. Recommendation: (a).
4. **How a floor is written in `Cargo.toml`.** (a) Cargo's default
   caret requirement, `"0.3.1"`, meaning at least 0.3.1 and below
   0.4.0. (b) `">=0.3.1"`, as conan and Python write it. (b) lets a
   0.4 satisfy, which cargo treats as a breaking release, and makes
   cargo keep two copies where its convention unifies them. (a) needs
   the floor bump to move the range when a sibling's major moves.
   Recommendation: (a).
5. **Floor legs.** The python kind tests the released floor in
   isolated legs; cargo's minimal-versions resolution is nightly-only.
   (a) Resolve each direct dependency at its floor with `cargo update
   --precise` on stable, the equivalent of `-Zdirect-minimal-versions`.
   (b) No floor legs for Rust. (a) does not cover transitive floors.
   Recommendation: (a).
6. **Which crates go to conan.** (a) A crate that declares a C
   interface (`staticlib` or `cdylib` and a cbindgen configuration)
   gets a conan package beside its crate. (b) None; C consumers use
   cargo-c's install layout. (c) Every library crate. (b) does not
   cover a C++ member depending on a Rust one; (c) packages crates
   that have no C interface to call. Recommendation: (a).
7. **Static, shared, or both** for a C interface crate in conan. (a)
   Both, through conan's `shared` option, with the refusal of two
   Rust static libraries in one binary until upstream fixes it. (b)
   Shared only until then. (b) does not cover a consumer that must
   link statically. Recommendation: (a).
8. **The default wheel mode.** Decided 2026-10-13: `mixed` (the
   decision record's entry of that day).
9. **What a Python extension's core crate builds against outside the
   workspace.** (a) The sdist carries the path dependencies, and the
   isolated legs build that sdist. (b) The isolated legs build against
   the core crate's released version from a registry, the split ruled
   for nanobind on 2026-09-26. (a) never tests the published crate;
   (b) needs the core crate published first, and an sdist a user builds
   needs that registry. Recommendation: (b) when the core crate is
   published as a crate of its own, (a) when it is not.
10. **Stubs.** (a) PyO3's introspection, generated at build time. (b)
    `.pyi` files by hand, checked against the module by mypy's
    stubtest. (a) is experimental in PyO3 and can change between
    releases; (b) drifts until stubtest catches it. Recommendation:
    (a), with PyO3 pinned and a stubtest run as the check.
11. **Where crates publish.** GitHub has no cargo registry, and a
    release asset is not a source cargo reads. Gitea has a cargo
    registry; GitLab's is in beta behind its `cargo_packages` flag;
    crates.io takes trusted publishing from GitHub Actions and
    gitlab.com, not from a self-hosted GitLab. (a) The ladder's cargo
    row: a declared registry, then the forge's (Gitea, GitLab with its
    flag on), then crates.io. (b) crates.io alone. (b) does not cover a
    private crate. Recommendation: (a). Also read before phase 11:
    whether crates.io lets trusted publishing make a crate's first
    release, which decides how names are claimed. Owner: the agent,
    at phase 11.
