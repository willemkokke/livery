"""The bench's suite runs apart from the machine and reads this repository's records.

The suite is run from a checkout under the workshop's venv, where the
workshop's isolation plugin points the runner's directories away from
the machine's, and by the release train's isolated legs, where the
bench is installed as a wheel with its own dependencies alone and no
plugin runs. The bench's tasks read the machine's provisioned prefix
and store through those directories, so the suite points them away
itself, the way the plugin does: once per session, and only where the
outer environment left them unset, since an outer redirect is a
redirect. The records live at the repository root and the bench finds
them from the run's project root, which a leg's scratch directory is
not, so the suite names them from its own position.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from livery.toolroom.bench import _tasks

RECORDS = Path(__file__).resolve().parents[3] / "records"
"""The repository's records, found from this file: the suite's own position."""

DIRECTORIES = ("FOOTMAN_DATA_DIR", "FOOTMAN_CACHE_DIR", "FOOTMAN_CONFIG_DIR")
"""The runner's directories the bench's tasks read the machine through."""


def pytest_configure(config: pytest.Config) -> None:
    """Point every unset runner directory at a fresh temporary home."""
    del config
    home = tempfile.mkdtemp(prefix="toolroom-bench-isolation-")
    for name in DIRECTORIES:
        if os.environ.get(name):
            continue
        target = Path(home) / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)


@pytest.fixture(autouse=True)
def repository_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bench at the repository's records for every test.

    A test that points `_RECORDS` elsewhere sets it after this fixture
    and wins.
    """
    monkeypatch.setattr(_tasks, "_RECORDS", RECORDS)
