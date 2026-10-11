"""The bench's isolation plugin: a session's home is this process's scratch."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

import livery.footman as footman
from livery.toolroom.bench import _pytest_isolation


def test_the_home_is_this_process_s_scratch_and_goes_at_unconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cache stays redirected, so the home lands in the session's own
    # cache and never in the machine's. Cleared, the other two variables
    # are the plugin's to set; a session that ends takes the home with
    # it, and one that is killed leaves it to footman's collector, which
    # reads the owner's pid off the name.
    monkeypatch.delenv("FOOTMAN_DATA_DIR", raising=False)
    monkeypatch.delenv("FOOTMAN_CONFIG_DIR", raising=False)

    class Config:
        stash = pytest.Stash()

    config = cast("pytest.Config", Config())
    _pytest_isolation.pytest_configure(config)
    home = Path(os.environ["FOOTMAN_DATA_DIR"]).parent
    assert home.parent == footman.cache_dir() / "scratch"
    assert home.name.startswith(f"toolroom-bench-isolation-{os.getpid()}-")
    assert Path(os.environ["FOOTMAN_CONFIG_DIR"]).parent == home
    _pytest_isolation.pytest_unconfigure(config)
    assert not home.exists()
    assert "FOOTMAN_DATA_DIR" not in os.environ
