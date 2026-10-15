"""Skip a test while an extension it mounts is not installed, naming its distribution.

The workshop's suite mounts extensions that ship as distributions of
their own: the tool extensions, and the package-level extensions that
left the workshop's wheel. A run of the workshop's wheel alone, as a
release leg makes, installs none of them, so a test that mounts one
calls `skip_unless_installed` before it does. In this repository's
gate every member is installed, and the test runs.
"""

from __future__ import annotations

import pytest


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The reason names each missing extension and the distribution that
    ships it, so a run that skips says what to install.
    """
    from livery.workshop._extensions import distribution_of, installed_declaration

    missing = [
        name
        for name in dict.fromkeys(extensions)
        if installed_declaration(name) is None
    ]
    if missing:
        named = ", ".join(f"{name} ({distribution_of(name)})" for name in missing)
        pytest.skip(f"not installed here: {named}")
