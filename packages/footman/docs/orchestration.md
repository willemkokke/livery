# Chaining & parallelism

How a command line becomes a plan, what runs concurrently versus one at a
time, and how you steer it. Independent tasks run in parallel by default;
`-s`, `-j`, and `-k` control the concurrency, and a few rules decide when
footman falls back to sequential. The fine print (one execution per
request, body calls, sharing, steps of your own) lives on
[The execution model](execution-model.md).

The examples on this page share a small cast of stand-in tasks:

```python
from footman import task


@task
def fmt(): ...


@task
def lint(): ...


@task
def typecheck(): ...


@task
def test(): ...


@task
def notify(): ...
```

## Chaining

`fm format lint --fix test` runs three tasks from one line: duty's muscle
memory, but with real flags. The split is driven by the manifest, so it is
deterministic; `+` is always available as an explicit boundary, and `--dry-run`
rehearses the chain, so bodies run with their bound values while footman's
own work is faked into receipts, so the split shows itself in what each
task would have run:

```console
$ fm --dry-run format lint --fix test
$ ruff format .
$ ruff check --fix .
$ pytest
```

## Parallel by default

Independent tasks run **in parallel by default**. That is the concurrency
model. Footman builds a dependency graph (a DAG, with no cycles allowed, and
a cycle is a taught error) from the chain and each task's declared dependencies,
then runs everything that isn't waiting on something else concurrently. Tasks
spend most of their life waiting on subprocesses, and a `run()` call releases
Python's interpreter lock while it waits, so threads give real wall-clock
speedups without process isolation:

```sh
fm a b c            # three 1s tasks -> ~1.0s, not 3.0s
fm -s a b c         # -s/--sequential runs them one at a time -> ~3.0s
```

In the measured [comparison](comparison.md), this default is most of the
story: the gap on a real gate is architecture, not dispatch speed.

Two flags size the concurrency, and they reach **both** engines: the scheduler
and a `parallel()` inside a task body.

- `-s/--sequential` runs one task at a time, with no concurrency anywhere.
- `-j/--jobs=N` caps the width; unset, footman uses one less than your core
  count, never below two.

Set either permanently as `sequential` or `jobs` in `[tool.footman]`. A run
stops on the first failure; `-k/--keep-going` runs every independent branch even
if one fails, and a task whose prerequisite failed is skipped.

!!! note "Output never interleaves"

    Each task's stdout is buffered and flushed as one contiguous block when it
    finishes, so concurrent tasks never scramble each other's lines. The
    block guarantee is about stdout; the run summary and the live status
    line are stderr commentary, so redirecting stdout captures task output
    alone.

## Failing a task

A task **succeeds** unless it says otherwise. Four ways to say otherwise, most to
least deliberate:

- **`fail("reason")`** — the blessed way to stop with an explanation. The reason
  prints on the failure line and lands in the [`--json`](json.md) `error` field,
  verbatim; `fail("…", code=3)` picks the exit code too. It is a *function*, not a
  `raise`, so it stays clean under a strict linter (flake8-errmsg's `EM101`,
  tryceratops' `TRY003` flag a string literal in a `raise`), the same reason
  `sys.exit()` and `pytest.fail()` are functions.
- **`return N`** — a bare non-zero exit code, no message. `return 0` (or falling
  off the end) is success.
- **`sys.exit("reason")` / `sys.exit(2)`** — the stdlib idiom, honoured: a string
  reason surfaces like `fail()`'s, an int is the code.
- **raise any exception** — a *crash*. Its type and message show
  (`RuntimeError: …`), signalling a bug rather than a chosen stop; the exit code
  is 1. Anything leaving the body counts, `BaseException` included, with two
  reserved: `KeyboardInterrupt` and `GeneratorExit` are the process being
  stopped and a frame being torn down, not the task failing, so they end the
  run rather than one task.

```python
from footman import task, fail


def open_pr() -> bool: ...  # your own lookup


@task
def release(armed: bool = False):
    if not open_pr():
        fail("no open PR for setup — run `fm create repo` first")
    ...
```

A `run()` command that exits non-zero raises `RunFailed` on your behalf (unless
`nofail=True`), so `fm` mirrors the command's own code and you rarely raise
that one yourself. To *catch* a deliberate `fail()`, `except footman.Failed:`.

## When a task fails: fail-fast & keep-going

A run is **fail-fast** by default: the first failure stops it. New tasks don't
start, *and* the sibling subprocesses still running are terminated: the whole
tree, each child *and its own children*, so a tool's workers (pytest-xdist,
`make -j`, a script's background jobs) die with it rather than orphaning. So a
doomed run dies at once instead of waiting out a long test suite. The kill is
SIGTERM, escalating to SIGKILL after a short grace if a tool ignores it. A task
cut off this way reports as **cancelled**, not failed, and the exit code is the
genuine failure's, never a kill signal. `Ctrl-C` reaps in-flight trees the same
way.

`--keep-going`/`-k` runs every independent branch regardless, so you see every
failure in one pass. `--fail-fast` forces the default back when a task declares
otherwise. Which wins is **three-state: command line > declared > built-in**.

```python
@task(keep_going=True)  # this gate wants to surface every problem at once
def check(): ...
```

- `fm check` keeps going, by its own declaration.
- `fm --fail-fast check` overrides it for this run.
- A task that declares nothing gets the built-in fail-fast.

The policy is **scoped per subtree**, not run-wide. A keep-going gate keeps its
own prerequisites going with it, while an independent task in the same run keeps
its own policy, so `fm check deploy`, with `check` keep-going and `deploy`
fail-fast, surfaces every `check` failure *and* still bails `deploy` on the first
one. A command-line `-k`/`--fail-fast` overrides every scope at once; a task's
own (or `.opts()`-set) policy always wins over one inherited from a gate above
it, so an explicit fail-fast prerequisite stays a fail-fast boundary. The kill is
scoped too: a failure reaps the fail-fast subprocess trees still in flight but
leaves a keep-going task's child running.

Three escape hatches for the kill:

- `@task(atomic=True)` opts a task's subprocesses out: they run to completion,
  so a formatter rewriting a file can't be truncated mid-write. The opt-out is
  total on purpose: a stop signal sent to footman itself (`timeout`,
  `docker stop`, a CI cancel) ends the run and leaves an atomic child to
  finish on its own — it is unregistered, so nothing footman does on the way
  out can truncate the write the flag protects. `Ctrl-C` at a terminal
  reaches it anyway, because the kernel signals the whole foreground group
  directly; a supervisor that must bound an atomic child does it with its
  own escalation.
- An `@task(interactive=True)` task owns the real terminal, so its subprocess
  stays attached to it and isn't group-isolated, so it keeps its controlling tty
  and its own `Ctrl-C`.
- An **in-process** tool call (a toolroom handle on its in-process path) has
  no subprocess to signal, so it always finishes on its own.

### Override a task's options per use: `.opts()`

`keep_going`, `atomic`, and the rest are set on the `@task` decorator, once. When
one *use* wants a different policy, `.opts()` overrides it there, without
touching the registered task:

<!-- example: revision -->
```python
from footman import Forward


@task(pre=[fmt.opts(atomic=True), lint])  # protect fmt's writes here, not everywhere
def check(fix: Forward[bool] = False): ...
```

`.opts()` returns the same task with the options overridden for that use only,
whether a `pre=`/`post=` target or a body call, and reads everywhere a bare
task does: same name, same signature, same call. It takes the policy options
`keep_going`, `atomic`, `interactive`, `progress`, `confirm`, `infinite`,
`shared`, `cwd`, `rel`, `lanes`, `serial`, and `exclusive`.

It takes **policy, not parameters**. A task's own arguments go in the call; the
options ride beside it, as in `deploy.opts(atomic=True)("prod")`, the same split
toolroom handles draw with their `.opts()`. Passing a task parameter to `.opts()` is a
taught error. A runnable group has `.opts()` too, riding its default action:
`pre=[lint.opts(keep_going=True)]` scopes keep-going to that prerequisite's
subtree (see per-subtree scoping above).

Deduplication keys on `(task, options)`. Same policy: one node, exactly as
a shared bare prerequisite runs once. Different policy: a different run, so
both appear in the graph. An empty `.opts()` is just the bare task, and
options must be hashable values.

## When something else asks the run to stop

A supervisor stops a program with a signal, and the senders are everyday:
`timeout`, `docker stop`, `kill`, a cancelled CI job, systemd, Kubernetes.
footman takes one as a stop the way it takes `Ctrl-C`: pending tasks are
cancelled, the subprocess trees still in flight are reaped (they are
group-isolated, so the signal never reached them on its own), stderr says
`terminated`, and a `--json` run still gets its one document. The exit code is
**143**, or **129** for `SIGHUP`; on Windows `SIGBREAK` — what a cancelled job
sends — means the same as `SIGTERM` and exits 143 too.

The stop is immediate: the grace belongs to whoever asked for it, so nothing
waits for work in flight. Reaping sends `SIGTERM` to each child's group and
insists with `SIGKILL` two seconds later, comfortably inside `docker stop`'s
ten seconds and Kubernetes' thirty. `SIGKILL` itself — and `taskkill /F` on
Windows — cannot be caught by any program in any language; a run ended that way
leaves no receipt, and nothing footman could do would change that.

## Interactive input

One parallelism consequence belongs here: a run that contains an
`@task(interactive=True)` task goes **fully sequential**, because that task owns the
real terminal, so it can't share it with parallel siblings, and the live status
line steps aside so its repaints can't scribble over a prompt. The three ways to
ask the person at the keyboard (`ask()` for a value, `@task(confirm=…)` for a
gate, and `interactive=True` for a mid-task wizard, all CI-safe by construction)
have their own page: [Asking for input](input.md).

## Dependencies with `pre` / `post`

Declare prerequisites and follow-ups on the task; footman schedules them (a
prerequisite mounted in twice runs once) and skips a task whose prerequisite
failed:

<!-- example: revision -->
```python
@task(pre=[fmt, lint, typecheck, test])  # all four run before check
def check(): ...


@task(post=[notify])  # notify runs after deploy succeeds
def deploy(): ...
```

`check`'s four prerequisites have no edges *between* them, so footman runs all
four at once and only starts `check` when the last finishes:

``` mermaid
graph LR
  fmt --> check
  lint --> check
  typecheck --> check
  test --> check
```

This is the **declared** graph: static, so `--dry-run` and completion show it
without running anything, and deduped by identity. A cycle in it is a taught
error naming the loop. A prerequisite is named by reference, so it runs with its
**defaults**: a task used as a prerequisite needs every parameter defaulted (a
required one errors with `missing required positional(s)`). To run a prerequisite
with specific arguments, name it in the chain: `fm build --release deploy` runs
`build --release` once, and `deploy`'s `pre=[build]` waits on that same run.

### Forward a value to what a task dispatches

Running defaulted is a *floor*, not a ceiling. Mark a parameter `forward` and
its value threads to every task this one dispatches (its `pre`/`post`
prerequisites and a [runnable group](#runnable-groups)'s members) that declares
a parameter of the same name:

<!-- example: revision -->
```python
from typing import Annotated
from footman import task
from footman.params import forward


@task(pre=[fmt, lint, test])
def check(fix: Annotated[bool, forward] = False):
    "fm check --fix reaches fmt & lint; test (no `fix`) runs defaulted."
```

`Forward[bool]` is the shorthand (`Forward[T]` ≡ `Annotated[T, forward]`, like
`Many[T]`). The rules:

- **Partial reach.** Only tasks that declare the parameter receive it; the rest
  run on their own defaults, so `check --fix` fixes what's fixable and lints the
  rest.
- **It chains.** A callee that re-declares `forward` passes the value on, so it
  reaches a group's members through its default.
- **Overrides a default, never rescues a required one.** A prerequisite stays
  runnable on its own; forwarding only changes a value that already has a
  default.
- **Different values are different work, not a conflict.** Two tasks forwarding
  different values to one shared prerequisite each get their own run of it —
  the same identity rule as every other request: resolve to the same
  arguments and you share, resolve to different arguments and you are asking
  for different work. Never a silent last-wins, and never a refusal.

Forwarding threads *values*, not graph structure, so `--dry-run` and completion
are unchanged. The explicit hand-forwarding of
[`inherited()`](cookbook.md#extend-an-inherited-task-instead-of-replacing-it)
stays for the override case: calling a task you *shadow* and changing what it
gets.

## Fan out from inside a task

`parallel()` runs **tasks and steps** concurrently, waits, and fails if any
fail. It honours the same `-s` and `-j` as the scheduler (one worker under
`-s`), so concurrency stays controlled in one place:

<!-- example: revision -->
```python
from footman import task, parallel, step


def clean(): ...


@task
def check():
    parallel(lint, typecheck, test, step(clean)())
```

Three things can go in:

| you pass | it runs as | reported as |
| --- | --- | --- |
| `lint` — a task | a full request | its own row, shares, hooks fire |
| `convert(images)` — a built step item | the step, pumped in a child | its own receipt, reviewable |
| `clean` — a zero-argument step maker | built here, then the same | same |

Nothing anonymous runs. Footman only schedules, records, and safely
cancels work it *owns*, and a bare callable is a stranger, with no name for
the report, no place in the plan and no way to stop it cleanly, so a lambda,
a `functools.partial`, or a plain function is a taught error naming the
one-word fix: `step(fn)(…)` lifts it, and the lift buys a receipt. A task
with arguments belongs in the block form below, where owned calls carry
them naturally. Counting is not affected either way: one piece of work is
one unit whichever way you wrote it.

### The block form, when you want the values

Passing arguments through built step items works for two or three; past
that, writing the calls plainly reads better, and the block form is the
only one that hands the return values back:

<!-- example: fragment -->
```python
@task
def release():
    with parallel() as p:
        build("web")
        build("api")
    web, api = p.results
    publish(web, api)
```

Inside the block a task call is **queued, not run**, so it has no value
there, and using one is a taught error rather than a silent `None`. Everything
runs when the block ends, under exactly the rules a call has anywhere else:
own result row, sharing, hooks, `-s`/`-j`. `p.results` is in the order you
wrote them; `p` itself is still the list of exit codes `parallel()` returns.

Footman only owns *its own* `__call__`, so a call to something that is not
a task runs where it stands rather than joining the fan-out. A lifted step
joins through `p(item)`, and its value lands in `results` in written
order:

<!-- example: fragment -->
```python
with parallel() as p:
    build("web")
    p(step(shutil.rmtree)(stale, ignore_errors=True))
```

Building an item runs nothing, so an item built inside the block and
never handed to it would be work that silently doesn't happen, the
classic forgotten `p(...)`. The block refuses instead: it raises before
running anything, naming the dead items, so the mistake costs one taught
error rather than a missing step nobody notices.

The one other difference from a plain call: a queued failure surfaces when
the block ends, not at the line that queued it.

Unlike `pre`/`post`, a `parallel()` fan-out is **in-body**, so footman can't see
it without running the task. That is the trade: declared prerequisites are
static and show up in `--dry-run`; an in-body fan-out is dynamic, since its
shape can depend on a `run()`'s output, but opaque to the planner, which
stops at the task body. Reach for declared prerequisites when you want the
plan to *see* the work, and `parallel()` when the fan-out has to be
computed at run time.

??? note "Passing data between tasks"

    Result data flows *within* a task, where `run()` hands back a `Result` (the
    exit code, which the value *is*, plus `.stdout`/`.stderr`) and a called
    function its return, and out of a `parallel()` fan-out through its block form
    (`p.results`), or through a shared closure the calls write to (they run
    in-process, so a captured list just works). `parallel(*calls)` itself
    returns exit *codes*, not values, and the declared graph carries no data
    between tasks: `pre`/`post` are ordering, not a pipe.

## Steps of your own

`run()` makes a step out of a command, and `step()` makes one out of your
own code: lifted functions, recorded blocks, generator checkpoints, each
earning a real receipt. The full contract lives on
[The execution model](execution-model.md#steps-you-make-yourself).

## Runnable groups

A group is a namespace: `fm lint.markdown` runs a task under `lint`, but bare
`fm lint` is an error. Give the group a **default action** with `@group.default`
and the bare form runs, while the members stay addressable:

<!-- example: fresh-session -->
```python
from footman import group, run, task
from footman.params import Forward
from toolroom import ruff, markdownlint, cspell

lint = group("lint")


@lint.task
def python(fix: bool = False):
    ruff("check", "src", fix=fix)


@lint.task
def markdown(fix: bool = False):
    markdownlint("**/*.md", fix=fix)


@lint.task
def spelling():
    cspell("lint", "**/*")  # no --fix


@lint.default
def lint_all(fix: Forward[bool] = False):
    "Lint everything; --fix reaches the members that support it."
```

- `fm lint` fans out every member task; `fm lint --fix` fixes what's fixable and
  lints the rest (the `forward` marker carries `--fix` to the members that take
  it; see [above](#forward-a-value-to-what-a-task-dispatches)).
- `fm lint.markdown` / `fm lint.markdown --fix` runs one member.
- The default's **signature is the group's whole CLI surface, positionals
  included**: `fm lint src/` hands `src/` to the default the way any task
  takes an argument. There is no ambiguity to guard against, since a nested task
  always keeps its own dotted address, so a bare word after the group is
  the default's value. When a value happens to equal (or nearly equal) a
  child's name, the run carries a one-line stderr note pointing at the
  dotted spelling (`note: ran lint's default with 'markdown'; for the
  subtask: fm lint.markdown`), and a path-shaped value (`fm lint
  ./markdown`) keeps quiet.
- The default registers as the child task named **`default`**, a fixed,
  well-known name, so the decorator you wrote is the address you type:
  `@lint.default` ↔ `fm lint.default`. Bare `fm lint` stays the idiomatic
  spelling, the way `GET /` serves `/index.html`, and like the index
  document, the default has a well-known name, not whatever the author
  called the function (which stays private; renaming it is free). The
  **name is the mechanism**: `@group.default` is sugar, and any *task*
  named `default` (`@task(name="default")`, or one arriving through
  `include()`) is its group's default through the same validations. So
  naming a task `default` *means something*, exactly like `tasks.py`
  itself does. A *group* named `default` is a load-time error (a
  group-typed default would make bare `fm lint` resolve to another bare
  group, which could point at another still, a regress with no floor).
- An **empty body** fans out the group's own tasks; a non-empty body is the
  escape hatch where you write the fan-out yourself. Empty means a body of
  only a docstring, `pass`, or `...` — the three stub spellings all read
  the same way, so the idiom you reach for first is the one that works.
- On an empty-body default, **mark a parameter `Forward` if you want it to reach
  the members.** The default has no body, so a plain parameter binds to it and
  goes nowhere: `fix: bool` accepts `--fix` and then nothing happens with it.
  `fix: Forward[bool]` threads the value to every member that declares `fix`.
  (A parameter the default *does* use in a custom body needs no `Forward`; the
  marker is only for values that must travel onward.)
- The default takes the same **policy options** as `@task`:
  `@lint.default(pre=[...], keep_going=True, confirm="…", atomic=True)` and the
  rest, with no `name` (the group already names it). `interactive=True` needs a
  real body: an empty-body default fans out in parallel, so there is no single
  body to own the terminal, and asking for one is a load-time error.

The group tab-completes (`fm lint <Tab>` offers `--fix` and the surface names)
and `fm --help lint` renders it as a command in its own right. And it composes: a
`check` gate reaches its members through the group with one forwarded flag —
`@task(pre=[format, lint, test]) def check(fix: Forward[bool])`, and
`fm check --fix` threads all the way down.

A runnable group is also **callable from a task body**, the way a task is:

```python
@task
def check(fix: bool = False):
    lint(fix=fix)  # runs lint's default — fans out, or runs its body
    if fix:
        run("./stamp-version.sh")
```

`lint(fix=fix)` runs the default's action synchronously and in order — its body
as written, or, for an empty-body default, the group's own tasks, each handed
the arguments it declares. Like every body call it forwards arguments explicitly
and runs to completion before the next statement; reach for `pre=`, a chain, or
`parallel()` when you want prerequisites or concurrency. The declarative
`pre=[lint]` form above is usually cleaner — a body call is for when you need
real control flow.

How often a call runs, what an omitted parameter binds to, and when work is
shared — the identity rules behind everything above — are the province of
[The execution model](execution-model.md), together with `step()` and tasks
made mid-run.

## Progress & the live status line

Both parallel engines — the scheduler and a `parallel()` inside a task body —
feed one live status line, so a chain and an in-body fan-out present
identically: a real progress bar once footman has learned the run's timing, a
bouncing pulse until then. A task can also report its own progress
(`track()` / `progress()`) and the bar fills from that instead of an estimate.
The whole story — the status line, the timing history, and the off switches —
is on [Progress & timing](progress.md).

## JSON for CI and agents

Pass `--json` and stdout becomes exactly one JSON document: per-task results
(with captured output, structured `run()` steps, and the task's own
`returned` data), or an error envelope when footman refuses the line. The
whole contract lives on [JSON output](json.md); the CI recipes on
[CI & automation](ci.md).
