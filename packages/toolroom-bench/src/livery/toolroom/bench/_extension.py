"""The extension declaration of ``livery.toolroom.bench``, read without its tasks.

It adds verbs alone, so it is a footman plugin listed as an extension
until the workshop mounts a project's plugins by its dependencies.
Data only: nothing here imports the workshop.
"""

from __future__ import annotations

#: The workshop extension API this declaration is written for.
API_VERSION = 1

#: Listed in ``[workspace] extensions``.
LEVELS = ("workspace",)

#: The footman plugin that carries the tools verbs.
PLUGIN = "livery.toolroom.bench"
