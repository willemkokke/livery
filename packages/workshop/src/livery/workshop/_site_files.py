"""Files a layer renders into the workspace beside the base's generated set.

The CI render writes the base's workflows and entry script; a layer
that owns a rendered file of its own, the site's override template
for one, declares it here when it mounts, and the render writes it
with the rest under the same provenance header.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

Render = Callable[[Path], str]

_FILES: dict[str, tuple[Render, str]] = {}


def register_site_file(path: str, render: Render, *, layer: str) -> None:
    """Declare that *layer* renders *path*, relative to the root, at every emission."""
    _FILES[path] = (render, layer)


def site_files() -> dict[str, Render]:
    """Every registered path with its renderer, in registration order."""
    return {path: render for path, (render, _layer) in _FILES.items()}


def unregister_site_file(path: str) -> None:
    """Withdraw *path*; nothing when it was never registered."""
    _FILES.pop(path, None)
