"""The docs extension's slots declared, as the mount declares a listed extension's.

A test that composes the site calls the docs extension's code directly,
with no mount before it. Importing ``docs_slots`` into a test module
declares the slots for each of its tests; a declaration made again keeps
the values already contributed.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def docs_slots() -> None:
    """Declare the docs extension's slots from its declaration file."""
    from livery.workshop._extensions import declaration, declare_slots

    found = declaration("docs")
    assert found is not None
    declare_slots("docs", found.slots)
