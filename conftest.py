"""Fixtures for every package: the developer's git signing never reaches the suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _unsigned_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn commit and tag signing off for every git the suite spawns.

    The seeds and the verbs under test commit with the developer's
    global config. A `commit.gpgsign` there hands every one of those
    commits to the signing agent, which fails when it is locked and
    waits for a person when it is not; twelve workers then wait
    together. The overlay reaches every git child through the
    documented `GIT_CONFIG_COUNT` environment; a helper that adds keys
    of its own appends after the ones already set.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "tag.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "false")
