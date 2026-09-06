# Running tools

A task runner exists, in the end, to call other command-line applications:
whatever else `check` does, it eventually shells out to ruff, to pytest, to
git. So making that one act ergonomic was worth real effort, and the result
turned out not to need a task runner at all. It is just as useful in a
script or a test, so it lives in its own package now:
**[toolroom](https://willemkokke.github.io/toolroom/)**, unchanged in what
it does, depending on nothing, least of all footman.

Use them together. footman never imports or names toolroom, and toolroom
works with footman absent; but a toolroom call made inside a task detects
the run and routes through `run()` on its own, so it earns a receipt,
obeys `--dry-run`, and shows up in `recording()` without opting in. That
is the whole seam, and it is why this page can describe `run()` and leave
the handles to
[toolroom's own docs](https://willemkokke.github.io/toolroom/). The seam
has a testing half too: where `recording()` captures bridge calls,
[`toolroom.testing.answers()`](https://willemkokke.github.io/toolroom/testing/)
*answers* them — canned output and injected failures, footman present or
not — see [Testing](testing.md).

Task bodies run tools through `run()`. With toolroom installed they can also
use its typed tool handles, which detect footman and route every call through
`run()` automatically. `run()` captures output and stays quiet on success,
**replaying it only on failure**, so a green run is calm and a red one shows
exactly what broke:

```python
from footman import task, run
from toolroom import pytest, ruff


@task
def check():
    ruff("check", "src", fix=False)  # subprocess (ruff is a binary)
    pytest("-x")  # in-process via pytest.main
    run("mkdocs build --strict")  # any command at all
```

Each toolroom handle is imported by name: `from toolroom import git` gives
you a typed `git` you call as `git.commit(…)`, and a tool nobody has heard
of imports just the same and runs as a subprocess. This page covers `run()` and
the task context; the handles — flag translation, disabling flags,
in-process execution, and why nothing is transcribed per tool — are
[toolroom's story](https://willemkokke.github.io/toolroom/usage/).

## `run()`

- Takes **one** command: a string, or a list of tokens. Arguments live inside
  the list: `run(["sh", "-c", script])`, not `run("sh", "-c", script)`, which
  is refused rather than dropping them. In-process work is a step,
  `step(fn)(…)`, never a `run()` argument.
- Raises on a non-zero exit; `nofail=True` returns the code instead.
- Answers with a `Result`, which *is* the exit code, carrying
  `.stdout`/`.stderr`, `.timed_out` (code 124 when a `timeout=` expired),
  and `.to_argv()`, the command back as raw tokens, re-quotable for
  whichever shell will actually parse them.
- Honours `--dry-run` (prints the command instead of running it).
- Takes the run's colour decision by default, and `color="always"`/`"never"`
  overrides it for one child, forcing the colour variables into that
  command's environment, or writing `NO_COLOR` and removing any inherited
  force. An explicit choice beats the ambient one, so `color="always"` holds
  under an exported `NO_COLOR`.
- Records a step for [`--json`](json.md) (command, code, duration, captured
  output); `capture=False` lets output through unbuffered and records an
  empty capture, for serve-style tasks that must not buffer.
- Runs from the task's context cwd — in a [cascade](monorepos.md) the directory
  the task was defined in — with the task's context env, a complete
  environment rather than a diff. Subprocess and in-process tools honour
  this identically.

## Fetch and cache files: `fetch()`

`fetch(url, sha256=…, into=…)` downloads into footman's own cache, the same
directory `$FOOTMAN_CACHE_DIR` moves and the cache collector tends, so vendored
artifacts for deleted projects clean themselves up:

```python
from pathlib import Path
from footman import fetch, task


@task
def vendor():
    "Fetch the pinned toolchain."
    fetch(
        "https://example.com/protoc-27.tar.gz",
        sha256="9f86d081884c…",
        into=Path("vendor/protoc"),
    )
```

Like `run()`, a fetch **is a step**: `--dry-run` prints it without touching the
network, `recording()` asserts on it in tests, [`--json`](json.md) carries it,
and its byte counts feed the [progress bar](progress.md). A second run
revalidates with the server (ETag / `If-None-Match`), so a `304` costs one
round trip and keeps "cached" true, and `sha256=` refuses anything that
arrived wrong. The backend is stdlib `urllib` by default (zero dependencies, and the
only one that can report bytes as they arrive); `curl`, `httpx`, `requests`, or
`auto` are available when named in `[fetch]` config, for a corporate proxy whose
TLS store Python can't see. The full worked example is in the
[cookbook](cookbook.md#fetch-and-cache-a-toolchain).

## No `ctx` needed

Everything a task ordinarily reaches for is a free function that finds the
running task by itself, so a body stays boilerplate-free:

```python
from footman import passthrough, task
from toolroom import pytest


@task
def test():
    pytest(*passthrough())  # fm test -- -k mytest -x
```

`run()`, `cwd()`, `project_root()`, `given()`, `passthrough()`, `prog()`,
`tty()`, `attended()`, `colored()`, `cache_dir()`, `data_dir()` — each
reads the current context for you. **Declaring `ctx` is the rare case**,
and it buys exactly one thing the free functions do not offer: the shape
of the *invocation itself*, which is not something most tasks should
branch on.

```python
from footman import Context, task, run


@task
def publish(ctx: Context):
    if ctx.dry_run:  # fm --dry-run publish
        print("would upload the built artifacts")
        return
    run("./upload dist/*")
```

`ctx.dry_run`, `ctx.verbose`, `ctx.quiet`, `ctx.jobs`, `ctx.sequential`
and `ctx.invoked_dir` are the run-mode facts with no free function, and
the rest of the object is footman's own machinery — the record being
built, the output routing, the resolved form of what your own `@task(…)`
already declared. If you are reading those, you are working on footman
rather than with it.

Declare it as a first parameter annotated `Context` (the name `ctx` is
conventional, not required) and footman injects it; it never becomes a
CLI argument.

One boundary to know: threads you spawn yourself don't inherit the task's
context. See
[Working directory & environment](working-dir.md#in-process-calls-and-the-directory).

## Machine-readable output

Under `--json`, every `run()` becomes a structured step inside the task's
entry, all task output is captured into the payload, and a task's return
value rides along under `returned`. The full contract — envelopes, refusals,
exit codes — lives in [JSON output](json.md).
