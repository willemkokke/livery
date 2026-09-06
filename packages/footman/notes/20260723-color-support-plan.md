# Colour support — implementation plan

Branch: `feat/color` (off `501c3e8` on `shell-subsystem`, or off `main` once
the shell subsystem lands). Sits beside the shell/`run`/`Result` work.

## The problem

footman spawns children over pipes, never a PTY, so a child's stdout fails
`isatty()` and well-behaved tools auto-disable colour. What footman loses
without a PTY is **live terminal control** (cursor addressing, alternate-screen
TUIs, size queries) — *not* colour. Colour is just SGR bytes in the stream:
position-independent, so it survives capture and replays faithfully onto
footman's own terminal. The job is to (a) make children emit colour when the run
is colourful, (b) push footman's monochrome decision down to children when it is
not, and (c) do both automatically for the curated tools.

## The model (settled)

- **One run-wide decision, delivered everywhere.** Colour is global — the same
  bit for footman's own chrome and for every process it spawns. That bit already
  exists: `_colored(ctx)` (`context.py:1014`), true iff footman's real stdout is
  a terminal, `--no-color`/`NO_COLOR` absent, and the run is not captured
  (`--json`/programmatic force it off). Reuse it verbatim; do **not** invent a
  second signal. `ctx.tty` (`schedule.py:261`) already folds in the replay
  destination, so a captured parallel task still reports the terminal's
  colour-ness — correct, because its bytes replay onto that terminal.
- **Tri-state surface.** `--color=always|never|auto` global, `[tool.footman]
  color = "auto"` in config, default `auto` (= today's `_colored(ctx)`
  behaviour). `--no-color` stays as an alias for `--color=never`. `always` forces
  colour on even when piped (a user replaying into `less -R`); `never` is
  `NO_COLOR` semantics.
- **Env-first, flags only when needed.** The generic mechanism is the
  environment overlay, applied to *every* child in the one `run_env` spot. A tiny
  per-tool flag table exists **only** for tools that ignore the env vars — it
  starts with just git and grows solely when the audit proves a tool needs an
  entry. No belt-and-suspenders: we don't append `--color=always` to a tool that
  already honours `FORCE_COLOR`, to keep the shown command line clean.
- **No stripping — force off via the flag instead.** When colour is off, footman
  sets `NO_COLOR`/`FORCE_COLOR=0` downward; a well-behaved tool goes quiet, and a
  tool writing to our pipe sees a non-tty regardless. A tool that ignores
  `NO_COLOR` but exposes a `--color=never` gets that flag (the off direction of
  its `_COLOR` entry) — the tool's own switch, not a post-hoc scrub of its bytes.
  The only thing left leaking is a tool that ignores `NO_COLOR` *and* has no
  colour flag at all (the `none` row below) — genuinely stuck, and named
  explicitly rather than silently rewritten. footman never strips: it can't tell
  a bug from a deliberate per-call `color="always"` override.

## The environment overlay (Mechanism 1 — generic)

A `color_env(on: bool) -> dict[str, str]` helper, overlaid at **lowest
precedence** so `ctx.env` and a call's `env=` still win:

```
run_env = {**os.environ, **color_env(on), **ctx.env, **(env or {})}
```

- colour **on**  → `FORCE_COLOR=1`, `CLICOLOR_FORCE=1`, `CLICOLOR=1`
- colour **off** → `NO_COLOR=1`, `FORCE_COLOR=0`

Lowest-precedence placement is deliberate: an inherited `FORCE_COLOR=1` from a
piped CI shell is correctly *overridden* to `0` when we decide off, while a user
who sets either by hand still wins. `TERM` is left untouched on purpose — forcing
a `TERM` can coax a tool into cursor control we can't replay; `FORCE_COLOR` /
`CLICOLOR_FORCE` override the isatty check without that risk.

This one change covers the modern set (ruff, uv, pytest, prek, …) both as
subprocesses and — folded into the `_process_state` overlay
(`context.py:766`) — as in-process tools (rich/click read the env). Confirm the
"no env → fast path" branch there does not regress the barrier-overlap
parallelism it documents (colour env is now always present, so that fast path is
taken less often).

## The per-tool flag table (Mechanism 2 — only when needed)

`_COLOR`, a module-level table in `tools.py` beside `_NEGATIONS`/`_WRAPPERS`,
keyed by `(tool, verb)` because `--color` is usually verb-scoped (`git diff
--color=always` is valid; `git --color` is not). Two entry shapes, both data:

- **auto-detected** (added by `sync`): a filter over the existing
  `ToolSpec`/`Option` — a verb with a `--color`/`--colors`/`--colour` option
  whose `choices` include `always`/`never` gets
  `ColorFlag(flag="--color", on="always", off="never")`. Both spellings come from
  the same `choices` list, so detecting the *off* direction costs nothing extra.
  This is where "we already have the parameters" pays off; the work is a scan
  added to `_stubgen.py`.
- **curated quirk** (hand-written in `_drivers.py`, like the `_NEGATIONS`
  exceptions): git's universal switch is not `--color` at all but a **pre-verb
  global**, `-c color.ui=always`, injected into the base ahead of the verb.

**Both directions.** A `ColorFlag` is symmetric because a tool needs telling
either way, and the two are independent per tool:

- colour **on**  → inject the tool's `on` value (force colour past an ignored
  `FORCE_COLOR`)
- colour **off** → inject the tool's `off` value (force quiet past an ignored
  `NO_COLOR` — the "we set `NO_COLOR` and the app ignores it, but it *has* a
  `--color=never`" case)

The off direction is the **principled replacement for stripping**: rather than
scrub ANSI out of a stubborn tool's output after the fact (which can't tell a bug
from a deliberate override), footman tells the tool in its own sanctioned switch
not to emit colour — deterministic, honest in the shown line, and exactly what
keeps a hardcoded-colour tool from corrupting a `--json` envelope. git shows why
the two are separate: it needs `on` (its default `auto` goes monochrome on a
pipe) but no `off` (that same `auto` is already quiet when piped), so its curated
entry sets `on` only.

At call time `Tool.__call__` (`tools.py:432`) injects the direction matching the
current colour decision **only if** `(argv0, verb)` has an entry, that direction's
value is set, **and** the caller did not pass `color=`/`colors=` explicitly
(never fight a deliberate override). The flag goes into both the executed argv
and `_show_parts`, so the painted line honestly shows what footman added. Env
stays generic in `run()`; flags stay per-tool in the bridge — the same
policy-vs-work split the bridge already draws.

Parity: `_COLOR` is a new module-level binding in `tools.py`, so it needs a
mirror line in `tools.pyi` (AST parity test — `_NEGATIONS`/`_WRAPPERS` are
declared at `tools.pyi:91`).

## Capture / replay hardening

Nothing needed on the capture path — the router streams child bytes to `real` on
replay and SGR is position-independent. One hardening: **reset colour at each
task/chunk boundary** — emit `\x1b[0m` after a child's blob in the parallel flush
(`context.py:1456`), so an unterminated colour from a crashed child can't bleed
into a sibling's interleaved output.

## Per-tool colour support

The audit output that answers "does the automatic path actually work for this
tool," inspected **in both directions** — can we force colour *on*, and can we
force it *off*. `env` = obeys `FORCE_COLOR`/`CLICOLOR_FORCE` and `NO_COLOR`, no
flag needed. `flag` = needs a `_COLOR` entry (possibly asymmetric — `on` only,
like git). **`none`** = a direction that can't be forced at all (ignores the env
var *and* has no colour flag for that direction); gets an explicit mention here
and in the docs table so the limitation is honest, never a silent surprise. To be
filled in by the sync/audit step against the provisioned binaries — the values
below are the expected first pass, verified during the build, not asserted from
memory.

| Tool | Mechanism | Notes |
|------|-----------|-------|
| ruff / ruff format | env | `FORCE_COLOR` honoured; also has `--color=always` |
| uv | env | `FORCE_COLOR` / `NO_COLOR` honoured |
| pytest | env | `FORCE_COLOR`; own `--color=yes/no/auto` if ever needed |
| prek | env | verify |
| basedpyright / mypy / ty | env | verify each |
| gh | env | `NO_COLOR` / `CLICOLOR_FORCE` honoured; verify |
| git | **flag** | pre-verb `-c color.ui=always` (curated); top-level `--color` invalid |
| docker | env? | verify — may need `--color`/no support on some subcommands |
| mkdocs / zensical | env (in-proc) | rich-based; env in the in-process overlay |
| coverage | env (in-proc) | verify it colours at all |
| cspell / markdownlint | env | node tools — `FORCE_COLOR` is the node convention |
| bun | env | node convention |
| cmake / ninja | verify | ninja colours on tty; check `CLICOLOR_FORCE` |
| djlint / twine / git-changelog / git-cliff | verify | fill from audit |
| **(cannot behave)** | **none** | *hopefully empty — any tool that lands here is named* |

## Build order

Dependencies run downward; each commit gated (`fm check` + coverage ≥ 92).

### 1 — Tri-state global + config + `Context`
- `split.py` `GLOBALS`: add `--color` (valued: `always|never|auto`); keep
  `--no-color` as the `never` alias. Derived `_GLOBAL_KIND`/`_CANON` pick it up.
- `config.py`: read `[tool.footman] color`; `_app.py` resolves CLI > config >
  env (`NO_COLOR`/`FORCE_COLOR`) > `auto`, collapsing to the existing
  `no_color`/tty inputs `_set_colors` and `schedule.py:261` already consume — so
  `_colored(ctx)` keeps being the one predicate.
- Tests: tri-state parses, `--no-color` still works, config default, resolution
  precedence (test_split, test_config, test_app).

### 2 — Environment overlay (Mechanism 1)
- `context.py`: `color_env(on)` helper; overlay in `run()`'s `run_env`
  (`context.py:1337`) and in the `_process_state` overlay for in-process tools.
- Tests: on-run sets force vars, off-run sets `NO_COLOR`/`FORCE_COLOR=0`,
  `ctx.env`/call `env=` override the overlay, inherited `FORCE_COLOR=1` is
  overridden off, in-process path sees it (test_context).

### 3 — Boundary reset
- `context.py` `parallel()` flush: append `\x1b[0m` after each child blob when
  `_colored(parent)`.
- Test: an unterminated colour in one child does not bleed into the next.

### 4 — Per-tool flag table (Mechanism 2)
- `tools.py`: `_COLOR` table + injection in `__call__`, guarded by
  colour-on + entry-exists + no explicit `color=`; mirror in `tools.pyi`.
- `_drivers.py`: git's curated `-c color.ui=always` pre-verb entry.
- `_stubgen.py`: auto-detect `--color` verbs from `ToolSpec`, emit symmetric
  `_COLOR` entries (both `on`/`off` from the `choices` list; regenerated with the
  stubs, like `_NEGATIONS`).
- Tests: git injects the pre-verb `on` form and nothing for `off`; an
  auto-detected tool appends `--color=always` on-run and `--color=never` off-run;
  explicit `color=` suppresses injection either way (test_tools).

### 5 — Audit + the colour column
- `fm footman tools audit` reports each tool's mechanism per direction
  (env / flag / none for on and off), populating the table above; any `none` is
  named loudly.
- Tests: audit classifies a known env tool, git (on-flag/off-env), and a
  hypothetical off-flag tool correctly.

### 6 — Docs + CHANGELOG
- `tools-bridge.md` / `configuration.md`: the tri-state, the env overlay, the
  no-PTY colour story (colour survives; live cursor control does not), and the
  per-tool support table — with any `none` tool called out.
- `CHANGELOG.md` `[Unreleased]` Added.

## Open decisions

_All three from the design chat are settled:_ tri-state ✅ · env-first, flags
only when needed ✅ · no stripping ✅.

Remaining, to resolve during the build (empirical, not blocking):

1. **Any tool in the `none` row?** Hopefully none. Whatever the audit finds gets
   named in the table and the docs, not hidden.
2. **`docker` per-subcommand colour** — confirm which subcommands honour the env
   vs need help; may or may not warrant a `_COLOR` entry.

## Verification

- `fm check` + `uv run pytest --cov` (≥ 92) per commit; docs build when docs
  change.
- Live on a real pty: `fm check` shows ruff/pytest in colour; `fm check | cat`
  is byte-clean (no escapes); `fm --color=always check | cat` keeps colour;
  `NO_COLOR=1 fm check` is monochrome and pushes `NO_COLOR` to children;
  `fm --json check` carries no ANSI in the envelope; a `tools.git.diff()` in a
  task shows colour on a terminal and none when piped.
- CI matrix, Windows included (the env overlay is cross-platform; git-bash and
  cmd children both get the vars).
