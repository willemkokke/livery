# Cookbook

Recipes, not reference: each one is a real shape you can paste and bend.
They assume the [getting started](getting-started.md) basics: tasks are
typed functions in `tasks.py`, `run()` executes commands, and the CLI is
derived from the signatures.

## Everyday shapes

### The gate

Every repo deserves one command that answers "is this fine?". Give the
independent checks to `parallel()` and let the machine use its cores:

```python
from livery.footman import task, parallel
from livery.toolroom.tools import basedpyright, pytest, ruff


@task
def lint(fix: bool = False):
    "Lint with ruff."
    ruff.check("src", "tests", fix=fix)


@task
def typecheck():
    "Type-check with basedpyright."
    basedpyright()


@task
def test(*pytest_args: str):
    "Run the test suite."
    pytest(*pytest_args)


@task
def check():
    "Lint, typecheck, and test, in parallel."
    # A call with arguments goes in the block form; bare tasks ride along.
    with parallel():
        lint(fix=False)
        typecheck()
        test()
```

`fm check` fans out across cores, keeps every task's output in one
uninterleaved block, and, once it has seen a few runs, shows a progress
bar that actually knows how long your gate takes. Wire it into CI as-is:
the same command, the same exit codes.

### Hand a tool its own flags

A `*args` parameter receives everything after `--`, verbatim, with no
quoting gymnastics, no flag collisions with footman's own:

<!-- example: revision -->
```python
@task
def test(*pytest_args: str):
    "Run the test suite."
    pytest(*pytest_args)
```

```console
$ fm test -- -k "grammar and not slow" -x --lf
```

Anything before `--` still belongs to footman (`fm -q test -- -x`), so
both grammars stay whole. A task can also read the raw list itself with
`footman.passthrough()`.

### One chain, each task with its own flags

Options bind to the task named just before them, and chains need no
separators:

```console
$ fm format lint --fix test
```

`--fix` is lint's, because it follows `lint`. Independent tasks in a
chain run in parallel by default; `-s` serialises the whole run (and
reaches `parallel()` calls inside task bodies too), `-k` keeps going past
failures, `-j=2` caps the width.

### Choices that teach

A `Literal` is a validated choice list, a completion menu, and a
did-you-mean in one annotation:

```python
from typing import Literal


@task
def deploy(target: Literal["dev", "staging", "prod"]):
    "Ship to an environment."
```

```console
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
```

Exit code 64, nothing executed, and the fix is in the message.

## Typed inputs & validation

### The belt-and-braces deploy

Markers stack. Each one validates eagerly, before anything runs, and
each failure is a taught error, not a traceback:

<!-- example: fresh-session -->
```python
from pathlib import Path
from typing import Annotated
from livery.footman import task, run
from livery.footman.params import between, check, env, isfile


def semver(value: str) -> None:
    import re

    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"expected MAJOR.MINOR.PATCH, got {value!r}")


@task
def deploy(
    config: Annotated[Path, isfile],
    version: Annotated[str, check(semver)],
    workers: Annotated[int, between(1, 32)] = 4,
    target: Annotated[str, env("DEPLOY_ENV")] = "staging",
):
    "Roll out."
    run(f"./rollout.sh {target} {version} --config {config} -j {workers}")
```

`config` must name an existing file; `version` goes through your own
validator (raise `ValueError` with a message written for the person at
the prompt); `workers` is bounds-checked; and `target` falls back to
`$DEPLOY_ENV` before its default, so CI sets the variable, humans say
`--target=prod`, and both flow through the same validation.

### Validate one input against another

A `check` validator that declares a *second* parameter also receives the
**siblings**, the parameters to its left at their effective values (provided,
or their default), coerced and read-only. That turns a static bound into a
dynamic, cross-field one: a new version checked against the *current* release of
the package named in an earlier argument, looked up at run time.

```python
from typing import Annotated
from livery.footman import task
from livery.footman.params import check


def current_version(name: str) -> str: ...  # your lookup (pyproject, git…)
def newer(version: str, current: str) -> bool: ...  # your comparison; none bundled


def newer_than_current(version, params):
    current = current_version(params["name"])
    if not newer(version, current):
        raise ValueError(f"{version} is not newer than {current}")


@task
def release(name: str, version: Annotated[str, check(newer_than_current)]):
    "Cut a release, but only forward."
    ...
```

`fm release core 1.4.0` binds `name` before validating `version`, so the bound
is *dynamic*, which a hard-coded `> 1.0.0` can't express. Reaching for the
current version keeps footman zero-dependency: you bring the lookup and the
comparison, footman turns your `ValueError` into a taught error. The first
parameter's check sees an empty dict; a sibling left at its default shows that
default (so your check never re-hardcodes it); a plain one-argument `check` is
unchanged. A `*args` parameter is in there too, as a tuple under its own name.

**Left only, and footman says so if you forget.** A parameter declared *after*
this one has no value yet, so asking for it is an error with the fix in it
rather than a silent `None`:

```text
'version' may only read parameters declared before it, and 'channel' comes
after — so it has no value yet. Move 'channel' above 'version' in the signature.
```

The same view, and the same rule, is what
[`default(fn)`](typing.md#a-default-computed-when-the-task-runs) reads when it
declares one parameter.

### TAB completes your git branches

`suggest()` attaches a completer, and footman runs it **fresh** when you complete
the value (in a bounded subprocess), so <kbd>Tab</kbd> offers current branches,
never a stale snapshot:

```python
from typing import Annotated
from livery.footman import task, run
from livery.footman.params import suggest
from livery.toolroom.tools import docker


def branches() -> list[str]:
    import subprocess  # inside the body, so importing tasks.py stays cheap

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return out.stdout.split()


@task
def review(branch: Annotated[str, suggest(branches)]):
    "Check out and gate a branch."
    run(f"git switch {branch}")
    run("fm check")
```

`fm review <TAB>` offers real branches. `suggest` is strict by default:
a typo'd branch is refused against a *fresh* call, so pass
`suggest(branches, strict=False)` when the values are hints, not law.

### KEY=VALUE options

A `dict` parameter speaks the `--name=KEY=VALUE` dialect, repeatable,
with taught errors for malformed pairs:

```python
@task
def image(tag: str, build_args: dict[str, str] | None = None):
    "Build the container image."
    docker.build(
        ".", tag=tag, build_arg=[f"{k}={v}" for k, v in (build_args or {}).items()]
    )
```

```console
$ fm image v3 --build-args=PYTHON=3.13 --build-args=DEBIAN=trixie
```

### Variadic in front, required option behind

A keyword-only parameter (after `*`) is an option, and without a
default, a *required* one. So a task can take an open list of inputs
positionally and still demand a named output:

```python
from pathlib import Path


@task
def bundle(*entries: str, out: Path):
    "Bundle entry points into one artifact."
    run(f"./bundle.sh {' '.join(entries)} -o {out}")
```

```console
$ fm bundle web api worker --out=dist/app.tar
$ fm bundle web
fm: bundle: missing required option(s): --out
```

## Orchestration & tools

### Dependencies that dedup

`pre` and `post` build a DAG; a dependency shared by several tasks runs
once per invocation:

<!-- example: fresh-session -->
```python
from livery.footman import run, task


@task
def proto():
    "Generate protobuf stubs."
    run("buf generate")


@task(pre=[proto])
def build():
    "Compile the service."
    run("cargo build --release")


@task(pre=[proto])
def docs():
    "Render the API docs."
    run("./render-docs.sh")


@task
def notify():
    "Announce the finished train."
    run("./notify.sh done")


@task(pre=[build, docs], post=[notify])
def release():
    "The whole train."
```

`fm build docs` runs `proto` exactly once, then both dependents in
parallel. A failed dependency skips its dependents loudly, never
silently, because a `check` that quietly dropped `lint` is how CI learns
to lie.

### A build matrix

The block form fans the same task over arguments; `keep_going` collects
every failure instead of stopping at the first:

<!-- example: fresh-session -->
```python
from livery.footman import parallel, run, task

TARGETS = ("linux-x86_64", "linux-arm64", "darwin-arm64")


@task
def build(target: str):
    "Compile one target."
    run(f"cargo zigbuild --target {target}")


@task
def matrix():
    "Compile every target."
    with parallel(keep_going=True) as p:
        for t in TARGETS:
            build(t)
    if any(p):  # the block is its list of codes
        raise SystemExit(1)
```

`-j` caps the fan-out's width from the command line; the timing history
keys on it, so `-j=2` runs learn their own duration.

### An endless dev server

Some tasks end when you say so, not when they finish. Mark them
`infinite` and footman stops pretending otherwise:

```python
@task(infinite=True)
def serve(port: int = 8000):
    "Run the dev server until Ctrl-C."
    run(f"uvicorn app:api --reload --port {port}")
```

`infinite=True` implies `progress=False` (a duration that never arrives
is not history), the status line yields to a one-time hint (`serve runs
until you stop it — Ctrl-C`) and listings carry the same note:
`serve  Run the dev server until Ctrl-C.  (runs until Ctrl-C)`. Ctrl-C
itself cancels cleanly: the run reports `interrupted` and exits 130, no
traceback. (This recipe used `progress=False` the day it was written;
`infinite` exists because Willem read the recipe and wanted the display
to say how the story ends. Cookbooks feed the kitchen too.)

### Tools you never declared

Every executable on PATH is already a tool. Attribute access chains
subcommands; keyword arguments translate mechanically (`detach=True` →
`--detach`, lists repeat, trailing `_` escapes Python keywords):

```python
from livery.toolroom.tools import docker, mkdocs, terraform


@task
def up(detach: bool = True):
    "Start the stack."
    docker.compose.up(detach=detach)


@task
def plan(out: str = "tf.plan"):
    "Terraform plan, saved."
    terraform.plan(out=out, input_=False)


@task
def site():
    "Build the docs."
    mkdocs.build(strict=True)  # in-process: no interpreter spawn
```

Two extras worth knowing: any tool's `installed_version()` for the
rare version-dependent branch, and the `off` sentinel
(`strict=off` → `--no-strict`) for negating a flag a tool turns on
by default. And on macOS, in-process is sometimes the only *correct*
option, because SIP strips `DYLD_*` from subprocesses, so a tool needing
Homebrew's native libraries only works inside the process.

## Working directory & environment

### A task rooted somewhere else

The cwd is policy, not an accident: root a task anywhere on the ladder, and
suffix one call without ceremony.

```python
from livery.footman import task, run


@task(cwd="root", rel="services/api")
def deploy():
    "Deploy the api service, wherever fm was invoked from."
    run("docker compose up -d")


@task
def bundle():
    "Build the web bundle from a subdirectory of this task's own dir."
    run("npm run build", rel="web")  # <task cwd>/web, this call only
```

### The task that anchors one branch on the root

A task whose *contract* is invocation-relative can still reach the repo
root for one branch of its work. `project_root()` is what `cwd="root"`
anchors on — the highest tasks file's directory in the cascade — readable
from any body, whatever the task's own cwd policy says:

```python
from livery.footman import project_root, task, run


@task
def audit(path: str = ".", affected: bool = False):
    "Audit a path as given, or with --affected the whole tree."
    target = project_root() if affected else path
    run(f"pytest {target}", shell=False)
```

The distinction doing the work: the task's *cwd* is policy, resolved once
per task, while where the cascade roots is *data*, carried alongside
whichever policy won. No `git rev-parse` required, and no VCS assumed:
the root is the cascade's, which is why it exists even in a repo git has
never seen. Outside a project it answers `None`.

### The legacy task that owns the process

A helper that genuinely chdirs, or drives a library that only reads the
process state, declares it and gets the real globals, safely:

```python
from livery import footman
from livery.footman import task, run


@task(serial=True)
def legacy_build():
    "One serial task at a time; the parallel pool keeps running around it."
    with footman.chdir(rel="vendor"):  # a real chdir, legal here
        run("make")
```

The serial lane costs less than it sounds for tools that parallelise
themselves (pytest with xdist, build systems): they saturate the machine on
their own, so serialising the *task* forgoes almost nothing.

### The benchmark that needs a quiet machine

`exclusive=True` is the real full drain, with nothing else in flight:

```python
@task(exclusive=True)
def bench():
    "Timings mean nothing with a build running next door."
    run("pytest tests/bench --benchmark-only")
```

### Environment for a child, not the world

Writes to `os.environ` in a parallel task scope to the task: its children
see them, siblings never do. The deliberate spellings say it out loud:

```python
@task
def publish(ctx):
    ctx.env["TWINE_NON_INTERACTIVE"] = "1"  # every child of this task
    run("twine upload dist/*", shell=True, env={"TWINE_VERBOSE": "1"})  # one call
```

### The release flow that asks once

`ask()` parameters front-load: every question is asked before anything
runs, so you answer and walk away, and a value on the command line, in the
environment, or in a default means no question at all.

```python
from typing import Annotated
from livery.footman import ask, task, run


@task(confirm="Publish to PyPI?")
def release(version: Annotated[str, ask()]):
    "fm release → asks version up front, confirms, then runs unattended."
    run(f"uv version {version}", shell=False)
    run("uv build")
    run("uv publish")
```

## Monorepos

### Monorepo: root gate, leaf overrides

`tasks.py` files cascade from the repo root down to wherever you stand;
nearer definitions win, and every task runs from the directory that defined
it:

```text
repo/
  tasks.py            # check, format, release: the shared surface
  svc/api/tasks.py    # serve, plus its own `check` override
  tools/legacy/footman.toml   # `uv = false`: run in the parent's env
```

From `svc/api`, `fm check` is the override; `fm -C=../.. check` is the
root's. A deep directory can adjust behaviour with a two-line
`footman.toml`, and the [configuration ladder](configuration.md) reaches
everywhere the cascade does.

### A tasks file that carries its own dependencies

A tasks file can declare what it needs, inline, with a
[PEP 723](https://peps.python.org/pep-0723/) header. Then it needs no
project at all: drop it in any directory and run it:

<!-- example: fragment -->
```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["livery-footman", "httpx"]
# ///
from livery.footman import task


@task
def health(url: str = "https://example.com"):
    "Check a deployment is up."
    import httpx

    print(httpx.get(url).status_code)
```

`fm health` builds that script environment once (uv does the work) and runs
inside it; every later run is a warm cache hit. Name any file with
`-f=deploy.py` and the same rule applies to that file's header. Because
uv reads the block natively, everything it understands works: a
`requires-python`, a `[tool.uv.sources]` pointing a dependency at a git
ref or a local path.

The full rules, in order. **A project that pins footman wins outright** —
inside one, the script block is ignored (worth seeing under `-v`, never a
warning), because the lockfile already declared what `fm` means there. The
handoff only fires where the cascade found exactly one file, and only when
that file's block declares dependencies. **The opt-outs**: `FOOTMAN_NO_UV=1`
disables both uv handoffs for a shell, and `uv = false` under
`[tool.footman]` does the same per project; with either set, or with no uv
on the PATH, the file simply runs as-is in the current environment.

The project handoff also covers the environment going stale. With the
project's venv active but out of date — a dependency locked after the last
sync — an import fails exactly where a missing package would; rather than
report the mystery, the run hands itself to `uv run --project`, which
syncs and retries, and a failure after that is real and surfaces
unchanged. Under the opt-outs (or with no uv to hand off to) the error
instead ends with the fix: `run uv sync`.

List footman itself among the dependencies: the file imports it, so the
script environment has to contain it. That is also the one thing footman
refuses, because the environment provably could not run the file.

Two touches make it a command in its own right:

<!-- example: fragment -->
```python
#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["livery-footman", "httpx"]
# ///
from livery import footman
from livery.footman import task


@task
def health(url: str = "https://example.com"): ...


if __name__ == "__main__":
    footman.main(__file__)
```

`chmod +x deploy.py`, and `./deploy.py health --url=…` runs from any
directory, with the same tasks, the same options and the same `--help`, and no
runner installed at all.

Checked into a project, nothing changes for people working in it: a
project whose lockfile pins footman owns its runs, so the header is
ignored there (`-v` mentions it). The file is portable *and* at
home in the repo.

### Extend an inherited task instead of replacing it

Overriding by name usually means *and also*, not *instead of*.
`inherited()` is footman's `super()`: inside an overriding task it hands
you the task you shadow, as the plain function it is.

```python
# svc/api/tasks.py; the repo root also defines `check`
from livery.footman import inherited, run, task


@task
def check(fix: bool = False, contracts: bool = True):
    "The shared gate, plus this service's contracts."
    inherited()(fix=fix)  # the root's check, arguments forwarded
    if contracts:
        run("./verify-contracts.sh")
```

Here the forwarding is spelled out on purpose. `inherited()` calls a task you
*shadow*, and the two signatures are genuinely independent (this leaf added
`--contracts`, which the root has never heard of) so you pass what you mean and
can *change* it on the way through: `inherited()(fix=False)` runs the root's gate
without letting it rewrite files. And being an ordinary call, it finishes before
your next line; write `parallel(inherited(), extra_checks)` when you'd rather
it didn't.

(For the other direction, threading a value *down* to a task's `pre`/`post`
prerequisites or a runnable group's surfaces, the
[`forward` marker](orchestration.md#forward-a-value-to-what-a-task-dispatches)
does it declaratively. `inherited()` stays the explicit form for the case it's
built for: calling the specific task you shadow, and choosing what it gets.)

It chains all the way up: a mid-level `check` that calls `inherited()`
reaches the root's, and the leaf's call reaches the mid's. Two commands
answer "what am I overriding, and what does it take?":

```console
$ fm --where=check
/repo/svc/api/tasks.py:6
/repo/svc/tasks.py:4     (shadowed)
/repo/tasks.py:9         (shadowed)

$ fm --help check
...
shadows /repo/svc/tasks.py:4 — inherited() calls it
  fm check [--fix]
```

That last line is the forwarding call, spelled out. Calling
`inherited()` in a task that shadows nothing is a taught error, not a
`None` to trip over.

## Progress, data & fetching

### A bar that knows exactly where it is

Some work knows its own progress (23 of 150 migrations, bytes of a
download) and that beats any timing history. Report it and the live
bar fills from the truth:

```python
from pathlib import Path
from livery.footman import task, track, progress


def load_records() -> list: ...  # your own work, whatever shape it takes
def apply(record): ...
def build_index(path): ...


@task
def migrate():
    "Apply pending migrations."
    for record in track(load_records()):  # total from len()
        apply(record)


@task
def index(path: Path):
    "Rebuild the search index."
    for done, total in build_index(path):
        progress(done, total)  # the explicit form
```

Counted beats estimated, so a reporting task is exact on its *first*
run, where the estimator would still be gathering samples. The full
story, covering the live status line, the timing history and the off switches, is on
[Progress & timing](progress.md).

### Fetch and cache a toolchain

`fetch()` downloads into footman's own cache: the same directory
`FOOTMAN_CACHE_DIR` moves and the cache collector tends, so vendored
artifacts for deleted projects clean themselves up.

```python
from pathlib import Path
from livery.footman import fetch, parallel, step, task

TOOLCHAIN = {
    "protoc": ("https://example.com/protoc-27.tar.gz", "9f86d081884c…"),
    "buf": ("https://example.com/buf-1.34.tar.gz", "2c26b46b68ff…"),
}


@task
def vendor():
    "Fetch the pinned toolchain, in parallel."
    # step(fetch) lifts the helper into an owned item, so each download
    # gets its own receipt and its own worker.
    parallel(
        *(
            step(fetch)(url, sha256=digest, into=Path("vendor") / name)
            for name, (url, digest) in TOOLCHAIN.items()
        )
    )
```

A second run revalidates with the server (ETag / `If-None-Match`)
instead of re-downloading; a `304` costs one round trip and keeps
"cached" true. `sha256=` refuses anything that arrived wrong, which is
what makes the task reproducible. And a fetch *is a step*: `--dry-run`
prints it without touching the network, `recording()` asserts on it in
tests, `--json` carries it, and it appears in the step lines beside your
`run()` calls. Byte counts feed the progress bar for free.

The default backend is stdlib `urllib`: zero dependencies, and the only
one that can report bytes as they arrive. Behind a corporate proxy whose
TLS store Python can't see, name the system curl once and every project
follows:

```toml
# ~/.config/footman/config.toml
[fetch]
backend = "curl"
```

`httpx` and `requests` are available when named; `auto` picks the best
importable one if you'd rather. It isn't the default on purpose, because a
download that silently changes engine when an unrelated dependency
appears would change its TLS and proxy behaviour with it.

### Tasks that return data

Return a dict and `--json` carries it verbatim under `returned`: your
task's own machine surface, no printing-and-parsing:

```python
@task
def coverage() -> dict:
    "Measure test coverage."
    run("pytest --cov=app --cov-report=json -q")
    import json

    percent = json.load(open("coverage.json"))["totals"]["percent_covered"]
    return {"percent": round(percent, 2)}
```

```console
$ fm --json coverage | jq '.items[0].returned.percent'
94.2
$ fm --json coverage | jq -e '.items[0].returned.percent >= 90' > /dev/null \
    || echo "coverage regression"
```

`Path`, `Enum`, `datetime`, `UUID`, `Decimal`, dataclasses, and sets all
serialise symmetrically with what footman coerces in; an `int` return
stays what it always was, an exit code.

## Machine use, testing & branding

### The coding-agent loop

Footman treats agents as users in their own right, and the loop is the same one
you'd teach a new colleague: discover, validate, run, read the receipt.

```console
fm --json --list                 # the full catalog: tasks, params, types, docs
fm --json --dry-run deploy prod  # rehearse: bodies run, footman's work is faked
fm --json deploy prod            # one envelope: results, output, returned
```

A refusal is machine-readable too (`{"error": {"code": 64, "message":
"…did you mean 'prod'?"}}`) so an agent can read the fix out of the
message the same way a human does. Two hooks close the loop for Claude
Code (this repo runs both on the agent that builds footman itself): an
edit-time hook running `fm format lint` after every Python edit, and a
stop-gate refusing to let the session end until `fm check` passes. The
paste-ready versions live on the [AI agents](agents.md) page, next to
the `CLAUDE.md` snippet that teaches the grammar in six lines.

### Test your tasks like code

Tasks are plain functions, so plain calls already work. `recording()`
asserts *which commands would run* without running them, and the pytest
fixtures scaffold whole projects:

<!-- example: fragment -->
```python
from livery.footman import recording
from tasks import deploy


def test_deploy_passes_the_workers_flag():
    with recording() as steps:
        deploy(config="app.toml", version="1.2.3", workers=8)
    assert steps[0].command.endswith("-j 8")


def test_release_refuses_bad_versions(fm_project):
    fm = fm_project("""
        from typing import Annotated
        from livery.footman import task
        from livery.footman.params import check

        def semver(v):
            import re
            if not re.fullmatch(r"\\d+\\.\\d+\\.\\d+", v):
                raise ValueError("expected MAJOR.MINOR.PATCH")

        @task
        def release(version: Annotated[str, check(semver)]): ...
    """)
    result = fm.invoke("release not-a-version")
    assert result.exit_code == 64
    assert "MAJOR.MINOR.PATCH" in result.stderr
```

The `fm`, `fm_project`, and `fm_record` fixtures auto-load; pytest is
never a footman dependency; only pytest itself imports the plugin. The
whole story: [Testing your tasks](testing.md).

### Ship your own CLI

A branded tool is footman with your name on it: same grammar, same
completion, same docs machinery, answering as itself:

```python
# acme_cli.py
from livery.footman import App

app = App(name="Acme", prog="acme", version="1.4.0")


def main() -> None:
    raise SystemExit(app.run())
```

```toml
[project.scripts]
acme = "acme_cli:main"
```

`acme --install-completion` installs for `acme`; errors say `acme:`; and
`acme docs.page` (the plugin overlays your own `docs` group) documents
*your* task surface under *your* prog. The details: [Custom CLI](custom-cli.md).
