# Startup cost: where the no-op launch went

Written 2026-08-16, chasing the gap between `comparison.md`'s "~38 ms" and a
measured ~67 ms. All numbers from one quiet machine (M-series, load < 4),
min-of-25 fresh processes unless said otherwise.

## The number did not regress — it aged

`~38 ms` was written 2026-07-16 at **v0.4.0**, and it was right: walking the
released wheels, 0.4.0 measures **38.9 ms**. It had not been re-measured in
the 37 releases since.

| version | `fm noop` | what landed |
|--------:|----------:|-------------|
| 0.4.0   | 38.9 ms   | the number was written here |
| 0.11.0  | 41.4 ms   | |
| 0.12.0  | 54.7 ms   | the progress bar (`statistics`, `hashlib`) |
| 0.20.0  | 61.3 ms   | |
| 0.30.0  | 72.5 ms   | |
| 0.38.0  | 66.9 ms   | the perf-polish release, -6 ms |
| 0.41.0  | 66.7 ms   | |

## The shape of a launch

| stage | cost |
|-------|-----:|
| bare interpreter | 13.8 ms |
| `import footman` | +1.5 ms |
| answering `--version` | +29.5 ms |
| `--list` over `--version` | +2.6 ms |
| running a no-op | +8.2 ms |

`import footman` costing 1.5 ms means the lazy `__init__` works. Almost
everything arrives through the single `from footman import _app` inside
`App.run()`, so `--version`, `--list` and every run pay the same bill.
Imports were 53% of the total, the interpreter 24%, real work 23%.

## What landed

- **`concurrent.futures` off the run path.** Two sites already deferred it,
  but "the moment of use" was every executed task: `_Cell.__init__` claimed a
  `Future`. It drags `logging` through `traceback`, and `logging` drags
  `_colorize`. The five methods used are a `threading.Event` and two slots.
- **A one-node plan runs on the calling thread**, no `ThreadPoolExecutor`.
  The regime is deliberately unchanged — routing single nodes to
  `_run_sequential` would have been shorter and wrong, because it builds a
  sequential context and `os.chdir` would then be legal in a one-task chain
  and refused in a two-task one.
- **`_install_multiprocessing` only patches if multiprocessing is imported.**
- **`statistics` moved past the guard in `estimate()`.**
- **`uv` resolved at the point of use**, not before six early returns.
- **`shutil` deferred in four modules** — 1.9 ms of archive codecs for one
  `get_terminal_size` and two `which` calls.
- **GC deferred through startup, frozen and re-enabled at the run boundary.**
  Startup allocates heavily and discards almost none of it; CPython ran ~11
  collections walking objects that were all still live.
- **The tempfile warm made conditional.** It now resolves in
  `_executor.run_bound`, where `ctx.cwd` is known, and only when the task's
  directory has actually shifted.

| | before | after |
|---|---:|---:|
| `fm noop` min | 67.2 ms | **50.6 ms** |
| `fm noop` median | 70.5 ms | **51.6 ms** |
| `fm --version` | 47.2 ms | 40.9 ms |
| `fm --list` | 49.6 ms | 44.5 ms |
| import self-time | 61.0 ms | ~35 ms |
| modules imported | 193 | 167 |

### GC: the shape that won

Measured back to back, 25 fresh processes per cell, medians of two runs:

| strategy | median |
|----------|-------:|
| plain | 61.5 / 62.0 |
| freeze after imports | 56.6 / 57.0 |
| disable, re-enable at exec | 60.0 / 60.7 |
| disable, re-enable + forced collect | 60.6 / 61.4 |
| **disable, freeze + re-enable at exec** | **54.3 / 55.0** |
| disable, never re-enable | 58.0 / 58.4 |

Forcing a collection on re-enable was slower than doing nothing — the
traversal costs more than the garbage is worth. Never re-enabling was slower
than freezing: a heap that never collects grows arenas instead.

### Why the tempfile warm exists, and why it moved

`tempfile.gettempdir()` builds its candidate list by appending `os.getcwd()`
— eagerly, despite the stdlib calling it a last resort (`tempfile.py:178`) —
so a task's first `mkdtemp()` reads the process cwd and the guard attributes
it to the task. Verified: with the warm removed, a task whose body is only
`tempfile.mkdtemp()` is told it "reads the process cwd".

But the guard only speaks when the task's directory differs from the process
directory, so the common case paid ~3 ms to prevent nothing. Pinned three
ways after the move: `mkdtemp` alone is silent, a genuine `os.getcwd()` is
still noted, and doing both still notes the real read.

**Why not just return `ctx.cwd` from the intercepted `getcwd()`?** Because
footman cannot make that lie true. It virtualises `os.environ` *and* injects
the same env into children, so read and effect agree; the cwd equivalent
would need `chdir`, which serialises the run. And the lie travels only half
the stack: `posixpath.abspath` goes through `os.getcwd()` (line 381), as do
`realpath`, `Path.resolve()` and `Path.cwd()`, while `open`/`stat`/`listdir`
hand relative paths to the kernel. Patched to lie, a process writes a file
and then computes a path for it that does not exist. A visible note beats a
silent divergence.

## The manifest rebuild: measured again, and smaller than it was

`sync_manifest` calls `build_manifest` on every run — resolving every task's
signature and parsing every docstring — then compares hashes to decide
whether to *write*. The write is guarded; the build is not.

Measured against a build skipped outright, **on the post-GC code** (two runs):

| tasks | rebuilds | skipped | saving |
|------:|---------:|--------:|-------:|
| 1     | 50.2 / 51.4 | 49.8 / 50.0 | 0.4 / 1.4 ms |
| 50    | 49.7 / 50.5 | 49.6 / 50.8 | 0.0 / -0.4 ms |
| 150   | 56.5 / 57.3 | 51.3 / 52.5 | 5.1 / 4.8 ms |
| 400   | 74.3 / 73.7 | 47.8 / 53.1 | 26.5 / 20.6 ms |

**This is no longer a general speedup.** Before the GC work it was worth
2.9 ms at one task and 7.0 ms at fifty; it is now worth nothing at that
scale, because most of what the build cost small projects was the collections
it triggered, not the introspection. What remains is a *scaling* fix: the run
becomes flat in task count (~50 ms) where a 400-task project now pays ~74 ms.

Build it for the argument "per-run cost should not scale with how much you
have written", not for "footman should be faster" — the milliseconds no
longer support the second claim.

### The inputs are knowable, and already computed

`_import_file` snapshots `before = set(sys.modules)` and `_evict_siblings`
uses the delta to drop sibling helpers. That delta **is** the dependency set,
and it is in hand for free — but only *before* eviction. Captured after, the
helpers are already gone.

For a tasks file importing a sibling `helpers.py`, a one-level `pkg/`, and
mounting a plugin:

    PROJECT-LOCAL (3): tasks.py, helpers.py, pkg/__init__.py
    INSTALLED (5):     the plugin's modules
    STDLIB (20)

Fingerprint cost, against a 2.9-26 ms saving:

| | cost |
|---|---:|
| mtime+size hash, 3 project files | 5.9 us |
| mtime+size hash, the 400-task file | 1.8 us |
| content hash, 3 project files | 41 us |
| content hash, the 400-task file | 47 us |

Content hashing is ~1/60th of the smallest saving, so there is no reason to
trade soundness for mtime granularity on project-local files.

The rest of the input set is scalars `sync_manifest` already takes:
`completion_max_age`, `project`, `bake_completers`, `key_dir`, `builtin`,
`tasks_file`, `SCHEMA_VERSION` — plus `footman.__version__`, since
introspection behaviour can change without a schema bump — and the resolved
config dict.

Where it is unsound, and must say so:

1. **Import-time dynamism.** A tasks file building tasks from an env var, the
   clock or a network call has identical files and a different tree. Same
   class as `make`; unfixable by any file-based scheme. Needs an escape hatch.
2. **Modules with no `__file__`** (namespace packages, C extensions) cannot be
   stat'd. Declining the shortcut when any appear is safer than ignoring them.
3. **Import-order attribution.** A module already in `sys.modules` will not
   appear in a later file's delta. The union across a run is complete and
   stable given stable import order — worth asserting, not assuming.
4. **The stakes are asymmetric.** A wrong cache hit does not merely slow
   things down; it makes completion offer tasks that do not exist.

## Dead ends, closed with measurements

- **`dataclasses` is not worth removing.** It looks like the biggest stdlib
  item at 5.41 ms cumulative, dragging `inspect` (3.24) and `re` (1.42). But
  `_manifest` imports `inspect` directly for `inspect.signature`, and `re` is
  used by `_describe`, `_script` and `docstrings` — all loaded anyway. True
  marginal cost of `dataclasses` itself: **~0.28 ms**.
- **The `@dataclass` decorators are a separate ~1.9 ms**, which scalene found
  and importtime structurally cannot (it charges class creation to the
  defining module). 16 classes at ~129 us each. `eq=False, repr=False` would
  save ~58 us apiece and `frozen=True` *costs* 98 us more than plain — but
  removing `__eq__`/`__repr__` from types tests compare and errors print is a
  bad trade for ~1 ms.
- **File I/O is not the problem** — but not for the reason first claimed.
  cProfile does *not* badly distort it (12.2 ms profiled vs 10.9 ms
  unprofiled). The ~10 ms it attributes to `io.open` is first-*call* lazy
  initialisation charged to whichever open runs first: it appears 9.8 ms
  before footman is imported at all. The authoritative process-level A/B —
  `python -c pass` 13.6 ms vs read-a-file 13.8 ms — puts real file I/O at
  **+0.2 ms**.
- **The editable install is not the problem.** Wheel vs editable, same code
  and interpreter: +0.9 ms, inside the noise.
- **Bytecode size is not the problem.** footman's modules average 14 us/KB,
  range 5-52. Modules far above the line are doing work on the way in
  (compiled regex tables, dataclass construction), not paying for their size.
  That ratio is the diagnostic worth reaching for next time.

## On the tools

Three profilers, genuinely complementary here:

- **`-X importtime`** is the only one that sees module-level import cost,
  which was 53% of the launch.
- **scalene** found the `@dataclass` `exec` cost nothing else could attribute.
  Costs: 6.7x overhead at 0.2 ms sampling (2.3x at 0.5 ms), 75% of the profile
  charged to an idle background thread (the status-line ticker — its
  Python/native/system split is what identifies it), and it silently profiles
  whatever `VIRTUAL_ENV` points at if run from the wrong directory.
- **line_profiler** is exact within a function and caught that the
  `docstrings` deferral was a no-op: the import is 94.6% of `_task_node`, and
  `_task_node` runs on every run because `sync_manifest` builds
  unconditionally. Only `--version` is spared by that commit.

## Still on the table

- footman's own 22 modules: ~11.9 ms.
- `tomllib` 0.85 ms, `hashlib` 0.66 ms.
- 13.8 ms of it is the interpreter, which none of this touches.

## Consequence for the docs

`comparison.md:106` quotes "footman ~38 ms, typer ~40 ms" for a single
launch. Measured on 3.13 before this work: footman **79 ms**, typer **56 ms**
— and the page ships the command that shows it. The completion claim it is
really selling holds and is better than stated: **0 ms** of project-import
cost per TAB against duty's and invoke's ~260 ms.
