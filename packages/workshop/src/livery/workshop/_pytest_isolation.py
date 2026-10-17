"""Every pytest under a workshop venv runs apart from the machine's live runner state.

The runner's directories, the data, cache, and config directories footman
reads through ``FOOTMAN_DATA_DIR``, ``FOOTMAN_CACHE_DIR``, and
``FOOTMAN_CONFIG_DIR`` (or the brand's own spelling), point at a fresh
temporary directory for the whole session: before the first test, and
for every child process a test starts, since the variables ride the
environment. A test then neither reads the developer's config, tokens,
worktrees, and caches, nor writes into them. A variable the outer
environment already set is kept, since an outer redirect is a redirect.
The one deliberate exception is a live test reading the dev containers'
credentials through `livery.forge.testing.shared_env_path`, which names
the real file by design and skips without it.

The process-global task registry is guarded twice. A production
module's tasks reach it only through ``plugin()`` or ``include()``,
never through a test module's import at collection: what collection
registered from outside the test files is dropped before the first
test, so a test never meets another package's ``lint`` or ``check``
because a worker happened to collect that package's tests. And a test
that leaves tasks or groups of its own in footman's root group
([livery.footman.root_group][]) fails at its teardown naming
them; a production module a test imported lazily is dropped the same
way as at collection. The registry is
restored either way, so the next test in the same worker never
inherits anything.

What an extension mount writes is put back after every test as well:
the steps the listed extensions declare, the CI jobs they contribute
and join, the task tree a run keeps, and the mount's record of what it
found. Each of the three modules that hold them saves and restores its
own state ([livery.workshop._steps.snapshot][],
[livery.workshop._points.snapshot][] and
[livery.workshop._extensions.snapshot][]). These registries are the
workshop's own, so footman's registry capture does not hold them.
Without the guard, a step one test mounted would judge the changes of
every later test in the worker. Registries the test loaded itself go
back as a process starts with them, and a session that loads none of
them imports nothing for the guard.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from livery.workshop._ci_context import LEG_VARIABLE

#: The directories the runner reads from the environment.
DIRECTORIES = ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR")

_HOME = pytest.StashKey[tuple[str, tuple[str, ...]]]()
_BASELINE = pytest.StashKey[tuple[frozenset[str], frozenset[str]]]()


def variables() -> tuple[str, ...]:
    """The variables to point away: stock footman's spelling and the brand's."""
    from livery.footman import directory_variable

    names = {f"FOOTMAN_{suffix}" for suffix in DIRECTORIES}
    names.update(directory_variable(suffix) for suffix in DIRECTORIES)
    return tuple(sorted(names))


#: The global git config every test's git reads in place of the
#: developer's: one identity, so a repository a test creates commits
#: without a config of its own, and none of the desk's own settings, a
#: signer among them. A test that needs a second author, a colleague's
#: clone, configures that clone, and the clone's own config wins over
#: this one as it would over a person's.
GIT_CONFIG = "[user]\n\tname = tester\n\temail = tester@example.invalid\n"

#: The identity variables git reads ahead of every config. The runner's
#: own git plugin sets them for a verb's task when the machine has no
#: identity, and the session's children would inherit them, where they
#: would beat the config a test gives a colleague's clone.
IDENTITY_VARIABLES = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


def pytest_configure(config: pytest.Config) -> None:
    """Point every unset runner directory at a fresh home in this process's scratch.

    Every test's git reads `GIT_CONFIG` as its global config too, unless
    the outer environment already points `GIT_CONFIG_GLOBAL` elsewhere,
    and none of the identity variables the outer environment carries.
    Nor the check leg's label: the job runner names the leg, its shard
    included, to every child it spawns, and a test that drives the gate
    in-process would otherwise run as that shard and leave every step
    without the flag to the first one. A test that wants a leg sets the
    variable itself.
    """
    from livery.footman import scratch_dir

    for name in (*IDENTITY_VARIABLES, LEG_VARIABLE):
        os.environ.pop(name, None)
    if "GIT_CONFIG_GLOBAL" not in os.environ:
        gitconfig = scratch_dir("tests") / "gitconfig"
        gitconfig.write_text(GIT_CONFIG, "utf-8")
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)

    home = str(scratch_dir("workshop-isolation"))
    set_here: list[str] = []
    for name in variables():
        if os.environ.get(name):
            continue
        target = Path(home) / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
        set_here.append(name)
    config.stash[_HOME] = (home, tuple(set_here))


def pytest_unconfigure(config: pytest.Config) -> None:
    """Drop the variables this session set, and the home behind them."""
    home, names = config.stash.get(_HOME, ("", ()))
    for name in names:
        os.environ.pop(name, None)
    if home:
        shutil.rmtree(home, ignore_errors=True)


def _source(task: Any) -> str:
    """The file a task's function was defined in, or empty when unknown."""
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
    path = _source(task)
    if not path:
        return True
    parts = Path(path).parts
    return Path(path).name.startswith("test_") or "tests" in parts


def _from_tests(group: Any) -> bool:
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
    before the first test.
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
            " register under registry.capture() instead"
        )


#: The workshop modules that hold what an extension mount writes.
MOUNT_MODULES = (
    "livery.workshop._extensions",
    "livery.workshop._points",
    "livery.workshop._steps",
)


def _mount_loaded() -> bool:
    """Whether this process has loaded any module a mount writes into."""
    return any(name in sys.modules for name in MOUNT_MODULES)


def _mount_reset() -> None:
    """Put the mount's registries back as a process starts with them."""
    from livery.workshop import _extensions, _points, _steps

    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))


@pytest.fixture(autouse=True)
def _mount_state_stays() -> Iterator[None]:
    """Put back what an extension mount wrote during the test.

    The registries go back as they were before the test. When the test
    loaded them itself, they go back as a process starts with them. A
    session that loads none of them imports nothing here.
    """
    if not _mount_loaded():
        yield
        if _mount_loaded():
            _mount_reset()
        return
    from livery.workshop import _extensions, _points, _steps

    steps, jobs, record = _steps.snapshot(), _points.snapshot(), _extensions.snapshot()
    yield
    _steps.restore(steps)
    _points.restore(jobs)
    _extensions.restore(record)
