"""The workshop's test session: a lock's default hosts include the host running it."""

from __future__ import annotations

import pytest

from livery.workshop import _tools
from workshop_hosts import HOSTS


@pytest.fixture(autouse=True)
def _lock_for_this_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test workspace locks for the gated three and the host running the test.

    A contract that names no hosts locks for the gated three, and a
    materialise on a fourth host refuses by name; a test on such a host
    is not that case, so its default gains the host.
    """
    monkeypatch.setattr(_tools, "DEFAULT_HOSTS", HOSTS)
