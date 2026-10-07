"""The docs extension's slots and CI jobs, declared as the mount declares them.

A test that composes the site, or renders the CI, calls the code
directly, with no mount before it. Importing ``docs_slots`` into a test
module declares the slots for each of its tests; a declaration made
again keeps the values already contributed. Importing ``docs_jobs``
adds the site's two jobs to their points for each of its tests, and
withdraws them after it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def docs_slots() -> None:
    """Declare the docs extension's slots from its declaration file."""
    from livery.workshop._extensions import declaration, declare_slots

    found = declaration("docs")
    assert found is not None
    declare_slots("docs", found.slots)


@pytest.fixture(autouse=True)
def docs_jobs() -> Iterator[None]:
    """Add the docs extension's jobs from its declaration file, then withdraw them."""
    from livery.workshop._extensions import declaration, register_jobs
    from livery.workshop._points import withdraw_job

    found = declaration("docs")
    assert found is not None
    register_jobs("docs", found.additions.jobs)
    yield
    for item in found.additions.jobs:
        withdraw_job(item.point, item.job.name)
