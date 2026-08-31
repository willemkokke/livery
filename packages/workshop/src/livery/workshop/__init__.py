"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the layer
walk (livery.workshop.layer_names, livery.workshop.mount_layers,
livery.workshop.workspace_root) and the package contracts
(livery.workshop.discover_packages, livery.workshop.verify_workspace
over livery.workshop.Package and livery.workshop.Edge). The forge
lane belongs to livery.forge.Forge; the workshop orchestrates local,
git, and forge steps and never hands a raw forge verb to a user.
"""

from __future__ import annotations

from livery.workshop._layers import layer_names, mount_layers, workspace_root
from livery.workshop._packages import (
    Edge,
    Package,
    discover_packages,
    verify_workspace,
)

__version__ = "0.0.2"

__all__ = [
    "Edge",
    "Package",
    "__version__",
    "discover_packages",
    "layer_names",
    "mount_layers",
    "verify_workspace",
    "workspace_root",
]
