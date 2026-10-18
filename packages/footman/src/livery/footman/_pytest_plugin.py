"""Pytest fixtures for testing tasks — auto-loaded when footman is installed.

Registered through the `pytest11` entry point, so `pip install footman`
alongside pytest is all it takes; there is nothing to enable. Each fixture is
a thin shim over `footman.testing` — non-pytest users get the same power from
that module directly.

Every session also runs apart from the machine's runner state. The
runner's directories, the data, cache and config directories footman
reads through ``FOOTMAN_DATA_DIR``, ``FOOTMAN_CACHE_DIR`` and
``FOOTMAN_CONFIG_DIR`` (or the brand's own spelling), point at a fresh
home in this process's scratch for the whole session: before the first
test, and for every child process a test starts, since the variables
ride the environment. A test then neither reads the developer's config,
tokens and caches nor writes into them. A variable the outer environment
already set is kept, since an outer redirect is a redirect.

The process-global task registry is guarded twice. A production
module's tasks reach it only through ``plugin()`` or ``include()``,
never through a test module's import at collection: what collection
registered from outside the test files is dropped before the first
test, so a test never meets another package's tasks because a worker
happened to collect that package's tests. And a test that leaves tasks
or groups of its own in [livery.footman.root_group][] fails at its
teardown naming them; a production module a test imported lazily is
dropped the same way as at collection. The registry is restored either
way, so the next test in the same worker never inherits anything.

A package adds guards of its own under the ``footman.test_guards``
entry-point group: each member is called once per test process, after
the directories point away, and the context manager it returns is
entered around every test.

Two deliberate laziness rules: this module is only ever imported by pytest
itself (footman's runtime stays zero-dependency), and it imports nothing from
footman at module level — pytest loads entry-point plugins before coverage
tools start measuring, so an eager import here would make every downstream
project's coverage of footman-adjacent code look worse than it is.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path
    from typing import Any

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


#: The runner's directories, by the suffix of the variable each is read from.
_DIRECTORIES = ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR")

#: The session's home and the variables it pointed there, for its end.
_HOME = pytest.StashKey[tuple[str, tuple[str, ...]]]()

#: The registry's names when the session started, for collection's end.
_BASELINE = pytest.StashKey[tuple[frozenset[str], frozenset[str]]]()

#: The entry-point group a package registers a test guard under.
TEST_GUARDS = "footman.test_guards"

#: What a test guard returns: a context manager to enter around each test.
_Guard = Callable[[], AbstractContextManager[object]]

#: The guards the session's members returned, in the members' name order.
_GUARDS = pytest.StashKey[tuple[_Guard, ...]]()


def pytest_configure(config: pytest.Config) -> None:
    """Point the runner's directories away, load the test guards, join a profile."""
    _point_directories_away(config)
    _load_test_guards(config)
    _join_profile(config)


def _variables() -> tuple[str, ...]:
    """The variables to point away: stock footman's spelling and the brand's."""
    from livery.footman import directory_variable

    names = {f"FOOTMAN_{suffix}" for suffix in _DIRECTORIES}
    names.update(directory_variable(suffix) for suffix in _DIRECTORIES)
    return tuple(sorted(names))


def _point_directories_away(config: pytest.Config) -> None:
    """Point every unset runner directory at a fresh home in this process's scratch.

    The home is made before any variable moves, so it lands in the cache
    the session starts with, and footman's collector removes it once this
    process is gone, whatever ended it. A session whose directories the
    outer environment already set makes no home at all: an xdist worker
    inherits its controller's.
    """
    import os

    from livery.footman import scratch_dir

    unset = [name for name in _variables() if not os.environ.get(name)]
    if not unset:
        return
    home = scratch_dir("test-session")
    for name in unset:
        target = home / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    config.stash[_HOME] = (str(home), tuple(unset))


def pytest_unconfigure(config: pytest.Config) -> None:
    """Drop the variables this session set, and the home behind them."""
    import os
    import shutil

    home, names = config.stash.get(_HOME, ("", ()))
    for name in names:
        os.environ.pop(name, None)
    if home:
        shutil.rmtree(home, ignore_errors=True)


def _load_test_guards(config: pytest.Config) -> None:
    """Call every member of the test-guard group, and keep what each returns.

    The members are called in name order, once per test process, after
    the directories point away, with the runner's directories as
    keyword facts. A member returns None, or a callable taking no
    arguments that returns a context manager, which
    `_test_guards_stay` enters around every test.

    Raises:
        pytest.UsageError: when a member cannot load or raises, naming
            it, so the session stops before its first test.
    """
    from livery.footman import _entries, _paths

    facts = {
        "data_dir": _paths.footman_data_dir(),
        "cache_dir": _paths.footman_cache_dir(),
        "config_dir": _paths.footman_config_dir(),
    }
    points = sorted(_entries.installed_entry_points(TEST_GUARDS), key=lambda p: p.name)
    guards: list[_Guard] = []
    for point in points:
        named = f"the test guard {point.name} ({point.value})"
        try:
            member = point.load()
        except Exception as error:
            raise pytest.UsageError(
                f"{named} could not load: {type(error).__name__}: {error};"
                " fix or uninstall the distribution that registers it"
            ) from error
        try:
            guard = member(**facts)
        except Exception as error:
            raise pytest.UsageError(
                f"{named} raised: {type(error).__name__}: {error};"
                " fix or uninstall the distribution that registers it"
            ) from error
        if guard is not None:
            guards.append(guard)
    config.stash[_GUARDS] = tuple(guards)


def _join_profile(config: pytest.Config) -> None:
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


#: The variable pytest itself sets around each phase of a test, left to pytest.
_PYTEST_OWN = "PYTEST_CURRENT_TEST"


@pytest.fixture(autouse=True)
def _environment_stays() -> Iterator[None]:
    """Give the process environment back as the test found it.

    A run inside a test writes the environment as a process of its own
    would: an env file it loaded, a variable a hook set, a profiled run's
    clock anchor, one set before an exec the test replaced. A process of
    its own ends and takes those writes with it; a test's process goes on
    to the next test, so they are undone here. A variable the test added
    is removed, and one it changed or removed gets its value back.
    pytest's own variable for the running test is left to pytest.
    """
    import os

    # pytest registers a plugin's autouse fixtures in the order `dir()`
    # lists them, which is alphabetical. This name sorts before
    # `_no_inherited_drop_box`, which asks for `monkeypatch`, so this
    # fixture is set up first and torn down after `monkeypatch` undoes
    # its changes, and a value `monkeypatch` puts back is replaced by the
    # one the test started with.
    before = dict(os.environ)
    yield
    for name in [name for name in os.environ if name not in before]:
        if name != _PYTEST_OWN:
            del os.environ[name]
    for name, value in before.items():
        if name != _PYTEST_OWN and os.environ.get(name) != value:
            os.environ[name] = value


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


def _source(task: Any) -> str:
    """The file a task's function was defined in, or empty when unknown."""
    import contextlib
    import inspect

    fn: Any = task
    with contextlib.suppress(AttributeError):
        fn = object.__getattribute__(task, "_opted_base")
    fn = getattr(fn, "__wrapped__", fn)
    if not callable(fn):
        return ""
    try:
        return inspect.getsourcefile(fn) or ""
    except TypeError:
        return ""


def _from_a_test(task: Any) -> bool:
    """Whether a task was defined in a test file, which may register at import."""
    from pathlib import Path

    path = _source(task)
    if not path:
        return True
    return Path(path).name.startswith("test_") or "tests" in Path(path).parts


def _from_tests(group: Any) -> bool:
    """Whether every task under a group was defined in a test file."""
    tasks = list(group.tasks.values())
    groups = list(group.groups.values())
    return all(_from_a_test(t) for t in tasks) and all(_from_tests(g) for g in groups)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Remember the registry the session started with."""
    from livery.footman import root_group

    session.stash[_BASELINE] = (
        frozenset(root_group.tasks),
        frozenset(root_group.groups),
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Drop what collection registered from production modules.

    A test file may register tasks at import, its own subject; a
    production module imported by a test file registers its whole
    surface into the global root, which no test asked for. Those go
    before the first test. Its functions stay importable and callable;
    only the registry forgets them.
    """
    from livery.footman import root_group

    tasks0, groups0 = session.stash.get(_BASELINE, (frozenset(), frozenset()))
    root = root_group
    for name in [
        n for n, t in root.tasks.items() if n not in tasks0 and not _from_a_test(t)
    ]:
        del root.tasks[name]
    for name in [
        n for n, g in root.groups.items() if n not in groups0 and not _from_tests(g)
    ]:
        del root.groups[name]


@pytest.fixture(autouse=True)
def _global_registry_stays() -> Iterator[None]:
    """Fail a test that leaves tasks in the global registry, and restore it."""
    from livery.footman import root_group

    root = root_group
    tasks, groups = dict(root.tasks), dict(root.groups)
    yield
    # A production module imported inside the test registers its surface
    # the same way it does at collection: dropped, silently. Only what a
    # test file itself registered and left is the test's fault.
    left = sorted(
        n for n in set(root.tasks) - set(tasks) if _from_a_test(root.tasks[n])
    )
    left += sorted(
        f"{n}.*" for n in set(root.groups) - set(groups) if _from_tests(root.groups[n])
    )
    root.tasks.clear()
    root.tasks.update(tasks)
    root.groups.clear()
    root.groups.update(groups)
    if left:
        pytest.fail(
            f"the test left tasks in the global registry: {', '.join(left)};"
            " register them under livery.footman.capture() instead"
        )


@pytest.fixture(autouse=True)
def _test_guards_stay(request: pytest.FixtureRequest) -> Iterator[None]:
    """Enter every guard the test-guard members returned, around the test.

    The guards are entered in their members' name order and left in the
    reverse. A guard that raises as it is left is an error at the test's
    teardown; the guards entered before it are still left.
    """
    import contextlib

    with contextlib.ExitStack() as stack:
        for guard in request.config.stash.get(_GUARDS, ()):
            stack.enter_context(guard())
        yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Unbind from footman every name a test bound on it, once the test ends.

    `livery.footman` serves most of its names through the module's
    `__getattr__`, reading the defining module on every access, so a
    test that patches `livery.footman._context.run` is seen by every
    reader of `livery.footman`. A test that patches the package itself,
    `monkeypatch.setattr(footman, "run", fake)`, binds the name into the
    package's own namespace, and the restore at its teardown binds the
    original there for good. From then on the package answers that name
    from its namespace, and a later patch of the defining module
    reaches no reader: the next test on the worker patches a seam
    nobody reads, and its rig leaks into the shared directories. This
    wrapper runs around the whole test, after its fixtures' own
    restores, so a name bound during the test leaves the package with
    it. A submodule the test imported first stays: the import system
    bound it there, and a later attribute walk to it needs the binding.
    """
    import sys

    import livery.footman as footman

    before = set(vars(footman))
    try:
        return (yield)
    finally:
        for name in set(vars(footman)) - before:
            if f"livery.footman.{name}" not in sys.modules:
                delattr(footman, name)
