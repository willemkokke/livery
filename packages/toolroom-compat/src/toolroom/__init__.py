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

# A literal, not a forwarded read: the release train verifies the
# released version is declared here, and the shim and its real
# package release identically under one number by ruling.
__version__ = "0.6.2"


def __getattr__(name: str) -> Any:
    return getattr(_real, name)


def __dir__() -> list[str]:
    return dir(_real)
