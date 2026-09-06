"""Test your tasks — an in-process CLI runner and a silent recording context.

Three altitudes, matching how tasks are actually written:

1. **Plain calls.** `@task` returns your function untouched, so a task body
   is unit-testable by just calling it: `lint(fix=True)`.
2. **Recording.** `recording()` captures the commands a block *would* run —
   silently, without executing anything — so a test can assert on them:

   ```python
   from livery.footman.testing import recording
   from tasks import lint

   def test_lint_fix_passes_the_flag():
       with recording() as steps:
           lint(fix=True)
       assert steps[0].command == "ruff check . --fix"
   ```

3. **CLI-level.** `Runner.invoke` drives argv → exit code → output →
   structured results, entirely in-process:

   ```python
   result = Runner().invoke("--dry-run release 1.2.0 --push")
   assert result.ok
   assert result.results[0].task == "release"
   ```

Everything here is stdlib-only — the zero-dependency promise holds. The
pytest fixtures in `footman.pytest_plugin` are thin shims over this module,
so non-pytest users get the same power.
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import tempfile
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.footman import _app, context
from livery.footman._executor import TaskResult
from livery.footman.app import App
from livery.footman.context import Context, Result, use_context
from livery.footman.registry import Group

__all__ = [
    "InvokeResult",
    "Result",
    "Runner",
    "TaskResult",
    "recording",
    "use_context",
]


@contextlib.contextmanager
def recording(
    *, answers: Mapping[Any, Any] | None = None, **overrides: Any
) -> Generator[list[Result]]:
    """Capture the commands a block would `run()` — silently, not executing.

    Yields the live step list; each `run()` call inside the block — hosted
    toolroom calls included — appends a `Result` instead of executing, and
    answers with a blank success. Keyword overrides go to the underlying
    `Context` (e.g. `env={...}`).

    `answers=` scripts the answers instead. Keys are command prefixes —
    `"uv tool list"`, or a tuple of tokens — matched against the recorded
    command (`Result.command`), longest match first. Values: a `str` is
    stdout with exit 0, an `int` is an exit code, a `Result` sets code and
    both streams, an exception instance is *raised* by the call (a missing
    binary), and a list answers consecutive matches in order and refuses by
    name when it runs dry. Unmatched calls keep the blank success. A
    non-zero answer takes the real failing lane — returned under
    `nofail=True`, raised as `RunFailed` otherwise — and a matched call is
    answered even when it opted out of the record with `recorded=False`.
    A recorded step also keeps the `env` and `cwd` it would have run with.

    toolroom's `answers()` is the same table applied one layer down, at the
    bridge's own seam; nested inside a recording, it wins and the record
    sees nothing.
    """
    # Build the kwargs dict so `overrides` can win over the dry_run/quiet
    # defaults — passing them as positional defaults made `recording(quiet=False)`
    # raise "got multiple values for keyword argument" (F51).
    # One merged dict, then one splat — a caller's override may replace the
    # defaults (a direct `Context(dry_run=True, **overrides)` would raise on
    # a duplicate keyword instead).
    merged: dict[str, Any] = {"dry_run": True, "quiet": True, **overrides}
    if answers is not None:
        merged["answers"] = context._Answers(answers)
    ctx = Context(**merged)
    with use_context(ctx):
        yield ctx.steps


@dataclass
class InvokeResult:
    """Everything one `Runner.invoke` produced.

    Named apart from the run-step `Result` (`footman.Result`, what `run()`
    returns): this is the outcome of a whole CLI invocation — its exit code,
    captured streams, and per-task `TaskResult`s."""

    exit_code: int
    stdout: str
    stderr: str
    results: list[TaskResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@contextlib.contextmanager
def _isolated(cwd: Path | None) -> Generator[None]:
    """A throwaway completion cache (and optional cwd) for one invocation.

    **The brand and its locations are restored too.** A real entry point runs
    one brand and deliberately never puts the module globals back — the
    process *is* that CLI, and `run` may not even return (the uv handoff
    re-execs). A test process is the one place that isn't true: it drives
    many brands in a row, and the next one must not read the last one's
    world. `_paths` was already restored here; `_app._brand` was not, so
    whichever `Runner(App(...))` ran first in a pytest-xdist worker silently
    decided what every later test saw — three macOS jobs failed on that, and
    only three, because the workers happened to order things differently.
    """
    from livery.footman import _app, _paths

    with tempfile.TemporaryDirectory(prefix="footman-test-") as tmp:
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = tmp
        where = _paths.child_args()
        brand = _app._brand
        try:
            if cwd is not None:
                with contextlib.chdir(cwd):
                    yield
            else:
                yield
        finally:
            _app._brand = brand
            _paths.configure_child(*where)
            if old is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old


class Runner:
    """Drive a footman CLI in-process, capturing output and results.

    Pass a branded `App` to test a custom CLI (`Runner(App(prog="acme"))`) —
    error prefixes, `--version`, and hints then use that brand, exactly as
    they would for real users.
    """

    app: App

    def __init__(self, app: App | None = None) -> None:
        self.app = app if app is not None else App()

    def invoke(
        self,
        args: str | list[str],
        *,
        tasks: Path | Group | None = None,
        cwd: Path | None = None,
        stdin: str | bytes | None = None,
        answers: Mapping[Any, Any] | None = None,
    ) -> InvokeResult:
        """Run one command line and return everything it produced.

        `args` is a string (shlex-split) or an argv list. `tasks` overrides
        discovery: a `Path` routes through `--tasks-file`, a `Group` skips
        discovery entirely (an in-memory tree, no files needed). Without it,
        the normal `tasks.py` cascade from `cwd` applies. `stdin` is what a
        pipe would have delivered — a `str` is encoded UTF-8 — and its
        absence means "a terminal": the invocation never reads the test
        harness's real stream, so `stdin`-bound parameters see exactly what
        the test says and nothing else. `answers` scripts the world the
        invocation rehearses in — the same table `recording(answers=…)`
        takes — and implies `--dry-run`, so the CLI runs end to end against
        scripted commands. Never raises on task failure — the code is in
        the `Result`; `KeyboardInterrupt` passes through.
        """
        argv = shlex.split(args) if isinstance(args, str) else list(args)
        if answers is not None and not any(a in ("--dry-run", "-n") for a in argv):
            argv = ["--dry-run", *argv]
        out, err = io.StringIO(), io.StringIO()
        collected: list[TaskResult] = []
        payload = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        previous = context._inject_stdin(payload)
        table = context._inject_answers(
            context._Answers(answers) if answers is not None else None
        )
        try:
            return self._invoke(argv, tasks, cwd, out, err, collected)
        finally:
            context._inject_answers(table)
            context._restore_stdin_payload(previous)

    def _invoke(
        self,
        argv: list[str],
        tasks: Path | Group | None,
        cwd: Path | None,
        out: io.StringIO,
        err: io.StringIO,
        collected: list[TaskResult],
    ) -> InvokeResult:
        with (
            _isolated(cwd),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            # `App.run` is bypassed below, so point the locations at this
            # brand's world here — the same call a real entry point makes
            # before anything reads a path.
            self.app.brand.install()
            if isinstance(tasks, Group):
                # One shared surface with the real CLI (help/version/list/tree/
                # json all honoured) — no drifting Group-mode re-implementation.
                code = _app.run_group(
                    tasks, argv, brand=self.app.brand, collect=collected
                )
            else:
                if tasks is not None:
                    argv = [f"--tasks-file={tasks}", *argv]
                # `_run`, not `run`: bypass the CLI's KeyboardInterrupt->130
                # wrapper so Ctrl-C reaches pytest (the invoke docstring's
                # contract). The wrapper stays for real CLI entry.
                # `handoff=False`: the uv re-exec must never fire from an
                # embedded invocation — it would execvp the HOST process
                # (pytest; under pytest-xdist the worker whose stdio is the
                # test-protocol channel, which the exec kills outright).
                code = _app._run(
                    argv, brand=self.app.brand, collect=collected, handoff=False
                )
        return InvokeResult(code, out.getvalue(), err.getvalue(), collected)
