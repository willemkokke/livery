# Process globals: a refinement pass

Planned 2026-07-29. Reasoning for the env change lives in
[20260729-env-handoff.md](20260729-env-handoff.md) — read that first; this
note is the work.

Four findings from one thread that started with "can a tool call skip being a
step". Three are worth doing, one is worth recording and leaving alone. The
theme: the virtualisation is right, but two of its models are more complex
than the problem, and one guard cries wolf.

## 1. `ctx.env` becomes the task's environment (the big one)

**Today.** A module-global `_snapshot` (a copy of `os.environ` pinned at the
run boundary) plus a per-task additive overlay, merged at three sites in three
spellings: `_merged()` uses `{**_snapshot, **overlay}`, the Popen router uses
`{**_snapshot, **ctx.env}`, `run()` uses `{**os.environ, **ctx.env, **env}` —
the last agreeing with the others only *because* `os.environ` is itself
virtualised, an invisible coupling. Plus an additive-only rule, an exception
to it (you may delete keys you set yourself), and a taught error for the rest.

**After.** `ctx.env` is initialised from the process environment and simply
*is* the task's environment. `os.environ` is virtualised onto it. `run(env=)`
**replaces**, exactly as `subprocess` does.

- `_merged()` → `ctx.env`; Popen injection → `ctx.env`; `run_env` →
  `{**ctx.env, **(env or {})}`.
- Deletion is ordinary `del`. No tombstone, no rule, no exception, no taught
  error.
- `_toolhelp.read_env()` disappears (it is "snapshot minus three", hand-rolled).
- The `isolated=` flag designed earlier is **not built** — it existed only to
  work around the overlay's inability to subtract.
- Both standard idioms work with no footman vocabulary:
  `run(env={**os.environ, "CI": "1"})` to add; `e = dict(os.environ);
  del e["X"]; run(env=e)` to remove.

**Why replace and not merge:** merging defeats copy-modify-pass — a deleted
key returns from `ctx.env`, which is the original bug relocated.

**Migration.** Four `env=` call sites in the whole tree; one is an overlay
(`tasks.py`, `COVERAGE_FILE`) and becomes `{**os.environ, …}`. `Context(env=)`
changes meaning from overlay to whole environment: a `default_factory`
snapshot covers construction, the explicit sites (`context.py:324`, tests)
need deliberate migration. `run(env={})` becomes "no `PATH`" — conventional
danger, identical to `subprocess`, so the reflex already exists.

## 2. The `getcwd` note stops crying wolf

**Today** it fires whenever a managed parallel task reads `os.getcwd()`, with
no check on whether the answer is actually misleading:

```python
if guarded:
    _note("getcwd", "…reads the process cwd — in a parallel run it can be
                     anyone's…")
```

Run `fm` from the directory your tasks file lives in — the common case — and
`ctx.cwd` *is* the process cwd, the read is correct, and you are told it might
not be. In-process tools trip it constantly (a subprocess never can: footman
passes `cwd=` at spawn).

**After:** note only when `ctx.cwd` differs from the real process directory.

The sibling guard already learned exactly this in PR #180 — `chdir` to where
the process already is "changes nothing for anyone" and stopped being refused
(pytest's defensive restore). `getcwd` never got the equivalent.

**Cost to watch:** `chdir` can afford `realpath` because it is rare; `getcwd`
may be polled in a loop. Compare the already-resolved `ctx.cwd` against a
cached process cwd rather than resolving per call.

## 3. `cwd` + `cwd_unmanaged` — proposed, then withdrawn

**The proposal was to collapse them into one field with an `UNMANAGED`
sentinel.** It is wrong, and the code says so plainly: `resolve_cwd` under
the unmanaged policy returns `(Path.cwd(), True)` — a real path *and* the
flag. The two carry different facts:

- **`ctx.cwd`** is *where the task is*: what `footman.cwd()` answers, what
  `rel=` builds on, what the in-process demotion compares against.
- **`cwd_unmanaged`** is *whether footman manages it*: it disarms the guards
  (an unmanaged task may `chdir`) and makes spawns inherit rather than
  receive an explicit `cwd=`.

A sentinel in `cwd` would destroy the path `cwd()` needs, or would have to
wrap it — the two fields again with more ceremony. The repetition that
prompted this (`None if ctx.cwd_unmanaged else ctx.cwd`) is not redundancy:
it reads "the spawn base, which is nothing when unmanaged", a different
question from "where am I".

**No change. Recorded so the next reader does not re-propose it.**

## 4. `sys.argv` — one bug fixed, one question deferred

Two separable things were tangled here.

**Fixed (done 2026-07-29).** `_ArgvProxy` overrode 18 list operations and
missed the in-place mutators — `__iadd__`, `__imul__`, `sort`, `reverse`,
`__reversed__`, `__mul__`. Unoverridden they fall through to `list`, which
edits the proxy's *base* storage, so a legacy `main()` doing
`sys.argv += ["-v"]` suffered both failures at once: the append **vanished**
from its own subsequent reads (those consult the override) *and* leaked into
every call without one. Silent in both directions. Two tests pin it — written
to fail first, and they did: the caller saw `['tool', '--x']` after appending,
and `reverse()` left the view untouched while reordering the base.

**Deferred: is the tools bridge's lock fallback enough on its own?**
`_ArgvProxy` is 95 lines for one case (a zero-argument `main()` that reads
`sys.argv`, parallelised), and carries holes that cannot be closed from
Python: a C extension reading the list storage sees the base, and
`sys.argv = [...]` replaces the proxy outright.

The bridge's alternative is a lock plus save/restore. It is *complete* —
nothing to intercept, so no method can be missed — but it is globally
visible: while held, `sys.argv` really is the patched list for every thread,
so it isolates nothing. The proxy is the better design; the lock is the more
complete implementation.

**Not redundant, and not reachable from a task.** The routers install only in
`run_plan`, so inside a task `_pg.active()` is true and the proxy always
wins. The lock is the *no-run* path — a script or REPL importing
`footman.tools`, or a test calling a tool directly — which is precisely the
standalone-library seam. Revisit whether the lock alone suffices **when that
split happens**, not before.

## Order, and what it unblocks

1. **(2) and (3)** first — small, independent, no migration.
2. **(1)** next, as its own change: the model, then the four call sites, then
   `Context(env=)`, then `read_env()`'s retirement.
3. **Then the conversion this thread started for**: the nine raw `subprocess`
   probes in `_toolhelp`, `_provision`, `_colorprobe`, `_toolfetch`,
   `_drivers` move to `run(step=False, timeout=…)` and gain `ctx.cwd`/
   `ctx.env`, colour handling, Windows quoting and the encoding policy they
   currently hand-roll.

**Resolved (#220):** `run()` now sets `CREATE_NO_WINDOW` for *every* captured
run, exempting `capture=False` and `interactive=True` — Willem's call, "always
set it until we find out a reason not to". So nothing blocks the conversion
any more:

- `step=False` (#209) — the probes report nothing
- `timeout=` (#212) — their hand-rolled `TimeoutExpired` branches retire, and
  gain a tree-kill they never had
- `env=` replaces (#219) — `read_env()`'s subtraction survives the trip, which
  under the old overlay it could not
- `CREATE_NO_WINDOW` (#220) — no longer needs saying per call

**Done 2026-07-29.** Twelve sites, not nine: `_drivers` (1), `_toolhelp` (3),
`_toolfetch` (2), `_provision` (2), `_colorprobe` (4). No raw `subprocess.run`
is left in any of the five. Each module reaches `run()` through a call-time
import (`_fm_run`), because the probes are reachable from the stub tooling,
which has no business importing the run machinery at module scope.

The ordering above was load-bearing, and `_colorprobe._capture` is the proof:
it builds its environment as *`os.environ` minus the colour variables* and
hands it over. Converted before the env change, the additive overlay would
have merged those very variables straight back in and the probe would have
read every tool as already-coloured — a silent wrong answer, not a failure.
Verified after the fact by probing git live through `run()`: `flag`/`env`
with both `color.ui` spellings and `pre_verb`, identical to the verdict
baked in `_colordata`.

## 5. The interpreter is never inherited (#222)

The rule that read the walk's 107 holes belongs to environments, not to
reads. `base_env()` drops `PYTHONHOME` and `PYTHONEXECUTABLE`, so they are
absent from every task's environment, in a run or out of one. A walk spawns
foreign tools; so does any task that calls one, and `run(["mypy", "."])`
against a console script from another environment broke identically.

- **Dropped from the inherited copy, not at spawn.** Nobody chooses to
  inherit. Setting either deliberately still arrives untouched through
  `os.environ`, `ctx.env` or `run(env=)`.
- **`PYTHONPATH` is inherited everywhere, reads included.** It is the one of
  the three people export on purpose, harmless when merely present, and
  indistinguishable from an inherited one. Stripping it for reads alone was
  the special case.
- **`read_env()` and `_wide_env()` are gone.** With the rule central they
  held no policy — only arguments about particular spawns (don't phone home,
  be wide, page with `cat`), which each read site now passes as its own
  `env=`.

Measured, not reasoned: `PYTHONHOME` is set only on Windows (uv 0.11.1 and
0.11.31 alike; absent on macOS/Linux across CPython 3.10–3.14, and
independent of whether the interpreter is uv-downloaded). It is not
load-bearing there — a uv-managed interpreter called without it runs and
reports an unchanged `sys.prefix` — which is the one thing that could have
made stripping it wrong. Windows CI covers it: a task spawns
`sys.executable` with the variable dropped.

**A prediction this note got wrong twice.** Item 1 said `read_env()` would
disappear because it was a workaround for the overlay; I then argued at
length that it must stay because its subtraction was permanent. Both were
about the function. What actually settled it was the rule moving out from
under it — after which there was nothing general left inside.

## Verification

`fm check` throughout. The env change specifically wants: a task deleting a
base key and its children not seeing it; `run(env=…)` handing over exactly
what was passed; the Popen router injecting the same value `run()` would; and
a parallel run where two tasks mutate their environments without seeing each
other's. The `getcwd` refinement wants a test that a task whose `ctx.cwd`
matches the process cwd gets **no** note, and one whose differs still does.
