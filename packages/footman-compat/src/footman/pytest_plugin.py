"""The ``footman.pytest_plugin`` spelling, forwarded whole.

New code imports [livery.footman.pytest_plugin][] directly.
"""

import livery.footman.pytest_plugin as _real

# The literal-False spelling checkers honour without importing typing:
# the completion hot path pays for this module on every TAB press.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any


def __getattr__(name: str) -> "Any":
    return getattr(_real, name)


def __dir__() -> list[str]:
    return dir(_real)
