"""The base's verbs: what importing the workshop's plugin entry registers first.

The plugin entry ([livery.workshop._mount][]) imports this module, then
mounts the extensions the contract lists, each through ``plugin()``
under its own name. Importing this module registers the base's tree
alone (the quality family, the content sync): mounting the further
extensions from inside this import would deliver their tasks under the
base's identity.

Tasks assume the working directory is the workspace root; ``fm`` is
invoked there.
"""

from __future__ import annotations

from livery.footman import Invocation, fail, pre_tasks, task

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


@pre_tasks
def link_task_names(inv: Invocation) -> None:
    """Link task names to the contract's ``[workspace] docs-url``, when it sets one.

    A configured ``docs-url`` comes first, and an extension that links
    task names to pages of its own leaves this key's template in place.
    The contract is read raw, as the mount reads it: the hook runs on
    every command, ``fm sync`` among them, so a template footman cannot
    fill is named on stderr and links nothing, and the command goes on.
    """
    import tomllib

    from livery.workshop._extensions import _note, _workspace_table, workspace_root

    root = workspace_root()
    if inv.docs_url is not None or root is None:
        return
    try:
        workspace = _workspace_table(root) or {}
    except (OSError, tomllib.TOMLDecodeError):
        return
    template = workspace.get("docs-url")
    if not isinstance(template, str):
        # Absent, or a value the contract's judge refuses by name.
        return
    try:
        inv.docs_url = template
    except ValueError as error:
        _note(f"workshop.toml [workspace] docs-url: {error}; no task links")


# The role verbs, generated from the checks registered above: every
# module that registers a check was imported first.
_checks_module.generate_verbs()
