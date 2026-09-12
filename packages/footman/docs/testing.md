# Testing your tasks

Tasks are code, so they deserve tests. A task runner that makes you choose
between "run it for real" and "don't test it" hasn't finished its job.
Footman gives you three altitudes, each a thin layer over the previous one.
Everything on this page is stdlib-only footman; the pytest fixtures at the
end auto-load when footman and pytest share an environment.

## Tasks are just functions

`@task` returns your function untouched. No wrapper, no argparse object, so
the first altitude of testing is plain Python:

<!-- example: fragment -->
```python
from tasks import lint


def test_lint_accepts_the_flag():
    lint(fix=True)  # a normal call, normal semantics
```

That covers logic, but any `run()` inside the body **really executes**. For
commands, you usually want the next altitude instead.

## Assert commands, don't run them

`recording()` captures every command a block would run, silently and without
executing any of them, then hands you the steps to assert on:

<!-- example: fragment -->
```python
from livery.footman.testing import recording
from tasks import release


def test_release_tags_and_pushes():
    with recording() as steps:
        release("1.2.0", push=True)
    assert [s.command for s in steps] == [
        "git tag v1.2.0",
        "git push --tags",
    ]
```

This works for the tool handles too, since every one funnels through `run()`.
One caveat, stated out loud: steps are faked under recording just like
commands. A `step(fn)(…)` or a `fetch()` produces a receipt rather than an
effect, while the body's own inline Python still runs for real. Remember it
when a task mixes subprocesses with in-process work.

### Script the answers

A plain recording answers every call with a blank success. When the task
under test *reads* a command's output, or has an error path worth
exercising, hand the recording a table of answers:

<!-- example: fragment -->
```python
from livery.footman.testing import recording
from tasks import release


def test_release_refuses_to_push_a_dirty_tree():
    with recording(
        answers={
            "git status --porcelain": " M README.md\n",  # str: stdout, exit 0
            "git push": 1,  # int: exit code
        }
    ) as steps:
        with pytest.raises(RunFailed):
            release("1.2.0")
    assert "git push" not in [s.command for s in steps]
```

Keys are command prefixes — the recorded command either *is* the key or
starts with it followed by a space — and the longest match wins, so
`"git"` can answer everything and `"git push"` override one verb. A tuple
of tokens is the same key spelled apart. Nothing is tokenised: a command
string is matched as written, on every platform alike.

Values are the answer:

| value | means |
| --- | --- |
| `"text"` | that stdout, exit 0 |
| `1` | that exit code, empty streams |
| `Result(2, stderr="boom")` | code and both streams |
| `FileNotFoundError("uv")` | the call **raises** it — the binary is missing |
| `["v1.0", "v1.1"]` | consecutive matches answer in order; a third refuses by name |

A non-zero answer takes the real failing lane: `RunFailed` unless the call
said `nofail=True`, fail-fast sees it, a `pre_record` reviewer runs on the
scripted draft exactly as on a live one. A call the table matches is
answered **even when it opted out of the record** with `recorded=False` —
a value read is exactly what a test wants to script — while unmatched
off-record calls still execute truthfully. Unmatched steps keep the blank
success, so a table changes nothing you did not name.

Each recorded step also keeps the environment and directory the call
would have run with, for the assertions that are about *where* a command
would have run rather than *that* it would:

<!-- example: fragment -->
```python
with recording() as steps:
    build_wheel()
assert "UV_TOOL_DIR" not in steps[0].env
assert steps[0].cwd == Path("packages/hse-devkit")
```

`Runner.invoke` takes the same table and implies `--dry-run`, so a CLI
drives end to end against a scripted world:

<!-- example: fragment -->
```python
result = fm.invoke("release 1.2.0", answers={"git describe": "v1.1.0\n"})
assert result.ok
```

toolroom's [`answers()`](https://willemkokke.github.io/toolroom/testing/)
is the same table applied one layer down, at the bridge's own seam — the
door to use when the code under test holds no footman at all, or when
the assertion is about how a *handle* rendered its call. Nested inside a
`recording()`, it wins: it intercepts before footman is involved, so its
answers stand and the record sees nothing.

Under the hood this is `Context(dry_run=True, quiet=True)` installed with
`use_context()`. Both are public, so you can compose your own variants:

<!-- example: fragment -->
```python
import os

from livery.footman import Context, use_context

with use_context(Context(env={**os.environ, "CI": "1"})) as ctx:
    deploy()  # runs for real, with CI=1 in its env
assert ctx.steps[-1].code == 0
```

`Context.env` is the task's *whole* environment, not a diff. Spread
`os.environ` in unless an empty world is the point.

## Drive the CLI

`Runner.invoke` runs a full command line in-process — globals, chaining,
taught errors, exit codes — and captures everything:

```python
from livery.footman.testing import Runner

TASKS = """
from livery.footman import task, run

@task
def format():
    "Format the tree."
    run("ruff format .")

@task
def lint(fix: bool = False):
    "Lint it."
    run("ruff check ." + (" --fix" if fix else ""))

@task
def test():
    "Run the suite."
    run("pytest -q")
"""


def test_the_check_pipeline(tmp_path):
    (tmp_path / "tasks.py").write_text(TASKS)
    result = Runner().invoke("--dry-run format lint --fix test", cwd=tmp_path)
    assert result.ok
    assert [t.task for t in result.results] == ["format", "lint", "test"]
    assert "ruff check . --fix" in result.stdout
```

`cwd` is where the `tasks.py` cascade starts, so it needs a tasks file to
find — an empty `tmp_path` earns `no tasks file found` and exit 64. The
`fm_project` fixture [below](#the-pytest-fixtures) does this scaffolding for
you.

`Runner.invoke` returns an `InvokeResult` (named apart from the run-step
`Result` that `run()` returns) carrying `exit_code`, `stdout`, `stderr`, the
structured `results: list[TaskResult]` (one per executed task, dependency
order), and an `ok` shorthand. Each `TaskResult` exposes the task's return
value as `.returned`, the same value `--json` publishes, so asserting on a
task's data needs no JSON parsing at all. `.returned` is the *reported*
value, the one a `pre_record` reviewer may have rewritten with
`set_returned`. The body's own return, the value dependents and body callers
received, rides beside it as `.body_returned`, so a test can assert on
either channel, or on the fact that a reviewer separated them. Taught errors
land in `result.stderr` with exit code 64; assert on them like any other
product surface. The completion cache is isolated per invocation
automatically, so tests never touch your real one. That falls out of
manifests keying per directory, not from test-only machinery.

For a task that reads the pipe, `stdin=` *is* the pipe:
`Runner().invoke("hooks.stop", stdin='{"stop_hook_active": true}')` binds
exactly as `fm hooks.stop < fixture.json` would. Leaving it off means "a
terminal", so a test never reads the harness's own stream.

Point it at a task surface three ways:

<!-- example: fragment -->
```python
Runner().invoke("build", cwd=project_dir)  # normal cascade discovery
Runner().invoke("build", tasks=Path("ci/tasks.py"))  # one file (--tasks-file)
Runner().invoke("build", tasks=my_group)  # an in-memory Group, no files
```

## The pytest fixtures

Installing footman next to pytest auto-loads three fixtures (`pytest11`
entry point, so there is nothing to enable, and pytest is never a footman
dependency):

```python
def test_release_dry(fm_project):
    fm = fm_project("""
        from livery.footman import task, run

        @task
        def release(version: str, push: bool = False):
            "Tag and optionally push."
            run(f"git tag v{version}")
            if push:
                run("git push --tags")
    """)
    result = fm.invoke("--dry-run release 1.2.0 --push")
    assert result.ok


def test_release_records_the_tag(fm_record):
    from tasks import release

    release("1.2.0")
    assert fm_record[0].command == "git tag v1.2.0"
    assert len(fm_record) == 1  # --push not given: no push
```

- **`fm`** — a `Runner` for the project the tests run in.
- **`fm_project(source, name="tasks.py")`** — scaffold an isolated project
  in `tmp_path` from a tasks-file string and return its `Runner`.
- **`fm_record`** — a recording context for the whole test; steps append as
  task code runs.

Footman's own suite uses these fixtures and `Runner`. The harness tests the
framework that ships it, which is the strongest claim a testing story can
make about itself.

## Golden tests: the `--json` surface

`--json` is the blessed machine surface: `{"schema": 1, "items": [...]}`,
documented in full on [JSON output](json.md) and additive-only after 1.0.
Filter the volatile fields and snapshot the shape:

```python
import json


def test_check_pipeline_shape(fm):
    payload = json.loads(fm.invoke("--json check").stdout)
    tasks = [(t["task"], t["ok"]) for t in payload["items"] if "task" in t]
    commands = [s["command"] for s in payload["items"] if "command" in s]
    assert tasks == [("lint", True), ("test", True)]
    assert commands == ["ruff check .", "pytest -q"]
```

`--dry-run` output stays human-oriented. Snapshot it within a pinned version
if you like, but there is no cross-version promise there.

## Testing a branded CLI

A custom `App` tests exactly like `fm`. Hand it to the `Runner` and every
user-facing string carries your brand, including the error prefix:

```python
from livery.footman import App
from livery.footman.testing import Runner


def test_acme_teaches_with_its_own_name(tmp_path):
    acme = Runner(App(name="Acme", prog="acme", version="1.4.0"))
    result = acme.invoke("nope", cwd=tmp_path)
    assert result.stderr.startswith("acme:")
```

Each invocation puts the brand back when it finishes, along with the folders
it reads, so a suite can drive several brands in a row and a bare `Runner()`
after a branded one is still stock footman. A real entry point does the
opposite on purpose (a process *is* one CLI, and `run` may never return), so
this restoring is the harness's job and only the harness's.

## CI notes

- Cache isolation is automatic, so parallel test runs can't fight over the
  completion manifest.
- `Runner.invoke` never raises on task failure; the code is in the `InvokeResult`.
  `KeyboardInterrupt` passes through, as it should.
- Embedded invocations never hand off to uv: the re-exec that keeps the real
  CLI on a project's pinned footman is disabled inside `invoke`, so the test
  process is never replaced, even when the suite runs from an interpreter
  outside the project venv (`uv run --with …`, tox-style envs).
- Chained/parallel semantics (`-s`, `-k`) work through `invoke` exactly as on
  the real command line, so you can test the orchestration you actually run
  in CI.
