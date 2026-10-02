"""The docs extension's declarations, read without its tasks.

Named under the ``workshop.extensions`` entry point group as ``docs``.
"""

from __future__ import annotations

from livery.extensions.docs._contract_keys import DECLARED as CONTRACT_KEYS

#: The workshop extension API this declaration is written for.
API_VERSION = 1

#: Listed in ``[workspace] extensions``.
LEVELS = ("workspace",)

#: The footman plugin that carries the site's verbs.
PLUGIN = "livery.extensions.docs"

__all__ = ["API_VERSION", "CONTRACT_KEYS", "LEVELS", "PLUGIN"]
