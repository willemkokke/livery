"""What the workshop mounts the basedpyright extension from: its declarations, as data.

Loaded for every installed extension (``fm doctor`` and discovery read
it), so it imports what the declarations name and nothing that runs.
"""

from __future__ import annotations

from livery.extensions.basedpyright._checks import CHECKS as CHECKS
from livery.extensions.basedpyright._checks import TYPECOMPLETE

#: The workshop's extension API this module is written for.
API_VERSION = 1

#: Listed in the workspace's list alone: one configuration for every member.
LEVELS = ("workspace",)

#: Off unless the workspace lists it, ``basedpyright[typecomplete]``.
OPTIONS = {TYPECOMPLETE: "verifies that every package's public API is type-complete"}
