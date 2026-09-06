# A blessed way to fail a task — design note

**Status:** agreed to build (Willem: "there should be a blessed way to deal with
this"). This note fixes the design before implementation. Small, additive,
independent of the dotted/#9 grammar work.

## The problem

footman's failure vocabulary is **implicit** — it works, but there's no single
blessed spelling for "deliberately fail this task, with a reason (and maybe a
code)." Today a task body can:

| Idiom | Means | Renders | Gap |
|-------|-------|---------|-----|
| `return 2` | fail with code 2 | `exited with code 2` | no reason |
| `sys.exit(2)` | fail with code 2 | `exited with code 2` | no reason; reads as "exit the process" |
| `sys.exit("reason")` | stop with a reason (code 1) | `task: reason` *(since F2)* | code is **always 1** — can't pair a reason with a custom code |
| `raise RuntimeError("x")` | crash | `task: RuntimeError: x` | reads as a bug, not a chosen stop |
| `raise RunFailed(...)` | a `run()` command failed | `task: RunFailed: …` | needs a hand-built `Result`; it's the *subprocess* signal, not a task one |

Two real gaps: **(1)** a reason and a custom code *together* (no idiom does
both), and **(2)** semantic clarity — `sys.exit()` in a task that runs in-process
in a thread pool reads as "exit the interpreter," which footman safely
reinterprets as "fail this task," but nothing *says* so.

`RunFailed` is **not** the answer: it is specifically "a `run()` command exited
non-zero" (it wraps a `Result`). It stays that.

This is the same place other runners landed a dedicated type: invoke's
`Exit(message, code)`, click's `ClickException(message)` / `Abort()`, typer's
`Exit(code)`.

## Proposal

A blessed failure type in the top-level namespace, carrying a **reason** and a
**code**:

```python
from footman import task, fail


@task
def release(armed: bool = False):
    if not open_pr():
        fail("No open PR for setup. Re-run `fm create repo`.")
    if reserved_branch():
        fail("refusing a reserved workflow/* branch", code=3)
```

The blessed idiom is a **function**, `footman.fail(reason="", *, code=1)`, not a
`raise` (see *Why a function* below):

- `fail("reason")` → task fails, exit **1**, reason rendered **verbatim** (no
  type prefix), and the `--json` `error` field carries it — exactly the path the
  F2 fix built for `sys.exit("reason")`.
- `fail("reason", code=3)` → exit **3** with the reason. **Closes gap 1.**
- `fail()` → exit 1, no reason (a bare deliberate failure).
- It says "fail *this task*," not "exit the process." **Closes gap 2.**

## Why a function, not `raise Fail(...)`

This is a **consumer-lint** decision. `footman.fail()` is *called* from tasks that
live in a user's repo, under *their* ruff. A
`raise SomeError("a literal message")` trips two popular strict rules **at the
call site, in the user's code**:

- **`EM101`** (flake8-errmsg) — "exception must not use a string literal, assign
  to a variable first."
- **`TRY003`** (tryceratops) — "avoid specifying long messages outside the
  exception class."

A **function call** is invisible to both (and to `N818`) — which is exactly why
`sys.exit("msg")`, `pytest.fail("msg")`/`pytest.skip("msg")`, and click's
`ctx.fail(message)` are all functions, not documented `raise`s. footman must not
bless an idiom that fights its users' linters on every failure. So:

- **`footman.fail(reason="", *, code=1)`** — the recommended, lint-clean idiom.
- **`footman.Failed`** — the exception it raises internally, exported only so a
  task can `except footman.Failed:` catch it. Raising it directly still works;
  the docs steer to `fail()`.

The stdlib idioms **stay fully supported** — `return N`, `sys.exit(...)`, and
raising an exception all keep working. `fail()` is the *recommended, documented*
way; footman never forces it on someone who reaches for `sys.exit`.

## Open decisions

### 1. Names — the function and its exception
The function is what tasks call; the exception is what it raises. Following
pytest (`pytest.fail()` raises `Failed`):
- **`footman.fail()`** *(recommended)* — lowercase verb, reads as an imperative:
  `fail("no open PR")`. Mirrors `sys.exit`/`pytest.fail`.
- **`footman.Failed`** *(recommended)* — the exception, past-tense outcome, a
  sibling of the existing **`RunFailed`** (house style: a deliberate control-flow
  outcome takes **no `Error` suffix** — see the note on naming below).

Alternatives for the verb: `abort` (implies cancellation, ambiguous on
success/failure), `exit` (collides with `sys.exit`, implies it also does clean
early success — which is just `return`). Leaning `fail` / `Failed`.

> **Naming aside (worth a line in CLAUDE.md).** footman splits its exceptions:
> genuine mistakes take an `Error` suffix (`ChainError`, `RegistrationError`,
> `ConfigError`); deliberate control-flow outcomes take **none** (`RunFailed`,
> `Unavailable`, `NotConfirmed`). `Failed` is the second kind. This is why the
> `N` (pep8-naming) ruleset — whose `N818` would demand an `Error` suffix — is
> deliberately **not** in footman's ruff `select`.

### 2. Exception base: plain `Exception` vs `BaseException`
The `fail()` function hides this from users, but the base still matters for how
the exception propagates:
- **Plain `Exception`** *(recommended)* — flows through footman's existing
  collection: `executor._call` and `parallel()` already catch `Exception`. Add a
  dedicated `except Failed` arm *before* the generic one to pull `.code` and carry
  `.reason`. A task's own broad `except Exception:` could swallow it — but it's
  the caller's code, and true of pytest's `Failed` too.
- `BaseException` (like `SystemExit`) — survives a broad `except Exception`, but
  then every catch site needs an explicit arm, and it reintroduces the
  `parallel()` "don't re-raise a `BaseException` past `except Exception`" problem
  the F2 fix just navigated.
- `subclass SystemExit` — the existing `except SystemExit` arms would catch it
  for free, but `SystemExit.code` is *either* an int *or* a message and `Failed`
  wants *both*; the F2 `has_reason` check keys on `code` being non-int, so it'd
  need a special-case anyway. No real saving.

Leaning **plain `Exception` + a dedicated arm**.

### 3. `parallel()` corner
A `Failed` raised inside a `parallel()` thunk: as an `Exception` it's collected and
re-raised by the gate (unlike a `BaseException`), so it reaches the task level and
its reason surfaces — *better* than the current `sys.exit`-in-parallel corner
(which normalises to a code-only `RunFailed`). One wrinkle: `parallel()`'s
per-thunk *return code* is currently a hardcoded `1` for a caught `Exception`; to
carry `Failed.code` through the parallel return list too, its handler would check
for `Failed`. Minor; matters only in `keep_going` mode.

## Implementation sketch

- **`context.py`** — `class Failed(Exception)` with
  `__init__(self, reason: str = "", *, code: int = 1)`; store both; `__str__`
  returns the reason. A module-level `def fail(reason="", *, code=1) -> NoReturn:
  raise Failed(reason, code=code)`. Re-export both as `footman.fail` /
  `footman.Failed`.
- **`executor._call`** — add `except Failed as exc: return exc.code, None, exc`
  *before* the generic `Exception` arm.
- **`_app` render** — add `Failed` to the verbatim-render set beside `SystemExit`
  (reason only, no type prefix). A shared predicate `_is_deliberate_stop(err)`
  covering `SystemExit` + `Failed` keeps the two in step.
- **`--json`** — already serialises `str(error)`; a `Failed` gives the reason free.
- **`parallel()`** — optionally special-case `Failed` to carry `.code` in the
  return list (decision 3).
- **Tests** — `fail("x")` surfaces `x` in stderr + `--json`; `fail("x", code=3)`
  exits 3; no type prefix; `return N`/`sys.exit` unchanged; **and a lint smoke
  test that `fail("literal")` triggers no `EM101`/`TRY003`** under a strict
  config, since that is the whole reason it's a function.
- **Docs** — a failure section: "`fail("reason")` is the blessed way; `return N`
  for a bare code; `sys.exit` still honoured." Note the lint-friendliness
  explicitly, so a migrating user knows *why* it's `fail()` not `raise`.
  **CHANGELOG** Added.

## Relationship to the rest

- Builds directly on the **F2 fix** (verbatim reason rendering + `--json` error) —
  `fail()` rides the same path, so this is genuinely additive.
- Independent of dotted addressing and #9 — no grammar interaction.
- `RunFailed` unchanged (it stays the `run()`-command-failed signal); `Failed` is
  its user-facing sibling for "I chose to fail this task."
