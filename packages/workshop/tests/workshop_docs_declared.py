"""The docs extension's CI jobs, declared as the mount declares them.

A test that renders the CI calls the code directly, with no mount
before it. Importing ``docs_jobs`` into a test module adds the site's
two jobs to their points for each of its tests, and puts the
contributed jobs back as they were after it: a mount earlier in the
worker's session keeps the jobs it added, so a render after the test
takes the same jobs as one before it. The docs extension is a
distribution of its own, ``livery-extensions-docs``: while it is not
installed, the fixture adds no job, and a test whose render reads the
site's jobs calls `skip_unless_installed` for docs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest


@contextmanager
def jobs_restored() -> Iterator[None]:
    """Put the contributed jobs back as they were on entry, whatever ran inside."""
    from livery.workshop import _points

    state = _points.snapshot()
    try:
        yield
    finally:
        _points.restore(state)


@pytest.fixture(autouse=True)
def docs_jobs() -> Iterator[None]:
    """Add the docs extension's jobs from its declaration file, for the test alone.

    None while the extension is not installed.
    """
    from livery.workshop._extensions import declaration, register_jobs

    found = declaration("docs")
    with jobs_restored():
        if found is not None:
            register_jobs("docs", found.additions.jobs)
        yield
