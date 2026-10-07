"""The base's verbs: what importing the workshop's plugin entry registers first.

The plugin entry ([livery.workshop._mount][]) imports this module, then
mounts the extensions the contract lists, each through ``plugin()``
under its own name. Importing this module registers the base's tree
alone (the quality family, the content sync, the agent hooks): mounting
the further extensions from inside this import would deliver their
tasks under the base's identity.

Tasks assume the working directory is the workspace root; ``fm`` is
invoked there.
"""

from __future__ import annotations

from livery.footman import fail, task

# Importing registers each module's tasks with footman.
from livery.workshop import _checks as _checks_module
from livery.workshop import (  # noqa: F401
    _ci_tasks,
    _clean,
    _commit,
    _devenv,
    _e2e,
    _env_tasks,
    _graph,
    _hooks,
    _issue_tasks,
    _new_project,
    _provenance,
    _quality,
    _release,
    _release_driver,
    _scale,
    _shell,
    _speed_tasks,
    _store_tasks,
    _submit,
    _sync,
    _templates,
    _tool_tasks,
    _update,
    _update_driver,
    _workflow_tasks,
)


@task
def extensions() -> None:
    """Print the workspace's extensions in precedence order.

    The list is the whole of discovery: what shapes this repository
    is exactly what it prints, and the instance's own files always
    win last.
    """
    from livery.workshop._extensions import (
        describe_extensions,
        missing_list,
        workspace_root,
    )

    root = workspace_root()
    if root is None:
        print("  no workspace: no workshop.toml above the working directory")
        return
    if why := missing_list(root):
        fail(why)

    try:
        lines = describe_extensions()
    except RuntimeError as error:
        fail(str(error))
    for line in lines:
        print(line)
    print("  ... then the instance's own files, which always win")


# The role verbs, generated from the checks registered above: every
# module that registers a check was imported first.
_checks_module.generate_verbs()
