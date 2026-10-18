"""Every pytest under a workshop venv undoes a test's patches everywhere.

footman's pytest plugin points the runner's directories away and guards
the task registry, livery-git's gives every test's git a config of the
session's own, and the workshop's test guard
([livery.workshop._test_guard][]), a member of footman's
``footman.test_guards`` group, puts back what an extension mount
writes. This plugin adds one guard.

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
import sys
from collections.abc import Iterator
from typing import Any

import pytest

# The value monkeypatch records for an attribute the owner did not have.
from _pytest.compat import NOTSET

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
