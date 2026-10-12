"""The stack a branch was started on, recorded in the checkout's git config.

``fm start --from`` records a branch's parent and the parent's tip at
the start as ``branch.<name>.workshop-parent`` and
``branch.<name>.workshop-parent-tip``; a sync, a submit and the
issue verbs read them back here. A branch standing on main has
neither key.
"""

from __future__ import annotations

from pathlib import Path

from livery import git


def stack(root: Path, branch: str) -> tuple[str, str]:
    """The parent *branch* was started on, and the commit it started from.

    Both are empty for a branch started on main; the commit is empty
    for a branch started before it was recorded. One process for both
    keys: a flow reads the stack several times.
    """
    prefix = f"branch.{branch}.workshop-parent"
    found = git.config_regexp(root, f"^{prefix}(-tip)?$")
    parent = found.get(prefix, "")
    return parent, found.get(f"{prefix}-tip", "") if parent else ""


def record_stack(root: Path, branch: str, parent: str, tip: str) -> None:
    """Record *branch* as stacked on *parent*, its own commits after *tip*."""
    git.config_set(root, f"branch.{branch}.workshop-parent", parent)
    git.config_set(root, f"branch.{branch}.workshop-parent-tip", tip)


def forget_stack(root: Path, branch: str) -> None:
    """Drop the stack record: *branch* stands on main now."""
    for key in ("workshop-parent", "workshop-parent-tip"):
        git.config_unset(root, f"branch.{branch}.{key}")
