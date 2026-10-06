"""The environment of a process that runs a workspace of its own."""

from __future__ import annotations

import os


def environment(**overrides: str) -> dict[str, str]:
    """This process's environment for a child that runs another workspace, unmetered.

    This suite's own coverage rides the environment (`COVERAGE_*`, and
    pytest-cov's `COV_CORE_*`). A child that inherits it meters the
    other workspace's code from that workspace's venv, under paths
    relative to it, which this workspace's report cannot resolve: the
    report then fails on code it has no source for. *overrides* are set
    after the variables are left out.
    """
    kept = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    return {**kept, **overrides}
