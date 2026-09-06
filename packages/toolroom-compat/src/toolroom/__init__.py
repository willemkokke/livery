"""The ``toolroom`` distribution, a thin shim over ``livery-toolroom``.

Every attribute forwards to [livery.toolroom][], the dynamic tool
minting included: ``toolroom.terraform`` answers exactly as
``livery.toolroom.terraform`` does. New code imports
``livery.toolroom`` directly; this shim keeps existing
``import toolroom`` spellings working while the old distribution
name continues its release line.
"""

from typing import Any

import livery.toolroom as _real


def __getattr__(name: str) -> Any:
    return getattr(_real, name)


def __dir__() -> list[str]:
    return dir(_real)
