# Freezing a Brand-based CLI

Explored 2026-07-30. **Nothing built, nothing decided** — this is the survey
that came out of one question: what stops footman shipping as an executable?

## What is being aimed at, and what is not

**The target: a Brand-based CLI whose tasks ship with it.** One binary,
carrying its own commands, for people who have no Python and should not need
one. Cross-platform, alongside PyQt6 GUIs built the same way.

**Not the target: freezing footman-the-task-runner.** It fights its own
design. footman's job is importing and running *your* code, and a frozen
interpreter is a closed world. A frozen `fm` can still import a `tasks.py`
from disk, but the moment those tasks `import httpx` the bundle has nothing
to give them. A Brand whose tasks ship with it has no such tension: closed
world, closed world, and the freeze is honest.

Worth keeping the distinction sharp, because the first sounds like a superset
of the second and is really a different thing wearing its clothes.

## What in footman would break

There is no `sys.frozen` or `_MEIPASS` handling anywhere — none of this has
been looked at before, so the list is what a read of the code turns up, not
what a build reported.

### 1. Four self-spawns assume `sys.executable` is Python — the real work

Frozen, `sys.executable` is *the binary*, so each of these re-invokes the CLI
with arguments its own grammar will try to read as a task chain:

- `_complete.py:603,606` — `[sys.executable, "-c", script]`, the dynamic
  completer
- `_complete.py:713` — `[sys.executable, "-m", "footman._suggest"]`
- `_app.py:1015` — the GC child, and the manifest refresh spawn it was
  copied from ("`_complete`'s refresh spawn, verbatim")

This is the piece with design in it rather than guards. Completion is a
headline feature and it spawns children three ways, so each needs the binary
to re-enter itself in a mode the CLI will not parse. The pattern and the
place both exist already: `main()` dispatches `--complete` before importing
anything, which is exactly where an internal-child flag belongs.

Open: whether one internal entry point serves all three, or whether the
refresh child (which must outlive its parent) wants its own shape.

### 2. `tools.python` becomes self-reference

`tools.py:844` is `Tool("python", path=sys.executable)`. Frozen, a task
calling `tools.python(…)` silently re-runs the CLI instead of an
interpreter. The worst failure shape on this list — wrong behaviour, not a
crash, and nothing in the run report would say so.

Open: does a frozen CLI want a `python` tool at all? Refusing it with a
taught error may beat resolving one from `PATH`, which would be a *different*
interpreter than the one the tasks were written against.

### 3. Entry-point discovery — mostly dissolves under this target

Five sites call `importlib.metadata.entry_points()` (`compose.py:195,247`,
`_app.py:617`, `tools.py:421`). A frozen app carries no `.dist-info` unless
the freezer is told to bundle it — PyInstaller's `--copy-metadata`, Nuitka's
`--include-distribution-metadata`, both opt-in per package — so discovery
returns empty with no error.

But tasks that ship with the CLI are composed with `include()`, not
`plugin()`, so this is not load-bearing here. What survives is
`tools.py:421`, which looks up `console_scripts` to find an installed tool's
script; it matters only for tasks that call Python-based tools, which a
frozen CLI mostly should not.

### 4. The uv handoffs — disable when frozen

`_app.py:1239` re-execs `[python, "-m", "footman", *argv]`, and script-tasks
does the same. A frozen binary is not installed in that environment, so
neither can work as written.

Straightforward, and there is precedent: script-tasks already decided a
non-stock Brand skips the script handoff, because footman cannot know the
brand's distribution name. "Frozen means no handoff" is that reasoning one
step further, and arguably a frozen Brand should refuse both handoffs on
principle — it carries its own dependencies, which is the whole point.

### Probably not an issue

`multiprocessing` is only *detected*, never used to spawn (`_globals.py:463`
patches `multiprocessing.process` to recognise a worker). PyInstaller wants
`freeze_support()` early on Windows; footman is threads and subprocesses
throughout, so this likely costs nothing — unverified.

## The landscape, 2026

**PyOxidizer is over.** Its author stepped back and it has been in
maintenance for years. The part worth having survived and outgrew it:
python-build-standalone is Astral's now, and it is what uv's managed
interpreters are. The idea won; the freezer did not.

| | fit |
|---|---|
| **PyInstaller** | The default. Best hook ecosystem, PyQt6 well-trodden. One-dir or one-file. |
| **Nuitka** | Compiles to C — better startup, some real speedups, `--enable-plugin=pyqt6`. Slower builds; some features commercial. |
| **Briefcase** (BeeWare) | Native *installers* (MSI/DMG/AppImage) rather than a bare executable. Right shape for app-store-style distribution; more opinionated. |
| **PyApp** | A Rust launcher that materialises a real Python environment on first run. Not a freeze — which for a CLI is an argument in its favour, since it never has the closed-world problem. |

**None of them cross-compile.** Each target OS builds its own, which the
existing CI matrix (ubuntu/macos/windows) already covers.

**Lean:** PyInstaller for the PyQt6 GUIs, Nuitka if startup time turns out to
matter. For the CLI, PyApp and `uv tool install` deserve a look before
committing to a freeze at all — but they do not answer "someone with no
Python", which is the case that started this.

## Open questions

1. Is the aim one binary per brand, or footman gaining a `freeze` task that
   drives PyInstaller/Nuitka for anyone's brand? The second is ordinary
   tooling; the first is the design above.
2. Does completion survive freezing in a form worth having? The hot path is
   a file read and a JSON parse, which is fine — but the refresh and the
   dynamic completer both spawn, and if the answer is "a frozen CLI gets
   baked completion only, never dynamic", that is a real reduction to
   decide on rather than discover.
3. Does a frozen brand refuse `tools.*` for Python-based tools, or resolve
   them from `PATH` and accept they are a different interpreter?
4. Where does the manifest cache live for a binary with no package
   directory?

## Next step, unstarted

Spike (1): make one self-spawn re-enter a frozen binary correctly and see
what it drags in. It is the item everything else waits behind, and it is the
one most likely to be either an afternoon or a fortnight.
