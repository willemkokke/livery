"""The task tree's groups, in one place so every module attaches cleanly."""

from __future__ import annotations

from footman import group

forge = group("forge", help="livery.forge development")
dev = forge.group("dev", help="Local forge containers (Gitea and GitLab)")
fixtures = forge.group("fixtures", help="Recorded HTTP fixtures (cassettes)")
agent_hooks = group("hooks", hidden=True, help="Agent lifecycle hooks (stdin-driven)")
