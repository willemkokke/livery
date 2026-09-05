"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the layer
walk (livery.workshop.layer_names, livery.workshop.mount_layers,
livery.workshop.workspace_root), the package contracts
(livery.workshop.discover_packages, livery.workshop.verify_workspace
over livery.workshop.Package and livery.workshop.Edge), and the one
helper a package's docs generator needs
(livery.workshop.rewrite_nav_block). The forge lane belongs to
livery.forge.Forge; the workshop orchestrates local, git, and forge
steps and never hands a raw forge verb to a user.
"""

from __future__ import annotations

from livery.workshop._docs import rewrite_nav_block
from livery.workshop._layers import layer_names, mount_layers, workspace_root
from livery.workshop._packages import (
    Edge,
    Package,
    discover_packages,
    verify_workspace,
)

__version__ = "0.1.0"

__all__ = [
    "Edge",
    "Package",
    "__version__",
    "discover_packages",
    "layer_names",
    "mount_layers",
    "rewrite_nav_block",
    "verify_workspace",
    "workspace_root",
]
