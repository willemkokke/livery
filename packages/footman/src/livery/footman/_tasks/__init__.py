"""footman's own built-in task families, one module per family: `self_` and `janitor`.

Each family is a `footman.tasks` entry point, `footman.self` and
`footman.janitor`, mounted with [livery.footman.api.plugin][]. The docs
family, whose functions a tasks file calls, is the public
[livery.footman.docs][]. Nothing here is imported by a bare import of
footman or on the completion hot path: a family imports only when its
plugin is mounted, so this package imports no submodule at init time.
"""

from __future__ import annotations
