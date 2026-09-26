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

from livery.toolroom.store import host_key

GATED = ("linux-x64", "macos-arm", "windows-x64")
"""The hosts the gate runs on, the lock's default."""

HERE = host_key(platform.system(), platform.machine())
"""The host running the tests."""

HOSTS = GATED if HERE in GATED else (*GATED, HERE)
"""What a test workspace locks for and a fake record serves."""
