"""The hosts a test workspace locks for: the gated three, and the host running the test.

A lock covers the hosts its contract names, the gated three unless it
says otherwise, and a materialise on a host the lock lacks refuses
every tool by name, which is the behaviour a fourth host must meet
from a real contract and never from a fixture. So a test workspace
locks for the gated three and, on any other host, for that host too,
and every fake record carries an artifact for each.
"""

from __future__ import annotations

import platform

import pytest

from livery.toolroom.store import host_key
from livery.workshop import _tools

GATED = ("linux-x64", "macos-arm", "windows-x64")
"""The hosts the gate runs on, the lock's default."""

HERE = host_key(platform.system(), platform.machine())
"""The host running the tests."""

HOSTS = GATED if HERE in GATED else (*GATED, HERE)
"""What a test workspace locks for and a fake record serves."""


@pytest.fixture(autouse=True)
def lock_for_this_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test workspace locks for the gated three and the host running the test.

    Imported into a test module that materialises, where it applies to
    every test of the module: a contract that names no hosts locks for
    the gated three, and a materialise on a fourth host refuses by
    name; a test on such a host is not that case, so its default gains
    the host.
    """
    monkeypatch.setattr(_tools, "DEFAULT_HOSTS", HOSTS)
