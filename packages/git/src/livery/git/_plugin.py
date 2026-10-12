"""The footman plugin: the environment every git spawn under footman runs with.

Mounted by `plugin("livery.git")`, on by listing and off by not. Its
one `pre_task` hook writes into each task's own environment, which the
task's subprocesses see: the ssh command that multiplexes connections
over one master per host, the identity every commit the tools make
carries when the person has none, an editor that never opens, and what
a task marks for itself, an identity of its own or unsigned commits.
Nothing in the library's functions imports footman; this module alone
does, and the only loader is footman's own `plugin()`, so footman is
present by construction.
"""

from __future__ import annotations

from typing import Any

import livery.footman as footman
from livery.git._environment import (
    config_environment,
    identity_environment,
    tool_identity,
)
from livery.git._marks import IDENTITY_MARK, UNSIGNED_MARK
from livery.git._ops import config_get
from livery.git._ssh import ssh_environment, ssh_sockets

#: How long a master lingers after the verb that opened it exits.
IDLE = "10m"

#: Per-process memory of the two config facts the hook reads once, at
#: the first task, since a verb's tasks all run in the same checkout:
#: `core.sshCommand`, and whether the person has an identity.
_READ: dict[str, str] = {}


def _config(key: str) -> str:
    if key not in _READ:
        _READ[key] = config_get(footman.cwd(), key)
    return _READ[key]


@footman.pre_task
def git_environment(inv: footman.Invocation, task: Any) -> None:
    """Write this task's git environment: the ssh command, the identity, the marks."""
    del inv
    environment, reason = ssh_environment(
        ssh_sockets(footman.data_dir()),
        idle=IDLE,
        environ=task.env,
        configured=_config("core.sshCommand"),
    )
    if reason and not hasattr(task.state, "ssh"):
        task.state.ssh = reason
        print(f"  {reason}")
    task.env.update(environment)
    identity = task.mark(IDENTITY_MARK)
    if identity is None and not _config("user.email"):
        identity = tool_identity(footman.prog())
    if identity is not None:
        name, email = identity
        task.env.update(identity_environment(name, email))
    if task.mark(UNSIGNED_MARK):
        task.env.update(
            config_environment(
                task.env, (("commit.gpgsign", "false"), ("tag.gpgsign", "false"))
            )
        )
    # No verb of a runner reads a message from an editor: a rebase that
    # stops, a merge commit, a `commit --no-edit` take the message as it
    # stands. A person's own GIT_EDITOR is kept.
    task.env.setdefault("GIT_EDITOR", "true")
