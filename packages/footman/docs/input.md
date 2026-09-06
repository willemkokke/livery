# Asking for input

Most values should be flags: typed, completable, and CI-safe. But some runs
genuinely need to ask the person at the keyboard: a version string, a
production confirmation, a pick from a list computed at run time. Footman has
three shapes for it, and all three are **CI-safe by construction**: off a
terminal they fail loudly or take a supplied answer, never hang. (A fourth
input is not a question at all: a document piped in on stdin, which is the
last section below.)

A bare `input()` doesn't work in a task: its prompt goes to stdout, which
footman buffers so parallel output can't interleave, so the prompt is
swallowed and the task looks hung. Reach for one of these instead.

## Ask for a value: `ask()`

Mark a typed parameter `ask()` and footman prompts for it when the command line
and its `env()` don't supply one, coercing the answer through the same pipeline
as a flag:

```python
from typing import Annotated, Literal
from footman import ask, task


@task
def release(version: Annotated[str, ask()]): ...


@task
def deploy(env: Annotated[Literal["staging", "prod"], ask()]): ...
```

`fm release --version=1.2.3` uses the flag; `fm release` asks `version:` and
runs the answer through coercion: a `Literal` is a typed choice, a bad value
re-asks. The precedence is **CLI > `env` > prompt > default**. (An `ask()`
parameter is a CLI-optional option, so it never becomes a required positional.)

A declared default becomes the **offer** rather than a reason not to ask:

<!-- example: fragment -->

```python
@task
def release(version: Annotated[str, ask()] = "patch"): ...
```

`fm release` asks `version [patch]:` and Enter takes `patch`. Naming the option
bare, as `fm release --version`, skips the question entirely, because the caller
has already said "the declared one".

The choices can be computed at run time, too: pair `ask()` with a
[`suggest()` completer](typing.md#dynamic-completion) and its values become a
numbered menu, asked up front like every other `ask()`:

```python
from footman import suggest


def stale_branches() -> list[str]:
    return ["old/spike", "old/wip"]


@task
def prune(branch: Annotated[str, ask(), suggest(stale_branches)]): ...
```

`fm prune --branch=old/wip` skips the question and validates against a fresh
call; `fm prune` shows the numbered list and takes a pick. `Many[str]` makes
it a multi-select (numbers comma-separated, `all`, `none`), and
`suggest(fn, strict=False)` demotes the values from law to hint: they show
beside a free-text prompt instead of becoming a menu. The same function also
answers <kbd>Tab</kbd> completion for the flag spelling — one list, three
surfaces. This is the up-front twin of `select()` (below): no
`interactive=True` needed, because the question is asked with the run's
other questions, before anything owns the terminal — reach for `select()`
only when the options aren't computable until the task is already running.

The safety is the point. Off a terminal, under `--no-input`, or in `--json`,
a parameter **with** a default quietly takes it, and one **without** *errors
naming the flag* instead of hanging. So `ask()` is safe to put on anything: a
person gets asked, an unattended run gets the default, and a value that has no
default still fails loudly rather than waiting for someone who isn't there.

![Animated: fm release prompts version, the typed answer runs through coercion, and the release runs](_generated/shots/ask-cast.svg)

## Secrets: `Secret` and `secret=True`

Two halves of the same idea: how a value is *collected*, and how it is
*shown*.

`secret=True` is the collection half: `ask(secret=True)` on a parameter, or
`prompt(secret=True)` mid-task, hides the typing (no echo) and hands back a
`Secret`.

```python
from typing import Annotated
from footman import Secret, Stdout, ask, run, task


@task
def login(token: Annotated[str, ask(secret=True)]): ...


@task
def publish(token: Secret): ...  # a flag or env() value, still redacted
```

`Secret` is the display half, and it stands alone: annotate any parameter
with it and whatever fills it (a flag, an `env()` fallback, a default)
redacts wherever footman *shows* it. Its repr is `Secret('***')`, so
tracebacks, logs and debuggers can't leak it, and structured surfaces
serialise it as `***`: the `--json` envelope, a `Stdout[…]` document, baked
manifest defaults.

A secret handed to `run()` as an argument of its own is a shown value too —
footman does the joining there, so it does the hiding:

```python
@task
def upload(token: Secret):
    run(["twine", "upload", "--password", token])  # shows `… --password ***`
```

The step line, the `--verbose` announce, the `--json` step row, a profile
span and the `RunFailed` message all read the redacted line. The record
underneath keeps the real one, so `recording()` assertions and
`result.command` see what the task actually ran; `result.shown` is the
printable form if you want it.

### What redaction does not cover, on purpose

The bytes a task deliberately writes. `Secret` is a real `str`, so every
string operation on it yields a plain one:

```python
@task
def env_export(token: Secret) -> Stdout[str]:
    return f"export TOKEN={token}"  # emits the real value; no switch needed
```

That is what makes footman usable as a filter for a task whose *job* is to
print a credential (`eval "$(fm env-export)"`), without a run-wide flag to
disarm protection everywhere else. The flip side is worth knowing: a secret
f-stringed into a log message loses its redaction the same way, because
neither footman nor Python can tell the two apart.

Where a `Secret` *would* survive into a structured surface and you mean it
to be emitted, say so:

```python
@task
def creds(token: Secret) -> Stdout[dict]:
    return {"token": token.reveal()}  # deliberate; a plain str from here on
```

`reveal()` exists so that intent is greppable: every deliberate exposure in
a codebase is one search away, which no global "don't redact" option could
give you.

## Gate a task: `@task(confirm=…)`

A yes/no question asked *before* the task and its prerequisites run:

<!-- example: revision -->
```python
@task(confirm="Deploy to production?")
def deploy(): ...
```

Deny it and the task never runs, the run exits non-zero, and anything that
depended on it skips, blamed on the denial. `--yes` auto-answers it (for CI
and scripts), and off a terminal without `--yes` the answer is no, because
footman never proceeds unasked.

A task that asks for confirmation gets it **however it is reached**: named on
the command line or mounted in as a `pre=`/`post=` prerequisite, the question
comes up front with the run's other questions: one reference, one question,
however many ways the plan reaches it. A body call is the one reach that
cannot be known up front, so it asks at the moment of the call.

![Animated: fm deploy asks Deploy to production, answered yes, then deploys](_generated/shots/confirm-cast.svg)

## Own the terminal: `@task(interactive=True)`

`prompt()`, `confirm()`, and `select()` ask mid-task, but they are **guarded**:
called inside an ordinary task they raise a taught error, because the prompt
would be swallowed by the capture buffer or race a parallel sibling. A task that
genuinely runs a wizard or a REPL declares itself interactive, and then owns the
real terminal, uncaptured, with sole stdio:

```python
from footman import prompt, select, task


@task(interactive=True)
def scaffold():
    name = prompt("project name? ")
    kind = select("what kind?", ["library", "app", "plugin"])
    ...
```

`select()` picks one — or `multiple=True` picks several — from a list computed
at run time, the case a flag can't cover. Two globals cover the rest: `--yes`
auto-answers every confirm, and `--no-input` refuses to prompt (a required
prompt errors instead).

`prompt()` speaks the parameter type language too: pass `type=` and the answer
runs through the same coercion pipeline a flag does, with a bad answer taught
and re-asked rather than raised:

<!-- example: fragment -->

```python
port = prompt("port? ", type=Annotated[int, between(1024, 65535)])
```

`int`, `Path`, an enum, a `Literal[…]`, a union, `check(fn)` — one type
vocabulary, whether the value arrives as a flag, an `ask()` question, or a
mid-task prompt. An empty answer takes `default`, and unattended runs take it
the same way. One value per prompt: collections and the markers that decide
how a *parameter* gets filled (`ask()`, `env()`, `suggest()`, …) are refused
by name — declare a parameter, or use `select()`.

Owning the terminal is a *lane*, not a lockdown: the interactive task runs
on the real stdio while the parallel pool keeps working around it,
captured — a sibling that finishes mid-prompt has its output held until
the terminal frees, and the live status line suspends for exactly the
ownership window, so nothing scribbles over a prompt.

![Animated: fm scaffold prompts for a project name, then a numbered what-kind menu picked by number](_generated/shots/interactive-cast.svg)

## Know your audience: `attended()`, `tty()`, `colored()`

Three readers answer "who is on the other end?", each a different question:

```python
from footman import attended, colored, tty


@task(interactive=True)
def setup(licence: str = "MIT"):
    if attended():
        licence = prompt("licence? ", default=licence)  # someone can answer
    print(f"licence: {licence}")  # CI, a pipe, --no-input: the quiet path
```

`attended()` is the input side: stdin is a real terminal and the run was not
declared unattended (`--no-input`, `--dry-run`). The prompts already degrade
to their defaults on their own — `attended()` is for changing the *shape* of
a run instead, like skipping an optional wizard outright. It licenses
nothing: a mid-body prompt still needs `interactive=True`. `--yes` does not
count as unattended — it auto-answers confirms but forbids nothing.

`tty()` is the output side, colour policy aside: the run's real stdout is a
live terminal and not a `--json` envelope. `NO_COLOR` and `--color` don't
change the answer, because a person with colour turned off is still
watching — gate a pager or a live display here.

`colored()` is the dressing: should this task's own output use colour? It
follows the same `never`/`always`/`auto` cascade as footman's own chrome, so
`--color=always` says yes even into a pipe. Emitting ANSI yourself, ask this
one, not `tty()`.

All three are plain calls, honest anywhere: `sys.stdout.isatty()` inside a
task lies (stdout is a capture buffer under parallelism), and these read the
real streams and the run's declared intent instead.

## Read the pipe: `stdin`

A piped stdin binds to typed parameters — `Stdin[str]` for the text, a
dataclass for a whole JSON document, `stdin(lines=True)` for one element
per line — with precedence **CLI > stdin > env > default > prompt**, so one
signature serves both spellings. The contract lives on
[Pipelines](pipelines.md). In tests, `Runner.invoke(..., stdin="payload")`
is the pipe; its absence means a terminal, so a test never reads the
harness's own stream.
