"""The workshop's footman plugin: what mounting the base layer runs.

Advertised as the ``footman.tasks`` entry point named
``livery.workshop``; a repository's whole ``tasks.py`` is
``plugin("livery.workshop")``. Importing this module registers the
task tree (the quality family, the forge dev loop, the agent hooks)
and mounts every further layer the workspace contract names, in
order, so one line composes everything.

Tasks assume the working directory is the workspace root; ``fm`` is
invoked there.
"""

from __future__ import annotations

from footman import task

# Importing registers each module's tasks with footman.
from livery.workshop import _forge_dev, _hooks, _quality  # noqa: F401
from livery.workshop._layers import SELF, layer_names, mount_layers


@task
def layers() -> None:
    """Print the workspace's layers in precedence order.

    The list is the whole of discovery: what shapes this repository
    is exactly what it prints, and the instance's own files always
    win last.
    """
    names = layer_names()
    if not names:
        print("  no workspace: no livery.toml above the working directory")
        return
    for name in names:
        marker = " (this package)" if name == SELF else ""
        print(f"  {name}{marker}")
    print("  ... then the instance's own files, which always win")


mount_layers()
