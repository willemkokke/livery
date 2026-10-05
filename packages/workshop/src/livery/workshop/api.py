"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the extension
walk (livery.workshop.api.extension_names, livery.workshop.api.mount_extensions,
livery.workshop.api.workspace_root), the package contracts
(livery.workshop.api.discover_packages, livery.workshop.api.verify_workspace
over livery.workshop.api.Package and livery.workshop.api.Edge), and the one
helper a package's docs generator needs
(livery.workshop.api.rewrite_nav_block). The forge lane belongs to
livery.forge.api.Forge; the workshop orchestrates local, git, and forge
steps and never hands a raw forge verb to a user.

An extension in its own wheel declares its checks with
[livery.workshop.api.CheckRecord][], its claims with
[livery.workshop.api.Claim][] and the files it manages with
[livery.workshop.api.Fragment][], in a ``CHECKS`` tuple of its declaring
module. A check's body takes a [livery.workshop.api.GateContext][]; a
check that narrows by paths (``narrowing=PATHS``) reads them from
[livery.workshop.api.scoped_paths][] and calls its tool through
[livery.workshop.api.run_batched][].
"""

from __future__ import annotations

from livery.workshop._checks import (
    PATHS,
    CheckRecord,
    Claim,
    GateContext,
    scoped_paths,
)
from livery.workshop._extensions import (
    extension_names,
    mount_extensions,
    workspace_root,
)
from livery.workshop._fragments import Fragment
from livery.workshop._invoke import run_batched
from livery.workshop._navblocks import rewrite_nav_block
from livery.workshop._packages import (
    Edge,
    Package,
    discover_packages,
    verify_workspace,
)

__version__ = "0.5.0"

__all__ = [
    "PATHS",
    "CheckRecord",
    "Claim",
    "Edge",
    "Fragment",
    "GateContext",
    "Package",
    "__version__",
    "discover_packages",
    "extension_names",
    "mount_extensions",
    "rewrite_nav_block",
    "run_batched",
    "scoped_paths",
    "verify_workspace",
    "workspace_root",
]
