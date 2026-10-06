"""Pytest fixtures for testing tasks — auto-loaded when footman is installed.

Registered through the `pytest11` entry point, so `pip install footman`
alongside pytest is all it takes; there is nothing to enable. Each fixture is
a thin shim over `footman.testing` — non-pytest users get the same power from
that module directly.

Two deliberate laziness rules: this module is only ever imported by pytest
itself (footman's runtime stays zero-dependency), and it imports nothing from
footman at module level — pytest loads entry-point plugins before coverage
tools start measuring, so an eager import here would make every downstream
project's coverage of footman-adjacent code look worse than it is.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator
    from pathlib import Path

    from livery.footman._context import Result
    from livery.footman.testing import Runner

__all__ = ["fm", "fm_project", "fm_record"]


@pytest.fixture
def fm() -> Runner:
    """A `Runner` for the project the test process runs in."""
    from livery.footman.testing import Runner

    return Runner()


@pytest.fixture
def fm_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Runner]:
    """Factory: scaffold an isolated project from tasks-file source.

    ```python
    def test_release(fm_project):
        fm = fm_project('''
            from livery.footman._registry import task
            from livery.footman._context import run

            @task
            def release(version: str):
                run(f"git tag v{version}")
        ''')
        assert fm.invoke("--dry-run release 1.2.0").ok
    ```

    Writes a minimal `pyproject.toml` plus the tasks file into `tmp_path`,
    chdirs there for the test, and returns a `Runner`. Pass `name=` to use a
    non-default tasks filename (wired up via `[tool.footman] tasks`).
    """
    from livery.footman.testing import Runner

    def make(source: str, *, name: str = "tasks.py") -> Runner:
        config = "" if name == "tasks.py" else f"\n[tool.footman]\ntasks = '{name}'\n"
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "test"\nversion = "0"\n{config}',
            encoding="utf-8",
        )
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        return Runner()

    return make


@pytest.fixture
def fm_record() -> Iterator[list[Result]]:
    """Recorded steps for the whole test: task code runs, commands don't.

    ```python
    def test_lint_fix(fm_record):
        from tasks import lint
        lint(fix=True)
        assert fm_record[0].command == "ruff check . --fix"
    ```
    """
    from livery.footman.testing import recording

    with recording() as steps:
        yield steps


def pytest_configure(config: pytest.Config) -> None:
    """Join a profiled footman run, when there is one to join.

    A profiled `fm` exports `FM_PROFILE_DIR` to every task's children; a
    pytest that inherits it drops a Chrome-trace fragment of its own tests
    there, and the profile embeds it — every test's setup/call/teardown on
    the run's timeline, xdist workers as named tracks. Unset — every pytest
    that is not a profiled run's child — this is one dict read."""
    import os

    # The convention's own name, spelled as a foreign tool would spell it:
    # this reads one variable and imports nothing of the plugin.
    sink = os.environ.get("FM_PROFILE_DIR")
    if sink and not hasattr(config, "workerinput"):
        # `workerinput` marks an xdist worker process — there the recorder
        # stays unregistered, because the controller replays every worker's
        # report and records it once. The env var alone cannot say which
        # side this is: a pytest *spawned by a test* inherits the outer
        # suite's variables, and it is a fresh controller, not a worker.
        config.pluginmanager.register(_TraceRecorder(sink), "footman-profile-trace")


@pytest.fixture(autouse=True)
def _no_inherited_drop_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test behaves differently for running inside a profiled run.

    A CI leg runs its gate under a profile, so every test in it inherits the
    drop box's name, and code that asks whether a trace is being kept answers
    one way on a runner and the other on a desk. That is the one thing a test
    must never do. So the box leaves each test's environment, and a test that
    wants one sets its own.

    The suite's own fragment is untouched: the recorder above took the name
    when the session started, and every test's timings still reach it. What
    goes is the *inheriting* — a `Runner` inside a test would otherwise write
    a fragment of its own into the leg's box, once per test.
    """
    monkeypatch.delenv("FM_PROFILE_DIR", raising=False)


class _TraceRecorder:
    """Per-test timings as a Chrome-trace fragment, per the profile plugin's
    drop-directory convention: `ts` in epoch microseconds, our own `pid`.

    Registered on controllers only (see `pytest_configure`), so each report
    is recorded exactly once — locally on a plain run, replayed-from-worker
    under xdist, on a track named for the worker it ran on.
    """

    def __init__(self, sink: str) -> None:
        self._sink = sink
        self._events: list[dict[str, object]] = []
        self._tracks: dict[str, int] = {}

    def _tid(self, track: str) -> int:
        return self._tracks.setdefault(track, len(self._tracks) + 1)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        import os

        start = getattr(report, "start", None)
        stop = getattr(report, "stop", None)
        if not isinstance(start, float) or not isinstance(stop, float):
            return
        track = getattr(report, "worker_id", None)
        self._events.append(
            {
                "ph": "X",
                "cat": f"test.{report.when}",
                "name": report.nodeid,
                "pid": os.getpid(),
                "tid": self._tid(track or "pytest"),
                "ts": start * 1e6,
                "dur": round((stop - start) * 1e6, 1),
            }
        )

    def pytest_sessionfinish(self) -> None:
        import json
        import os
        import sys

        if not self._events:
            return
        pid = os.getpid()
        metadata: list[dict[str, object]] = [
            {"ph": "M", "name": "process_name", "pid": pid, "args": {"name": "pytest"}}
        ]
        for track, tid in self._tracks.items():
            metadata.append(
                {
                    "ph": "M",
                    "name": "thread_name",
                    "pid": pid,
                    "tid": tid,
                    "args": {"name": track},
                }
            )
        try:
            with open(
                os.path.join(self._sink, f"pytest-{pid}.json"), "w", encoding="utf-8"
            ) as sink:
                json.dump({"traceEvents": metadata + self._events}, sink)
        except OSError as exc:  # a broken drop never fails the suite it rode in
            print(f"footman profile fragment not written: {exc}", file=sys.stderr)


@pytest.fixture(autouse=True)
def _fresh_entry_point_scan() -> Iterator[None]:
    """Scan the installed entry points afresh for every test.

    footman keeps one scan per process; a test that installs a
    distribution on its path, or fakes a scan, must see its own and
    leave nothing for the next.
    """
    from livery.footman import _entries

    _entries.rescan_entry_points()
    yield
    _entries.rescan_entry_points()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Unbind from the api every name a test bound on it, once the test ends.

    `livery.footman.api` serves most of its names through the module's
    `__getattr__`, reading the defining module on every access, so a
    test that patches `livery.footman._context.run` is seen by every
    reader of the api. A test that patches the api itself,
    `monkeypatch.setattr(api, "run", fake)`, binds the name into the
    api's own namespace, and the restore at its teardown binds the
    original there for good. From then on the api answers that name
    from its namespace, and a later patch of the defining module
    reaches no reader of the api: the next test on the worker patches a
    seam nobody reads, and its rig leaks into the shared directories.
    This wrapper runs around the whole test, after its fixtures' own
    restores, so a name bound during the test leaves the api with it.
    """
    import livery.footman.api as api

    before = set(vars(api))
    try:
        return (yield)
    finally:
        for name in set(vars(api)) - before:
            delattr(api, name)
