"""The workshop's footman plugin: what mounting the base extension runs.

Advertised as the ``footman.tasks`` entry point named
``livery.workshop``; a repository's ``tasks.py`` starts with
``plugin("livery.workshop")`` and then calls
[livery.workshop.api.mount_extensions][] itself. Importing this module
registers the base extension's tree alone (the quality family, the
content sync, the agent hooks); mounting the further extensions from
inside this import would deliver their tasks under this extension's
identity, so composition belongs to the workspace's own file. A
repository's own tasks go below the mount lines, in its own file.

Tasks assume the working directory is the workspace root; ``fm`` is
invoked there.
"""

from __future__ import annotations

from livery.footman.api import fail, task

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
