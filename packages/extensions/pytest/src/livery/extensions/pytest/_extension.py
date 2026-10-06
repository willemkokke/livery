"""What the workshop mounts the pytest extension from: its declarations, as data.

Loaded for every installed extension (``fm doctor`` and discovery read
it), so it imports what the declarations name and nothing that runs.
"""

from __future__ import annotations

from livery.extensions.pytest._checks import CHECKS as CHECKS

#: The workshop's extension API this module is written for.
API_VERSION = 1

#: Listed in the workspace's list alone: one configuration for every member.
LEVELS = ("workspace",)
