"""The setup around footman's examples: a captured registry and a recording run.

Every example defines tasks as a tasks file would, so it runs inside a
fresh [livery.footman.registry.capture][], and a top-level run or tool
call records instead of executing, under [livery.footman.recording][].
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from livery.footman import recording, registry


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Run an example inside a captured registry and a recording."""
    if item.get_closest_marker("example") is None:
        yield
        return
    with registry.capture(), recording():
        yield
