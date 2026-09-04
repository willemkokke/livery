"""One backend per registered kind; dispatch through the registry.

The quality verbs read each package's ``type`` from its contract
and ask livery.workshop._kinds for the backend; an unregistered
type refuses by name before anything runs, because a package the
gate silently skips is a package the gate lies about.

Adding a kind means: a backend module exposing the build callables
(livery.workshop._backends._python is the shape), a
livery.workshop._kinds.KindRecord registering it with its template,
parent, tools, and CI contract, and nothing else: the dispatch
layer absorbs the new kind automatically.
"""

from __future__ import annotations

from livery.workshop._kinds import backend_for, kind_for
from livery.workshop._packages import Package

__all__ = ["backend_for", "require_backends"]


def require_backends(packages: tuple[Package, ...]) -> None:
    """Refuse any package whose declared type is unregistered."""
    for type_name in sorted({package.type for package in packages}):
        kind_for(type_name)
