"""The workshop's plugin entry: the base's own verbs, then each listed extension's.

The ``livery.workshop`` entry point names this module in ``footman.tasks``
and ``footman.builtin``, so a project that depends on the workshop gets
it through footman's project rung, and its ``tasks.py`` mounts nothing.
Importing the base's task module registers its verbs inside footman's
capture; [livery.workshop._extensions.mount_extensions][] then mounts
every extension the contract lists through ``plugin()``, each under its
own name.
"""

from __future__ import annotations

from livery.workshop import _tasks  # noqa: F401 - registers the base's verbs
from livery.workshop._extensions import mount_extensions

mount_extensions()
