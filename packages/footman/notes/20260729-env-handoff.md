# The environment handoff: what a child inherits, and what a task can hide

Started 2026-07-29, from a small question — hse wanted `git rev-parse` in a
release task without a step — that turned into the environment model. This
note exists because Willem had argued for env *replacement* before, been
argued out of it, and could not remember why. The reasoning is worth keeping
even where it turned out to be wrong.

## The two layers, and which is which

- **`ctx.env` is a per-task overlay.** Private. A sibling never sees it.
- **`_globals._snapshot` is a copy of `os.environ`, pinned once at the run
  boundary.** Every task reads through `{**_snapshot, **overlay}`.

The snapshot being a *copy* is the fact everything below turns on, and it is
easy to misremember as live shared state (I did, twice, in one conversation).
Nothing a task reads touches the real process environment; footman never
mutates it in a parallel task.

Choosing a snapshot over live reads is **arbitrary but usually right**
(Willem's phrase). It buys determinism across a parallel run and pins
run-boundary state — the colour variables — into the base. It costs truth: if
anything outside footman mutates `os.environ` mid-run, no task will see it.
Usually nobody does.

## Why deletion was refused, and why that reason does not survive scrutiny

The guard refuses `del os.environ[k]` for a **base** key, teaching:

> scoped env is additive; spawn the child with an explicit `env=` that omits
> it, or mark the task serial.

The recorded principle is *"the guards refuse SUBTRACTIVE/VISIBLE moves, not
sibling-invisible ones"* (PR #180 made set-then-delete round-trip, since a key
the task itself set is invisible to siblings either way).

The stated justification does not hold up: because the base is a private copy,
a **tombstone in the overlay would be sibling-invisible too**. `_merged()`
skips tombstoned keys, the Popen injection honours them, and nobody else can
tell. So the refusal is a *representation choice* — `dict[str, str]` needed no
sentinel — and not a constraint the architecture imposes.

**What that leaves broken.** Many variables carry *presence* semantics rather
than value semantics; footman itself reads one that way
(`"NO_COLOR" not in os.environ`). Overriding to `""` is not unsetting. For a
subprocess this does not matter — you construct the child's environment. For
an **in-process** tool there is no child: it reads `os.environ` inside the
task's own process, and the overlay is the only lever. So the one execution
mode footman is proudest of is the one where the escape hatch does not exist.

Not urgent, and it should wait for a real caller. But if it comes up: the fix
is a tombstone sentinel honoured in `_merged`, the `__delitem__` guard, the
Popen injection, and `run_env` construction.

## The handoff is deliberate, not leaky

Worth stating because it inverts the obvious framing. footman does not
*accidentally* leak its environment to children — it hands one over on
purpose, everywhere:

- `run()` builds `{**os.environ, **ctx.env, **(env or {})}`
- the Popen router fills `{**_snapshot, **ctx.env}` into any raw
  `subprocess` call that left `env=` unset, with a teach-once note

So when `PYTHONHOME` reaches a foreign tool, that is footman putting it there,
via machinery designed to be thorough. The gap was never interception; it was
that the handoff had no vocabulary for "everything except this".

Residual gaps in the virtualisation, for completeness: `os.environb` (a
different instance, passes through), C-level `getenv()` (physics — a native
extension reads the real environment), `os.system`/`os.exec*`/`os.spawn*` (not
intercepted), the 10-positional `Popen` spelling (deliberately left alone),
serial/exclusive tasks (they own the real globals), and `cwd_unmanaged`.

## The decision (revised 2026-07-29, same day — the first answer was wrong)

**`ctx.env` becomes the task's environment, not an overlay.** Initialised from
the process environment at the run boundary; `os.environ` is virtualised onto
it; `run(env=)` **replaces**, exactly as `subprocess` does.

Willem's objection, and it is the right one: the overlay is artificial. Once
you see that the base is a private copy, there is no reason a task cannot own
a full environment and delete from it freely — only things inside that task
see it, and it is handed verbatim to every child footman launches.

What that buys, in one line each:

- `_merged()` → `ctx.env`; the Popen injection → `ctx.env`; `run_env` →
  `{**ctx.env, **(env or {})}`. Three merge spellings become one value.
- Deletion is ordinary `del`. No tombstone, no additive rule, no
  set-then-delete exception, no taught error.
- `read_env()` disappears — it is a hand-rolled "snapshot minus three".
- **`isolated=` is never built.** It existed only to work around the
  overlay's inability to subtract; remove the overlay and the flag has no job.
- Both standard idioms work with no footman vocabulary at all:
  `run(env={**os.environ, "CI": "1"})` to add, `e = dict(os.environ);
  del e["X"]; run(env=e)` to remove. Because `os.environ` is virtualised,
  `dict(os.environ)` is exactly what the child will get — the copy-modify-pass
  convention becomes correct rather than approximately correct.

**Why `env=` must replace rather than merge.** Merging defeats the convention:
`e = dict(os.environ); del e["Y"]; run(cmd, env=e)` puts `Y` straight back
from `ctx.env`, which is the original bug relocated. Deletion is only
expressible if what you pass is what the child gets.

**Costs, both accepted:**

- `run(env={})` becomes a child with no `PATH`, where today it is harmless.
  That is exactly `subprocess`'s behaviour, so it is conventional danger
  people already have a reflex for, not a new trap.
- `Context(env={"CI": "1"})` changes meaning from overlay to whole
  environment. A `default_factory` snapshot covers construction; the explicit
  sites need deliberate migration. Four `env=` call sites exist in the whole
  tree, one of them an overlay (`tasks.py`, `COVERAGE_FILE`).

**Superseded by this:** the `isolated=` design below the line in the previous
revision (inheritance semantics, `PYTHONHOME`/`PYTHONEXECUTABLE` by default,
`PYTHONPATH` opt-in) and `replace_env=`. Both were solving a problem this
model does not have. The rejected alternatives above still stand as rejected,
for the same reasons.

## Loose end

`_toolhelp`'s comment attributes the interpreter variables to `uv run`
exporting `PYTHONHOME`. Measured on uv 0.11.x / macOS, `uv run` adds only `UV`
and `UV_RUN_RECURSION_DEPTH` and changes `PATH` — none of the three. The
*symptom* was real and measured (107 Windows holes); the *cause* in the
comment does not reproduce here, so it is either Windows-specific, an older
uv, or something else entirely (the provisioning path uses `uv tool install`,
a different mechanism). Worth confirming before anyone reasons from it — a
load-bearing comment that is wrong is worse than none.
