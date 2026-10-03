"""The tools the dev-container verbs need, as data the workshop reads.

The ``workshop.tools`` entry point named ``livery.forge`` names
``TOOLS`` here: a module of data alone, so reading it imports none of
the plugin's tasks. docker runs the containers, and a workspace that
never brings them up does without it.
"""

from __future__ import annotations

TOOLS = ("docker?",)
