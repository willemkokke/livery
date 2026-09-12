# Hooks & plugin options

The moments a lifecycle hook can attach to are not a plugin API bolted on: they are
[the execution model](execution-model.md)'s own request lifecycle — bind,
body, record, report — exposed under stable names. A plugin rides the same
moments every chain segment already passes through, which is why the rules
below never special-case "plugin work": there is no such thing, only work.

[Composing the task surface](composing.md) covers where tasks come from:
hiding, `include()`, `plugin()`. This page is what runs *around* them: the
run-wide and per-task hook family, hooks that live on one task's handle,
and a plugin's own global options.

## Editing the discovered tree

Sometimes a policy spans many tasks — every `deploy-*` task gets an `audit`
step first, a handful of tasks are switched off in this checkout — and editing
each `@task` by hand is the wrong tool. `@pre_tasks` registers a hook that runs
once per invocation on the **fully-merged** tree, before availability gates, the
manifest, or any task. It is footman's `pytest_collection_modifyitems`.

```python
# repo/tasks.py
from livery import footman
from livery.footman import task


@task
def audit(): ...


@footman.pre_tasks
def gate_deploys(inv):
    for t in inv.tasks:
        if t.name.startswith("deploy") and "audit" in inv.tasks:
            t.add_pre(inv.tasks["audit"])
```

The hook is handed the **`Invocation`**: what this `fm` line is doing, and the
one object every lifecycle hook sees. `inv.tasks` is a `Tasks` view of the
merged tree: iterate it for every task, or index it by **address** —
`inv.tasks["deploy-web"]`, `inv.tasks["docs.build"]`. Each task comes back as
a `TaskView`:

- **wiring** — `t.address` (the whole dotted spelling), `t.path` (its
  segments), `t.name` (the leaf alone), `t.group` (the owning group, or `None`
  at top level), `t.pre`, `t.post`, `t.disabled`;
- **policy flags** — `t.keep_going`, `t.atomic`, `t.infinite`, `t.interactive`,
  `t.timed`, `t.confirm`;
- **provenance** — `t.mounted_from` (which provider it came from),
  `t.defining_dir` (the directory it was defined in), `t.shadowed` (the task
  it overrides one level up), `t.shadow_chain`, and `t.source_file`;
- **edits** — `t.add_pre(…)`, `t.add_post(…)`, `t.disable("reason")`, and
  `t.set_opts(…)` (permanent, tree-wide policy, the discovery-time
  counterpart to a per-use `.opts()`).

`t.fn` is the underlying function if you need to reach past the view, which
deliberately keeps footman's private task attributes out of your hooks.

Provenance lets a hook decide by *where* a task came from. To gate every
task defined under an `infra/` directory, regardless of its name:

```python
@footman.pre_tasks
def gate_infra(inv):
    for t in inv.tasks:
        if (t.defining_dir or "").endswith("infra"):
            t.add_pre(inv.tasks["audit"])
```

To decide by *who* a task came from instead, read `t.mounted_from`: the
`footman.tasks` entry-point identity for a [`plugin()`](plugins.md), the module
name for an [`include()`](composing.md), and `None` for a task your own tasks
file writes — the honest answer rather than a hole, since the project owns it.
It names a provider, not a file, so it goes on answering for a task whose body
is not Python.

Provenance is exact on tasks, and stricter on groups. A group carries
`mounted_from` only when a mount landed it whole, on a name nothing else
claimed. The moment a mount composes into a group that already exists, that
group is *shared* — some of its tasks are yours, some the provider's — so it
answers `None`, while every task under it still names its own provider. Read
ownership from the tasks; read a group's answer as "this entire subtree is
theirs".

Because the hook runs **at discovery**, its edits are part of the plan, not
a runtime surprise: an added `pre` runs and shows in `fm <task> --dry-run`, and
a disabled task drops from `--list`, `--help`, and <kbd>Tab</kbd> completion,
exactly as if you had written it into the task.

In a [monorepo](monorepos.md), a **root** `tasks.py` can edit a subdirectory's
tasks, because the hook sees the whole merged tree. When several files in the
cascade each register a hook, they run in **cascade order**: root first, the
directory nearest your cwd last, each seeing the previous edits. That is the
same "local overrides global" precedence the cascade itself uses, so a
subdirectory refines what root did.

## Around every task: `@pre_task` and `@post_task`

Where `@pre_tasks` runs once over the plan, the per-task pair runs around
every **execution**: a chain segment, a prerequisite, a fan-out member and a
body call all count the same. `pre_task(inv, task)` fires after binding, so
it sees the arguments the body actually receives; `post_task(inv, task,
result)` fires after the body, whatever the outcome. Both run on the task's
worker thread, in parallel across tasks:

<!-- example: fragment -->
```python
from livery import footman


@footman.pre_task
def open_span(inv, task):
    task.state.span = tracer.start(task.name, dict(task.args))


@footman.post_task
def close_span(inv, task, result):
    task.state.span.end(ok=result.ok, took=result.duration)
```

The `task` handle carries the execution's facts and the two channels a hook may
write through:

- **`task.name`** — the address the task was reached through; **`task.args`**
  — the bound arguments, defaults included, read-only; **`task.source_hash`**
  — a digest of the task's own body (a tripwire, not an identity: `None` when
  the source can't be read, and shallow: it covers nothing the body calls).
- **`task.state`** — scratch private to *your plugin and this execution*,
  delivered back to your `post_task`. Another plugin's hooks cannot see it,
  and the next execution starts clean.
- **`task.env`** — the task's own environment: `run()` hands it to
  every subprocess, and in-body `os.environ` reads see it. Never write
  `os.environ` from a per-task hook, because that is shared with every
  parallel sibling; `inv` is frozen here for the same reason.

`result` reads everything — `ok`, `code`, `returned`, `error`, `duration`,
`output`, `steps` — and writes nothing: **observers see, never judge**. The
record was sealed when the review window closed, and every write there
(title, code, the reported value via `set_returned`) belongs to a
`pre_record` reviewer, where it is attributed in the record's audit.
`set_returned` rewrites the *report* only. Dependents and body callers
always receive the body's own return, since a redaction or a summary never
changes what a program computed with, and the untouched value stays
readable beside the reported one as `result.body_returned`. An
observer that finds a problem is not powerless: `footman.fail(reason,
code)` from a `post_task` hook fails the task with the hook's own code, the
failure named and the moment recorded. A `shared` row reports what the
execution's sealed record reported: a shared answer is the record reused, so
a review's rewrite covers the shares automatically.

Pres run in plugin order and posts unwind in reverse, so the first plugin in
speaks last. The post is the **task-finished event**: once an execution
reaches the body stage, every registered `post_task` fires when it concludes,
whether or not that plugin registered a `pre_task` and however any pre fared,
so a span opened in a pre always closes, even when another plugin's pre
killed the task. Failures are loud and named: a raising `pre_task` fails
the task like a failed prerequisite (the body never runs), and a raising
`post_task` fails an otherwise-green task, because a reporter that crashed
must not pass silently. A `--dry-run` rehearses: bodies run, so the hooks
fire exactly as they would live, and only footman's own recorded work is
faked.

**The pair is per request; only the body is shared.** A request satisfied by
an execution the run already performed still gets the whole lifecycle: its
`pre_task` fires post-bind, before the wait, and its `post_task` closes it
with the `shared` row. Pairing never depends on sharing, so a span opened
for a request always closes. `result.state == "shared"` is how a
reporter that cares tells a share from a run; one that doesn't care never
has to think about it.

### Before binding: `@pre_bind`

One moment sits earlier still. `pre_bind(inv, task)` fires before the task's
parameters are bound, so what it writes into `task.env` is what `env()`
fallbacks resolve, what coercion sees, and what `check(fn)` validators read. It is
the one moment a plugin can influence what the body will be handed:

<!-- example: fragment -->
```python
@footman.pre_bind
def credentials(inv, task):
    task.env["DEPLOY_TOKEN"] = vault.read("deploy")


@task
def deploy(token: Annotated[str, env("DEPLOY_TOKEN")] = ""): ...
```

Nothing is bound yet, so `task.args` is not readable here; read values in
`pre_task`, the post-bind moment. The same handle carries through the whole
lifecycle (`pre_bind → bind → pre_task → body → post_task`), so state set at
`pre_bind` is there at `post_task`. A body call binds like a segment, and its
binding sees the same injected environment.

One boundary fact: **a bind failure still fires the posts**,
with the refusal as the result. The attempt concluded, and a bind-time span
needs closing. Everything else follows the one rule above: the lifecycle is per
request, only the body is shared.

The window the lifecycle runs in is the task's managed window, opened before
binding: hook code and validator code answer to the same rules a body does
(an `os.environ` write lands in the task's own environment, a prompt outside
an interactive task is refused), while footman's own prompts — `ask()`
menus, `confirm=` — use the real terminal and are never caught.

### After the run: `@post_tasks`

The closing bookend to `@pre_tasks`: once per invocation, on the main
thread, after every task has concluded and *before* the summary or the
`--json` envelope prints, so a rewrite a hook makes through a result view
is what gets reported. The invocation now carries the whole story:

<!-- example: fragment -->
```python
@footman.post_tasks
def digest(inv):
    failed = [r for r in inv.results if not r.ok]
    slack.post(f"{len(failed)} failed of {len(inv.results)}, {inv.total_ms:.0f} ms")
```

`inv.results` is every row, in the order the work was created: executions,
`shared` rows, refusals, and `skipped` nodes (`inv.skipped` is that subset). This is the
moment that sees what never ran: a `post_task` reporter only meets requests
whose lifecycle opened, so the run-level view is where "what didn't happen"
becomes visible. Under `--json` anything a hook prints goes to stderr,
because the envelope owns stdout. Hooks run in cascade order; a raising hook is named
and fails the invocation, exactly as a crashing reporter should.

### Which moments may call a task

The four **per-task** moments run inside the run, so a task called from one
is a request like any other: its own row, sharing with the rest, the same
refusals a body call gets (a `serial=` task, or one that would wait on
itself, is taught rather than deadlocked):

<!-- example: fragment -->
```python
@footman.pre_task
def ensure(inv, task):
    if task.name.startswith("deploy"):
        build()  # a real request, reported and shared
```

The two **run-wide** moments sit outside the run, and calling a task from
one is a refusal that says so. `@pre_tasks` runs at discovery — including
inside the child that rebuilds the completion manifest, where a call would
run the task on a <kbd>Tab</kbd> press — and `@post_tasks` runs once the
report is already built, with nowhere to put a new row. Say the ordering
instead (`t.add_pre(...)` on the editable tree), or move the call to a
per-task moment.

Calling a task from ordinary Python outside a run — a REPL, an import of
the tasks module — is untouched: it is the plain function call it looks
like.

### One generator instead of a pair: `@wrap_task` and `@wrap_bind`

When the pre and the post are two halves of one thought — open a span, close
it; start a clock, log it — a wrapper says it in one place, with locals
instead of `task.state` and `try/finally` doing the pairing:

<!-- example: fragment -->
```python
@footman.wrap_task
def span(inv, task):
    s = tracer.start(task.name, dict(task.args))
    result = yield  # the body runs here
    s.end(ok=result.ok, took=result.duration)
```

`wrap_task` is sugar over the pair. It enters at the `pre_task` moment and
is resumed with the `ResultView`, so every rule above is its rule too: per
request (a request satisfied by an execution the run already performed is
resumed with its `shared` row), reverse unwinding, a raising half failing
the task, named. The names say it: a `pre_` hook runs *before* its
moment; a `wrap_` hook *enters at* its moment and rides to the end.

The one thing `wrap_task` cannot see is a task that failed to **bind**: its
anchor moment never fires, so there is no generator to unwind. `wrap_bind`
enters at the bind boundary, takes two yields, and closes even then:

<!-- example: fragment -->
```python
@footman.wrap_bind
def audit(inv, task):
    started = clock()
    try:
        bound = yield  # after binding: the real values
        result = yield  # after the body: the outcome
    finally:
        log(task.name, clock() - started)
```

A failed bind arrives as the failure raised at the first yield, so the
`try/finally` (or an `except`) around it observes it and still closes.
Yield-count violations are taught, naming the wrapper: `wrap_task` takes
exactly one, `wrap_bind` exactly two. Spans pair per execution, even
nested ones: a body call with a different binding runs its execution
inline, inside the caller's, and each span closes with its own record, the
inner first, the outer still.

One more contract, stated for the future: a pre hook's **return value is
reserved**: the moment where a pre supplies the task's result and the body
is skipped belongs to a later cache. Today a returned value gets a note and
is ignored; state belongs on `task.state`. (A wrapper never touches the
channel: its `yield` is the moment itself.)

## Hooks that live on the task

Plugin hooks are deliberately **global**: they see every task, which is
right for concerns with no task knowledge in them, like a tracing exporter,
a timing collector, or a CI annotator. A rule about *one* task belongs on that
task, and every task's handle carries its own lifecycle for exactly this:

```python
from livery.footman import fail, task


@task
def build(target: str = "web"): ...


@build.pre_task  # setup that belongs to build
def warm(): ...


@build.pre_record  # build's reviewer: the draft, before sealing
def review(view):
    view.title = f"build: {view.returned or 'ok'}"


@build.post_task  # watch build's sealed record; veto via fail()
def budget(result):
    if result.duration > 60.0:
        fail(f"too slow: {result.duration:.0f}s")
```

The line between the two channels is one sentence: the moment a global hook
would say "if this is task X", it belongs on X. Everything else follows
the shapes above: attachment is permanent (the task changes for every
requester, wherever the attaching module was imported from), and each
attacher returns the hook unchanged, so the decorators stack and the
functions stay plain callables. The task's own hooks need no arguments a
plugin hook would (the task is the handle's own): `pre_bind` and
`pre_task` take nothing, the reviewer takes the draft, the observer takes
the sealed record. `@build.wrap_task` and `@build.wrap_bind` mirror the
plugin wrappers, one task at a time.

Plugins remain the outer ring: their pres run first and their posts run
last, so a task's own hooks nest closest to the body, and the handle
channel fires whether or not any plugin is registered. Reviewers compose
inside-out wherever they were attached: stacked `@pre_record` decorators
first (nearest the `def` leading), handle attachments in the order they
were made, a per-call `.opts(pre_record=…)` always last, so the use site
keeps the final word.

## A plugin's own globals: `GlobalOption`

A plugin whose behaviour is invocation-wide wants an invocation-wide switch:
`--env-file=…` beside `--jobs=…`, not a flag repeated on every task.
Constructing a `GlobalOption` **is** registering it: a module-level singleton
in the provider, stamped with the module that defined it, riding the same
carriage as lifecycle hooks, so it reaches a run only when its owner is
mounted, and an unmounted owner's option is an unknown option, taught.

```python
from pathlib import Path
from livery.footman import GlobalOption

ENV_FILE = GlobalOption("env-file", Path, help="load this .env file first")
AUDIT = GlobalOption("audit", help="report, change nothing")  # bool → a flag
```

The value is `=`-attached like every option's, long-form only, coerced and
validated through the same pipeline as a task parameter: `Literal` choices,
`Path` file completion, bounds and dynamic `suggest()` choices all work,
because the manifest describes it with the same machinery and <kbd>Tab</kbd>
answers from that (a dynamic completer is recomputed fresh at the keystroke,
exactly as a task parameter's is). It may also be named without a value —
`--profile` beside `--profile=out.json` — which carries presence rather than a
value: `ENV_FILE.given` says whether the line named it, and an absent option
falls back through `env()`, then `default(fn)`, then its declared default,
exactly as a task parameter does. Read it anywhere in-run as
`ENV_FILE.value` (parsed once, frozen for the run; outside a run the read is
a taught error). Cross-plugin use is an ordinary import of the singleton.

A task that reads one says so — `@task(uses=[ENV_FILE])` — which puts the
dependency in the manifest for help and agents: the task's `--help` ends
with `reads --env-file (from footman.env_files)`. An undeclared read still
works, with a note naming the fix; a declared option the task finished
without reading is an advisory under `--verbose`. And an option nothing is
wired to read at all (no lifecycle hook from its owner, no declaring task)
draws a warning at discovery, because a switch nobody answers to is dead
weight. Names collide loudly: with footman's own globals naming footman,
between two plugins naming both owners.

## The built-in: `footman.env_files`

The funnel plugin: one mount, one visible behaviour, and a working example
of everything above (a lifecycle hook, a `GlobalOption`, an optional
dependency):

```python
from livery.footman.compose import plugin

plugin("footman.env_files")
```

Mounted, it loads `.env` from the invocation's directory at the run's
single-threaded moment — before availability gates, so `@requires_env` sees
it — with **env wins**: a key the real environment already carries is never
overwritten, so a checkout cannot surprise a shell. `--env-file=PATH` names
another file (path-typed, so <kbd>Tab</kbd> offers files), and a bare
`--env-file` asks for the default out loud. A missing file refuses whenever
someone asked for one, named or bare, while a missing default nobody
mentioned is simply nothing to do. Values are read by
python-dotenv — an optional dependency the plugin imports lazily and teaches
by name when absent — with interpolation off: a value is the text on its
line. Unmounted, none of this exists, not even the option, but typing
`--env-file` anyway is answered by name rather than as a spelling mistake:

```console
$ fm --env-file=.env build
fm: --env-file comes from footman.env_files, which this project has not
mounted — add plugin("footman.env_files") to tasks.py
```

footman finds that answer by loading the plugins it is willing to speak for
and reading what globals they declare. Nothing lists the flags anywhere, so
a new option is taught the day it ships. Two packages qualify, and never a
third:

- **footman's own.** `--env-file` and `--profile` belong to the framework,
  they are useful to anything built on it, and footman is imported by
  definition, so a [branded CLI](custom-cli.md) teaches them too, whether or
  not it ever named a distribution.
- **the brand's**, once a branded CLI names one with `dist=`. That is the
  case worth having: a distribution ships several plugins, a tasks file
  mounts some of them, and a flag from one of the others should not read as
  a typo.

Everything else keeps the plain "unknown global option". Reaching a third
party's flag would mean importing, on a typo, code the project deliberately
did not mount.

## The caching contract, stated once

Hiding, `include()`, `plugin()`, and `@pre_tasks` all resolve at
import/manifest-build time, so the *structure* completion offers reflects the
last real run. Dynamic `suggest()` choices are the opposite contract: never
served from the cache — a TAB that lands on one recomputes it fresh, in a
child, as [Completion](completion.md) and [Typing](typing.md) describe.
Availability (`@requires`) is likewise never trusted from the cache: it
re-checks live at the moment of execution.
