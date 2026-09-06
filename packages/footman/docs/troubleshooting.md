# Troubleshooting

Footman treats error messages as product surface: every one names the
culprit, states the expectation, and proposes the fix. This page is the
catalog. Each example below is real output, not paraphrase. If you ever
hit a raw Python traceback instead of one of these, that's a footman bug;
please report it.

## Reading an error

```console
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
```

The shape is always `prog: task: what — hint`. The prefix is the brand
(`fm`, or your own CLI's name), so in a chain of tools you know who's
talking.

## Parse errors: exit 64, nothing has run

The splitter validates the whole command line against the manifest before
executing anything, so a typo never half-runs a chain.

| You'll see | It means | The fix |
| ---------- | -------- | ------- |
| `no task named 'linnt' (know: deploy, lint, up)` | no task by that name here | the `know:` list is your menu; `fm --list` for help text |
| `lint: unknown option --fx — did you mean '--fix'?` | a near-miss on one of lint's options | take the hint; with no close match the message explains placement instead (task options come right after their task; globals go before the first task) |
| `deploy: missing required positional(s): <target>` | a positional with no default wasn't given | required params have no default — pass a value |
| `deploy: <target> must be one of dev\|staging\|prod (got 'produ') — did you mean 'prod'?` | eager choice validation (`Literal`, `Enum`, strict `suggest`) | take the hint |
| `deploy: <target> must be one of dev\|staging\|prod — 'check' looks like the next task; did you forget <target>?` | a chain word landed where a required value belongs | give the value, then the next task |
| `test: --jobs expects an integer (got 'many')` | eager type validation from the annotation | typed params parse before anything runs |
| `test: --jobs must be between 1 and 32 (got '99')` | a `between(...)`/`range` bound | bounds are inclusive; the message quotes them |
| `render: <template> must be an existing file (got 'missing.toml')` | an `exists`/`isfile`/`isdir` marker | the path is checked before the task runs |
| `deploy: --env expects KEY=VALUE (got 'DEBUG')` | a `dict[K, V]` param needs pairs | `--env=DEBUG=1`, comma-split or repeated |
| `lint: --fix is a flag and takes no value` | `--fix=yes` on a `bool` param | flags are bare: `--fix`, or `--no-fix` |
| `--where expects a value, attached: --where=TASK` | a value-bearing option given bare | `--where=TASK` — a value is always `=`-attached |
| `--target takes its value attached — did you mean --target=prod?` | a value across a space | attach it: `--target=prod` |
| `unknown global option --bogus (global options go before the first task)` | not one of fm's globals | `fm --help` lists them all |
| `fm` runs some other program, or prints another tool's help | a different `fm` sits earlier on your `PATH` | `footman` is the same program under its long name; `uv run fm` always reaches the project's own copy |

One asymmetry worth knowing: constraints on **env-supplied** values
(`env("VAR")` fallbacks) are enforced at binding time rather than parse
time (the parser never sees your environment), so those surface as a
failed task result with the same wording, plus the source:
`--jobs (from $JOBS) must be between 1 and 32 (got 99)`.

## Tasks-file errors: your `tasks.py` needs attention

| You'll see | It means | The fix |
| ---------- | -------- | ------- |
| `/repo/tasks.py: the root already has a task named 'build'` | two tasks claimed one name | rename one, or `@task(name=...)` |
| `failed to import /repo/tasks.py: SyntaxError: ...` | the file doesn't parse | the named file is the culprit, cascade or not |
| `failed to import /repo/tasks.py: ImportError: ...` | an import inside the tasks file failed | footman shows the type and message, never a traceback |
| `<target>: env('DEPLOY_ENV') needs a default — an env fallback makes the parameter optional, so it needs somewhere to fall` | `env()` on a required param | give it a default |
| `<opts>: env() is not supported on dict parameters` | `env()` on a `dict[K, V]` | read the variable inside the task instead |
| `dynamic choices from projects() failed: FileNotFoundError: ... — fix the completer, or pass suggest(fn, strict=False) if this data is best-effort` | a strict completer raised | strict promises validation, so it fails loudly rather than validating nothing |
| `include('shared_tasks'): the module was already imported outside include(), so its tasks were never captured — ...` | a bare `import` beat your `include()`, and that module *does* define tasks | `include()` first, or expose an explicit `Group` |
| `include('empty_helpers'): the module registered no tasks and has no module-level Group to adopt — ...` | the module holds nothing footman can mount | mount the module that has the tasks — a package you only pass *through* (`include("devkit.tasks")` walks `devkit`) may be empty and is not an error |
| `include('shared_tasks'): no task or group at 'lnt' in the provider's tree (has: fmt, lint)` | a typo in `only=`/`exclude=` or a dotted sub-path | the message lists what the provider has |
| `plugin('mkdocs'): no 'footman.tasks' entry point matches (installed: footman.docs, footman.env_files, ...)` | the mounted plugin isn't installed | install the package that advertises it, or drop the `plugin("mkdocs")` line from your tasks file — mounts are authored there, never in config |
| `plugin 'mkdocs': failed to import (ModuleNotFoundError: ...)` | the plugin is installed but its own import failed (a missing optional dep) | install what the plugin needs, or drop it — footman names the cause, never a traceback |

A parameter whose annotation footman can't use (an unresolved name, a
value) emits a `UserWarning`, and values pass through as plain text until you
fix the annotation.

## Run-time errors: a task went wrong

| You'll see | It means |
| ---------- | -------- |
| ``test: RunFailed: `pytest -q` exited with code 1`` (plus the replayed output) | a `run()` command failed; its captured output is shown only now |
| `build: CommandNotFound: no executable 'buf' found on PATH — ...` | the command doesn't exist, so nothing ran — there is no exit code, and `nofail=` doesn't apply; install the tool, or gate the task with `@requires_tool` so it lists as unavailable instead |
| `release: ValueError: 'nope' is not MAJOR.MINOR.PATCH` | the task (or a `check(fn)` validator) raised; type and message, no traceback |
| `build: exited with code 3` | the task returned a non-zero int |
| `up: Unavailable: requires docker on PATH` | a `@requires`-gated task was asked to run; the reason is live, not cached |
| `dependency cycle: b -> a -> b (check the pre/post declarations of these tasks)` | your `pre`/`post` graph loops |
| `interrupted` (exit 130) | Ctrl-C — pending tasks were cancelled |
| `terminated` (exit 143) | something asked the run to stop — `timeout`, `docker stop`, `kill`, a cancelled CI job. Pending tasks were cancelled and the subprocess trees reaped, exactly as for Ctrl-C (`hung up`, exit 129, is `SIGHUP`) |

In a chain, a failed task's dependents are skipped; `-k/--keep-going` runs
every independent branch anyway. Output from parallel tasks never
interleaves: each task's buffer is flushed as one block to stdout, while
the `ok`/`FAIL` summary itself is stderr commentary.

## Seeing more: the debugging ladder

A failing task prints one line by design — the innermost frame that is
*yours*, footman's own frames dropped — because at a terminal the culprit
line usually is the story. When it isn't, each rung shows more:

1. **`-v`** prints the full traceback for an unexpected exception (and it
   prints anyway whenever stderr is not a terminal, so CI logs always
   carry everything).
2. **`--json`** puts the same `traceback` on the failing task's row, plus
   the captured output and exit code, machine-readable.
3. **A run that stops moving** answers to <kbd>Ctrl</kbd>+<kbd>\</kbd> (`SIGQUIT`):
   every thread's stack dumps to stderr and the run carries on — press it
   twice and compare frames to tell a deadlock from slow progress. Nobody
   is at a keyboard in CI, so `FOOTMAN_STACKS_AFTER=30` arms the same
   dump on a repeating timer. Both are covered in
   [When a run stops moving](#when-a-run-stops-moving) below.
4. **`fm --profile <chain>`** writes a Perfetto trace when the question is
   *where the time went* rather than what broke.

## Config errors

A malformed **discovered** config (a `pyproject.toml` or `footman.toml` in
the cascade) warns and is skipped, because one broken file between the repo
root and your cwd must not brick every invocation:

```console
fm: ignoring malformed config: /repo/footman.toml: Expected '=' after a key in a key/value pair (at line 1, column 5)
```

A file you named **explicitly** with `--config` is a hard error (exit 64) when
it's malformed, unreadable, or missing. You asked for that file on purpose,
so a typo like `--config=prod.tmol` is reported (`--config: prod.tmol: no such
file`), never silently ignored.

The encoding is part of being well formed. TOML must be UTF-8, so a config
saved as anything else is malformed and takes the same two paths, naming the
byte that gave it away rather than guessing at what the file meant. The one
exception is a byte-order mark: a UTF-8 mark, which some Windows editors add,
is stripped and the file reads as ordinary UTF-8.

## When a run stops moving

A hang says nothing on its own, so footman hands you the one thing that does:
every thread's stack, live.

Press `Ctrl-\` (`SIGQUIT`) and they go to stderr — one block per thread,
innermost frame first — and **the run carries on**. That is the point: press it
again a few seconds later and compare. Frames that moved are slow progress;
frames that did not are a deadlock, and the top of each block names the file
and line to look at.

Nobody is at a keyboard when CI hangs, and Windows has no `SIGQUIT`, so the
same dump is reachable on a timer:

```sh
FOOTMAN_STACKS_AFTER=30 fm check
```

That run dumps its stacks every 30 seconds and carries on each time. The timer
counts wall-clock, not stuckness, so pick a number comfortably longer than a
healthy run and the log stays quiet until something genuinely wedges.

!!! note "Python 3.11 and 3.12"

    The repeating timer rides CPython's `faulthandler` watchdog, and on
    3.11/3.12 that watchdog can be lost to an interpreter race: a dump that
    fires while threads are starting or stopping can land on a freed thread
    state (`<tstate is freed>` in the output, or a dump cut off mid-line),
    after which no further dumps arrive. Python 3.13 fixed the race. The
    key press is unaffected — `SIGQUIT` dumps from the signal, not the
    watchdog — so on an older Python treat a timer that has gone quiet as
    suspect and reach for <kbd>Ctrl</kbd>+<kbd>\</kbd> where a keyboard exists.

Both forms write to stderr, so `--json` keeps stdout a clean document.

## Timing estimates

The progress bar's estimates come from `*.times.json` files beside the
completion manifests (`~/.cache/footman/`, or wherever
`$FOOTMAN_CACHE_DIR` points). The cache also tends itself: at most once
a day, the cache collector removes pairs whose directory no longer
exists and pairs idle for 90 days. Everything in the cache rebuilds on
the next run, so collection can never lose anything that matters.
Delete files by hand to reset a stale history, or
turn the whole apparatus off: `--no-progress` for a run,
`progress = false` in `[tool.footman]` for good.

## Exit codes

The two worth remembering here: **64 always means footman refused before or
while binding** — a parse, tasks-file, config, or availability problem, and
nothing ran — and any other non-zero code is the first failing task's own.
The full table is part of the machine contract:
[JSON output § exit codes](json.md#exit-codes). `--json` consumers get the
same story per task in the envelope: `ok`, `code`, `error`.
