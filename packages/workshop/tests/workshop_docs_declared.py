"""The docs extension's slots and CI jobs, declared as the mount declares them.

A test that composes the site, or renders the CI, calls the code
directly, with no mount before it. Importing ``docs_slots`` into a test
module declares the slots for each of its tests; a declaration made
again keeps the values already contributed. Importing ``docs_jobs``
adds the site's two jobs to their points for each of its tests, and
puts the contributed jobs back as they were after it: a mount earlier
in the worker's session keeps the jobs it added, so a render after the
test takes the same jobs as one before it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest


@pytest.fixture(autouse=True)
def docs_slots() -> None:
    """Declare the docs extension's slots from its declaration file."""
    from livery.workshop._extensions import declaration, declare_slots

    found = declaration("docs")
    assert found is not None
    declare_slots("docs", found.slots)


@contextmanager
def jobs_restored() -> Iterator[None]:
    """Put the contributed jobs back as they were on entry, whatever ran inside."""
    from livery.workshop import _points

    before = dict(_points._CONTRIBUTED_JOBS)
    try:
        yield
    finally:
        _points._CONTRIBUTED_JOBS.clear()
        _points._CONTRIBUTED_JOBS.update(before)


@pytest.fixture(autouse=True)
def docs_jobs() -> Iterator[None]:
    """Add the docs extension's jobs from its declaration file, for the test alone."""
    from livery.workshop._extensions import declaration, register_jobs

    found = declaration("docs")
    assert found is not None
    with jobs_restored():
        register_jobs("docs", found.additions.jobs)
        yield
