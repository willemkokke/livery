"""Every pytest under a workshop venv runs with the workshop's own test guards.

footman's pytest plugin points the runner's directories away and guards
the task registry for every session; this one adds what the workshop
owns.

Every test's git reads a global config of the session's own, with one
identity and none of the desk's settings, and none of the identity
variables the outer environment carries.

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

A patch a test made with `monkeypatch.setattr` is undone everywhere,
not only on the object it was set on. A module first imported while
the patch was active copied the patched value (`from owner import
name`); without the guard it keeps that value after monkeypatch puts
the owner back, and every later test in the worker calls the test's
stand-in, so the outcome depends on which test imported the module
first.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from collections.abc import Iterator
from typing import Any

import pytest

# The value monkeypatch records for an attribute the owner did not have.
from _pytest.compat import NOTSET

from livery.workshop._ci_context import LEG_VARIABLE

#: The session's scratch directory, which holds its git config.
_HOME = pytest.StashKey[str]()

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
    """Give every test's git the tester identity, and drop the leg's label.

    Every test's git reads `GIT_CONFIG` as its global config, unless
    the outer environment already points `GIT_CONFIG_GLOBAL` elsewhere,
    and none of the identity variables the outer environment carries.
    Nor the check leg's label: the job runner names the leg, its shard
    included, to every child it spawns, and a test that drives the gate
    in-process would otherwise run as that shard and leave every step
    without the flag to the first one. A test that wants a leg sets the
    variable itself.
    """
    for name in (*IDENTITY_VARIABLES, LEG_VARIABLE):
        os.environ.pop(name, None)
    if "GIT_CONFIG_GLOBAL" in os.environ:
        return
    from livery.footman import scratch_dir

    home = scratch_dir("workshop-isolation")
    gitconfig = home / "gitconfig"
    gitconfig.write_text(GIT_CONFIG, "utf-8")
    os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    config.stash[_HOME] = str(home)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the session's git config."""
    home = config.stash.get(_HOME, "")
    if home:
        shutil.rmtree(home, ignore_errors=True)


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


#: Values the interpreter may share between unrelated names, so that a
#: module's own constant can be the very object a test patched in.
#: Only a patched value of another type is told apart by its identity.
SHARED = (type(None), bool, int, float, complex, str, bytes, tuple, frozenset)


#: The modules loaded before a test's setup began, for its teardown.
_LOADED = pytest.StashKey[frozenset[str]]()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    """Note the modules loaded before the test's fixtures and body run."""
    item.stash[_LOADED] = frozenset(sys.modules)
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    """Give the modules the test imported the originals its patches replaced.

    Runs before any fixture's teardown, so the test's patches are still
    in place: a global of a module first imported during the test that
    is a patched value gets the first original back, under whatever
    name the module bound it, and `monkeypatch` then restores the
    owners themselves. The test's own `monkeypatch` is read from the
    fixtures it already resolved; this hook requests none, since a
    fixture asking for `monkeypatch` changes the order other plugins'
    fixtures are set up and torn down in, which footman's environment
    restore depends on. An attribute the patch added, which had no
    original, is left as it is, and so is a patched value of a type in
    `SHARED`, whose identity a module's own constant can share.
    """
    funcargs: Any = getattr(item, "funcargs", {})
    patched = funcargs.get("monkeypatch") if isinstance(funcargs, dict) else None
    before = item.stash.get(_LOADED, None)
    if isinstance(patched, pytest.MonkeyPatch) and before is not None:
        _restore_copies(patched, before)
    return (yield)


def _restore_copies(monkeypatch: pytest.MonkeyPatch, before: frozenset[str]) -> None:
    """Put the originals back in the modules loaded since *before*."""
    # monkeypatch keeps its record as (owner, name, value before) and
    # exposes no public view of it.
    patches: list[tuple[object, str, object]] = list(
        getattr(monkeypatch, "_setattr", ())
    )
    if not patches:
        return
    loaded = [
        module for name, module in list(sys.modules.items()) if name not in before
    ]
    if not loaded:
        return
    originals: dict[tuple[int, str], object] = {}
    replaced: dict[int, object] = {}
    for owner, name, value in patches:
        key = (id(owner), name)
        if key in originals:
            # A later patch of the same attribute: the value it replaced
            # was an earlier patch's.
            replaced[id(value)] = originals[key]
        else:
            originals[key] = value
    for owner, name, _value in patches:
        with contextlib.suppress(AttributeError):
            replaced[id(getattr(owner, name))] = originals[(id(owner), name)]
    replaced = {key: value for key, value in replaced.items() if value is not NOTSET}
    for module in loaded:
        names = getattr(module, "__dict__", {})
        for name, value in list(names.items()):
            if isinstance(value, SHARED):
                continue
            original = replaced.get(id(value), value)
            if original is not value:
                names[name] = original
