"""cmake's build and test steps, registered as the mount registers them.

The mount registers cmake's ``build.configure``, ``build.compile`` and
``test.ctest`` for a workspace whose packages hold cmake, and a test
that walks, orders or claims them calls the registry with no mount
before it. A test module imports `cmake_steps_fixture` and names
``cmake_steps`` as a parameter: the three are registered from the
extension's own declaration, under its name, and the registry is put
back after the test. cmake is a distribution of its own,
``livery-extensions-cmake``, so the test skips while it is not
installed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livery.workshop._steps import StepRecord, restore, snapshot
from workshop_installed import skip_unless_installed


@pytest.fixture(name="cmake_steps")
def cmake_steps_fixture() -> Iterator[tuple[StepRecord, ...]]:
    """Register cmake's steps from its declaration for the test; the records."""
    from livery.workshop._extensions import declaration, register_declared

    skip_unless_installed("cmake")
    found = declaration("cmake")
    assert found is not None
    state = snapshot()
    register_declared("cmake", found.additions)
    try:
        yield found.additions.steps
    finally:
        restore(state)
