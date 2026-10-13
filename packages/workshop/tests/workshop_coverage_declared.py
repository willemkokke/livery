"""The coverage extension's CI jobs, declared as the mount declares them.

The mount adds coverage's entry to the check legs and its judge to the
gate job for a workspace that lists coverage, and a test that runs or
renders the gate point calls the code directly, with no mount before
it. A test that uses the ``coverage_jobs`` fixture has both, read from
the extension's own declaration; the contributed jobs are put back as
they were after it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from workshop_docs_declared import jobs_restored


@pytest.fixture
def coverage_jobs() -> Iterator[None]:
    """Add coverage's jobs from its declaration file, for the test alone."""
    from livery.workshop._extensions import declaration, register_jobs

    found = declaration("coverage")
    assert found is not None
    with jobs_restored():
        register_jobs("coverage", found.additions.jobs)
        yield
