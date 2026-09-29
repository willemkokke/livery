"""Every pytest with the bench installed runs apart from the machine's runner state.

The bench's tasks read the machine's provisioned prefix and its store
through the runner's directories, the data, cache and config
directories footman reads through ``FOOTMAN_DATA_DIR``,
``FOOTMAN_CACHE_DIR`` and ``FOOTMAN_CONFIG_DIR``. A test session that
left them at the machine's would read this desk's provisioned tools
and write into them, so this plugin points every unset one at a fresh
temporary home for the whole session: before the first test, and for
every child process a test starts, since the variables ride the
environment. A variable the outer environment already set is kept,
since an outer redirect is a redirect; that is how a workspace whose
own isolation plugin runs first and this plugin agree, and how a
release leg that installs the bench alone is isolated the same way.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

DIRECTORIES = ("FOOTMAN_DATA_DIR", "FOOTMAN_CACHE_DIR", "FOOTMAN_CONFIG_DIR")
"""The runner's directories the bench's tasks read the machine through."""

_HOME = pytest.StashKey[tuple[str, tuple[str, ...]]]()


def pytest_configure(config: pytest.Config) -> None:
    """Point every unset runner directory at a fresh temporary home."""
    home = tempfile.mkdtemp(prefix="toolroom-bench-isolation-")
    set_here: list[str] = []
    for name in DIRECTORIES:
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
