"""The workshop's footman plugin: what mounting the base layer runs.

Advertised as the ``footman.tasks`` entry point named
``livery.workshop``; a repository's whole ``tasks.py`` starts with
``plugin("livery.workshop")``. Importing this module registers the
task tree (the quality family, the content sync, the agent hooks)
and mounts every further layer the workspace contract names, in
order, so one line composes everything. A repository's own tasks go
below the plugin line, in its own file.

Tasks assume the working directory is the workspace root; ``fm`` is
invoked there.
"""

from __future__ import annotations

from footman import task

# Importing registers each module's tasks with footman.
from livery.workshop import (  # noqa: F401
    _ci_tasks,
    _clean,
    _env_tasks,
    _graph,
    _hooks,
    _issue_tasks,
    _quality,
    _release,
    _release_driver,
    _shell,
    _submit,
    _sync,
    _templates,
    _update,
    _update_driver,
    _workflow_tasks,
)
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
        print("  no workspace: no workshop.toml above the working directory")
        return
    for name in names:
        marker = " (this package)" if name == SELF else ""
        print(f"  {name}{marker}")
    print("  ... then the instance's own files, which always win")


mount_layers()
