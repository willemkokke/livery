"""footman's own first-party task plugins — one module per family.

Each family is its own `footman.tasks` entry point, mounted with `plugin()`
(see `compose.plugin`):

* `footman.docs`  → `livery.footman.tasks.docs:tasks` — task-documentation
  generation (`fm docs …`). The end-user-facing family.

Where a family lands is the consumer's call — `into=` mounts it
wherever you want. Nothing here is imported by a bare `import footman`, or on the
completion hot path — a family imports only when its plugin is mounted, so
this package imports neither submodule at package-init time.
"""

from __future__ import annotations
