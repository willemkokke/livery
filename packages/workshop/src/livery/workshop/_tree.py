"""The task tree's groups, in one place so every module attaches cleanly."""

from __future__ import annotations

from footman import group

agent_hooks = group("hooks", hidden=True, help="Agent lifecycle hooks (stdin-driven)")
