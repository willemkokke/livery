"""The ``footman`` distribution, a thin shim over ``livery-footman``.

Every attribute forwards to [livery.footman][], the lazy re-exports
included: ``footman.task`` answers exactly as ``livery.footman.task``
does. New code imports ``livery.footman`` directly; this shim keeps
existing ``import footman`` spellings working while the old
distribution name continues its release line. The console scripts
(``fm``, ``footman``) and the entry points ship with the real
distribution, never here.
"""

import livery.footman as _real

# The literal-False spelling checkers honour without importing typing:
# the completion hot path pays for this module on every TAB press.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any


def __getattr__(name: str) -> "Any":
    return getattr(_real, name)


def __dir__() -> list[str]:
    return dir(_real)
