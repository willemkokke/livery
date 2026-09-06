"""The ``toolroom.testing`` spelling, forwarded whole.

New code imports [livery.toolroom.testing][] directly.
"""

from typing import Any

import livery.toolroom.testing as _real


def __getattr__(name: str) -> Any:
    return getattr(_real, name)


def __dir__() -> list[str]:
    return dir(_real)
