"""The marks a task sets on itself, read back as the plugin's hook reads them."""

from __future__ import annotations

from livery import git
from livery.git._marks import IDENTITY_MARK, UNSIGNED_MARK


def test_an_unmarked_task_carries_nothing() -> None:
    def build() -> None: ...

    assert getattr(build, IDENTITY_MARK, None) is None
    assert getattr(build, UNSIGNED_MARK, False) is False


def test_the_marks_ride_the_function_and_stack() -> None:
    @git.identity("loop", "loop@loop.invalid")
    @git.unsigned
    def release() -> None: ...

    assert getattr(release, IDENTITY_MARK) == ("loop", "loop@loop.invalid")
    assert getattr(release, UNSIGNED_MARK) is True
    assert release() is None  # the function is returned unchanged
