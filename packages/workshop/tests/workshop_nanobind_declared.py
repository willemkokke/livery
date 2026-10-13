"""The nanobind extension's CI jobs, declared as the mount declares them.

The mount adds nanobind's jobs for a workspace whose packages hold
nanobind, and a test that renders or runs the release point calls the
code directly, with no mount before it. A test that uses the
``nanobind_jobs`` fixture has the wheels job and the publish job's wait
on it in the release point, read from the extension's own
declaration; the contributed jobs are put back as they were after it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from workshop_docs_declared import jobs_restored


@pytest.fixture
def nanobind_jobs() -> Iterator[None]:
    """Add nanobind's jobs from its declaration file, for the test alone."""
    from livery.workshop._extensions import declaration, register_jobs

    found = declaration("nanobind")
    assert found is not None
    with jobs_restored():
        register_jobs("nanobind", found.additions.jobs)
        yield
