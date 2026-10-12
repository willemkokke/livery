"""Marks a task sets on itself for the git plugin: an identity, or unsigned commits.

A mark is an attribute on the task's function, written by a decorator
below or above `@task` and read by the plugin's hook through the
handle's `task.mark(...)`. The decorators import nothing of footman,
so a module that only marks its tasks stays importable without it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_F = TypeVar("_F", bound=Callable[..., object])

#: The attribute names the plugin's hook reads off a task.
IDENTITY_MARK = "livery_git_identity"
UNSIGNED_MARK = "livery_git_unsigned"


def identity(name: str, email: str) -> Callable[[_F], _F]:
    """Make the decorated task's commits carry *name* and *email*, whatever git says."""

    def mark(fn: _F) -> _F:
        setattr(fn, IDENTITY_MARK, (name, email))
        return fn

    return mark


def unsigned(fn: _F) -> _F:
    """Turn commit and tag signing off for the decorated task's git."""
    setattr(fn, UNSIGNED_MARK, True)
    return fn
