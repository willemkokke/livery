"""One backend module per declared package type.

The quality verbs read each package's ``type`` from its contract and
dispatch here; a type without a backend is refused by name, before
anything runs, because a package the gate silently skips is a package
the gate lies about.
"""

from __future__ import annotations

from livery.workshop._backends import _python
from livery.workshop._packages import Package

#: The backends by the contract's ``type`` value.
_BACKENDS = {"python": _python}


def require_backends(packages: tuple[Package, ...]) -> None:
    """Refuse any package whose declared type has no backend yet."""
    unknown = sorted({package.type for package in packages} - set(_BACKENDS))
    if unknown:
        raise ValueError("no backend for package type(s): " + ", ".join(unknown))
