# Interactive input — implementation plan

> **Partially superseded (2026-07-25):** stdin is now the fourth process
> global — see [process-globals.md](process-globals.md), "The fourth global:
> stdin". **Phase 4 (the scheduler seam) is replaced**: the
> `_prompt_lock`-held-for-the-node's-life becomes the `console` arbiter lane
> (interactive overlaps the parallel pool — the width>1 hard error is dead),
> and ask-at-launch upgrades to ask-as-early-as-correct. The question-layer
> phases (1-3, 5-8: globals, stamping, guarded primitives, `ask()` marker,
> dynamic select, hardening, docs) remain valid as specced below.

Branch: `feat/interactive-input` (off `e5b353f`, the v0.16.0 release commit).
Companion: [interactive-input.md](interactive-input.md) (problem-space map).

## The model (settled)

- **Two layers.** *Question layer* — `ask()` params + `@task(confirm=)`,
  resolved before the body runs. *Terminal-ownership layer* —
  `@task(interactive=True)`, owns real stdio for a wizard/REPL.
- **Precedence for a parameter:** CLI value → `env` var → **param default** →
  prompt. The prompt is the last resort, and *only* for a **defaultless**
  `ask()` param. A default short-circuits it. Off a TTY with no default → loud
  error naming the flag (never a silent default, never a hang).
- **Guarded public primitives.** `prompt()`/`confirm()`/`select()` are public —
  the *same* functions the framework uses for `ask()` resolution — but calling
  them **inside a non-interactive task body raises a loud taught error** naming
  both exits (`@task(interactive=True)`, or declare the value with `ask()`).
- **ask-serial / run-parallel.** Questions resolve at node-launch under one
  process-wide terminal lock (already exists: `context._prompt_lock`,
  `context.py:373`); execution stays parallel. Ask A → dispatch A → ask B while
  A runs.
- **Dynamic select = the `suggest` completer, run live** at prompt time (reuses
  `manifest._run_completer`, `manifest.py:231`) — no separate `select()` API for
  static sets (`Literal`→menu, `Many[Literal]`→multi-select come free).
- **Globals:** `--yes` (auto-confirm every `confirm=`), `--no-input` (refuse to
  prompt; error instead).

## Build order

Dependencies run downward; each commit gated (`fm check` + coverage ≥ 92).

### 1 — Globals + `Context` fields (scaffolding, no behaviour yet)
- `split.py:64-94` `GLOBALS`: add `("--yes","-y","flag",…)` and
  `("--no-input",None,"flag",…)`; derived `_GLOBAL_KIND`/`_CANON` pick them up.
- `_app.py:992-1004` `ctx_config`: add `"assume_yes": bool(g.get("yes"))`,
  `"no_input": bool(g.get("no_input"))`.
- `context.py:55-106` `Context` dataclass: add fields `assume_yes: bool=False`,
  `no_input: bool=False`, `interactive: bool=False`, `in_task: bool=False`
  (every `ctx_config` key MUST be a `Context` field — splatted at
  `schedule.py:163`).
- Tests: globals parse (test_split), fields thread through (test_app).

### 2 — `@task(confirm=, interactive=)` stamping
- `registry.py:70-96` both overloads + real sig; `registry.py:156-172`
  `register()`: `if confirm: fn._footman_confirm = confirm`,
  `if interactive: fn._footman_interactive = True` (only-when-non-default idiom).
- Accessors mirroring `is_infinite` (`registry.py:224`): `task_confirm(fn)`,
  `is_interactive(fn)`.
- `manifest.py:275-317` `_task_node`: additive `confirm`/`interactive` keys for
  `--help`/`--list`/`--json` (like the `infinite` key, `manifest.py:309`).
- Tests: stamping + manifest keys (test_registry, test_manifest).

### 3 — The guarded primitives `prompt` / `confirm` / `select`
- Resurrect from stash: `git checkout stash@{0} -- src/footman/context.py
  tests/test_context.py` gives back `prompt`/`confirm` (route to
  `real_stderr()`, serialize `_prompt_lock`, `status.notify()` clear, off-tty
  degrade). **Then split**: a private `_prompt_core(...)` (unguarded) that the
  framework calls, and public `prompt()/confirm()/select()` = **guard +**
  `_prompt_core`.
- The guard: `ctx = current(); if ctx.in_task and not ctx.interactive: raise
  RuntimeError(<taught>)`. Also honour `ctx.no_input` (→ default or error) and,
  in `confirm`, `ctx.assume_yes` (→ True).
- `select(message, options, *, multiple=False, default=…)`: numbered menu on
  `real_stderr()`, parse indices/`all`/`none`, coerce via `coerce.py`. Options
  are strings or `(label, value)`.
- Export in `__init__.py` (TYPE_CHECKING + `__all__` + `__getattr__` context
  group — `prompt`/`confirm` already listed; add `select`).
- Tests: from stash + guard (raises in a plain task, works in interactive),
  `--no-input`, `select` parsing.

### 4 — The scheduler seam (where it all comes together)
- `schedule.py:355-359` (parallel ready→submit) and `schedule.py:265`
  (sequential inline): before dispatch, under `_prompt_lock` —
  (a) **confirm gate** if `task_confirm(fn)` and not `ctx.assume_yes`: ask;
  deny → skip node + subtree, taught "not confirmed", non-zero. Fires **before**
  the node's pre-deps (mise's footgun — gate before you build).
  (b) **ask() resolution** for the node's params (Phase 5).
  Then `pool.submit`. Nodes with no questions skip the lock → parallel intact.
- `interactive=True`: force `ctx.sink=None` (no capture — `schedule.py:164`
  keyed on `is_interactive`), **guard the flush** `if ctx.sink is not None` at
  `schedule.py:335`, hold `_prompt_lock` for the node's whole life (exclusive
  stdio), and **hard-error under `--json` or parallel width>1** (tox's rule).
  Set `ctx.interactive`/`ctx.in_task` around the body in `run_node`.
- Tests: interleave (A dispatches while B is asked), confirm-deny skips subtree,
  `--yes` bypass, interactive task refuses `--json`, capture-off.

### 5 — `ask()` marker + resolution + coercion reuse
- `params.py` (beside `env`/`check`): `ask(*, secret=False, prompt=None,
  confirm=False)` — read via `coerce.peel` into `Peeled` (extend it, or a
  sibling marker scan).
- `executor.py:167-174` absent-param branch, **after** the env fallback,
  **before** `continue`: if the param is `ask()` **and has no default** and
  we're on a tty → `_prompt_core`, then **`coerce.coerce_token(raw,
  peeled.element)`** (strict — re-ask on `ValueError`) + `_run_checks`
  (`executor.py:65`); `Literal`/`Enum` → menu via `coerce.all_choices`; secret →
  getpass. No tty / `--no-input` → default or loud error naming the flag.
  Resolution is invoked from the Phase-4 locked launch, not mid-body.
- **Timing excludes think-time.** An `ask()`-param task keeps progress, but the
  recorded duration must exclude prompt/confirm resolution (the human-wait):
  bracket the resolution phases (they already hold the terminal lock) and
  subtract their summed wall time from the recorded run total — so history
  reflects work, not how long someone took to answer, and it generalises to a
  chain with several prompts. (`interactive=True` records nothing regardless.)
- Tests: flag wins over prompt; env wins over prompt; default short-circuits;
  bad value re-asks; `Literal` menu; off-tty errors (typecheck + runtime);
  recorded duration excludes a simulated answer delay.

### 6 — Dynamic select via the live completer
- When an `ask()` param carries a `suggest` completer, run it **live**:
  `manifest._run_completer(peeled.completer, {})` (honours `strict`) → menu.
  `Many[...]` + `suggest` → multi-select. Reuses the exact build-time invoker.
- Tests: completer runs at prompt time (not cache); multi-select returns typed
  values; sees a dep's effects (resolved after pre-deps).

### 7 — Security hardening
- A redacting `Secret` wrapper (repr/`--json`/log → `***`); secret `ask()` params
  excluded from the completion manifest and redacted in `--json`.
- Strip C0/`0x1b` from any echoed untrusted string (menu labels, defaults, error
  text) — the ANSI-injection class.
- Ctrl-C / Ctrl-D at a prompt → clean abort (already partly in the prototype);
  trailing newline on hidden prompts.
- Tests: secret never in `--json`/manifest; escape bytes neutralised; abort.

### 8 — Docs + CHANGELOG
- `orchestration.md`: rewrite "Asking the person running it" — the two layers,
  the precedence, the guard, ask-serial/run-parallel, off-tty behaviour.
- `api.md`/`reference.md`: `prompt`/`confirm`/`select`, `ask`, `@task(confirm=,
  interactive=)`, `--yes`/`--no-input`.
- `CHANGELOG.md` `[Unreleased]` Added.

## Open decisions (want your call before/while building)

1. **Interactive in-process task** — ✅ *try both empirically.* Build the
   exclusive-lock-in-process path first (the terminal lock exists regardless),
   and keep interactive execution structured so force-external can be swapped in
   to compare. Default decided after we've run both.
2. **`confirm` vs pre-deps ordering** — ✅ *gate top-down.* The invoked task's
   confirm is asked **first**; as the scheduler descends the dep tree, each
   pre-dep's confirm is asked next (2nd, 3rd…), all **before any execution**;
   execution then runs bottom-up. Each confirm gates its own subtree.
3. **`select()` + type dispatch** — ✅ ship `select()` for in-task prompting
   (explicit `multiple=` — no parameter type at a runtime call site to read).
   The **declarative `ask()` path dispatches on type**: `peeled.multiple` (the
   `Many`/`list` detection `coerce.py` already does) picks the widget —
   `Literal[…]` → single menu, `Many[Literal[…]]` → multi-select. Type decides
   there; `select()` says it explicitly.
4. **Guard detection** — ✅ `ctx.in_task && !ctx.interactive`, with `in_task`
   set around the body in `run_node`.

## Verification

- `fm check` + `uv run pytest --cov` (≥ 92) per commit; docs build when docs
  change.
- Live on a real pty: `fm release` prompts + coerces; `fm release --version x`
  skips it; `fm -j4 a b c` where two need input interleaves (ask, dispatch, ask);
  an `@task(interactive=True)` wizard runs its own `input()` loop; the same task
  under `--json` errors loudly; `--no-input`/`--yes` behave.
- CI matrix (the free-threaded run especially — prompts touch the scheduler).
