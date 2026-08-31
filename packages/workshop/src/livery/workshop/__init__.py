"""The livery ecosystem's devkit.

The task surface, the materialiser, and the template driver arrive
phase by phase; this release carries the plugin host and the layer
walk (livery.workshop.layer_names, livery.workshop.mount_layers,
livery.workshop.workspace_root) and reserves the distribution name.
The forge lane belongs to livery.forge.Forge; the workshop
orchestrates local, git, and forge steps and never hands a raw forge
verb to a user.
"""

from __future__ import annotations

from livery.workshop._layers import layer_names, mount_layers, workspace_root

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "layer_names",
    "mount_layers",
    "workspace_root",
]
