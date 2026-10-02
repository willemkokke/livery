"""One backend per registered kind; dispatch through the registry.

The quality verbs read each package's ``kind`` from its contract
and ask livery.workshop._kinds for the backend; an unregistered
kind refuses by name before anything runs, because a package the
gate silently skips is a package the gate lies about.

Adding a kind means: a backend module exposing the build callables
(livery.workshop._backends._python is the shape), a
livery.workshop._kinds.KindRecord registering it with its template,
parent, tools, and CI contract, and nothing else: the dispatch
extension absorbs the new kind automatically.
"""

from __future__ import annotations

from livery.workshop._kinds import backend_for, kind_for
from livery.workshop._packages import Package

__all__ = ["backend_for", "require_backends"]


def require_backends(packages: tuple[Package, ...]) -> None:
    """Refuse any package whose declared kind is unregistered."""
    for kind_name in sorted({package.kind for package in packages}):
        kind_for(kind_name)
