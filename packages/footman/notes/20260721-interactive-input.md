# Interactive input in footman — problem-space map

Where this came from: migrating a private project's duties onto footman
surfaced bare
`input()` in task bodies, which the capture model swallows (the prompt goes to
the buffered stdout and is never seen; the task looks hung). A `prompt()` /
`confirm()` primitive is already prototyped in the tree (see §7). This map is
the *thorough* pass Willem asked for before we commit to a shape: the
competitive landscape, where types come in, how it degrades in CI and parallel,
piping, security — and the open decisions.

---

## 0. The four decisions (read this first)

The mechanism is clear; the *shape* has four genuine forks. My leanings, for
you to confirm or overrule:

1. **`ask()` parameter vs. `@task(interactive=True)` task-type — it's BOTH,
   cleanly separated.** go-task and mise both split "ask a question" from "own
   the terminal." So do we: `ask()` params + `@task(confirm=)` are the *question*
   layer (resolved up front, coerced, CI passes flags); `@task(interactive=True)`
   is the *capture-opt-out* layer (a wizard/REPL/editor, exclusive stdio lock).
   **Lean: build the question layer first; interactive task-type second.**
2. **Confirm: decorator or parameter?** `@task(confirm="Deploy to prod?")` has
   direct prior art (just `[confirm]`, go-task `prompt:`, mise `confirm`);
   `confirm: bool = ask()` keeps it in the signature. **Lean: decorator as the
   headline (matches the field), with the bool param falling out of `ask()` for
   free.**
3. **How far on select now?** Static option sets are *free* the moment `ask()`
   exists: a `Literal[...]` param prompted becomes a typed single-select,
   `Many[Literal[...]]` a multi-select. Runtime `select(options)` (options not
   known until the task runs) is the only piece that needs new surface — and the
   scan of the consuming project found **zero** runtime multi-selects.
   **Lean: ship the free static case; defer runtime `select()` until a real
   dynamic case appears.**
4. **Bless inbound stdin piping?** tox's precedent is elegant: stdin reaches the
   command **only on a real TTY** (`USER if isatty() else OFF`) — never hangs a
   pipe/CI. **Lean: don't forbid it, gate it on a TTY (the same `isatty()` gate
   the prompts need); outbound piping already works.**

---

## 1. Competitive landscape (11 tools)

Nobody ships **typed** prompting. Two families exist: a declarative **confirm
gate** (just / go-task / mise) and a **capture-opt-out escape** (tox / doit /
go-task / mise). footman already sits in the capture-by-default camp and would
be the first to make prompting type-aware.

| tool | confirm gate | typed prompt | secret input | stdio default | CI / no-TTY |
|---|---|---|---|---|---|
| **duty** | — | — | — | capture stdout; `pty` kills stdin | n/a — never asks |
| **invoke** | — (watchers answer *subprocesses*) | — | from config | always-capture + echo toggle | n/a |
| **poe** | — | — | — | inherit — clean pipes both ways | n/a |
| **nox** | — | — | — | inherit; `silent=True` to capture; logs→stderr | `session.interactive` signal |
| **tox** | — | — | — | capture; **stdin `isatty`-gated** | stdin OFF off-TTY; `-i` escape ⊥ `--result-json`/parallel |
| **doit** | — | — | — | capture stdout (verbosity 0/1/2) | verbosity-driven, not TTY |
| **just** | `[confirm]` / `[confirm('msg')]` | — (`y`/`yes`) | — | stream; echo→stderr | EOF→not-confirmed→**error**; `--yes`/`JUST_YES` |
| **go-task** | `prompt:` (str or list) | — | — | stream; `output: group` capture; `interactive:` escape | **fails by default**; `--yes`/`-y` (`CLI_ASSUME_YES`) |
| **cargo-make** | — | — | — | stream (`[cargo-make] INFO` chatter) | n/a |
| **npm / pnpm** | — | — | — | inherit; **banner on STDOUT** (dirty) | delegated to child |
| **make** | — | — | — | inherit; **recipe echo on STDOUT** (dirty) | delegated to child |
| **mise** | `confirm="…"` (+`default`) | — | output `redactions` only | **capture + line-buffer + `[label]` prefix**; `raw`/`interactive` escape | captured: no stdin; `--yes`/`MISE_YES` |
| **footman today** | — | — | — | **capture by default** (parallel-safe + `--json`) | prompts degrade to default/raise |

**Three patterns recur:**

1. **Confirm gate = y/no + `--yes` bypass + hard-fail (not skip) in CI.** just
   → `not confirmed` error; go-task → exit 205 / "fails by default"; mise →
   `--yes`. Three independent implementations of the same shape. That *is* the
   confirm design; we shouldn't reinvent it, and hard-fail is the right default.
2. **Capture-by-default demands a blessed escape.** Every tool that captures
   (tox, doit, mise, go-task's `group`) also ships an opt-out for tasks that
   need the real terminal (`-i` / `Interactive` / `raw`+`interactive` /
   `interactive:`). footman captures by default, so it needs the same — this is
   the cost of the model that buys parallel-safe output and `--json`.
3. **Chatter belongs on stderr.** poe and nox get it right (task stdout stays
   clean); **npm, pnpm, and make all pollute stdout** with pre-command banners /
   recipe echo, breaking `x | gzip` unless silenced. **footman already gets this
   right** — "redirecting stdout captures task output alone."

**The gap:** none derives a prompt from a parameter's *type*. That's the open
ground.

---

## 2. Where the types come in — the differentiator

Typer/click proved the pattern: a parameter declares it's promptable, and if
it's not passed on the CLI you get asked — and the answer runs through the *same
type-coercion as a flag value*. footman is better positioned than either,
because it already (a) turns typed signatures into flags and (b) owns a
coercion engine (`coerce.py`) plus a marker vocabulary (`suggest`, `Many`,
`between`, `env`, `check`, `exists`).

So prompting is **one more facet of a parameter you already declared**, not a
new input system:

```python
@task
def release(
    version: str = ask(),  # flag OR prompt, coerced str
    env: Literal["staging", "prod"] = ask(),  # typed single-select, for free
    token: str = ask(secret=True),  # getpass, redacted everywhere
): ...
```

- `fm release --version 1.2.3 --env prod --token …` → pure flags, no prompt.
- `fm release` on a TTY → prompts each, **coerced and re-asked through
  `coerce.py`** (so `between()`, `Literal` choices, `exists`, unions validate
  identically to a flag; a bad answer re-asks; a bad *secret* shows a generic
  error, never the typed text).
- `fm release` in CI / `--json` / no-TTY → **loud error naming `--version`**,
  never a hang.

That one idea collapses several questions into a single mechanism:

- **confirm on a parameter** → `confirm: bool = ask()` on a `bool` is a y/N gate.
- **select / multiselect** → `Literal[...]` *is* a validated single-select;
  `Many[Literal[...]]` *is* a multi-select — free for static option sets. Only
  runtime-computed options need `select(options)`.
- **"loudly errors on CI"** → falls out for free: a prompt with no answer and no
  default hard-errors naming the flag to pass.

Coercion/validation reuse is the crux — the prompt returns a raw string and
feeds the *existing* `coerce.py` path. Not a second validator.

---

## 3. The two separable layers (the reframe)

go-task and mise independently split these; footman should too:

- **Question layer** — `ask()` params + `@task(confirm=)`. Resolved **up front**
  (bind/executor phase, *before* the parallel scheduler runs) or serialized
  behind a single TTY lock. Never prompt mid-DAG from two tasks at once. CI
  passes flags; a missing required prompt is a loud error.
- **Terminal-ownership layer** — `@task(interactive=True)`. Opts the task out of
  capture and wires real stdio through, for a genuine wizard/REPL/editor. Takes
  an **exclusive stdio lock** for its duration (mise's `interactive`, not its
  global-serializing `raw`). **Mutually exclusive with `--json` and parallel**
  (tox enforces exactly this for `-i`) — hard-error, don't try to cooperate.

These coexist for different reasons and shouldn't be conflated into one knob.

---

## 4. CI & parallel degradation

- **Hard-fail, don't guess.** No TTY + no default → error naming the flag/env to
  supply. Matches just/go-task/mise. Never hang; never silently proceed.
- **A `--yes` global** (three tools have it: `JUST_YES`, `CLI_ASSUME_YES`,
  `MISE_YES`) auto-confirms every `confirm=` gate for scripts/CI. Belongs in
  `split.py`'s `GLOBALS`.
- **`isatty()` on both ends.** Require `stdin` *and* the prompt stream (stderr /
  `/dev/tty`) to be TTYs before prompting.
- **Parallel:** resolve prompts before scheduling, or serialize behind a lock.
  An `interactive` task can't run under parallel or `--json` — hard-error.
- **`confirm` vs `pre=` ordering** (mise's footgun): the confirm gate should
  fire **before** pre-deps run — don't build/test and *then* ask "are you sure
  you want to deploy?". Decide and document this explicitly.

---

## 5. Piping data through tasks

- **Outbound already works.** footman keeps task stdout clean and chatter on
  stderr, so `fm build > out.tar` is a clean data channel today — the poe/nox
  discipline, which npm/pnpm/make violate.
- **Inbound is the open call (fork #4).** stdin is a single shared resource that
  fights both prompts and parallel-by-default. tox's answer: pass it through
  **only on a real TTY**, disabled under pipe/CI (`USER if isatty() else OFF`) —
  never a hang. Same gate the prompts use. Cleanest middle path.
- **Disambiguation note** (go-task's #1593): a runner that reads stdin as *both*
  config and task data has to disambiguate. footman doesn't read config from
  stdin, so we're clear — but worth not breaking.

---

## 6. Security surface (the checklist)

Secrets are where "delightful" has to not become "leaked." From the research:

- [ ] Secrets via `getpass` (no echo); its prompt goes to `/dev/tty`/stderr —
      never captured stdout or the `--json` envelope. Keep the value off stdout too.
- [ ] Secrets never accepted as a plain argv flag (shell history), and **never
      placed in a subprocess command line** — `/proc/<pid>/cmdline` is
      world-readable (`ps -ef`, `auditd`). Use stdin / a `600` file / the child's env.
- [ ] Wrap secrets in a **redacting type** so repr/logs/`--json` emit `***`
      (redact at the type boundary — no call site can leak it). mise's output
      `redactions` is the precedent.
- [ ] Optional `confirmation_prompt` (ask twice) for new secrets; on a bad hidden
      value re-ask with a **generic** error, never echo the typed text.
- [ ] Ctrl-C / Ctrl-D at any prompt → **clean abort** (non-zero), trailing
      newline on hidden prompts; closed stdin aborts, never spins.
- [ ] Prompt only when stdin **and** the prompt stream are TTYs; else refuse with
      a message naming the flag/env; honor `--yes`/`--no-input`.
- [ ] Optional input **timeout** for unattended runs.
- [ ] Destructive actions: default "no," **preview first**, **type-the-name** for
      irreversible/prod, `--force` escape hatch.
- [ ] **Strip ANSI / `0x1b`** from any untrusted string before echoing to the
      terminal or logs (choice labels, defaults, error text). Live CVE class —
      Codex CLI reflected `--model` → RCE-adjacent. Cheap, robust, do it.
- [ ] Secret flags excluded from the completion manifest and redacted from `--json`.

---

## 7. Current prototype (already in the tree)

`context.py` already has `prompt(message, *, default=None, secret=False)` and
`confirm(message, *, default=False)` — the **low-level primitive**: writes to
`real_stderr()` (bypasses capture, keeps `--json` clean), serializes behind
`_prompt_lock`, clears the live status line via `status.notify()`, and degrades
off a TTY (returns `default` or raises — never hangs). 5 tests pass; docs +
CHANGELOG drafted. **Uncommitted, held** pending this design.

The typed layer (`ask()`, `@task(confirm=)`, `@task(interactive=True)`) sits
**on top** of this primitive — `ask()` calls `prompt()`/`select()` under the
hood and pipes the result through `coerce.py`. So the prototype isn't wasted;
it's the floor. Open question: do `prompt()`/`confirm()` stay public as the
escape hatch, or become private under `ask()`? (Lean: keep `confirm()` public —
it's the imperative form of the gate; keep `prompt()` public as the raw line
reader.)

---

## Relevant files

- `coerce.py` — reuse as the single validate/re-ask path for prompted values.
- `params.py` — where an `ask()` / `Secret()` marker joins `env`/`check`/`exists`.
- `context.py` — the `prompt`/`confirm` primitive; routes to stderr, off capture.
- `schedule.py` — the parallel-vs-prompt hazard (resolve up front / serialize).
- `split.py` — the `--yes` / `--no-input` global in `GLOBALS`.
- `manifest.py` — redact secret flags from the manifest and `--json`.
- `registry.py` — where `@task(confirm=, interactive=)` are stamped.
